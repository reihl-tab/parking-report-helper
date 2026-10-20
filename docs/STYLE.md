# Style guide

Working rules for this repository. This document grows as conventions are agreed (#67).

## Diagrams

Any issue or proposal that needs design - API, containers, architecture, data flow, security boundaries, test pipeline - includes a Mermaid diagram in the issue body or in `docs/`. Mermaid renders natively on GitHub, stays in version control, and adds no binary files. A diagram without a design question is decoration; a design without a diagram is incomplete.

Diagrams never contain real licence plates, exact addresses, GPS coordinates, or any living-person data. Use synthetic placeholders. This applies to issue text, `docs/`, and PR descriptions alike (owner rule, 2026-10-20).

## Commit and PR style

One issue per change, one PR per issue, full rationale in the commit body. Squash merge to `main`; CI must be green.
