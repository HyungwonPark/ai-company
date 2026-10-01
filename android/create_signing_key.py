"""Create a new private Android key outside Git; never replace an existing key."""
import argparse
import hashlib
import json
import os
from pathlib import Path
import secrets
import subprocess


def main():
    p = argparse.ArgumentParser()
    p.add_argument("--directory", type=Path, required=True)
    p.add_argument("--keytool", type=Path, required=True)
    args = p.parse_args()
    root = args.directory.resolve()
    parent = root.parent
    parent.mkdir(parents=True, exist_ok=True, mode=0o700)
    if subprocess.run(["git", "-C", str(parent), "rev-parse", "--show-toplevel"],
                      stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL).returncode == 0:
        raise SystemExit("Signing keys must be outside a Git checkout")
    os.umask(0o077)
    root.mkdir(mode=0o700)  # Refuse an existing ownership directory, even if empty.
    password = root / "store-password"
    password.write_text(secrets.token_urlsafe(48) + "\n")
    store = root / "release.p12"
    subprocess.run([str(args.keytool), "-genkeypair", "-noprompt", "-alias", "ai-company",
        "-keyalg", "RSA", "-keysize", "3072", "-sigalg", "SHA256withRSA",
        "-validity", "10000", "-dname", "CN=AI Company, OU=Android, O=Hyungwon, C=KR",
        "-storetype", "PKCS12", "-keystore", str(store), "-storepass:file", str(password),
        "-keypass:file", str(password)], check=True)
    cert = root / "certificate.der"
    subprocess.run([str(args.keytool), "-exportcert", "-alias", "ai-company", "-keystore", str(store),
                    "-storepass:file", str(password), "-file", str(cert)], check=True)
    raw = hashlib.sha256(cert.read_bytes()).hexdigest().upper()
    fingerprint = ":".join(raw[i:i+2] for i in range(0, len(raw), 2))
    metadata = dict(package_id="cloud.hyungwon.aicompany", alias="ai-company",
                    certificate_sha256=fingerprint, store_type="PKCS12", purpose="direct APK distribution")
    (root / "public-certificate.json").write_text(json.dumps(metadata, indent=2) + "\n")
    for path in root.iterdir():
        path.chmod(0o600)
    print(json.dumps(metadata))


if __name__ == "__main__":
    main()
