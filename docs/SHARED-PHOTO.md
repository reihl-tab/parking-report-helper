# Explicitly approved shared photos

One unchanged original photo can show two independently identifiable cars
(#271). Each car is still its own intake, with its own payload digest,
category, case receipt and state. The photo is never re-encoded or cropped to
get past the original-photo index. A manifest is an audit record, never
permission by itself.

Each of the two private records carries the same `shared_photo` with exactly
`intake_ids`, `plates`, `photo_sha256` and `authority_reference`:

- `intake_ids`: exactly two distinct intake folder names, sorted.
- `plates`: the two distinct plates, normalized digits only, in the same order
  as `intake_ids`. A plate that the strict standard-plate normalizer rejects
  cannot join a group. Each record's own `plate` must normalize to its entry.
- `photo_sha256`: the sha256 of the original photo bytes; it must equal the
  record's actual photo.
- `authority_reference`: the caller-verified approval evidence reference, in
  the same format as a follow-up `approval_reference`.

The manifest and the member's own intake ID are bound into that member's
preview digest. A live member also requires `--approved-shared-photo-sha256`
equal to that digest, as well as all normal gates. Shared-photo previews never
reserve a ledger slot, even with `--ledger-dir`. A record with both `followup`
and `shared_photo` is rejected.

The durable ledger stays version 1. The first member to reserve locks the
group: an optional `shared_photos` index maps the photo hash to a plate-free
group fingerprint (intake IDs, photo hash and authority reference) and the two
hashed intake IDs, and the member entry gains a `shared_photo` relation. The
original photo index keeps pointing at the first member and is never
rewritten. The second member must present the identical group; a different
group, a third intake, an unrelated photo or a changed manifest is refused,
before or after any submission. An intake already reserved without a manifest
cannot join a group later; that is a changed input. Plates are never written
to the ledger, logs or any stored hash.

## Sibling check (fail-closed)

The ledger holds nothing derived from plates, so it cannot compare the two
members' plate lists. Without another check, two cars whose manifests name
different plate lists that share one plate (the same car twice) would each pass
against their own record. So every reservation of a member, first or second,
and every retry also reads the sibling's record and refuses unless the two
manifests are identical.

Layout assumption: both intake folders sit side by side under one parent, and
each folder's name is its intake ID. The sibling's record is
`<reserving folder's parent>/<other intake ID from the manifest>/report.json`.
The sibling ID must pass the same opaque folder-name rule the ledger applies to
intake IDs, so it cannot contain a path separator or `..`.

The two manifests are compared in full, intake IDs and plate order included,
as canonical bytes: each is validated, then serialized as JSON with sorted
keys and no whitespace, and the bytes must be equal. Only the key order inside
the object is ignored; every value must match.

A reservation is refused, with the usual fixed blocked output and exit code,
when the sibling folder or its `report.json` is missing, is not a regular file
(a FIFO or device is refused before any read, so the check never blocks; #284),
cannot be read or is not a JSON object, has no `shared_photo`, or carries a different or invalid
manifest. Both folders must therefore exist side by side before either car is
submitted, and the sibling must stay in place for retries. Previews do not
read the sibling.

There is no path option and no setting to relax this. A workflow that keeps
the two intake folders apart must come back for an explicit path flag; moving
or copying records to get past the check is not a substitute.

## Retries and uncertainty

Retries are per intake: a completed member returns its stored receipt.
Unlike follow-ups, where uncertainty blocks siblings, an uncertain creation of
one member does not block the other: they are distinct cases. The uncertain
member itself stays refused with no automatic retry, as before. Ordinary
duplicate photos remain blocked exactly as before.

The manifest proves only that the caller recorded an approval. The caller must
independently verify the authority, that both cars are distinct and that the
photo is the unchanged original, immediately before live use.
