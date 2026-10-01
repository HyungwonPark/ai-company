#!/usr/bin/env python3
"""Build a deterministic four-file trusted reviewer package and manifest."""

from __future__ import annotations

import argparse
import hashlib
import json
from pathlib import Path
import tarfile
import tempfile


FILES = {
    "runner.py": Path("scripts/review_pr_with_claude.py"),
    "claude_control.py": Path("src/ai_company/adapters/claude_control.py"),
    "shared_calls.py": Path("src/ai_company/shared_calls.py"),
    "tick.py": Path("scripts/review_claude_quota_tick.py"),
}


def sha256(path: Path) -> str:
    return hashlib.sha256(path.read_bytes()).hexdigest()


def build(output: Path, *, source_root: Path, source_commit: str, base: str,
          patch_sha256: str) -> tuple[Path, dict]:
    output = output.resolve()
    source_root = source_root.resolve()
    with tempfile.TemporaryDirectory(prefix="ai-company-review-package-") as temp:
        staging = Path(temp)
        for name, relative in FILES.items():
            source = source_root / relative
            if not source.is_file() or source.is_symlink():
                raise SystemExit(f"missing or symlinked source: {relative}")
            target = staging / name
            target.write_bytes(source.read_bytes())
            target.chmod(0o700 if name in {"runner.py", "tick.py"} else 0o600)
        manifest = {
            "schema_version": 1,
            "source_commit": source_commit,
            "base": base,
            "patch_sha256": patch_sha256,
            "files": {name: sha256(staging / name) for name in FILES},
            "entrypoint": "runner.py",
            "tick": "tick.py",
        }
        (staging / "manifest.json").write_text(
            json.dumps(manifest, ensure_ascii=False, sort_keys=True, indent=2) + "\n"
        )
        (staging / "manifest.json").chmod(0o600)
        output.parent.mkdir(parents=True, exist_ok=True)
        with output.open("wb") as stream:
            with tarfile.open(fileobj=stream, mode="w:gz", compresslevel=9,
                              format=tarfile.PAX_FORMAT) as archive:
                for name in (*FILES, "manifest.json"):
                    info = archive.gettarinfo(staging / name, arcname=name)
                    info.uid = info.gid = 0
                    info.uname = info.gname = ""
                    info.mtime = 0
                    info.mode = 0o700 if name in {"runner.py", "tick.py"} else 0o600
                    with (staging / name).open("rb") as source:
                        archive.addfile(info, source)
        manifest["package_sha256"] = sha256(output)
        output.with_suffix(output.suffix + ".sha256").write_text(
            f"{manifest['package_sha256']}  {output.name}\n"
        )
        output.with_suffix(output.suffix + ".manifest.json").write_text(
            json.dumps(manifest, ensure_ascii=False, sort_keys=True, indent=2) + "\n"
        )
    return output, manifest


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("output", type=Path)
    parser.add_argument("--source-root", type=Path, default=Path.cwd())
    parser.add_argument("--source-commit", required=True)
    parser.add_argument("--base", required=True)
    parser.add_argument("--patch-sha256", required=True)
    args = parser.parse_args()
    path, manifest = build(args.output, source_root=args.source_root,
                           source_commit=args.source_commit, base=args.base,
                           patch_sha256=args.patch_sha256)
    print(json.dumps({"package": str(path), **manifest}, ensure_ascii=False,
                     sort_keys=True))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
