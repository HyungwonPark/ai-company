"""Render a reviewed TWA association; never supply a default app or signing key.

This is a preparation tool, not proof of signing ownership or domain verification.
It does not publish files. --output refuses to replace an existing artifact.
"""
import argparse
import json
from pathlib import Path
import re


def association(package_name: str, fingerprints: list[str]) -> list[dict]:
    if not re.fullmatch(r"[A-Za-z][A-Za-z0-9_]*(?:\.[A-Za-z][A-Za-z0-9_]*)+", package_name):
        raise ValueError("A new Android application ID is required")
    if package_name == "life.talentaedward.app":
        raise ValueError("The existing couple-service application ID must not be reused")
    normalized = [value.strip().upper() for value in fingerprints]
    if not normalized or any(not re.fullmatch(r"(?:[0-9A-F]{2}:){31}[0-9A-F]{2}", value)
                             for value in normalized):
        raise ValueError("At least one actual APK signing certificate SHA-256 fingerprint is required")
    return [{"relation": ["delegate_permission/common.handle_all_urls"], "target": {
        "namespace": "android_app", "package_name": package_name,
        "sha256_cert_fingerprints": sorted(set(normalized)),
    }}]


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--package", required=True)
    parser.add_argument("--cert-sha256", action="append", required=True)
    parser.add_argument("--output", type=Path)
    args = parser.parse_args()
    try:
        text = json.dumps(association(args.package, args.cert_sha256), indent=2) + "\n"
        if args.output is None:
            print(text, end="")
        else:
            with args.output.open("x", encoding="utf-8") as stream:
                stream.write(text)
    except (ValueError, OSError) as error:
        parser.error(str(error))


if __name__ == "__main__":
    main()
