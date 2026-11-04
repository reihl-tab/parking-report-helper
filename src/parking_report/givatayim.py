"""Fill-only Givatayim resident form. No report submission capability."""

from __future__ import annotations

import logging
import re
from dataclasses import dataclass, field
from pathlib import Path
from typing import TYPE_CHECKING

if TYPE_CHECKING:
    from playwright.sync_api import Browser, BrowserContext, Locator, Page, Route

URL = "https://www.givatayim.muni.il/moked/"
LOG = logging.getLogger(__name__)
FILE_FIELDS = ("c35100", "c39100", "c45100", "c1684132793")
TEXT_FIELDS = {
    "c2100": "first_name",
    "c6100": "last_name",
    "c8100": "mobile_phone",
    "c21100": "tz",
    "c11100": "street_id",
    "c14100": "house_number",
    "c26100": "event_street_id",
    "c29100": "event_house_number",
    "c32100": "event_description",
}


class FormError(ValueError):
    """Safe stage code only; never includes applicant data."""


@dataclass(frozen=True, repr=False)
class ResidentReport:
    """All contact values belong to the resident, never a fallback identity."""

    first_name: str
    last_name: str
    mobile_phone: str
    national_id: str
    resident_street_id: str
    resident_house_number: str
    incident_street_id: str
    incident_house_number: str
    description: str
    attachments: tuple[Path, ...] = ()

    def validate(self) -> None:
        names = (self.first_name, self.last_name)
        numeric = (
            self.resident_street_id,
            self.resident_house_number,
            self.incident_street_id,
            self.incident_house_number,
        )
        if not all(re.fullmatch(r"[0-9a-zA-Zא-ת]{2,100}", x) for x in names):
            raise FormError("input_name")
        if not re.fullmatch(r"[0-9]{10}", self.mobile_phone):
            raise FormError("input_mobile")
        if not re.fullmatch(r"[0-9]{9}", self.national_id):
            raise FormError("input_identity")
        if not all(re.fullmatch(r"[0-9]{1,8}", x) for x in numeric):
            raise FormError("input_address")
        if not self.description.strip() or len(self.description) > 700:
            raise FormError("input_description")
        if len(self.attachments) > 4:
            raise FormError("input_attachment_count")
        for path in self.attachments:
            if (
                not isinstance(path, Path)
                or path.is_symlink()
                or not path.is_file()
                or path.suffix.lower() not in {".jpg", ".jpeg", ".png"}
                or not 0 < path.stat().st_size <= 10_000_000
            ):
                raise FormError("input_attachment")


@dataclass(repr=False)
class FillSession:
    """Fresh resident context. Close after preview, never reuse for submission."""

    context: BrowserContext
    page: Page
    _loaded: bool = False
    _used: bool = False
    submission_enabled: bool = field(default=False, init=False)

    def open(self) -> None:
        if self._loaded:
            raise FormError("session_already_opened")
        self.page.goto(URL, wait_until="domcontentloaded")
        self._loaded = True
        if self.page.url != URL:
            raise FormError("page_origin")

    def close(self) -> None:
        self.context.close()

    def fill(self, report: ResidentReport) -> dict[str, bool | int]:
        """Validate, fill and read back. Does not interact with Turnstile."""
        report.validate()
        if self._used or not self._loaded or self.page.url != URL:
            raise FormError("session_state")
        self._used = True
        LOG.debug("givatayim stage=schema")
        try:
            self._fill(report)
        except FormError:
            raise
        except Exception:
            raise FormError("browser_fill_failed_close_session") from None
        LOG.debug("givatayim stage=preview_ready submission_enabled=false")
        return {
            "submission_enabled": False,
            "attachment_count": len(report.attachments),
        }

    def _fill(self, data: ResidentReport) -> None:
        form = self.page.locator("form#form-46")
        if form.count() != 1 or any(
            form.get_attribute(k) != v
            for k, v in {
                "action": "/moked/#form-46",
                "method": "post",
                "enctype": "multipart/form-data",
            }.items()
        ):
            raise FormError("form_schema")
        self._check_controls(form)
        resident = form.locator('[name="c47100"][value="תושב/ת העיר"]')
        resident.check()
        values = {
            "c2100": data.first_name,
            "c6100": data.last_name,
            "c8100": data.mobile_phone,
            "c21100": data.national_id,
            "c11100": data.resident_street_id,
            "c14100": data.resident_house_number,
            "c26100": data.incident_street_id,
            "c29100": data.incident_house_number,
            "c32100": data.description,
        }
        for name in ("c2100", "c6100", "c8100", "c21100", "c11100", "c14100"):
            self._set(form, name, values[name])
        next_step = form.locator("button.next-step")
        if next_step.count() != 1:
            raise FormError("step_schema")
        next_step.click()
        for name in ("c26100", "c29100", "c32100"):
            self._set(form, name, values[name])
        for name, path in zip(FILE_FIELDS, data.attachments):
            form.locator(f'[name="{name}"]').set_input_files(path)
        for name, value in values.items():
            if form.locator(f'[name="{name}"]').input_value() != value:
                raise FormError("readback_mismatch")
        form.locator('[type="submit"]').evaluate_all(
            "elements => elements.forEach(element => element.disabled = true)"
        )

    @staticmethod
    def _check_controls(form: Locator) -> None:
        for name, key in TEXT_FIELDS.items():
            control = form.locator(f'[name="{name}"]')
            if (
                control.count() != 1
                or control.get_attribute("data-keyname") != key
                or control.input_value()
                or control.is_disabled()
                or control.get_attribute("readonly") is not None
            ):
                raise FormError("field_schema_or_prefilled")
        resident = form.locator('[name="c47100"][value="תושב/ת העיר"]')
        other = form.locator('[name="c47100"][value="תושב/ת חוץ"]')
        if (
            resident.count() != 1
            or other.count() != 1
            or other.is_checked()
            or resident.is_checked()
        ):
            raise FormError("resident_branch")
        for name in FILE_FIELDS:
            control = form.locator(f'[name="{name}"]')
            if (
                control.count() != 1
                or control.get_attribute("type") != "file"
                or control.get_attribute("data-max-size") != "10"
                or control.input_value()
                or control.is_disabled()
            ):
                raise FormError("attachment_schema")
        if form.locator('[name="c32100"]').get_attribute("data-maxlength") != "700":
            raise FormError("description_schema")

    @staticmethod
    def _set(form: Locator, name: str, value: str) -> None:
        # Locator is duck-typed to keep Playwright an optional dependency.
        control = form.locator(f'[name="{name}"]')
        if name in {"c11100", "c26100"}:
            if control.locator(f'option[value="{value}"]').count() != 1:
                raise FormError("street_not_in_live_form")
            control.select_option(value)
        else:
            control.fill(value)


def create_session(browser: Browser) -> FillSession:
    """Create isolated context with write blocking before the first page load."""
    context = browser.new_context(service_workers="block")
    context.add_init_script(
        "document.addEventListener('submit', event => {event.preventDefault(); "
        "event.stopImmediatePropagation();}, true)"
    )
    page = context.new_page()
    session = FillSession(context, page)

    def guard(route: Route) -> None:
        request = route.request
        if request.method not in {"GET", "HEAD"} or (
            request.is_navigation_request() and (session._loaded or request.url != URL)
        ):
            route.abort("blockedbyclient")
        else:
            route.continue_()

    context.route("**/*", guard)
    return session
