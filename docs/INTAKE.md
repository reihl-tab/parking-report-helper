# Intake from private GitHub storage

Original photos and report.json records are saved into a separate PRIVATE GitHub
repository, with one folder per report under reports/. The public
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

See [private-runner.yml](private-runner.yml) for a private draft-preparation
workflow template. A deployment may install it as .github/workflows/prepare.yml.
The template uses only
workflow_dispatch, read-only permissions and a pinned helper commit. The reports
are mounted read-only at runtime and excluded from the Docker build context.
The container has no network access and only prepares drafts. Output and diagnostics
are retained as a private artifact for 7 days. No form filling or submission runs.

The checked-in template is gated by REVIEWED_HELPER=false and carries a fixed
helper revision. Before using it, review and pin the intended helper revision,
then enable the gate in the private deployment through a reviewed change. The
template does not establish the status or approval of any installed workflow.

No Google credential, Drive token, PAT or external key is needed for this path.
The optional legacy --drive-folder client is unused by this workflow.

Keep reporter identity files outside GitHub. Missing identity values remain marked
missing. Original photos are unmodified; deleting them from the current tree does
not erase Git history. Never change the data repository to public.

## House numbers

Every ready report must include a house number. If it is missing, intake skips the
record with a clarification reason. The intake reader never fabricates a number or
performs a geocoding request. Preparation must confirm the number with the user or
research the nearest numbered address from a real map source before setting ready.
For a nearest-building approximation, keep the correct street and number together,
set address.approximate=true, and store source/source_url and distance in notes.
Do not combine a number from one street with another street's name. A range-
interpolated point is not a verified building frontage.

## Location description

New records must include location_description: a factual Hebrew description of where
the car is relative to visible buildings, entrances, bicycle racks or other landmarks.
Do not infer blocked passage when only bicycle parking is blocked. Keep exact visible
placement separate from an approximate numbered address. Intake appends this description,
photo GPS coordinates (when numeric) and an approximation warning to the violation text.
The existing report renderer adds capture date/time and numbered street address. Legacy
records without location_description remain readable; new preparation must populate it.
