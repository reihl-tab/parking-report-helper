"""Generate Hebrew parking-violation report drafts from JSON vehicle records."""

from .report import REQUIRED, Car, build_report, build_reports, format_datetime

__all__ = ["REQUIRED", "Car", "build_report", "build_reports", "format_datetime"]
__version__ = "0.2.0"
