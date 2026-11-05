"""Offline-only contact fixtures built from deliberately synthetic components.

Production accepts only Israeli-shaped mobile numbers and checksum-valid IDs.
These fixtures retain that validation coverage with the existing zero-pattern
values. Construction is NOT proof of non-allocation. Never use these fixtures
with a live transport or put their evaluated values into public examples.
"""

SYNTHETIC_PHONE = "05" + "0" * 8
SYNTHETIC_ID = "0" * 7 + str(18)
