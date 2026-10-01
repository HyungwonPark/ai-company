"""Synthetic fingerprints here do not verify a real Android application."""
import json
from pathlib import Path
import subprocess
import sys
import tempfile
import unittest

from render_assetlinks import association


class AssetlinksPreparationTests(unittest.TestCase):
    def test_missing_or_malformed_fingerprint_grants_no_association(self):
        for fingerprints in ([], [""], ["TODO"], ["AA:BB"], ["GG:" * 31 + "GG"]):
            with self.subTest(fingerprints=fingerprints), self.assertRaises(ValueError):
                association("cloud.hyungwon.aicompany", fingerprints)

    def test_existing_service_identifier_cannot_be_reused(self):
        with self.assertRaises(ValueError):
            association("life.talentaedward.app", ["AA:" * 31 + "AA"])

    def test_valid_fixture_normalizes_without_claiming_verification(self):
        fingerprint = "ab:" * 31 + "ab"
        result = association("cloud.hyungwon.aicompany", [fingerprint, fingerprint.upper()])
        self.assertEqual(result[0]["target"]["sha256_cert_fingerprints"], [fingerprint.upper()])
        self.assertNotIn("verified", json.dumps(result))

    def test_cli_invalid_input_never_creates_artifact_or_overwrites_file(self):
        script = Path(__file__).with_name("render_assetlinks.py")
        with tempfile.TemporaryDirectory() as directory:
            output = Path(directory) / "assetlinks.json"
            argv = [sys.executable, str(script), "--package", "cloud.hyungwon.aicompany",
                    "--cert-sha256", "", "--output", str(output)]
            result = subprocess.run(argv, capture_output=True, text=True)
            self.assertNotEqual(result.returncode, 0)
            self.assertEqual(result.stdout, "")
            self.assertFalse(output.exists())
            output.write_text("[]\n")
            argv[5] = "AA:" * 31 + "AA"
            result = subprocess.run(argv, capture_output=True, text=True)
            self.assertNotEqual(result.returncode, 0)
            self.assertEqual(output.read_text(), "[]\n")


if __name__ == "__main__":
    unittest.main()
