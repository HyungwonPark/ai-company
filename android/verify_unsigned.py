"""Inspect a built release, never infer APK identity from source configuration."""
import argparse
import hashlib
import json
import os
from pathlib import Path
import re
import shutil
import subprocess
import xml.etree.ElementTree as ET
import zipfile

PACKAGE = "cloud.hyungwon.aicompany"
ORIGIN = "https://hyungwon.cloud"
ANDROID = "{http://schemas.android.com/apk/res/android}"
MANAGE_ACTIVITY = "com.google.androidbrowserhelper.trusted.ManageDataLauncherActivity"


def verify_management_activity(application):
    activities = [entry for entry in application.findall("activity")
                  if entry.get(ANDROID + "name") == MANAGE_ACTIVITY]
    assert len(activities) == 1, "TWA startup requires ManageDataLauncherActivity to be declared"
    assert activities[0].get(ANDROID + "exported") == "false"
    assert application.get(ANDROID + "manageSpaceActivity") == MANAGE_ACTIVITY
    urls = [entry.get(ANDROID + "value") for entry in activities[0].findall("meta-data")
            if entry.get(ANDROID + "name") == "android.support.customtabs.trusted.MANAGE_SPACE_URL"]
    assert urls == [ORIGIN + "/"], "Site settings must remain bound to the approved origin"


def verify_web_association(application, resources):
    match = re.search(r'resource (0x[0-9a-f]+) string/asset_statements\n\s+\(\) "(.*)"', resources)
    assert match, "Compiled asset_statements resource is missing"
    metadata = [entry for entry in application.findall("meta-data")
                if entry.get(ANDROID + "name") == "asset_statements"]
    assert len(metadata) == 1 and metadata[0].get(ANDROID + "resource") == "@ref/" + match[1], \
        "Application metadata must reference the inspected compiled resource"
    assert json.loads(match[2]) == [{"relation": ["delegate_permission/common.handle_all_urls"],
                                   "target": {"namespace": "web", "site": ORIGIN}}], \
        "Compiled app-to-website association differs from the approved origin"


def command(*argv):
    return subprocess.check_output([str(value) for value in argv], text=True, timeout=120)


def inspect(apk, build_tools, apkanalyzer):
    manifest_text = command(apkanalyzer, "manifest", "print", apk)
    manifest = ET.fromstring(manifest_text)
    assert manifest.attrib["package"] == PACKAGE
    assert manifest.attrib[ANDROID + "versionCode"] == "2"
    assert manifest.attrib[ANDROID + "versionName"] == "0.1.1"
    sdk = manifest.find("uses-sdk")
    assert sdk.attrib[ANDROID + "minSdkVersion"] == "26"
    assert sdk.attrib[ANDROID + "targetSdkVersion"] == "36"
    application = manifest.find("application")
    assert application.attrib.get(ANDROID + "debuggable", "false") == "false"
    assert application.attrib[ANDROID + "allowBackup"] == "false"
    assert application.attrib[ANDROID + "usesCleartextTraffic"] == "false"
    verify_management_activity(application)
    permissions = sorted(p.attrib[ANDROID + "name"] for p in manifest.findall("uses-permission"))
    assert set(permissions) <= {"android.permission.INTERNET", "android.permission.ACCESS_NETWORK_STATE",
                               PACKAGE + ".DYNAMIC_RECEIVER_NOT_EXPORTED_PERMISSION"}, permissions
    launcher = next(a for a in application.findall("activity")
                    if a.attrib[ANDROID + "name"] == "com.google.androidbrowserhelper.trusted.LauncherActivity")
    default_url = next(m.attrib[ANDROID + "value"] for m in launcher.findall("meta-data")
                       if m.attrib[ANDROID + "name"] == "android.support.customtabs.trusted.DEFAULT_URL")
    assert default_url == ORIGIN + "/"
    filters = launcher.findall("intent-filter")
    verified = next(f for f in filters if f.attrib.get(ANDROID + "autoVerify") == "true")
    assert [(d.attrib.get(ANDROID + "scheme"), d.attrib.get(ANDROID + "host"))
            for d in verified.findall("data")] == [("https", "hyungwon.cloud")]
    assert any(c.attrib[ANDROID + "name"] == "android.intent.category.LAUNCHER"
               for f in filters for c in f.findall("category"))
    resources = command(build_tools / "aapt2", "dump", "resources", apk)
    verify_web_association(application, resources)
    badging = command(build_tools / "aapt2", "dump", "badging", apk)
    assert "application-label:'AI Company'" in badging
    assert "native-code:" not in badging  # The APK has no ABI-specific native payload.
    with zipfile.ZipFile(apk) as z:
        assert not any(name.startswith("lib/") for name in z.namelist())
        assert not any(name.lower().endswith((".jks", ".keystore", ".p12", ".pfx")) for name in z.namelist())
        payload = {name: hashlib.sha256(z.read(name)).hexdigest() for name in sorted(z.namelist())
                   if not name.startswith("META-INF/")}
    return dict(package_id=PACKAGE, version_name="0.1.1", version_code=2,
                min_sdk=26, target_sdk=36, launch_url=default_url, permissions=permissions,
                debuggable=False, native_libraries=False, compiled_app_to_website_association_verified=True,
                startup_management_component_verified=True,
                payload_sha256=payload), manifest_text, badging, resources


def main():
    p = argparse.ArgumentParser()
    p.add_argument("--apk", type=Path, required=True)
    p.add_argument("--build-tools", type=Path, required=True)
    p.add_argument("--apkanalyzer", type=Path, required=True)
    p.add_argument("--source-commit", required=True)
    p.add_argument("--output", type=Path, required=True)
    args = p.parse_args()
    assert re.fullmatch("[0-9a-f]{40}", args.source_commit)
    assert command("git", "rev-parse", "HEAD").strip() == args.source_commit
    facts, manifest, badging, resources = inspect(args.apk, args.build_tools, args.apkanalyzer)
    args.output.mkdir(parents=True, exist_ok=True)
    unsigned = args.output / "ai-company-0.1.1-unsigned.apk"
    shutil.copyfile(args.apk, unsigned)
    facts.update(source_commit=args.source_commit, apk_sha256=hashlib.sha256(unsigned.read_bytes()).hexdigest(),
                 artifact_kind="unsigned_release_not_for_installation", workflow=".github/workflows/android.yml",
                 workflow_run_id=os.environ.get("GITHUB_RUN_ID"), workflow_run_attempt=os.environ.get("GITHUB_RUN_ATTEMPT"))
    (args.output / "build-evidence.json").write_text(json.dumps(facts, indent=2) + "\n")
    for name, value in [("AndroidManifest.xml", manifest), ("apk-badging.txt", badging), ("apk-resources.txt", resources)]:
        (args.output / name).write_text(value)
    print(json.dumps({k: v for k, v in facts.items() if k != "payload_sha256"}))


if __name__ == "__main__":
    main()
