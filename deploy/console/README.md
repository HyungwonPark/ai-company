# Public console runtime

The Dockerfile builds the management UI/API only. Pass a reviewed official Python
image digest with `--build-arg PYTHON_IMAGE=python@sha256:...`; dependencies come
from `uv.lock`. The root `.dockerignore` sends only source and dependency inputs.
Do not build from a directory containing credentials without that context filter.

Run the image as the UID that owns the shared state. Use a read-only root,
`cap_drop: [ALL]`, `no-new-privileges`, memory/CPU/PID/log limits, a small `/tmp`
tmpfs, and only the state directory plus a read-only login token mount. Do not
mount the Docker socket, model credentials, home directory, or couple app data.

The deployed container has a fixed RFC1918 address on `ai-company-control`, an
external Docker network created with `--internal`. It publishes no ports. Existing
Caddy alone joins that network and forwards `hyungwon.cloud` to the private API.
Keep its original network at the higher gateway priority. Preserve Host/Origin
headers; the application validates the exact configured HTTPS origin and sets
Secure, HttpOnly, SameSite=Strict cookies. Forwarded headers cannot redefine it.

The actual reviewed Compose file, image ID, config backups, apply journal and
rollback script are retained in the private deployment directory documented in
`docs/public-domain-validation-2026-09-15.md`. Never copy the login token into Git.

`verify_public.py` sends only unauthenticated GET requests to the deployed domains.
Its separate GitHub job validates public connectivity; it does not attest that a
PR candidate was deployed, approve a plan, authenticate a master, or replace the
approved candidate CI provenance verifier.
