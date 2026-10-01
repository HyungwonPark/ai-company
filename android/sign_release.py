"""Sign an inspected APK locally; key/password contents never enter arguments or logs."""
import argparse
import hashlib
import json
import os
from pathlib import Path
import re
import stat
import subprocess
import zipfile


def sha(path):
    return hashlib.sha256(path.read_bytes()).hexdigest()


def payload(path):
    with zipfile.ZipFile(path) as archive:
        return {name: hashlib.sha256(archive.read(name)).hexdigest() for name in sorted(archive.namelist())
                if not name.startswith("META-INF/")}


def main():
    parser = argparse.ArgumentParser()
    for name in ("apk", "build-evidence", "keystore", "password-file", "java", "apksigner-jar", "output"):
        parser.add_argument("--" + name, type=Path, required=True)
    parser.add_argument("--expected-source", required=True)
    parser.add_argument("--expected-cert-sha256", required=True)
    args = parser.parse_args()
    expected = args.expected_cert_sha256.replace(":", "").lower()
    assert re.fullmatch("[0-9a-f]{64}", expected)
    evidence = json.loads(args.build_evidence.read_text())
    assert evidence["source_commit"] == args.expected_source
    assert evidence["package_id"] == "cloud.hyungwon.aicompany"
    assert (evidence["version_name"], evidence["version_code"], evidence["launch_url"]) == ("0.1.1", 2, "https://hyungwon.cloud/")
    assert evidence["startup_management_component_verified"] is True
    assert sha(args.apk) == evidence["apk_sha256"]
    assert payload(args.apk) == evidence["payload_sha256"]
    for file in (args.keystore, args.password_file):
        info = file.lstat()
        assert stat.S_ISREG(info.st_mode) and info.st_uid == os.getuid() and stat.S_IMODE(info.st_mode) == 0o600
        assert subprocess.run(["git", "-C", str(file.resolve().parent), "rev-parse", "--show-toplevel"],
                              stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL).returncode != 0
    args.output.mkdir(parents=True, exist_ok=True)
    signed = args.output / "ai-company-0.1.1.apk"
    assert not signed.exists(), "Never overwrite a released APK"
    java = [str(args.java), "-jar", str(args.apksigner_jar)]
    subprocess.run([*java, "sign", "--ks", str(args.keystore), "--ks-type", "PKCS12",
        "--ks-key-alias", "ai-company", "--ks-pass", "file:" + str(args.password_file),
        "--v1-signing-enabled", "false",
        "--v2-signing-enabled", "true", "--v3-signing-enabled", "true", "--v4-signing-enabled", "false",
        "--out", str(signed), str(args.apk)], check=True, timeout=120)
    verification = subprocess.check_output([*java, "verify", "--verbose", "--print-certs", str(signed)],
                                           text=True, timeout=120)
    observed = re.findall(r"^Signer #\d+ certificate SHA-256 digest: ([0-9a-f]+)$", verification, re.M)
    assert observed == [expected], "APK signer differs from the dedicated release certificate"
    assert "Verified using v2 scheme (APK Signature Scheme v2): true" in verification
    assert "Verified using v3 scheme (APK Signature Scheme v3): true" in verification
    assert payload(signed) == evidence["payload_sha256"], "Signing changed inspected application payload"
    record = {k: v for k, v in evidence.items() if k not in ("payload_sha256", "apk_sha256", "artifact_kind")}
    record.update(artifact_kind="signed_release_apk", apk_sha256=sha(signed), apk_bytes=signed.stat().st_size,
                  certificate_sha256=args.expected_cert_sha256.upper(), unsigned_apk_sha256=evidence["apk_sha256"],
                  signature_schemes=["v2", "v3"], inspected_payload_unchanged=True,
                  local_signing=True, private_key_uploaded=False,
                  signing_tool_sha256=sha(Path(__file__)), apksigner_jar_sha256=sha(args.apksigner_jar),
                  domain_association="requires_live_verification", physical_device="not_tested")
    (args.output / "release.json").write_text(json.dumps(record, indent=2) + "\n")
    (args.output / "apksigner-verification.txt").write_text(verification)
    (args.output / "SHA256SUMS").write_text(record["apk_sha256"] + "  " + signed.name + "\n")
    print(json.dumps({k: record[k] for k in ("source_commit", "package_id", "version_name", "apk_sha256", "certificate_sha256")}))


if __name__ == "__main__":
    main()
