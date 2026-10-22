# Own-case attachment reads

`Municipality.case_files(token, configured_reporter, case_id=..., from_date=...)`
returns a list of byte strings in memory. It never writes files or logs their
contents, follows returned URLs, or uses server-supplied filenames as paths.

The exact attachment route was established from the supplied app/private prior
own-case reads. Public documentation uses placeholders:

```text
GET <municipal-base>/<attachment-route>/<encoded-token>/<incident-case-id>
```

Important: the observed route returns all attachments in ONE GET per CASE, not
one GET per file. Before that GET, the method calls `list_cases` once and requires
exactly one matching incident `caseId` under the configured reporter. Do not pass
a web submission ID. No guessed/individual-file endpoint was added. No new live
requests were needed for implementation. Tests use only synthetic bytes.

Bounds: at most32MiB JSON response,20 attachments,20MiB decoded per attachment;
base64 is strictly validated. Empty attachment lists are valid. Redirects are
refused, errors redact underlying URL/body/exception chain, and no retry occurs.
No contact registration, upload, filing or other municipal write occurs.

Caller must establish authority for the reporter identity. App login and checksum
validation do not prove ownership. A configured start date may exclude an old case;
absence then means not verified in this listing, not that the case does not exist.
Changing dates does not authorize querying somebody else's identity.

Treat returned bytes as untrusted private evidence: they may contain plates,
faces, GPS or ID documents. Do not publish them, emit them in public CI or issue
comments, automatically execute/open them, or add them to a public artifact.
Rendering and disclosure need their own checks. The method is library-only, with
no public HTTP/CLI endpoint. It does not change the ledger or infer enforcement.
