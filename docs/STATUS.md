# Private report status and reminder drafts

```sh
umask 077
PYTHONPATH=src python -m parking_report.status /private/ledger/reports.csv \
  --as-of 2026-10-08 > /private/status.md
```

This offline module generates a private Hebrew status table and review-only reminder
text. It does not send, schedule, update the ledger, query mail or contact 106.
The table contains private plates and references: never use public Actions logs,
artifacts or a public output destination. Review recipients and final text before
sending. Recheck live mail/case state immediately before any send.

Required CSV columns are folder_id, filed_date (YYYY-MM-DD), street, event_type,
municipal_ref and ref_mapping. Optional existing plate and x_post_url plus new
facebook_post_url appear as plain text in the table. Extra columns are ignored.
Legacy rows without response fields show unknown and cannot trigger reminders.

Optional response fields:
- municipal_response: unknown (or empty), no_reply, received. A receipt opening
  a case is not a substantive municipal response. Do not mark received merely
  because a confirmation email arrived.
- response_checked_at: local review date, YYYY-MM-DD.
- response_evidence: private source/check reference supporting that observation.
  Both date and evidence are required for no_reply or received.
- last_reminder_at: date of an actually sent reminder, not a generated draft.
- receipt_evidence: private matching-evidence reference. The candidate gate uses
  verified_receipt plus this field and the complete municipal_ref. It reads the
  operator's annotations; it does not authenticate evidence or establish a match.

The owner's selected threshold is seven calendar days from filed_date. A candidate
requires at least seven days, an explicit no_reply check exactly on --as-of, and a
verified_receipt mapping with evidence. Unknown, FIFO and owner_stated_order
mappings cannot produce a case-specific reminder draft. A reminder already sent
on that check date suppresses another candidate. This is not a retry/recurrence
policy; a later reviewed check can produce a new draft, never an automatic send.

Rows sort by filing date and stable ID. Invalid/future dates, missing required
columns, duplicate IDs/references, unknown response codes and control characters
block the whole output. Generic errors contain no private values. Markdown
metacharacters and HTML are escaped; URLs are text, not clickable message targets.
Synthetic unit/CLI tests cover day 6/7/8, received/unknown/stale responses, missing
mapping evidence, prior reminder, validation, escaping and a legacy BOM CSV.

## Reading the municipal record

The106 library now has an explicit read-only own-reporter case listing method.
See [CASE-LISTING.md](CASE-LISTING.md) for the verified app route, privacy limits
and caller authority requirements. No public CLI/HTTP data dump is exposed.
