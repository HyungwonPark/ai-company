# Persistent management API

This control plane extends the existing `STATE_DIR/sessions/sessions.sqlite`. It does not create a second execution queue. `ManagementStore.link_task` references an already-submitted `flow_tasks.task_id`; Dispatcher retains policy, ownership, quota, attempt, recovery and verification authority. Manager messages are persisted as `role=user`, `status=awaiting_pm`. No HTTP request calls a model, invents an assistant reply, submits a flow, executes an approval, or deploys a service.

The current foundation is locally testable. PM interpretation/response delivery, structured role harness enforcement, task creation from an approved plan, deployment execution and public/TWA integration remain unverified/unimplemented. Existing task dependencies and owner sessions are projected from Dispatcher facts. Task links preserve the active harness revision at link time. They do not silently rewrite an existing immutable FlowSpec.

## Local server

`ai-company manage serve --state-dir /path/to/isolated-state --token-file /path/to/token --host 127.0.0.1 --port 8765`

The CLI entry point uses `management_server.serve`. The default static directory is `src/ai_company/web` (packaged alongside Python). `serve` also accepts `web_root` and `public_origin` for a future explicitly configured HTTPS proxy. Binding is restricted to IPv4 loopback. No reverse proxy, DNS, TLS, operational queue or timer is changed.

Create the token file separately with a cryptographically random token (at least 32 characters), current-user ownership and mode `0600`. The server rejects symlinks, other-owner files and group/world access. Do not put this token into source control or URLs. The shared SQLite file is also private (`0600`). The server is a single-master console; it does not claim multi-user authorization or a production identity-provider integration.

## Browser authentication

- `GET /api/session` → `{authenticated:false}` or `{authenticated:true,csrf_token}`.
- `POST /api/login` with `{token}` → `{authenticated:true,csrf_token}` and an eight-hour session cookie. The cookie is `HttpOnly; SameSite=Strict; Path=/`; HTTPS origin adds `Secure`. Only a hash of the session bearer token is stored. Login rotates the prior browser session; failures are throttled.
- `POST /api/logout` with `{}` invalidates the session in SQLite and expires the cookie.

Every POST, including login, requires an exact matching `Origin`. Authenticated POSTs additionally require the `X-CSRF-Token` returned by login/session, and the cookie. Every request requires the configured `Host`. Cross-site writes, unauthenticated private API reads/writes, unknown request fields, non-JSON bodies, missing/duplicate lengths and bodies over 64 KiB are rejected. Static files have a bounded extension/size policy and cannot escape the web root, including via symlinks. Responses disable caching and framing, use a restrictive same-origin CSP, and never reflect login secrets in logs/errors. The API has no CORS opt-in.

## API contract

Timestamps are Unix seconds. Project, role, approval and message IDs are UUID hex. Flow task IDs retain their existing identifiers. Errors are `{error:{code,message}}` with HTTP 400/401/403/404/405/409/413/415/429/500.

| Method/path | Request | Response |
|---|---|---|
| `GET /api/projects` | — | `{projects:[{id,name,goal,status,harness_version,source,created_at}]}` |
| `POST /api/projects` | `{name,goal,roles?:[{name,responsibility}]}` | 201 `{project}` |
| `GET /api/projects/{id}/overview` | — | `{project,roles,tasks,reports,approvals,messages,harnesses,readiness}` |
| `POST /api/projects/{id}/messages` | `{content}` | `{message}`; pending PM, no model call |
| `POST /api/projects/{id}/harness` | `{content,base_version}` | `{harness}`; creates draft only |
| `POST /api/projects/{id}/approvals/{approval_id}/decisions` | `{decision,comment?,subject_digest,idempotency_key}` | `{approval}` |
| `GET /api/projects/{id}/events?after=N` | nonnegative cursor | `{events:[{id,kind,subject_id,created_at}],cursor}`; up to 200 |

Overview is read in one SQLite transaction so role, task, harness and approval projections share a consistent snapshot. Roles include `id,name,responsibility,status,assigned_model,session_id,current_task_id,next_task_id,wait_reason,resume_at,handoffs`. Tasks include `id,title,role_id,status,stage,dependencies,flow_task_id,worktree,harness_version,active,agents,handoffs,repair_reason`. A live `session_jobs` RUNNING/WAITING fact can supply the effective display status/session before Dispatcher records the next state; no queue or flow state is rewritten by a read.

`project.harness_content` holds the active text; `harnesses` lists immutable contents with `version,status,digest,created_at,base_version?`. Initial version 1 stores only the user-provided goal/roles. Draft creation checks the active base version. The trusted Python `activate_harness(project_id,version,expected_digest)` operation rejects stale bases/digests and preserves old revisions. It is deliberately not an automatic PM action or browser endpoint yet.

Reports are derived from persisted flow status, reason and verification, with `source=system`; they do not claim PM judgment. Evidence links are constructed only from bounded GitHub repository, commit and verified run identifiers. Messages contain `id,role,content,created_at,status`. Readiness remains `mode=unverified` for real state until separate end-to-end proof exists; `pm=awaiting_worker` means a saved request needs the still-unimplemented PM bridge. A requested model name in the role view is not runtime model/effort proof.

Management changes append events. SQLite triggers append `flow_updated` for linked flow writes on the same store. Session-only state changes may precede a flow update, so clients should periodically refresh overview even if the event cursor has not advanced. Reconnect always reloads durable state.

## Approval boundary

A trusted producer calls `request_approval(project_id, subject)` with required `title,action,environment,artifact_sha,cost_usd,expires_at,impact,rollback,verification`. The SHA is a full commit SHA-1 or artifact SHA-256. Cost must be finite and nonnegative; expiry must be future. A canonical digest binds all subject fields and the project. Approval subjects cannot be edited through this API.

A decision is `approve`, `reject`, or `request_changes`. The supplied digest must match both the stored digest and a fresh digest of the stored subject. Expired requests, altered artifacts/environments/costs/conditions, cross-project requests and repeated decisions with a new key fail. Retrying the exact same idempotency key/body returns the original record without another decision/event; reusing a key for different data fails.

`validate_approval(project_id,approval_id,subject)` performs a read-only current-subject/expiry/status preflight and rejects fixtures. Recording or validating approval never executes anything. A future execution integration must recheck exact artifact, environment, cost, expiry and independent verification immediately before performing an effect, and use the existing execution ownership/idempotency guards. This API does not implement an external-effect executor or imply that an unverified flow passed final review.

## Isolated fixtures and validation

`ManagementStore(Path('/explicit/isolated/state')).seed_demo()` is an explicit fixture-only helper. It refuses stores containing any project, flow task or session job. It creates four visibly simulated roles, a pending PM request, a fixture report and an approval with no execution effect. It does not add rows to the flow/session execution queues. Fixture approvals can be exercised in the UI but always fail executable preflight.

Run `uv run --frozen python -m unittest discover -s tests -p 'test_management*.py' -v`. Tests cover durable pending-PM read/approval availability, shared Dispatcher facts, harness revisions, approval replay/tamper/expiry/cost/environment binding, fixture separation, cookie authentication, origin/CSRF/Host controls, path escape protection and request bounds. These tests are local; they do not establish real model, public login, remote CI, deployment or Android validation.
