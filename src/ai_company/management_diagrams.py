"""Authenticated derived artifacts; never an execution or approval source."""
import fcntl
import hashlib
import json
import os
from pathlib import Path
import re
import stat

from ai_company.diagram_export import DiagramExportError, export_diagram
from ai_company.management import ManagementError


FILES = {"index.html", "diagram.svg", "input.json", "archify.json", "mapping.json",
         "compiler-receipt.json", "receipt.json", "LICENSE", "THIRD_PARTY_NOTICES.md"}


def initialize(db):
    db.execute("""CREATE TABLE IF NOT EXISTS management_diagrams (
        project_id TEXT NOT NULL, request_key TEXT NOT NULL, request_digest TEXT NOT NULL,
        document TEXT NOT NULL, PRIMARY KEY(project_id, request_key))""")
    db.commit()


def _json(value):
    return json.dumps(value, ensure_ascii=False, sort_keys=True, separators=(",", ":"), allow_nan=False)


def _directory(store, project_id):
    store._project(project_id)
    base = store.root / "diagrams"
    target = base / project_id
    for path in (base, target):
        if path.is_symlink():
            raise ManagementError("unsafe_artifact", "그림 저장 경로를 확인할 수 없습니다.", 409)
        path.mkdir(exist_ok=True, mode=0o700)
    return target


def _public(document):
    return {key: value for key, value in document.items() if key != "snapshot"}


def listing(store, project_id):
    store._project(project_id)
    return {"project_id": project_id, "items": [_public(json.loads(row[0])) for row in store.db.execute(
        "SELECT document FROM management_diagrams WHERE project_id=? ORDER BY rowid DESC LIMIT 100", (project_id,))],
        "limit": 100}


def generate(store, project_id, value):
    """One local compiler at a time; retries use the initially frozen bytes.

    The durable prepared record survives a worker/HTTP interruption. Resuming it
    may rerun a deterministic local renderer; it cannot call a model or start work.
    Failed requests keep their result; a deliberate new request is needed to retry.
    """
    if not isinstance(value, dict) or set(value) != {"snapshot_id", "fingerprint", "idempotency_key"}:
        raise ManagementError("invalid_diagram", "그림 대상과 요청 키를 확인해 주세요.", 400)
    if (not isinstance(value["snapshot_id"], str) or not 1 <= len(value["snapshot_id"]) <= 400
            or not re.fullmatch(r"[a-f0-9]{64}", str(value["fingerprint"]))
            or not re.fullmatch(r"[A-Za-z0-9_-]{1,100}", str(value["idempotency_key"]))):
        raise ManagementError("invalid_diagram", "그림 대상과 요청 키를 확인해 주세요.", 400)
    target = _directory(store, project_id)
    request_digest = hashlib.sha256(_json(value).encode()).hexdigest()
    fd = os.open(target.parent / ".generation.lock", os.O_CREAT | os.O_RDWR | os.O_NOFOLLOW, 0o600)
    try:
        try:
            fcntl.flock(fd, fcntl.LOCK_EX | fcntl.LOCK_NB)
        except BlockingIOError as exc:
            raise ManagementError("diagram_busy", "다른 그림을 만들고 있습니다. 같은 요청으로 다시 확인해 주세요.", 409) from exc
        row = store.db.execute("SELECT request_digest,document FROM management_diagrams WHERE project_id=? AND request_key=?",
                               (project_id, value["idempotency_key"])).fetchone()
        if row:
            if row[0] != request_digest:
                raise ManagementError("idempotency_conflict", "같은 요청 키의 그림 대상이 달라졌습니다.", 409)
            document = json.loads(row[1])
            if document["status"] != "prepared":
                return _public(document)
        else:
            snapshots = store.overview(project_id)["workspace_graph"]["snapshots"]
            snapshot = next((s for s in snapshots if s["id"] == value["snapshot_id"]), None)
            if not snapshot or snapshot.get("fingerprint") != value["fingerprint"]:
                raise ManagementError("snapshot_changed", "그림 대상의 기록이 바뀌었습니다. 진행 화면에서 다시 선택해 주세요.", 409)
            if len(_json(snapshot).encode()) > 1_000_000:
                raise ManagementError("diagram_too_large", "원본이 그림 생성 크기 제한을 넘었습니다. 원본 기록은 보존합니다.", 413)
            count = store.db.execute("SELECT COUNT(*) FROM management_diagrams WHERE project_id=?", (project_id,)).fetchone()[0]
            if count >= 1000:
                raise ManagementError("diagram_storage_limit", "프로젝트 그림 보관 한도에 도달했습니다. 운영자에게 보관 정책 검토를 요청해 주세요.", 409)
            document = {"project_id": project_id, "request_key": value["idempotency_key"], "status": "prepared",
                        "snapshot_id": snapshot["id"], "snapshot_fingerprint": snapshot["fingerprint"],
                        "created_at": store.clock(), "snapshot": snapshot}
            with store.db:
                store.db.execute("INSERT INTO management_diagrams VALUES (?,?,?,?)",
                                 (project_id, value["idempotency_key"], request_digest, _json(document)))
        try:
            receipt = export_diagram(document["snapshot"], target, project_id=project_id, timeout=30)
            document.update(status="completed", receipt=receipt)
        except DiagramExportError as exc:
            document.update(status="failed", error={"code": exc.code, "message": str(exc)}, failure=exc.receipt)
        document["finished_at"] = store.clock()
        with store.db:
            store.db.execute("UPDATE management_diagrams SET document=? WHERE project_id=? AND request_key=?",
                             (_json(document), project_id, value["idempotency_key"]))
        return _public(document)
    finally:
        os.close(fd)


def read_file(store, project_id, artifact_id, name):
    if name not in FILES or not re.fullmatch(r"[a-f0-9]{64}", artifact_id):
        raise ManagementError("not_found", "그림 파일을 찾을 수 없습니다.", 404)
    store._project(project_id)
    receipt = None
    for row in store.db.execute("SELECT document FROM management_diagrams WHERE project_id=?", (project_id,)):
        document = json.loads(row[0])
        if document.get("status") == "completed" and document.get("receipt", {}).get("id") == artifact_id:
            receipt = document["receipt"]
            break
    if not receipt:
        raise ManagementError("not_found", "이 프로젝트의 그림을 찾을 수 없습니다.", 404)
    if name == "receipt.json":
        return (_json(receipt) + "\n").encode()
    directory = _directory(store, project_id) / artifact_id
    if directory.is_symlink():
        raise ManagementError("unsafe_artifact", "그림 경로가 변경되었습니다.", 409)
    expected = receipt.get("files", {}).get(name)
    if not expected:
        raise ManagementError("not_found", "그림 파일을 찾을 수 없습니다.", 404)
    try:
        fd = os.open(directory / name, os.O_RDONLY | os.O_NOFOLLOW)
        with os.fdopen(fd, "rb") as handle:
            info = os.fstat(handle.fileno())
            if not stat.S_ISREG(info.st_mode) or info.st_size > 5_000_000:
                raise OSError("invalid file")
            value = handle.read(5_000_001)
        if len(value) != expected["bytes"] or hashlib.sha256(value).hexdigest() != expected["sha256"]:
            raise OSError("invalid hash")
    except OSError as exc:
        raise ManagementError("artifact_integrity", "그림 파일 검증에 실패했습니다. 원본 기록은 보존되어 있습니다.", 409) from exc
    return value
