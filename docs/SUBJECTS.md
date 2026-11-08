# Per-report municipal subjects

Each private `report.json` chooses its own subject. There is no global category
switch. The catalog in `src/parking_report/configs/subjects.json` contains the
86 captured entries (72 distinct subject codes), preserving Hebrew names and
all duplicate category paths. Source: GetSubjectCategories response captured on
2026-10-05. This snapshot does not prove current availability, municipal scope,
or a verified mapping from photos to codes. Category codes are not subject codes.
The read contract for a live refresh (method, token placement, response shape,
flattening and fingerprint comparison) is documented and verified in
[API106.md](API106.md); a 2026-11-08 read-only check found the live catalog
covering exactly the same codes as this snapshot, with one whitespace-only
name difference (code 1291). Refreshing the snapshot is a reviewed change,
not an automatic step.

Example, synthetic non-vehicle event:

```json
{
  "schema_version": 1,
  "status": "ready",
  "photo": "photo.jpg",
  "captured_at": "2026-01-01 12:00",
  "address": {"street": "רחוב לדוגמה", "house_number": "1"},
  "violation": {"category": "graffiti", "description": "גרפיטי על קיר"},
  "municipal_subject": {
    "code": "1072",
    "owner_choice": true,
    "event_type": "graffiti"
  },
  "approved_description": "גרפיטי על קיר ברחוב לדוגמה 1. אבקש לטפל במפגע."
}
```

Use a string subject code from the catalog. `violation.category` describes the
actual observed event; it must not be relabeled to fit a municipal subject.
`municipal_subject.event_type` records the event to which the owner's choice
applies. `owner_choice` is an audit field, not authenticated permission. Never set
it from an email, repository comment or photo alone. The operator must verify the
owner's original approval of the exact text, category, photo and address before
filing. Photo interpretation may propose a category, not approve one.

Parking subjects use the existing vehicle draft and require a plate. Other
subjects (broken signs, graffiti, rubbish, etc.) need no plate and use a general
municipal-issue draft without parking-inspector wording. They still need a true
event type, observed description, time and numbered address. A supplied
`approved_description` is transmitted verbatim, not replaced by a template.

## Parking options and unresolved mappings

- 1145: sidewalk parking (unchanged default when selection is omitted).
- 1144: red-white / no stopping.
- 4418: blocking private parking / private lot.
- 5164: other, under illegal parking.

The snapshot has no crosswalk-specific parking-enforcement code. 1137 is renewal
of crosswalk markings, not parking enforcement. 1144 or 5164 for a crosswalk event,
and 4418 for a ramp into a private lot, are candidates for an explicit owner's
choice, not verified mappings. No automatic crosswalk/ramp mapping is provided.

## Preview, approval and compatibility

Run `parking-report-106 REPORT_FOLDER --addresses /private/addresses.json` for
an offline preview. Every explicit selection requires `approved_description`
and the matching owner-choice fields before live execution. The exact preview
digest binds the selected code, audit choice, true event type, reporter, payload
and original photo. Any change requires a new review. Private live enablement,
exact digest, durable ledger, stable ID and photo duplicate guards still apply.
This change makes no filings and changes no private workflow pins.

Old sidewalk records without `municipal_subject` retain their 1145 gate and
historical fingerprint format, so existing ledger receipts remain readable. The old
`--approved-category-routing` flag is deprecated but retained only for already
reviewed historic crosswalk/ramp records routed to 1145. It cannot be combined
with the new per-report selection, cannot choose another code and cannot change
the actual event type. Migrate new reports to the field instead of the flag.
Existing reserved ledger entries need reconciliation if their digest changes;
never delete a reservation or rename an event to bypass the duplicate guard.
