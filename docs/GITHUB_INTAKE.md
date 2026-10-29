# Intake from private GitHub storage

Original photos and report.json records are saved into a separate PRIVATE GitHub
repository, parking-reports, with one folder per report under reports/. The public
helper repository contains code and synthetic fixtures only. The private repository
also hosts its manual workflow, so no cross-repository data credential is needed.
The tool reads a local checkout and prepares drafts. It never submits anything.

## Layout

```
reports/
  2026-10-05_1005_ab12/
    photo.jpg      original photo, unmodified
    report.json    the record below
```

## report.json (schema_version 1)

```json
{
  "schema_version": 1,
  "status": "ready",
  "captured_at": "2026-10-05 10:05",
  "gps": {"lat": 31.75081, "lon": 35.21398, "heading_deg": 338},
  "address": {"street": "התנופה", "house_number": "17", "neighborhood": "תלפיות",
              "city": "ירושלים", "approximate": true, "source": "OpenStreetMap Nominatim"},
  "plate": "00-000-00",
  "car_type": "קופרה",
  "violation": {"description": "על המדרכה וחוסם אותה למעבר הולכי רגל",
                "category": "sidewalk", "confidence": "high",
                "needs_clarification": false, "question": ""},
  "photo": "photo.jpg",
  "notes": "",
  "user_corrections": []
}
```

- `status`: `ready`, `needs_clarification` or `reported`. Only `ready` records are used.
- `captured_at`: Israel local time from the photo EXIF, `YYYY-MM-DD HH:MM`.
- `violation.description`: Hebrew, used as the report text. `category` is one of `sidewalk`,
  `crosswalk`, `bus_stop`, `disabled_spot`, `corner`, `fire_hydrant`, `double_parking`, `other`.
- `violation.question`: what to ask the reporter when the violation is unclear.
- `user_corrections`: the reporter's corrections, kept so the next guesses improve.
- Required for use: `plate`, `captured_at`, `address.street`, `address.house_number`. Anything else is optional.
  A missing house number is skipped with a clarification reason and never invented; see
  [House numbers](INTAKE.md#house-numbers) in the canonical intake guide for the
  nearest-building approximation rules.
- Records that are not ready, or lack a required value, are skipped with a reason on stderr.
  They are never guessed.

## Usage

```sh
# a local checkout of the private repository
parking-report --intake ./reports --municipality jerusalem --reporter ~/reporter.json
```

Only ready records are used. Skipped details and photo paths go to stderr. BOTH
stdout and stderr may contain private information: never print them in public
workflow logs or upload them as artifacts of the public code repository.

## Private manual workflow

See [private-runner.yml](private-runner.yml) for the workflow installed in the
private parking-reports repository as .github/workflows/prepare.yml. It uses only
workflow_dispatch, read-only permissions and a pinned helper commit. The reports
are mounted read-only at runtime and excluded from the Docker build context.
The container has no network access and only prepares drafts. Output and diagnostics
are retained as a private artifact for 7 days. No form filling or submission runs.

The runner is gated by REVIEWED_HELPER=false until helper PR #8 is reviewed. After
review, pin the approved helper revision and set the gate to true in the private
repository. No merge or real-data run is part of this change.

No Google credential, Drive token, PAT or external key is needed for this path.
The optional legacy --drive-folder client is unused by this workflow.

Keep reporter identity files outside GitHub. Missing identity values remain marked
missing. Original photos are unmodified; deleting them from the current tree does
not erase Git history. Never change the data repository to public.
