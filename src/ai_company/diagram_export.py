"""Deterministic, explicitly requested Archify snapshots. No execution authority."""
from __future__ import annotations

import hashlib
import html
import json
import math
import os
from pathlib import Path
import re
import shutil
import subprocess
import tempfile
import xml.etree.ElementTree as ET

ARCHIFY_COMMIT = "72c750bb070d95171dbb2244e5b62b1b7da69c12"
GENERATOR_VERSION = "ai-company-archify-1"
MANIFEST_SHA256 = "ea780a88c751270cee61afc120948dba2673073bf7b102fe9e4d429172816a65"
ROOT = Path(__file__).resolve().parent
BINDING = ("project_id", "plan_id", "plan_digest", "run_id")
CSS = """svg{--paper:#fafbf8;--ink:#24342e;--line:#74867d;--accent:#276c56;background:var(--paper);font-family:'Noto Sans CJK KR','Noto Sans KR',sans-serif}svg text{fill:var(--ink)}.c-mask{fill:var(--paper)}.c-grid{fill:none;stroke:#dfe7df}.c-lane{fill:#f1f5ef;stroke:#d6dfd5}.c-backend,.c-external,.c-database,.c-security,.c-frontend,.c-cloud,.c-messagebus{fill:var(--paper);stroke:var(--accent)}.c-security-group{fill:#fcf4e8;stroke:#936b35}.t-primary,.t-muted,.t-backend,.t-external,.t-database,.t-security,.t-frontend,.t-cloud,.t-messagebus{fill:var(--ink)}.a-default,.a-emphasis,.a-security,.a-dashed{fill:none;stroke:var(--accent)}.a-dashed{stroke-dasharray:5 4}.m-default,.m-emphasis,.m-security,.m-dashed{fill:var(--accent)}.semantic-sigil{fill:none;stroke:var(--accent);stroke-width:1}.sigil-fill{fill:var(--accent)}@media(prefers-color-scheme:dark){svg{--paper:#17211d;--ink:#edf4ec;--line:#9bb2a5;--accent:#a5d7bb}.c-lane{fill:#1e2b24;stroke:#45594d}.c-grid{stroke:#26382e}}"""


STATE_LABELS = {"PLANNED": "예정", "RUNNING": "진행", "READY": "준비", "IDLE": "대기", "COMPLETE": "완료", "COMPLETED": "완료", "AVAILABLE": "확인 가능", "MERGE_READY": "검수 통과", "CONTRIBUTION_READY": "산출물 준비", "WAITING_CAPACITY": "공유 한도 대기", "WAITING_QUOTA": "사용량 대기", "WAITING_RETRY": "재시도 대기", "WAITING_DEPENDENCY": "선행 작업 대기", "BLOCKED": "차단", "STOPPED": "중단", "FAILED": "실패", "pending": "미결", "completed": "완료", "waiting": "대기", "started": "시작", "received": "수신", "failed": "실패", "planned": "예정"}


def _status(value):
    return STATE_LABELS.get(value, value or "상태 미확인")


class DiagramExportError(ValueError):
    def __init__(self, code, message, receipt=None):
        super().__init__(message)
        self.code = code
        self.receipt = receipt


def _bytes(value):
    return (json.dumps(value, ensure_ascii=False, sort_keys=True, separators=(",", ":"), allow_nan=False) + "\n").encode()


def _hash(value):
    return hashlib.sha256(value).hexdigest()


def _text(value, limit=10000):
    if not isinstance(value, str) or len(value) > limit or any(ord(c) < 32 and c not in "\n\t" for c in value):
        raise DiagramExportError("invalid_text", "그림에 표시할 문자열을 확인해 주세요.")
    return value


def _validate(snapshot, project_id):
    if not isinstance(snapshot, dict) or snapshot.get("project_id") != project_id:
        raise DiagramExportError("project_mismatch", "프로젝트와 그림의 원본이 다릅니다.")
    if not re.fullmatch(r"[A-Za-z0-9_-]{1,100}", project_id):
        raise DiagramExportError("invalid_project", "프로젝트 식별자가 올바르지 않습니다.")
    if snapshot.get("source") not in ("fixture", "planned", "execution") or snapshot.get("mode") not in ("planned", "execution"):
        raise DiagramExportError("invalid_source", "계획·실행·예시 출처를 확인할 수 없습니다.")
    if not re.fullmatch(r"[a-f0-9]{64}", str(snapshot.get("plan_digest", ""))):
        raise DiagramExportError("plan_required", "고정된 계획 digest가 필요합니다.")
    _text(snapshot.get("id"), 400)
    _text(snapshot.get("plan_id"), 100)
    if snapshot.get("run_id") is not None:
        _text(snapshot["run_id"], 100)
    if not snapshot.get("plan_id") or (snapshot["mode"] == "execution") != bool(snapshot.get("run_id")):
        raise DiagramExportError("invalid_binding", "계획과 실행 연결이 올바르지 않습니다.")
    if snapshot.get("warnings"):
        raise DiagramExportError("unverified_binding", "원본 연결에 경고가 있어 그림을 고정하지 않았습니다.")
    if isinstance(snapshot.get("cursor"), bool) or not isinstance(snapshot.get("cursor"), int) or snapshot["cursor"] < 0:
        raise DiagramExportError("invalid_cursor", "조회 순서를 확인할 수 없습니다.")
    at = snapshot.get("observed_at")
    if isinstance(at, bool) or not isinstance(at, (int, float)) or not math.isfinite(at):
        raise DiagramExportError("invalid_time", "자료 기준 시각을 확인할 수 없습니다.")
    nodes, edges = snapshot.get("nodes"), snapshot.get("edges")
    if not isinstance(nodes, list) or not 1 <= len(nodes) <= 32 or not isinstance(edges, list) or len(edges) > 128:
        raise DiagramExportError("size_limit", "첫 그림은 노드 32개·관계 128개까지 지원합니다. 원본 이력은 보존합니다.")
    identities = set()
    for item in [*nodes, *edges]:
        if not isinstance(item, dict) or any(item.get(key) != snapshot.get(key) for key in BINDING):
            raise DiagramExportError("scope_mismatch", "다른 프로젝트·계획·실행의 자료가 섞여 있습니다.")
        identity = _text(item.get("id"), 400)
        if identity in identities:
            raise DiagramExportError("duplicate_identity", "중복된 노드 또는 관계 식별자가 있습니다.")
        identities.add(identity)
        if item.get("phase") not in ("planned", "recorded"):
            raise DiagramExportError("invalid_phase", "예정 관계와 저장된 관계를 구분할 수 없습니다.")
        if item.get("source") not in ("fixture", "planned", "execution"):
            raise DiagramExportError("invalid_source", "노드 또는 관계의 출처를 확인할 수 없습니다.")
        if snapshot["source"] != "fixture" and item.get("source") == "fixture":
            raise DiagramExportError("fixture_mismatch", "예시 자료를 실제 실행 그림으로 표시할 수 없습니다.")
    for field in ("report_refs", "approval_refs"):
        for reference in snapshot.get(field, []):
            if not isinstance(reference, dict) or any(reference.get(key) != snapshot.get(key) for key in BINDING):
                raise DiagramExportError("scope_mismatch", "다른 실행의 보고 또는 승인 참조가 섞여 있습니다.")
    node_ids = {node["id"] for node in nodes}
    if any(edge.get("from") not in node_ids or edge.get("to") not in node_ids for edge in edges):
        raise DiagramExportError("missing_endpoint", "연결선의 원본 노드를 찾을 수 없습니다.")
    for node in nodes:
        _text(node.get("name", "")); _text(node.get("status", ""))
    for edge in edges:
        _text(edge.get("title") or edge.get("kind", "")); _text(edge.get("reason") or ""); _text(edge.get("status") or "")


def to_archify_workflow(snapshot, *, project_id):
    _validate(snapshot, project_id)
    nodes = sorted(snapshot["nodes"], key=lambda n: n["id"])
    edges = sorted(snapshot["edges"], key=lambda e: e["id"])
    mapping = {item["id"]: "a_" + _hash(item["id"].encode())[:32] for item in [*nodes, *edges]}
    columns = {"pm": 0, "role": 1, "task": 1, "check": 2, "reviewer": 3, "final": 4, "document": 5, "translator": 5}
    lanes, translated = [], []
    for index, node in enumerate(nodes):
        lane = "lane_" + str(index)
        lanes.append({"id": lane, "label": _text(node["name"], 100)})
        status = "예정" if node["phase"] == "planned" else _status(node.get("status"))
        wait = _text(node.get("wait_reason") or "")
        brief = wait[:24] + ("…" if len(wait) > 24 else "")
        tag = ("대기 · " + brief) if brief and str(node.get("status", "")).startswith("WAIT") else None
        translated.append({"id": mapping[node["id"]], "lane": lane, "col": columns.get(node.get("kind"), 1),
                           "type": "backend", "label": node["name"],
                           "width": max(160, len(node["name"]) * 14 + 48, len(status) * 11 + 48, len(tag or "") * 9 + 48),
                           "height": 86 if tag else 64, "sublabel": status,
                           **({"tag": tag} if tag else {})})
    workflow = {"schema_version": 2, "diagram_type": "workflow",
                "meta": {"title": "역할 계획" if snapshot["mode"] == "planned" else "실행 보고", "quality_profile": "standard",
                         "animation": "none", "legend": {"mode": "hidden"}}, "lanes": lanes, "nodes": translated,
                "edges": [{"id": mapping[edge["id"]], "from": mapping[edge["from"]], "to": mapping[edge["to"]],
                           "label": _text(edge.get("title") or edge["kind"], 200),
                           "variant": "dashed" if edge["phase"] == "planned" else "default"} for edge in edges if edge["from"] != edge["to"]]}
    return workflow, mapping


def _verify_vendor():
    directory = ROOT / "vendor" / "archify"
    manifest_path = directory / "UPSTREAM.json"
    if manifest_path.resolve() != manifest_path.absolute() or _hash(manifest_path.read_bytes()) != MANIFEST_SHA256:
        raise DiagramExportError("renderer_integrity", "Archify 파일 허용 목록이 변경됐습니다.")
    manifest = json.loads(manifest_path.read_text())
    if manifest.get("commit") != ARCHIFY_COMMIT:
        raise DiagramExportError("renderer_version", "고정 Archify 버전이 다릅니다.")
    for name, expected in manifest["files"].items():
        file = directory / name
        if file.resolve() != file.absolute() or not file.resolve().is_relative_to(directory.resolve()) or _hash(file.read_bytes()) != expected:
            raise DiagramExportError("renderer_integrity", "Archify 원본 파일 검증에 실패했습니다.")
    return _hash(_bytes(manifest))


def _safe_svg(value, snapshot):
    if len(value) > 4_000_000 or "<!DOCTYPE" in value.upper() or "<!ENTITY" in value.upper():
        raise DiagramExportError("unsafe_svg", "SVG 구조를 확인할 수 없습니다.")
    root = ET.fromstring(value)
    allowed = {"svg", "title", "desc", "defs", "marker", "polygon", "pattern", "path", "rect", "text", "line", "g", "circle", "ellipse", "polyline", "tspan"}
    for item in root.iter():
        if item.tag not in allowed:
            raise DiagramExportError("unsafe_svg", "SVG에 허용되지 않은 요소가 있습니다.")
        for key, text in item.attrib.items():
            if key.lower().startswith("on") or key.lower().endswith("href") or "javascript:" in text.lower() or "url(" in text and not re.fullmatch(r"url\(#[A-Za-z0-9_-]+\)", text):
                raise DiagramExportError("unsafe_svg", "SVG에 외부 참조 또는 실행 요소가 있습니다.")
    root.set("xmlns", "http://www.w3.org/2000/svg")
    root.set("lang", "ko")
    for item in root.findall("desc"):
        item.text = "고정된 계획 또는 실행 기록을 그린 그림입니다. 실행 순서나 승인 권한을 변경하지 않습니다."
    style = ET.Element("style")
    style.text = CSS
    root.insert(0, style)
    metadata = ET.SubElement(root, "metadata")
    metadata.text = _bytes({key: snapshot.get(key) for key in (*BINDING, "source", "mode", "observed_at", "cursor")}).decode()
    skipped = sum(edge["from"] == edge["to"] for edge in snapshot["edges"])
    width, height = (float(part) for part in root.get("viewBox").split()[2:])
    root.set("viewBox", f"0 0 {width:g} {height + 130:g}")
    lines = [("예시 · " if snapshot["source"] == "fixture" else "") + f"고정 자료 · 조회 순서 {snapshot['cursor']} · 이관 이력 {skipped}건은 원본 목록에 보존",
             "프로젝트 " + str(snapshot["project_id"]), "계획 " + str(snapshot["plan_id"]) + " · 실행 " + str(snapshot.get("run_id") or "없음"),
             "계획 digest " + str(snapshot["plan_digest"]), "자료 시각 " + str(snapshot["observed_at"])]
    for index, text in enumerate(lines):
        footer = ET.SubElement(root, "text", {"x": "12", "y": str(height + 20 + index * 22), "font-size": "12"})
        footer.text = text
    return ET.tostring(root, encoding="unicode")


def _document_parts(snapshot):
    esc = lambda value: html.escape(str(value), quote=True)
    facts = "".join(f"<dt>{label}</dt><dd>{esc(snapshot.get(key) or '없음')}</dd>" for key, label in
                    (("project_id", "프로젝트"), ("plan_id", "계획"), ("plan_digest", "계획 digest"), ("run_id", "실행"),
                     ("observed_at", "자료 시각"), ("cursor", "조회 순서")))
    rows = "".join(f"<li><strong>{esc(node['name'])}</strong> · {esc(_status(node.get('status')))}<p>{esc(node.get('current_task_title') or node.get('responsibility',''))}</p>{('<p>대기 이유 · ' + esc(node['wait_reason']) + '</p>') if node.get('wait_reason') else ''}</li>" for node in snapshot["nodes"])
    names = {node["id"]: node["name"] for node in snapshot["nodes"]}
    relationships = []
    for edge in snapshot["edges"]:
        artifacts = "".join(f"<li>{esc(item.get('label') or '산출물')} · <code>{esc(item.get('sha') or '식별자 미확인')}</code></li>" for item in edge.get("artifact_refs", []))
        relationships.append(f"<li><strong>{esc(names[edge['from']])} → {esc(names[edge['to']])}</strong><p>{esc(edge.get('title') or edge['kind'])} · {esc(_status(edge.get('status')))} · {'예정' if edge['phase']=='planned' else '저장된 관계'}</p><p>{esc(edge.get('reason') or '')}</p><details><summary>근거</summary><p>관계 ID · <code>{esc(edge['id'])}</code></p>{('<ul>' + artifacts + '</ul>') if artifacts else '<p>연결된 산출물 식별자 없음</p>'}</details></li>")
    relationships = "".join(relationships)
    return facts, rows, relationships


def _html(snapshot, svg):
    facts, rows, relationships = _document_parts(snapshot)
    excluded = sum(edge["from"] == edge["to"] for edge in snapshot["edges"])
    exclusion = f"자기 자신에게 연결되는 이관 이력 {excluded}건은 그림의 선에서 제외하고 아래 원본 관계 목록에 보존했습니다." if excluded else "모든 조회 관계를 그림에 포함했습니다."
    source = "예시 자료 · 실제 실행 아님" if snapshot["source"] == "fixture" else "고정 계획 · 실제 실행 전" if snapshot["mode"] == "planned" else "특정 시점의 저장된 실행 기록"
    return f'''<!doctype html><html lang="ko"><meta charset="utf-8"><meta name="viewport" content="width=device-width,initial-scale=1"><meta http-equiv="Content-Security-Policy" content="default-src 'none'; style-src 'unsafe-inline'; script-src 'none'; base-uri 'none'; form-action 'none'"><title>AI Company · 그림</title><style>body{{margin:0;background:#f6f7f2;color:#24342e;font:1rem/1.6 'Noto Sans CJK KR','Noto Sans KR',sans-serif}}main{{max-width:1100px;margin:auto;padding:24px}}h1{{font-size:1.75rem}}a,summary{{color:inherit;min-height:44px}}.picture{{overflow:auto;border:1px solid #9cae9e}}.picture svg{{display:block;min-width:900px;width:100%;height:auto}}dt{{font-weight:bold}}dd{{margin:0 0 12px;overflow-wrap:anywhere}}li{{margin:16px 0}}p{{margin:4px 0}}code{{overflow-wrap:anywhere}}nav{{display:flex;gap:24px;margin:16px 0;flex-wrap:wrap}}@media(prefers-color-scheme:dark){{body{{background:#111a16;color:#edf4ec}}}}@media(max-width:400px){{main{{padding:16px}}}}</style><main><h1>그림</h1><p>{source}</p><p>아래 그림은 고정된 자료입니다. 실시간 상태·승인·실행을 변경하지 않습니다.</p><nav><a href="#roles">역할 목록</a><a href="#relations">관계 목록</a><a href="#source">원본 기준</a></nav><p>{exclusion}</p><div class="picture" tabindex="0" role="region" aria-label="좌우로 이동할 수 있는 역할 그림">{svg}</div><p>좁은 화면에서는 그림을 좌우로 이동할 수 있습니다. 같은 내용은 아래 목록에서도 읽을 수 있습니다.</p><section id="roles"><h2>역할</h2><ul>{rows}</ul></section><section id="relations"><h2>관계</h2><ul>{relationships}</ul></section><details id="source"><summary>원본 기준</summary><dl>{facts}</dl><p>Archify {ARCHIFY_COMMIT} · {GENERATOR_VERSION}</p><p>그림 검사는 실행 검사·독립 검수·마스터 승인을 대신하지 않습니다.</p></details></main></html>'''


def _preview_html(snapshot, svg, theme):
    """A fixed-theme inert frame. The parent owns the heading and source notice."""
    if theme not in ("light", "black"):
        raise ValueError("unsupported preview theme")
    facts, rows, relationships = _document_parts(snapshot)
    dark = theme == "black"
    paper, ink, border = ("#111a16", "#edf4ec", "#45594d") if dark else ("#f6f7f2", "#24342e", "#9cae9e")
    svg_css = CSS.split("@media(prefers-color-scheme:dark)", 1)[0]
    if dark:
        svg_css += "svg{--paper:#17211d;--ink:#edf4ec;--line:#9bb2a5;--accent:#a5d7bb}.c-lane{fill:#1e2b24;stroke:#45594d}.c-grid{stroke:#26382e}.c-security-group{fill:#352b1e;stroke:#ba996b}"
    # This exact stylesheet is inserted by _safe_svg, not supplied by the caller.
    expected = "<style>" + CSS + "</style>"
    if svg.count(expected) != 1:
        raise DiagramExportError("unsafe_svg", "고정 그림 스타일을 확인할 수 없습니다.")
    preview_svg = svg.replace(expected, "<style>" + svg_css + "</style>", 1)
    return f'''<!doctype html><html lang="ko" data-theme="{theme}"><meta charset="utf-8"><meta name="viewport" content="width=device-width,initial-scale=1"><meta http-equiv="Content-Security-Policy" content="default-src 'none'; style-src 'unsafe-inline'; script-src 'none'; base-uri 'none'; form-action 'none'"><title>AI Company · 그림 미리보기</title><style>:root{{color-scheme:{'dark' if dark else 'light'}}}body{{margin:0;background:{paper};color:{ink};font:1rem/1.6 'Noto Sans CJK KR','Noto Sans KR',sans-serif;word-break:keep-all;overflow-wrap:break-word}}main{{max-width:1100px;margin:auto;padding:0 1rem 1rem}}nav{{display:flex;gap:1.5rem;flex-wrap:wrap;margin:0 0 .5rem}}a,summary{{color:inherit;min-height:44px;display:inline-flex;align-items:center}}a:focus-visible,summary:focus-visible{{outline:2px solid currentColor;outline-offset:2px}}.picture{{border:1px solid {border}}}.picture svg{{display:block;width:100%;max-width:100%;height:auto}}h2{{font-size:1.25rem;margin:1.5rem 0 .5rem}}li{{margin:1rem 0}}p{{margin:.25rem 0}}ul{{padding-left:1.25rem}}dt{{font-weight:bold}}dd{{margin:0 0 .75rem}}code,dd{{word-break:normal;overflow-wrap:anywhere}}section,details{{scroll-margin-top:1rem}}</style><main><nav aria-label="그림 읽기"><a href="#roles">역할 목록</a><a href="#relations">관계 목록</a></nav><div class="picture" role="region" aria-label="역할 그림 전체 개요">{preview_svg}</div><section id="roles"><h2>역할</h2><ul>{rows}</ul></section><section id="relations"><h2>관계</h2><ul>{relationships}</ul></section><details id="source"><summary>원본 기준</summary><dl>{facts}</dl><p>Archify {ARCHIFY_COMMIT} · {GENERATOR_VERSION}</p></details></main></html>'''


def _atomic_json(path, value):
    fd, name = tempfile.mkstemp(prefix=".receipt-", dir=path.parent)
    try:
        with os.fdopen(fd, "wb") as output:
            output.write(_bytes(value))
        os.replace(name, path)
    finally:
        if os.path.exists(name):
            os.unlink(name)


def export_diagram(snapshot, output_dir, *, project_id, node_command="node", timeout=60):
    """Create a content-addressed bundle. Failed attempts never replace latest.json.

    Paths in receipts are relative opaque bundle paths; server code must bind the
    directory to the authenticated project, never accept an arbitrary client path.
    """
    target = Path(output_dir)
    if target.is_symlink() or target.resolve() != target.absolute():
        raise DiagramExportError("unsafe_path", "심볼릭 링크를 그림 저장 경로로 사용할 수 없습니다.")
    target.mkdir(parents=True, exist_ok=True)
    encoded = _bytes(snapshot)
    if len(encoded) > 2_000_000 or isinstance(timeout, bool) or not isinstance(timeout, (int, float)) or not 1 <= timeout <= 120:
        raise DiagramExportError("size_limit", "그림 입력 또는 실행 시간 제한을 초과했습니다.")
    request_hash = _hash(encoded)
    try:
        workflow, mapping = to_archify_workflow(snapshot, project_id=project_id)
        vendor_hash = _verify_vendor()
        adapter_hash = _hash((ROOT / "vendor" / "diagram_render.mjs").read_bytes())
        generator_hash = _hash(Path(__file__).read_bytes())
        identity = _hash(_bytes([project_id, request_hash, GENERATOR_VERSION, generator_hash, vendor_hash, adapter_hash]))
        final = target / identity
        if final.is_symlink():
            raise DiagramExportError("unsafe_path", "그림 보관 경로에 심볼릭 링크가 있습니다.")
        if final.exists():
            if any(file.is_symlink() for file in final.iterdir()):
                raise DiagramExportError("unsafe_path", "그림 파일에 심볼릭 링크가 있습니다.")
            receipt = json.loads((final / "receipt.json").read_text())
            expected_files = {"input.json", "archify.json", "diagram.svg", "index.html", "preview-light.html", "preview-black.html", "mapping.json", "compiler-receipt.json", "LICENSE", "THIRD_PARTY_NOTICES.md"}
            if (receipt.get("id") != identity or receipt.get("input_sha256") != request_hash
                    or any(receipt.get(key) != snapshot.get(key) for key in (*BINDING, "source", "mode", "cursor", "observed_at"))
                    or receipt.get("archify_commit") != ARCHIFY_COMMIT or receipt.get("generator_sha256") != generator_hash
                    or set(receipt.get("files", {})) != expected_files):
                raise DiagramExportError("artifact_integrity", "그림 보관 receipt가 고정 대상과 다릅니다.")
            for name, record in receipt["files"].items():
                if _hash((final / name).read_bytes()) != record["sha256"]:
                    raise DiagramExportError("artifact_integrity", "저장된 그림 파일 hash가 다릅니다.")
            _atomic_json(target / "latest.json", receipt)
            (target / "last_failure.json").unlink(missing_ok=True)
            return receipt
        with tempfile.TemporaryDirectory(prefix=".diagram-", dir=target) as temporary:
            staging = Path(temporary)
            (staging / "input.json").write_bytes(_bytes(snapshot))
            (staging / "archify.json").write_bytes(_bytes(workflow))
            # Resolve the trusted server runtime before clearing the child environment.
            # HTTP callers cannot provide this command or PATH; NODE_OPTIONS is never inherited.
            executable = shutil.which(node_command)
            if not executable:
                raise DiagramExportError("node_unavailable", "그림 생성용 Node 실행기를 찾지 못했습니다. 운영자에게 실행 환경 확인을 요청해 주세요.")
            process = subprocess.run([executable, str(ROOT / "vendor" / "diagram_render.mjs"), str(staging / "archify.json")],
                capture_output=True, timeout=timeout, check=False, env={"PATH": os.defpath, "ARCHIFY_UPDATE_CHECK_DISABLED": "1", "LANG": "C.UTF-8"})
            if process.returncode:
                raise DiagramExportError("renderer_failed", "Archify 배치 검사가 실패했습니다. 이전 그림은 유지합니다.")
            rendered = json.loads(process.stdout)
            if not rendered.get("ok"):
                raise DiagramExportError("renderer_failed", "Archify 컴파일러가 그림을 승인하지 않았습니다.")
            svg = _safe_svg(rendered["svg"], snapshot)
            (staging / "diagram.svg").write_text(svg, encoding="utf-8")
            (staging / "index.html").write_text(_html(snapshot, svg), encoding="utf-8")
            for theme in ("light", "black"):
                (staging / f"preview-{theme}.html").write_text(_preview_html(snapshot, svg, theme), encoding="utf-8")
            (staging / "mapping.json").write_bytes(_bytes(mapping))
            (staging / "compiler-receipt.json").write_bytes(_bytes(rendered["compiler_receipt"]))
            shutil.copyfile(ROOT / "vendor" / "archify" / "LICENSE", staging / "LICENSE")
            shutil.copyfile(ROOT / "vendor" / "archify" / "THIRD_PARTY_NOTICES.md", staging / "THIRD_PARTY_NOTICES.md")
            receipt = {"schema_version": 1, "id": identity, "status": "completed", **{k: snapshot.get(k) for k in BINDING},
                "snapshot_id": snapshot.get("id"), "snapshot_fingerprint": snapshot.get("fingerprint"), "source": snapshot["source"],
                "mode": snapshot["mode"], "cursor": snapshot["cursor"], "observed_at": snapshot["observed_at"], "input_sha256": request_hash,
                "generator_version": GENERATOR_VERSION, "generator_sha256": generator_hash, "adapter_sha256": adapter_hash,
                "archify_commit": ARCHIFY_COMMIT, "vendor_sha256": vendor_hash, "directory": identity,
                "renderer_exclusions": [{"edge_id": edge["id"], "reason": "self_loop_unsupported", "original_reason": edge.get("reason"), "status": edge.get("status")}
                                        for edge in snapshot["edges"] if edge["from"] == edge["to"]],
                "validation": {"compiler": "passed", "profile": "standard", "scope_and_svg": "passed", "browser": "not_run", "visual_review": "not_run", "official_deliver": "not_run"},
                "files": {file.name: {"sha256": _hash(file.read_bytes()), "bytes": file.stat().st_size} for file in sorted(staging.iterdir())}}
            (staging / "receipt.json").write_bytes(_bytes(receipt))
            os.rename(staging, final)
        _atomic_json(target / "latest.json", receipt)
        (target / "last_failure.json").unlink(missing_ok=True)
        return receipt
    except (DiagramExportError, OSError, ValueError, subprocess.SubprocessError) as error:
        failure = {"schema_version": 1, "status": "failed", "project_id": project_id, "input_sha256": request_hash,
                   "snapshot_id": snapshot.get("id") if isinstance(snapshot, dict) else None,
                   "code": getattr(error, "code", "generation_failed"), "generator_version": GENERATOR_VERSION,
                   "archify_commit": ARCHIFY_COMMIT, "previous_preserved": (target / "latest.json").exists()}
        failure_id = _hash(_bytes(failure))
        failures = target / "failures"
        if failures.is_symlink():
            raise DiagramExportError("unsafe_path", "실패 기록 경로에 심볼릭 링크가 있습니다.") from error
        failures.mkdir(exist_ok=True)
        failure["failure_reference"] = "failures/" + failure_id + ".json"
        _atomic_json(failures / (failure_id + ".json"), failure)
        _atomic_json(target / "last_failure.json", failure)
        raise DiagramExportError(failure["code"], str(error) if isinstance(error, DiagramExportError) else "그림 생성에 실패했습니다. 원본과 이전 그림은 유지됩니다.", failure) from error
