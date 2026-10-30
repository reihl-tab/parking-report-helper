# Explicitly approved follow-ups

A follow-up is a new municipal case, not a retry. The caller must independently
verify owner authority, the current closed parent case, and the precise scope.
A manifest is an audit record, never permission by itself.

The private record may carry `followup` with exactly `parent_id`, `parent_digest`,
`parent_case_number`, `parent_closed_at`, and `approval_reference`. No identity or
private evidence belongs in public fixtures. The entire manifest is bound into
the normal preview digest. A live follow-up also requires
`--approved-followup-sha256` equal to that digest, as well as all normal gates.

Follow-up previews never reserve a ledger slot, even with `--ledger-dir`.

The durable ledger requires a completed parent with matching digest and photo.
It retains the original photo index and adds a parent relation to the child.
A nonempty case number in the parent receipt must agree with the manifest.
Legacy receipts with no case number still require caller verification.
Only one child may reserve a parent; uncertainty blocks siblings. Further rounds
must explicitly name their immediately completed parent. Reusing a completed
child returns its receipt. Changed inputs and ordinary duplicate photos remain
blocked. There is no re-encoding, index deletion, automatic follow-up, or new
municipal write route.

Closure and case-number metadata are caller-verified observations, not assertions
that this program can authenticate. The normal source-of-truth check and review
must happen immediately before live use. No live use before reviewed merge.
