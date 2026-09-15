"""Reject compiled APK metadata that merely mentions the expected origin."""
import json
from pathlib import Path
import sys
import unittest
import xml.etree.ElementTree as ET

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
from verify_unsigned import ANDROID, ORIGIN, verify_web_association


class CompiledAssociationTests(unittest.TestCase):
    def setUp(self):
        self.application = ET.Element("application")
        self.metadata = ET.SubElement(self.application, "meta-data", {
            ANDROID + "name": "asset_statements", ANDROID + "resource": "@ref/0x7f0d001e"})
        self.statement = [{"relation": ["delegate_permission/common.handle_all_urls"],
                           "target": {"namespace": "web", "site": ORIGIN}}]

    def resources(self):
        return 'resource 0x7f0d001e string/asset_statements\n  () "' + json.dumps(self.statement) + '"\n'

    def test_compiled_origin_and_metadata_reference_match(self):
        verify_web_association(self.application, self.resources())

    def test_expected_origin_elsewhere_does_not_authorize_another_site(self):
        self.statement[0]["target"]["site"] = "https://example.invalid"
        with self.assertRaises(AssertionError):
            verify_web_association(self.application, self.resources() + ORIGIN)

    def test_metadata_referencing_different_resource_is_rejected(self):
        self.metadata.set(ANDROID + "resource", "@ref/0x7f0d002a")
        with self.assertRaises(AssertionError):
            verify_web_association(self.application, self.resources())

    def test_unrelated_relation_is_rejected(self):
        self.statement[0]["relation"] = ["delegate_permission/common.get_login_creds"]
        with self.assertRaises(AssertionError):
            verify_web_association(self.application, self.resources())

    def test_unbound_resource_is_rejected(self):
        self.application.remove(self.metadata)
        with self.assertRaises(AssertionError):
            verify_web_association(self.application, self.resources())


if __name__ == "__main__":
    unittest.main()
