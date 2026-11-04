"""Run explicitly in browser container; all network is synthetic/intercepted."""

import os
import tempfile
import unittest
from pathlib import Path

from playwright.sync_api import sync_playwright
from test_givatayim import report

from parking_report.givatayim import URL, FormError, create_session

FIXTURE = Path(__file__).parents[1] / "fixtures/givatayim/form.html"


class BrowserTests(unittest.TestCase):
    def test_engines(self):
        with sync_playwright() as pw:
            for engine in ("chromium", "webkit"):
                with self.subTest(engine=engine):
                    browser = getattr(pw, engine).launch()
                    try:
                        self.check_engine(browser, engine)
                    finally:
                        browser.close()

    def session(self, browser, transform=lambda x: x):
        session = create_session(browser)

        # Intercept fixture GET only. All other requests reach the write guard.
        def fixture(route):
            if route.request.method == "GET":
                route.fulfill(
                    body=transform(FIXTURE.read_text()), content_type="text/html"
                )
            else:
                route.fallback()

        session.context.route(URL, fixture)
        session.open()
        return session

    def check_engine(self, browser, engine):
        session = self.session(browser)
        writes = []
        session.context.on("request", lambda req: writes.append(req.method))
        with tempfile.TemporaryDirectory() as tmp:
            image = Path(tmp) / "synthetic.png"
            # A valid tiny synthetic PNG, no personal photo fixture.
            import base64

            image.write_bytes(
                base64.b64decode(
                    "iVBORw0KGgoAAAANSUhEUgAAAAEAAAABCAQAAAC1HAwCAAAAC0lEQVR42mP8/x8AAusB9Wl6rhkAAAAASUVORK5CYII="
                )
            )
            result = session.fill(report(attachments=(image,)))
            self.assertEqual(
                result, {"submission_enabled": False, "attachment_count": 1}
            )
            page = session.page
            self.assertEqual(
                page.locator('[name="csrf"]').input_value(), "synthetic-fresh-csrf"
            )
            self.assertTrue(page.locator('[type="submit"]').is_disabled())
            # Even direct DOM form submission cannot get a request accepted.
            page.evaluate("document.querySelector('form').requestSubmit()")
            self.assertEqual(page.url, URL)
            self.assertEqual(writes, [])
            self.assertEqual(
                page.evaluate(
                    "fetch('/moked/', {method:'POST',body:'test'}).then(()=>false).catch(()=>true)"
                ),
                True,
            )
            self.assertTrue(all(x == "POST" for x in writes))
            output = os.environ.get("GIVATAYIM_SCREENSHOTS")
            if output:
                Path(output).mkdir(parents=True, exist_ok=True)
                page.screenshot(
                    path=str(Path(output) / f"givatayim-{engine}.png"), full_page=True
                )
            with self.assertRaises(FormError):
                session.fill(report())
        session.close()
        for transform in (
            lambda s: s.replace(
                'action="/moked/#form-46"', 'action="https://example.invalid/"'
            ),
            lambda s: s.replace('data-keyname="first_name"', 'data-keyname="changed"'),
            lambda s: s.replace('data-maxlength="700"', 'data-maxlength="900"'),
            lambda s: s.replace('name="c2100"', 'name="c2100" value="prefilled"'),
        ):
            session = self.session(browser, transform)
            with self.assertRaises(FormError):
                session.fill(report())
            session.close()
        session = self.session(browser)
        with self.assertRaises(FormError):
            session.fill(report(incident_street_id="999999"))
        session.close()


if __name__ == "__main__":
    unittest.main()
