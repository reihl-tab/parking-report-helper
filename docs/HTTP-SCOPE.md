# Decision record: HTTP service scope

- **Issue:** #72
- **Status:** Decided by Reihl on 2026-10-29: option A (keep the default).
- **Date:** 2026-10-29

## Context

The Render HTTP service (`src/parking_report/http_service.py`, see
[HTTP106.md](HTTP106.md)) serves exactly three endpoints today:

| Endpoint | Auth | Effect |
|---|---|---|
| `GET /healthz` | none | network-free health JSON |
| `POST /v1/login-check` | bearer | one municipal Login per process; never returns the token |
| `POST /v1/reports` | bearer | always `409 submission_disabled`, a code gate |

[openapi.json](openapi.json) describes that contract, and
`tests/test_openapi_contract.py` keeps it equal to the code. The spec is JSON,
not YAML, so the contract test needs no new dependency.

The question in #72: should any further endpoint ever be added, for example a
read-only case-status proxy, on Render or on another host?

## Constraints

- Case data concerns living people and vehicles. The public repo carries no
  plates, IDs, addresses or report content, and the service stores none.
- Render's terms bar government ID numbers, and HTTP106.md already bars
  extending Render into reporting or a reporter-ID route.
- Render Free sleeps, restarts and keeps files only ephemerally, so it cannot
  hold a durable attempt ledger or audit trail.
- Every municipal call through the service uses the owner's credentials. A
  read endpoint is still a credential-bearing proxy, so a leaked bearer token
  would expose case data, not just a Login attempt.
- The private Git-backed ledger and the CLI already cover status lookups
  (`docs/STATUS.md`, `docs/CASE-LISTING.md`) without a network listener.

## Decision

**No new network surface.**

1. The service stays at the three endpoints above. `POST /v1/reports` stays
   disabled in code.
2. No read-only endpoint, including a case-status proxy, is added on Render.
   Status lookups stay in the CLI and the private repository.
3. Any change to the endpoint set, on any host, first needs:
   - a decision by Reihl on #72 or a successor issue
   - a reviewed change to `docs/openapi.json` in the same PR as the code
   - a compliant host chosen separately, with the private ledger, a
     repo-scoped credential route and durable before-effect transitions, as
     HTTP106.md requires for reporting

## Consequences

- The contract test fails if an endpoint, status code, status value or
  response key is added to the code without the spec, or the other way round.
  Such a PR then has to touch this record.
- The spec documents behavior only. It does not publish the private origin
  (`servers` uses a placeholder) and it is not a promise of a public API.

## Options Reihl may choose instead

- **A. Keep the default** (above).
- **B. Allow a read-only endpoint on a separately selected compliant host
  only**, never on Render. That needs its own issue with a threat model
  (token scope, rate limit, logging, what data leaves the host) before any
  code.
- **C. Retire the HTTP service.** The README says it was built to test
  hosting options and is not the filing route: production filing goes
  through GitHub Actions and the VPN tunnel. Retiring it removes the
  remaining network surface.
