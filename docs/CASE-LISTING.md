# Read-only case listing

`Municipality.list_cases(token, reporter, from_date=...)` uses the existing
app GET route, not endpoint discovery:

```text
GET <municipal-base>/GetContactCases/<url-encoded-token>/100000000/<configured-reporter-id>?from=<YYYY-MM-DD>&caseType=2
```

Evidence: the owner's supplied106+ app8.6.4 bundle, inspected offline in the
private reference record; the established private `tools/p106_client.py` read
client and own-case reconciliations since18October2026. No app binary, API key,
identity, live response or private report is included here. No new live call was
needed for this implementation.

The token comes from the existing login method. This read method itself performs
exactly one GET, does not retry, does not register/update a contact, and cannot
submit, attach or change a case. Redirects are refused. An explicit ISO start
date is required. Responses must contain an `incidents` list of objects; an empty
list is valid. Unknown fields are retained. A16MiB limit bounds the read.

The reporter must be supplied from authorized private configuration in the same
contact shape used by the client (`ContactIdType`, `ContactIdNumber`). Only the
observed national-ID type is supported. Format/checksum validation does not prove
ownership: callers must establish that the identity is theirs to query. Never
accept arbitrary third-party IDs from untrusted input. This app-level token is
not evidence of authority over every ID.

This is a library method only. No public HTTP route or CLI dump is added. Callers
must keep raw responses and transport URLs private: the route contains a token
and national ID, and returned descriptions can contain plates and personal data.
Errors suppress underlying exception chains, URL and body. Do not enable urllib
HTTP debugging or put real examples in CI, public logs, issues or artifacts.

Use records to reconcile, not to refile: match exact description/plate/address/date
and preserve ambiguity, delayed creation, duplicate cases and unknowns. A web
case ID is not a municipal reference. A closed status is not proof of enforcement.
The method never changes ledger entries or sends reminders automatically.
