# Render Login-only HTTP service

This stage verifies connectivity only. No signup, deployment, Login or municipal
submission is part of the code/CI tests. The report endpoint is hard-disabled in
code, regardless of environment values. It requires a separately reviewed change
and a specific owner-approved report after the Login check succeeds.

## Deploy

Create a Render Web Service using this public repository. Choose Docker and set
Dockerfile Path to `Dockerfile.render`. Choose Free, one instance, and a region.
Set health-check path to `/healthz`. The service binds `0.0.0.0:$PORT` (default
10000), runs as a non-root user, and stores no report data.

Set these private Render environment variables yourself, never in repo/chat:

- `PARKING_HTTP_TOKEN`: random high-entropy bearer token, at least 32 characters.
- `PARKING_106_SUBSCRIPTION_KEY`
- `PARKING_106_USERNAME`
- `PARKING_106_PASSWORD`

The Login test does not need reporter details or government ID numbers. Never
send or store government ID numbers through Render. Its terms prohibit that data;
this deployment is strictly Login-only and must not be extended into a reporter-ID
route. No reporter variables are accepted by this service.
Never bake credentials into the Docker image, build arguments or render.yaml.

The private manual GitHub caller needs secrets `PARKING_HTTP_BASE_URL` (Render
HTTPS origin only) and `PARKING_HTTP_TOKEN` (same bearer token). Never use a token
in query parameters or invoke Login from a health probe/startup task.

## Contract

Machine-readable: [openapi.json](openapi.json), kept equal to the code by
`tests/test_openapi_contract.py`. Scope decision: [HTTP-SCOPE.md](HTTP-SCOPE.md).

- `GET /healthz`: public, network-free `{status: healthy, submission_enabled: false}`.
- `POST /v1/login-check`: bearer-authenticated, empty body, no query string. Calls
  only Login. Returns `login_ok` without the token, or a safe failure class.
- `POST /v1/reports`: authenticated but returns `submission_disabled`. No municipal
  effects, even if a live env flag is set.

One Login check is allowed per process. Its in-memory gate is consumed before
network access, including failures. Render restarts erase that gate, so this is
not durable dedupe or an authorization grant. Never auto-retry a failed/uncertain
check, deploy to reset it, or dispatch multiple jobs without fresh operator review.
The private caller requires a specific check ID, is manual-only and serializes
with existing 106 work. Do not replay it. A persistent attempt ledger remains
required before implementing real reporting.

Client/server request headers, tokens, reporter data and arbitrary errors are not
logged or returned. This is a small low-volume operator tool, not a general public
API. Rotate the bearer token if exposed. Render's platform access logs are outside
this application's control; do not put secrets in URLs. The single-worker WSGI
server and 45-second municipal timeout suit this limited diagnostic, not a busy
production service.

## Free hosting limits and next stage

Render Free is a web service, not a free cron/one-off worker. It sleeps after
15 idle minutes and cold start can take about a minute. Files are ephemeral and
restarts are possible. Its egress is not verified as acceptable to the municipality.
A successful Login does not prove that contact creation/case/attachment work.

Any future non-Render reporting route must use the private Git-backed ledger, fixed payload digest and
original photo table. Local Render files are never authoritative. Do not enable a report handler on Render. For a separately selected compliant
host, design a private repo-scoped credential route and durable before-effect
transitions; do not replace that with a local JSON/cache/artifact. Municipal
uncertainty blocks creation retries. Existing CLI supports those safeguards; this
service does not yet connect them to HTTP.

## Sources

- https://render.com/terms
- https://render.com/docs/free
- https://render.com/docs/docker
- https://render.com/docs/web-services
