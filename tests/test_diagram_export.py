"""Actual pinned Archify rendering with isolated output directories, never models."""
import copy
import hashlib
import json
import os
from pathlib import Path
import shutil
import subprocess
import sys
import tempfile
import unittest
from unittest.mock import patch
import xml.etree.ElementTree as ET

from ai_company import diagram_export as diagrams


def snapshot():
    binding = {"project_id": "fixture_project", "plan_id": "fixture_plan", "plan_digest": "a" * 64, "run_id": None}
    nodes = [{**binding, "id": "graph:" + identity, "name": name, "kind": kind, "status": "PLANNED",
              "source": "fixture", "phase": "planned", "responsibility": "고정된 계약을 따릅니다."}
             for identity, name, kind in [("pm", "PM", "pm"), ("dev", "개발", "role"), ("test", "검사", "role")]]
    edges = [{**binding, "id": "edge:" + target, "from": "graph:pm", "to": "graph:" + target,
              "kind": "planned_specification", "title": "역할 제안", "reason": "실제 배정이 아닙니다.",
              "source": "fixture", "phase": "planned", "status": "planned"} for target in ("dev", "test")]
    return {**binding, "id": "graph:fixture", "fingerprint": "b" * 64, "source": "fixture", "mode": "planned",
            "cursor": 5, "observed_at": 1700000000, "nodes": nodes, "edges": edges, "warnings": [], "report_refs": [], "approval_refs": []}


@unittest.skipUnless(shutil.which("node"), "Node is required for the real pinned Archify compiler")
class DiagramExportTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        self.root = Path(self.temp.name)
        self.value = snapshot()
        self.output = self.root / "output"

    def render(self, value=None, output=None, **kwargs):
        return diagrams.export_diagram(value or self.value, output or self.output, project_id="fixture_project", **kwargs)

    def test_same_input_generates_identical_svg_html_and_receipt_in_separate_directories(self):
        first = self.render()
        second = self.render(output=self.root / "other")
        self.assertEqual(first, second)
        self.assertEqual(first["archify_commit"], diagrams.ARCHIFY_COMMIT)
        self.assertEqual(first["validation"]["compiler"], "passed")
        self.assertEqual(first["validation"]["official_deliver"], "not_run")
        self.assertEqual(first["validation"]["browser"], "not_run")
        for name, value in first["files"].items():
            path = self.output / first["directory"] / name
            self.assertEqual(hashlib.sha256(path.read_bytes()).hexdigest(), value["sha256"])
            self.assertEqual(path.read_bytes(), (self.root / "other" / second["directory"] / name).read_bytes())
        self.assertNotIn(str(self.root), json.dumps(first))

    def test_fixed_theme_previews_are_deterministic_inert_and_keep_reading_content(self):
        first = self.render()
        second = self.render(output=self.root / "second-preview")
        directory = self.output / first["directory"]
        full = (directory / "index.html").read_text()
        self.assertIn("<h1>그림</h1>", full)
        self.assertIn("실시간 상태·승인·실행을 변경하지 않습니다.", full)
        self.assertIn("min-width:900px", full)
        self.assertIn("prefers-color-scheme", full)
        for theme, paper, svg_paper, color_scheme in (("light", "#f6f7f2", "#fafbf8", "light"),
                                                     ("black", "#111a16", "#17211d", "dark")):
            name = f"preview-{theme}.html"
            page = (directory / name).read_text()
            self.assertEqual(first["files"][name], second["files"][name])
            self.assertEqual((directory / name).read_bytes(), (self.root / "second-preview" / second["directory"] / name).read_bytes())
            self.assertIn(f'data-theme="{theme}"', page)
            self.assertIn(f"color-scheme:{color_scheme}", page)
            self.assertIn(f"background:{paper}", page)
            self.assertIn(f"--paper:{svg_paper}", page)
            self.assertNotIn("prefers-color-scheme", page)
            self.assertNotIn("<h1>", page)
            self.assertNotIn("실시간 상태·승인·실행을 변경하지 않습니다.", page)
            self.assertNotIn("min-width:", page)
            self.assertNotIn("<script", page)
            self.assertIn("script-src 'none'", page)
            self.assertIn("max-width:100%", page)
            self.assertIn("min-height:44px", page)
            self.assertIn("font:1rem/1.6", page)
            self.assertIn("word-break:keep-all", page)
            self.assertIn("overflow-wrap:anywhere", page)
            self.assertLess(page.index("그림 읽기"), page.index('class="picture"'))
            self.assertLess(page.index('class="picture"'), page.index('<section id="roles">'))
            self.assertIn("PM → 개발", page)
            self.assertIn("실제 배정이 아닙니다.", page)
            self.assertIn("원본 기준", page)
            self.assertIn(self.value["plan_digest"], page)

    def test_id_mapping_and_planned_relation_semantics_are_preserved(self):
        result = self.render()
        directory = self.output / result["directory"]
        workflow = json.loads((directory / "archify.json").read_text())
        mapping = json.loads((directory / "mapping.json").read_text())
        self.assertEqual(set(mapping), {item["id"] for item in [*self.value["nodes"], *self.value["edges"]]})
        self.assertTrue(all(edge["variant"] == "dashed" for edge in workflow["edges"]))
        self.assertTrue(all(":" not in node["id"] for node in workflow["nodes"]))
        self.assertEqual(json.loads((directory / "input.json").read_text()), self.value)

    def test_korean_html_is_inert_and_user_markup_is_text(self):
        self.value["nodes"][1]["name"] = '<img onerror="X">'
        self.value["nodes"][1]["responsibility"] = '</script><script>alert(1)</script>'
        self.value["edges"][0]["reason"] = '<iframe src="https://example.invalid">'
        result = self.render()
        directory = self.output / result["directory"]
        document = (directory / "index.html").read_text()
        self.assertIn('<html lang="ko">', document)
        self.assertIn("script-src 'none'", document)
        self.assertNotIn('<script', document)
        self.assertNotIn('<iframe', document)
        self.assertNotIn('<img', document)
        svg = ET.parse(directory / "diagram.svg").getroot()
        self.assertEqual(svg.get("lang"), "ko")
        self.assertTrue(any('<img onerror="X">' == item.text for item in svg.iter()))

    def test_cross_project_plan_run_and_reference_mixing_are_rejected_before_render(self):
        changes = [("nodes", "project_id", "other"), ("edges", "plan_digest", "c" * 64), ("nodes", "run_id", "other")]
        for collection, key, value in changes:
            candidate = copy.deepcopy(self.value)
            candidate[collection][0][key] = value
            with self.subTest(collection=collection, key=key), patch.object(diagrams.subprocess, "run") as run:
                with self.assertRaises(diagrams.DiagramExportError) as error:
                    self.render(candidate)
                self.assertEqual(error.exception.code, "scope_mismatch")
                run.assert_not_called()
        candidate = copy.deepcopy(self.value)
        candidate["approval_refs"] = [{"project_id": "other"}]
        with self.assertRaises(diagrams.DiagramExportError):
            self.render(candidate)

    def test_renderer_failure_preserves_last_good_and_records_failure_reference(self):
        original = self.render()
        before = (self.output / "latest.json").read_bytes()
        changed = copy.deepcopy(self.value); changed["cursor"] += 1
        with self.assertRaises(diagrams.DiagramExportError) as error:
            self.render(changed, node_command="/definitely/missing/node")
        self.assertEqual(before, (self.output / "latest.json").read_bytes())
        failure = error.exception.receipt
        self.assertTrue(failure["previous_preserved"])
        self.assertEqual(failure, json.loads((self.output / failure["failure_reference"]).read_text()))
        self.assertEqual(original, self.render())
        self.assertFalse((self.output / "last_failure.json").exists())
        self.assertTrue((self.output / failure["failure_reference"]).exists())

    def test_self_handoff_exclusion_retains_original_relation_and_reason(self):
        edge = copy.deepcopy(self.value["edges"][0])
        edge.update(id="edge:handoff", kind="handoff", title="담당 이관", reason="공유 한도 대기 후 담당 이관",
                    **{"from": "graph:dev", "to": "graph:dev", "phase": "recorded", "status": "waiting"})
        self.value["edges"].append(edge)
        result = self.render()
        self.assertEqual(result["renderer_exclusions"][0]["edge_id"], edge["id"])
        directory = self.output / result["directory"]
        self.assertIn("공유 한도 대기 후 담당 이관", (directory / "index.html").read_text())
        for name in ("preview-light.html", "preview-black.html"):
            self.assertIn("공유 한도 대기 후 담당 이관", (directory / name).read_text())
            self.assertIn("개발 → 개발", (directory / name).read_text())
        self.assertIn("이관 이력 1건", (directory / "diagram.svg").read_text())
        self.assertIn(edge, json.loads((directory / "input.json").read_text())["edges"])

    def test_recorded_status_wait_reason_and_handoff_endpoints_and_artifact_are_readable(self):
        self.value["mode"] = "execution"
        self.value["run_id"] = "fixture_run"
        for item in [*self.value["nodes"], *self.value["edges"]]:
            item["run_id"] = "fixture_run"
        developer = self.value["nodes"][1]
        developer.update(phase="recorded", status="WAITING_QUOTA", wait_reason="공유 계정 한도 해제를 기다립니다. 예약은 계속 보존됩니다.")
        self.value["nodes"][2].update(phase="recorded", status="NEW_UNKNOWN_STATE")
        edge = copy.deepcopy(self.value["edges"][0])
        edge.update(id="edge:handoff", kind="handoff", title="담당 이관", reason="공유 한도 해제 후 이관",
                    **{"from": "graph:dev", "to": "graph:dev", "phase": "recorded", "status": "waiting",
                       "artifact_refs": [{"label": "인계 체크포인트", "sha": "c" * 64}]})
        self.value["edges"].append(edge)
        result = self.render()
        directory = self.output / result["directory"]
        page = (directory / "index.html").read_text()
        self.assertIn("개발 → 개발", page)
        self.assertIn("담당 이관 · 대기", page)
        self.assertIn("인계 체크포인트", page)
        self.assertIn("c" * 64, page)
        self.assertIn(developer["wait_reason"], page)
        workflow = json.loads((directory / "archify.json").read_text())
        node = next(node for node in workflow["nodes"] if node["label"] == "개발")
        self.assertEqual(node["sublabel"], "사용량 대기")
        self.assertIn("공유 계정 한도", node["tag"])
        other = next(node for node in workflow["nodes"] if node["label"] == "검사")
        self.assertEqual(other["sublabel"], "NEW_UNKNOWN_STATE")
        self.assertIn("사용량 대기", (directory / "diagram.svg").read_text())

    def test_cached_artifact_tampering_and_receipt_path_traversal_are_rejected(self):
        result = self.render()
        directory = self.output / result["directory"]
        with (directory / "index.html").open("a") as stream:
            stream.write("tampered")
        with self.assertRaises(diagrams.DiagramExportError) as error:
            self.render()
        self.assertEqual(error.exception.code, "artifact_integrity")
        receipt = json.loads((directory / "receipt.json").read_text())
        receipt["files"]["../../outside"] = {"sha256": "x"}
        (directory / "receipt.json").write_text(json.dumps(receipt))
        with self.assertRaises(diagrams.DiagramExportError) as error:
            self.render()
        self.assertEqual(error.exception.code, "artifact_integrity")

    def test_symlink_output_and_cached_file_are_rejected(self):
        outside = self.root / "outside"; outside.mkdir()
        self.output.symlink_to(outside, target_is_directory=True)
        with self.assertRaises(diagrams.DiagramExportError):
            self.render()
        self.assertEqual(list(outside.iterdir()), [])
        self.output.unlink()
        result = self.render()
        html_path = self.output / result["directory"] / "index.html"
        html_path.unlink(); html_path.symlink_to(outside / "secret")
        with self.assertRaises(diagrams.DiagramExportError) as error:
            self.render()
        self.assertEqual(error.exception.code, "unsafe_path")

    def test_vendor_manifest_tampering_is_rejected(self):
        package = self.root / "package"
        vendor = package / "vendor" / "archify"; vendor.mkdir(parents=True)
        manifest = json.loads((diagrams.ROOT / "vendor" / "archify" / "UPSTREAM.json").read_text())
        manifest["files"]["../../external"] = "x"
        (vendor / "UPSTREAM.json").write_text(json.dumps(manifest))
        with patch.object(diagrams, "ROOT", package), self.assertRaises(diagrams.DiagramExportError) as error:
            self.render()
        self.assertEqual(error.exception.code, "renderer_integrity")

    def test_timeout_and_limits_do_not_change_existing_artifact(self):
        self.render(); original = (self.output / "latest.json").read_bytes()
        changed = copy.deepcopy(self.value); changed["cursor"] += 1
        with patch.object(diagrams.subprocess, "run", side_effect=subprocess.TimeoutExpired("node", 1)):
            with self.assertRaises(diagrams.DiagramExportError):
                self.render(changed)
        self.assertEqual(original, (self.output / "latest.json").read_bytes())
        for timeout in (0, 121, True):
            with self.assertRaises(diagrams.DiagramExportError):
                self.render(timeout=timeout)

    def test_oversized_input_and_executable_svg_are_rejected(self):
        oversized = copy.deepcopy(self.value)
        oversized["nodes"][0]["responsibility"] = "x" * 2_000_001
        with patch.object(diagrams.subprocess, "run") as run:
            with self.assertRaises(diagrams.DiagramExportError):
                self.render(oversized)
            run.assert_not_called()
        original = self.render()
        changed = copy.deepcopy(self.value); changed["cursor"] += 1
        for svg in ('<svg viewBox="0 0 20 20"><script>alert(1)</script></svg>',
                    '<svg viewBox="0 0 20 20"><image href="https://example.invalid/x"/></svg>'):
            result = subprocess.CompletedProcess([], 0, stdout=json.dumps({"ok": True, "svg": svg, "compiler_receipt": {}}).encode())
            with patch.object(diagrams.subprocess, "run", return_value=result):
                with self.assertRaises(diagrams.DiagramExportError) as error:
                    self.render(changed)
            self.assertEqual(error.exception.code, "unsafe_svg")
        self.assertEqual(json.loads((self.output / "latest.json").read_text())["id"], original["id"])

    def test_package_relative_renderer_works_outside_repository_checkout(self):
        package = self.root / "installed" / "ai_company"
        package.mkdir(parents=True)
        (package / "__init__.py").write_text("")
        shutil.copyfile(diagrams.__file__, package / "diagram_export.py")
        shutil.copytree(diagrams.ROOT / "vendor", package / "vendor")
        value = self.root / "snapshot.json"; value.write_text(json.dumps(self.value))
        program = "import json;from ai_company.diagram_export import export_diagram;s=json.load(open('snapshot.json'));r=export_diagram(s,'installed-output',project_id=s['project_id']);print(r['status'])"
        result = subprocess.run([sys.executable, "-c", program], cwd=self.root, env={"PYTHONPATH": str(package.parent), "PATH": os.environ["PATH"]}, capture_output=True, text=True, check=False)
        self.assertEqual(result.returncode, 0, result.stderr)
        self.assertEqual(result.stdout.strip(), "completed")

    def test_operator_node_path_resolved_without_inheriting_node_options(self):
        executable = shutil.which("node")
        self.assertTrue(executable)
        installed = self.root / "operator-bin"
        installed.mkdir()
        (installed / "node").symlink_to(executable)
        # Reproduce hosted CI/NVM-style runtime placement outside os.defpath.
        with patch.dict(os.environ, {"PATH": str(installed), "NODE_OPTIONS": "--require /must-not-load.js"}):
            self.assertEqual(self.render()["status"], "completed")
