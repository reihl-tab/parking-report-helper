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

## Extension after a completed intake (#293)

The manifest above covers two cars known *before* either reservation. One
further, separately verified car may be reported later from the unchanged
original photo of one **completed ordinary** vehicle intake, under the full
contract in [the design note](SHARED-PHOTO-EXTENSION-DESIGN.md):

- The new private record carries `shared_photo_extension` with exactly
  `parent_id`, `parent_digest`, `photo_sha256`, `parent_plate`, `plate` and
  `authority_reference`. The two plates are distinct and normalized; there is
  no municipal case number in the manifest. All fields plus the new intake ID
  enter the new preview digest, and live use needs
  `--approved-extension-sha256` equal to that digest on top of every ordinary
  gate. A record with both `followup` or `shared_photo` and an extension is
  rejected. Extension previews reserve nothing.
- The parent folder must sit beside the new folder, and its record is read
  with the nonblocking regular-file reader (#284): missing, unreadable,
  non-object or non-regular inputs are refused without hanging. Both original
  photos must be byte-identical and carry the declared hash, and each record's
  structured plate must match the manifest. Plates are never parsed from
  prose.
- The ledger parent entry is the source of truth for the submission digest,
  and the preserved private submission artifacts must corroborate it: a
  recorded `submission_digest` in the parent's report.json or the saved
  submitted payload `municipal-payload.json`, plus a recompute of the original
  payload through the preparation contract with the original photo and
  reporter inputs (never printed or persisted). A saved digest string alone is
  not sufficient; missing, changed or conflicting evidence makes the parent
  ineligible. A municipal reference stays optional unverified metadata and
  never selects the parent.
- The parent must be complete, ordinary, and own the original photo index;
  existing children, follow-ups or shared groups make it ineligible. The
  parent entry and the photo index stay byte-for-byte unchanged.
- The reservation stores a plate-free relation on the new entry (hashed
  parent ID, parent digest, photo hash, authority reference) and, in the
  optional private `photo_vehicle` index, one derived HMAC guard key per
  (original photo, plate) plus a bounded slot allowing exactly one extension
  per original photo. This is the single intentional private-index exception
  to the rule that the ledger holds nothing derived from plates: the index
  stores keyed hashes only, never raw plates, and nothing relaxes the rule for
  any public output or diagnostic.
- The index is initialized only by an explicit reviewed provisioning operation
  with one key ID and its verification value (constant-time verified before
  any reservation); a missing, invalid, unknown-key-ID or mismatched seed
  fails closed, and there is no silent initialization or re-keying. The seed
  is a private-environment secret supplied at runtime as `PARKING_GUARD_SEED`
  (hex, at least 128 bits) with `PARKING_GUARD_KEY_ID`; seed provisioning is a
  separate owner-approved step, never part of a submission.
- Retries with the same intake and manifest resume the checkpoint and
  recheck both private records; a changed relation, renamed intake, different
  photo or second target fails closed. An uncertain new create blocks that
  intake without automatic retry and never resubmits the parent.
