# Public console runtime

The Dockerfile builds the management UI/API only. Pass a reviewed official Python
image digest with `--build-arg PYTHON_IMAGE=python@sha256:...`; dependencies come
from `uv.lock`. The root `.dockerignore` sends only source and dependency inputs.
Do not build from a directory containing credentials without that context filter.

Run the image as the UID that owns the shared state. Use a read-only root,
`cap_drop: [ALL]`, `no-new-privileges`, memory/CPU/PID/log limits, a small `/tmp`
tmpfs, and only the state directory. Use `manage serve --password-login` for the
public console; no login secret needs to be mounted in its container. Do not
mount the Docker socket, model credentials, home directory, or couple app data.

The deployed container has a fixed RFC1918 address on `ai-company-control`, an
external Docker network created with `--internal`. It publishes no ports. Existing
Caddy alone joins that network and forwards `hyungwon.cloud` to the private API.
Keep its original network at the higher gateway priority. Preserve Host/Origin
headers; the application validates the exact configured HTTPS origin and sets
Secure, HttpOnly, SameSite=Strict cookies. Forwarded headers cannot redefine it.

The actual reviewed Compose file, image ID, config backups, apply journal and
rollback script are retained in the private deployment directory documented in
`docs/public-domain-validation-2026-09-15.md`. Never copy credentials into Git.

`verify_public.py` sends only unauthenticated GET requests to the deployed domains.
Its separate GitHub job validates public connectivity; it does not attest that a
PR candidate was deployed, approve a plan, authenticate a master, or replace the
approved candidate CI provenance verifier.


Create a master account with the trusted server CLI before starting password mode:

```bash
ai-company manage create-user --state-dir /path/to/state --username edward \
  --temporary-password-file /private/new-temporary-password
```

The command creates a random 24-character temporary password in a new mode-0600
file. It does not print the password and refuses existing users/files. Deliver the
password privately to its owner. It expires after 24 hours. Login grants only the
password-change page until the owner sets a different password (10–128 characters).
The change requires the current password, same-origin request and session CSRF;
it rotates the session and invalidates all previous sessions for that account.
The account menu also allows later changes. Browsers may use their password manager;
application local/session storage never contains passwords or session credentials.

Accounts use salted scrypt N=2^17, r=8, p=1, following the
[OWASP password storage guidance](https://cheatsheetseries.owasp.org/cheatsheets/Password_Storage_Cheat_Sheet.html).
Password requests are serialized and throttled in SQLite, including across restart.
The single-master login throttle is shared across account names; forwarded IPs are
not trusted. Existing token sessions cannot authenticate in password mode; startup
also refuses token mode when password accounts exist. Account/session tables are
additive and do not modify project, approval, PM, usage or execution history.
