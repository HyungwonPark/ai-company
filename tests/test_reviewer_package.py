import hashlib
import importlib.util
import json
from pathlib import Path
import subprocess
import tarfile
import tempfile
import unittest


ROOT = Path(__file__).resolve().parents[1]
PACKAGER = ROOT / 'scripts/package_claude_reviewer.py'


class ReviewerPackageTests(unittest.TestCase):
    def test_package_contains_explicit_four_files_and_manifest_hashes(self):
        with tempfile.TemporaryDirectory() as temporary:
            output = Path(temporary) / 'reviewer.tar.gz'
            subprocess.run([
                'python3', str(PACKAGER), str(output), '--source-root', str(ROOT),
                '--source-commit', 'a' * 40, '--base', 'b' * 40,
                '--patch-sha256', 'c' * 64,
            ], check=True, capture_output=True, text=True)
            self.assertEqual(len(hashlib.sha256(output.read_bytes()).hexdigest()), 64)
            with tarfile.open(output, 'r:gz') as archive:
                names = set(archive.getnames())
                self.assertEqual(names, {'runner.py', 'claude_control.py',
                                         'shared_calls.py', 'tick.py', 'manifest.json'})
                manifest = json.loads(archive.extractfile('manifest.json').read())
                self.assertEqual(set(manifest['files']), {
                    'runner.py', 'claude_control.py', 'shared_calls.py', 'tick.py'})
                for name, expected in manifest['files'].items():
                    self.assertEqual(hashlib.sha256(
                        archive.extractfile(name).read()).hexdigest(), expected)
            external = json.loads(output.with_suffix(output.suffix + '.manifest.json').read_text())
            self.assertEqual(external['package_sha256'], hashlib.sha256(output.read_bytes()).hexdigest())

    def test_staged_runner_loads_four_file_package_without_source_imports(self):
        with tempfile.TemporaryDirectory() as temporary:
            output = Path(temporary) / 'reviewer.tar.gz'
            subprocess.run([
                'python3', str(PACKAGER), str(output), '--source-root', str(ROOT),
                '--source-commit', 'a' * 40, '--base', 'b' * 40,
                '--patch-sha256', 'c' * 64,
            ], check=True)
            staged = Path(temporary) / 'staged'
            staged.mkdir()
            with tarfile.open(output, 'r:gz') as archive:
                archive.extractall(staged)
            spec = importlib.util.spec_from_file_location('trusted_runner', staged / 'runner.py')
            module = importlib.util.module_from_spec(spec)
            spec.loader.exec_module(module)
            self.assertEqual(module.MODEL, 'claude-opus-5-5')
            self.assertTrue(hasattr(module, 'reserve_for_review'))
            self.assertEqual(module.SHARED_CONTROL, staged / 'shared_calls.py')
            tick_spec = importlib.util.spec_from_file_location('trusted_tick', staged / 'tick.py')
            tick = importlib.util.module_from_spec(tick_spec)
            tick_spec.loader.exec_module(tick)
            manifest = json.loads((staged / 'manifest.json').read_text())
            self.assertEqual(tick.trusted_hashes(staged), {
                name: manifest['files'][name]
                for name in ('runner.py', 'claude_control.py', 'shared_calls.py')
            })
