"""Jerusalem API transport adapted from the user-supplied client. No embedded secrets."""

from __future__ import annotations

import base64
import hashlib
import io
import json
import math
import os
import re
import subprocess
import tempfile
import unicodedata
import urllib.error
import urllib.parse
import urllib.request
import uuid
import warnings
from datetime import date
from pathlib import Path
from typing import Any

from filelock import FileLock, Timeout
from PIL import Image, ImageOps
from pyproj import Transformer

ROOT = Path(__file__).resolve().parent
BASE = "https://api.jerusalem.muni.il/CRMCityConnect/api/jerusalem/"
MAX_IMAGE = 20 * 1024 * 1024
Image.MAX_IMAGE_PIXELS = 40000000
ITM = Transformer.from_crs("EPSG:4326", "EPSG:2039", always_xy=True)


class InputError(Exception):
    pass


class APIError(Exception):
    pass


class ReviewNeeded(Exception):
    pass


def json_file(path: Any) -> Any:
    try:
        return json.loads(path.read_text(encoding="utf-8"))
    except (OSError, ValueError) as exc:
        raise InputError(f"Cannot read valid UTF-8 JSON from {path}.") from exc


def normalize(text: Any) -> Any:
    return "".join(c for c in unicodedata.normalize("NFKC", text) if c.isalnum())


def check_text(value: Any, label: Any) -> Any:
    value = value.strip()
    if not value:
        raise InputError(f"{label} must not be empty.")
    try:
        restored = value.encode("latin-1").decode("utf-8")
    except (UnicodeEncodeError, UnicodeDecodeError):
        restored = value
    if restored != value and re.search("[א-ת]", restored):
        raise InputError(
            f"{label} contains corrupted Hebrew encoding. Supply the original Unicode text."
        )
    return value


def make_contact(args: Any) -> Any:
    number = args.national_id.strip()
    if not re.fullmatch("[0-9]{1,9}", number):
        raise InputError("National ID must contain one to nine digits.")
    number = number.zfill(9)
    products = [int(digit) * (1 + i % 2) for (i, digit) in enumerate(number)]
    if number == "000000000" or sum(n if n < 10 else n - 9 for n in products) % 10:
        raise InputError("National ID checksum is invalid.")
    phone = re.sub("[\\s()\\-]", "", args.phone)
    if phone.startswith("+972"):
        phone = "0" + phone[4:]
    if not re.fullmatch("05[0-9]{8}", phone):
        raise InputError(
            "Supply an Israeli mobile number, such as 05XXXXXXXX or +9725XXXXXXXX."
        )
    email = args.email.strip()
    if not re.fullmatch("[^\\s@]+@[^\\s@]+\\.[^\\s@]+", email):
        raise InputError("Email address syntax is invalid.")
    return {
        "ContactIdInCRM": "",
        "ContactIdType": "100000000",
        "ContactIdNumber": number,
        "FirstName": check_text(args.first_name, "First name"),
        "LastName": check_text(args.last_name, "Last name"),
        "MobilePhoneNumber": phone,
        "Email": email,
        "CityCode": "3000",
        "CityName": "ירושלים",
        "StreetCode": "",
        "StreetName": "",
        "HouseNumber": "",
        "ApartmentNumber": "",
        "SecondPhoneNumber": "",
        "Postbox": "",
        "ZIPCODE": "",
        "AllowSendMarketingMaterial": False,
    }


def photo_gps(photo: Any) -> Any:
    try:
        gps = photo.getexif().get_ifd(34853)

        def dms(v: Any) -> Any:
            return float(v[0]) + float(v[1]) / 60 + float(v[2]) / 3600

        (lat, lon) = (dms(gps[2]), dms(gps[4]))
        refs = [
            v.decode("ascii").rstrip("\x00") if isinstance(v, bytes) else v
            for v in (gps[1], gps[3])
        ]
        if refs[0] not in ("N", "S") or refs[1] not in ("E", "W"):
            return None
        lat *= -1 if refs[0] == "S" else 1
        lon *= -1 if refs[1] == "W" else 1
        if not (math.isfinite(lat) and math.isfinite(lon)) or (lat == 0 and lon == 0):
            return None
        return (lat, lon)
    except (
        KeyError,
        ValueError,
        TypeError,
        IndexError,
        ZeroDivisionError,
        AttributeError,
    ):
        return None


def read_picture(path: Any) -> Any:
    try:
        with path.open("rb") as stream:
            raw = stream.read(MAX_IMAGE + 1)
    except OSError as exc:
        raise InputError(f"Cannot read photo: {path}") from exc
    if not raw or len(raw) > MAX_IMAGE:
        raise InputError("The photo must be nonempty and at most 20 MB.")
    try:
        with warnings.catch_warnings():
            warnings.simplefilter("error", Image.DecompressionBombWarning)
            with Image.open(io.BytesIO(raw)) as photo:
                if photo.format not in {"JPEG", "PNG", "WEBP", "TIFF"}:
                    raise InputError("Supported photo formats: JPEG, PNG, WebP, TIFF.")
                photo.load()
                gps = photo_gps(photo)
                if gps and (not in_jerusalem(*gps)):
                    raise InputError(
                        "The photo GPS is outside the supported Jerusalem area."
                    )
                clean = ImageOps.exif_transpose(photo).convert("RGB")
                clean.thumbnail((2560, 2560))
                out = io.BytesIO()
                clean.save(out, "JPEG", quality=90)
        return (hashlib.sha256(raw).hexdigest(), gps, out.getvalue())
    except InputError:
        raise
    except Exception as exc:
        raise InputError("Cannot decode the photo or its metadata.") from exc


def in_jerusalem(lat: Any, lon: Any) -> Any:
    return 31.65 <= lat <= 31.95 and 35.05 <= lon <= 35.35


class Addresses:
    def __init__(self, path: Any) -> None:
        data = json_file(path)
        if not isinstance(data, dict) or data.get("version") != 1:
            raise InputError("Unsupported address lookup format.")
        self.streets = {str(s["StreetCode"]): s for s in data["streets"]}
        self.aliases: dict[str, set[str]] = {}
        for code, street in self.streets.items():
            for key in ("StreetName", "StreetArabicName"):
                if street.get(key):
                    self.aliases.setdefault(normalize(street[key]), set()).add(code)
        self.points = []
        for lat, lon, house, name in data["addresses"]:
            codes = self.aliases.get(normalize(name), set())
            if not re.fullmatch("\\d+[\\w /\\-]*", str(house)):
                continue
            if not in_jerusalem(lat, lon):
                continue
            (x, y) = ITM.transform(lon, lat)
            self.points.append(
                (x, y, str(house), next(iter(codes)) if len(codes) == 1 else None, name)
            )

    def resolve(self, street_name: Any, house: Any, gps: Any) -> Any:
        gps_xy = ITM.transform(gps[1], gps[0]) if gps else None
        if street_name:
            codes = self.aliases.get(normalize(street_name), set())
            if len(codes) != 1:
                raise InputError(
                    "Street name has no unique municipal match. Use the exact Hebrew street name."
                )
            code = next(iter(codes))
            if not re.fullmatch("[0-9]+[\\w /\\-]*", house) or len(house) > 16:
                raise InputError("Invalid house number.")
            points = [
                p
                for p in self.points
                if p[3] == code and normalize(p[2]) == normalize(house)
            ]
            if gps_xy:
                (x, y) = gps_xy
                if points and min(math.hypot(p[0] - x, p[1] - y) for p in points) > 200:
                    raise InputError(
                        "The supplied address is more than 200 m from the photo GPS. Check the address."
                    )
                source = "photo_gps"
            else:
                if not points:
                    raise InputError(
                        "Photo has no usable GPS and this house number is absent from the address data."
                    )
                (x, y) = points[0][:2]
                if any(math.hypot(p[0] - x, p[1] - y) > 100 for p in points):
                    raise InputError(
                        "This address has multiple locations. Use a photo with GPS."
                    )
                source = "address_lookup"
        else:
            if not gps_xy:
                raise InputError(
                    "Photo has no usable GPS. Supply --address or --street and --house."
                )
            (x, y) = gps_xy
            (nearby, seen) = ([], set())
            for point in sorted(
                self.points, key=lambda p: (p[0] - x) ** 2 + (p[1] - y) ** 2
            ):
                (px, py, number, street_code, name) = point
                distance = math.hypot(px - x, py - y)
                if distance > 150:
                    break
                identity = (street_code or normalize(name), number)
                if identity not in seen:
                    seen.add(identity)
                    nearby.append((distance, number, street_code, name))
                if len(nearby) == 5:
                    break
            if (
                not nearby
                or nearby[0][2] is None
                or nearby[0][0] > 35
                or (len(nearby) > 1 and nearby[1][0] - nearby[0][0] < 8)
            ):
                choices = "; ".join(
                    f"{(self.streets[c]['StreetName'] if c else name)} {h} ({d:.0f} m)"
                    for (d, h, c, name) in nearby
                )
                raise InputError(
                    "GPS does not identify one clear address. Supply --address. Nearby: "
                    + (choices or "none")
                )
            (_, house, nearest_code, _) = nearby[0]
            # The guard above already rejected a None street code; this only
            # narrows the type for mypy (no behavior change).
            assert nearest_code is not None
            code = nearest_code
            source = "photo_gps"
        return {
            "street_code": code,
            "street_name": self.streets[code]["StreetName"],
            "house": house,
            "x": int(x),
            "y": int(y),
            "coordinate_source": source,
        }


class NoRedirect(urllib.request.HTTPRedirectHandler):
    def redirect_request(
        self, req: Any, fp: Any, code: int, msg: str, headers: Any, newurl: str
    ) -> None:
        return None


CREDENTIALS_ERROR = (
    "Credentials must contain subscription_key and login.UserName/login.Password."
)


class Municipality:
    def __init__(self, credentials: Any) -> None:
        try:
            # Explicit checks, not assert: asserts vanish under python -O and
            # this is a fail-closed input boundary (#110).
            valid = bool(
                isinstance(credentials["subscription_key"], str)
                and credentials["subscription_key"]
                and credentials["login"]["UserName"]
                and credentials["login"]["Password"]
            )
        except (KeyError, TypeError) as exc:
            raise InputError(CREDENTIALS_ERROR) from exc
        if not valid:
            raise InputError(CREDENTIALS_ERROR)
        self.credentials = credentials

    def request(self, endpoint: Any, body: Any) -> Any:
        request = urllib.request.Request(
            BASE + endpoint,
            data=json.dumps(body, ensure_ascii=False).encode("utf-8"),
            method="POST",
            headers={
                "Content-Type": "text/plain; charset=utf-8",
                "User-Agent": "App 106",
                "Ocp-Apim-Subscription-Key": self.credentials["subscription_key"],
            },
        )
        try:
            with urllib.request.build_opener(NoRedirect()).open(
                request, timeout=45
            ) as response:
                result = json.load(response)
        except urllib.error.HTTPError as exc:
            raise APIError(f"{endpoint}: HTTP {exc.code}.") from exc
        except (OSError, ValueError, urllib.error.URLError) as exc:
            raise APIError(f"{endpoint}: network failure or invalid response.") from exc
        if not isinstance(result, dict) or result.get("Success") is not True:
            raise APIError(f"{endpoint}: municipality did not confirm success.")
        return result

    def login(self) -> Any:
        token = self.request("Login", self.credentials["login"]).get("Token")
        if not token:
            raise APIError("Login returned no token.")
        return token

    def list_cases(
        self, token: str, reporter: dict[str, Any], *, from_date: str
    ) -> dict[str, Any]:
        """GET the authorized reporter's cases; never register/update a contact.

        Caller must obtain reporter from its private configuration and establish
        authority separately. Neither a national-ID checksum nor this app token
        proves ownership. Raw case data must not reach public logs/artifacts.
        """
        number = reporter.get("ContactIdNumber") if isinstance(reporter, dict) else None
        kind = reporter.get("ContactIdType") if isinstance(reporter, dict) else None
        if (
            kind != "100000000"
            or not isinstance(number, str)
            or not re.fullmatch(r"[0-9]{9}", number)
        ):
            raise InputError(
                "Supply the configured reporter identity and supported ID type."
            )
        products = [int(digit) * (1 + i % 2) for i, digit in enumerate(number)]
        if number == "000000000" or sum(n if n < 10 else n - 9 for n in products) % 10:
            raise InputError("Configured reporter ID checksum is invalid.")
        if (
            not isinstance(token, str)
            or not token
            or len(token) > 4096
            or any(c.isspace() or ord(c) < 32 for c in token)
        ):
            raise InputError("Invalid session token.")
        try:
            if (
                not isinstance(from_date, str)
                or len(from_date) != 10
                or date.fromisoformat(from_date).isoformat() != from_date
            ):
                raise ValueError()
        except (ValueError, TypeError):
            raise InputError("Supply an explicit ISO start date.") from None
        # Shape confirmed from the supplied app and the established private
        # read-only client, not endpoint discovery. Encode the token as one segment.
        endpoint = (
            "GetContactCases/"
            + urllib.parse.quote(token, safe="")
            + "/100000000/"
            + number
            + "?from="
            + from_date
            + "&caseType=2"
        )
        request = urllib.request.Request(
            BASE + endpoint,
            method="GET",
            headers={
                "Ocp-Apim-Subscription-Key": self.credentials["subscription_key"],
                "Content-Type": "text/plain",
                "User-Agent": "App 106",
            },
        )
        try:
            with urllib.request.build_opener(NoRedirect()).open(
                request, timeout=45
            ) as response:
                raw = response.read(16 * 1024 * 1024 + 1)
            if len(raw) > 16 * 1024 * 1024:
                raise ValueError()
            result = json.loads(raw)
            if (
                not isinstance(result, dict)
                or result.get("Success") is False
                or not isinstance(result.get("incidents"), list)
                or any(not isinstance(item, dict) for item in result["incidents"])
            ):
                raise ValueError()
        except urllib.error.HTTPError as exc:
            # Never include request URL, response body, identity or token, even
            # in chained exception tracebacks. Redirects are refused, not followed.
            raise APIError(f"Case listing: HTTP {exc.code}.") from None
        except (OSError, ValueError, urllib.error.URLError, RecursionError):
            raise APIError(
                "Case listing failed or returned an invalid response."
            ) from None
        return result

    def case_files(
        self, token: str, reporter: dict[str, Any], *, case_id: str, from_date: str
    ) -> list[bytes]:
        """Read an owned incident's attachment bytes, never a guessed file URL.

        The established app route returns ALL files in one GET per case. First
        verify exact incident membership using a fresh own-reporter listing.
        Filenames/URLs are deliberately not exported or followed.
        """
        if not isinstance(case_id, str) or not re.fullmatch(
            r"[0-9a-fA-F]{8}-[0-9a-fA-F]{4}-[0-9a-fA-F]{4}-[0-9a-fA-F]{4}-[0-9a-fA-F]{12}",
            case_id,
        ):
            raise InputError(
                "Supply a municipal incident UUID from the own-case listing."
            )
        own = self.list_cases(token, reporter, from_date=from_date)["incidents"]
        matches = [
            item
            for item in own
            if isinstance(item.get("caseId"), str)
            and item["caseId"].lower() == case_id.lower()
        ]
        if len(matches) != 1:
            raise InputError(
                "Case is not uniquely present in the configured reporter listing."
            )
        endpoint = "GetCaseFiles/" + urllib.parse.quote(token, safe="") + "/" + case_id
        request = urllib.request.Request(
            BASE + endpoint,
            method="GET",
            headers={
                "Ocp-Apim-Subscription-Key": self.credentials["subscription_key"],
                "Content-Type": "text/plain",
                "User-Agent": "App 106",
            },
        )
        try:
            with urllib.request.build_opener(NoRedirect()).open(
                request, timeout=45
            ) as response:
                raw = response.read(32 * 1024 * 1024 + 1)
            if len(raw) > 32 * 1024 * 1024:
                raise ValueError()
            data = json.loads(raw)
            if not isinstance(data, list) or len(data) > 20:
                raise ValueError()
            files = []
            for item in data:
                if not isinstance(item, dict) or not isinstance(
                    item.get("Content"), str
                ):
                    raise ValueError()  # noqa: TRY004 - malformed input data; callers catch ValueError
                content = base64.b64decode(item["Content"], validate=True)
                if not content or len(content) > 20 * 1024 * 1024:
                    raise ValueError()
                files.append(content)
        except urllib.error.HTTPError as exc:
            raise APIError(f"Case attachments: HTTP {exc.code}.") from None
        except (OSError, ValueError, urllib.error.URLError, RecursionError):
            raise APIError(
                "Case attachments failed or returned an invalid response."
            ) from None
        return files

    def contact(self, token: Any, reporter: Any) -> Any:
        contact = dict(reporter, Token=token)
        response = self.request("createupdatecontact", dict(contact, SignUpByApp=True))
        if not response.get("ContactIdInCRM"):
            raise APIError("Contact registration returned no ID.")
        contact["ContactIdInCRM"] = response["ContactIdInCRM"]
        return contact

    def create(self, token: Any, contact: Any, report: Any) -> Any:
        return self.request("CreateCase", dict(report, Token=token, contact=contact))

    def attach(self, token: Any, case: Any, jpeg: Any, filename: Any) -> Any:
        return self.request(
            "AddFileToCase",
            {
                "token": token,
                "caseIdInCRM": case["CaseIdInCRM"],
                "webCaseIdInCRM": case["WebCaseIdInCRM"],
                "fileName": filename,
                "fileContent": base64.b64encode(jpeg).decode("ascii"),
            },
        )


def duplicate_case_numbers(incidents: Any, description: str) -> Any:
    """Case numbers whose description matches the submitted text exactly (#160).

    The municipality has created two CRM cases from a single CreateCase POST,
    seconds apart. Detection compares the exact submitted text only; case
    numbers are safe to log, descriptions are not.
    """
    if not isinstance(description, str) or not description:
        raise InputError("Supply the exact submitted description.")
    numbers = []
    for incident in incidents if isinstance(incidents, list) else []:
        if not isinstance(incident, dict):
            continue
        if incident.get("description") != description:
            continue
        number = incident.get("case_number")
        if isinstance(number, str) and number:
            numbers.append(number)
    return sorted(numbers)


def save_state(path: Any, state: Any) -> Any:
    (fd, temporary) = tempfile.mkstemp(prefix=".progress-", dir=path.parent)
    try:
        with os.fdopen(fd, "w", encoding="utf-8") as stream:
            json.dump(state, stream, ensure_ascii=False)
            stream.flush()
            os.fsync(stream.fileno())
        os.chmod(temporary, 384)
        os.replace(temporary, path)
        if hasattr(os, "O_DIRECTORY"):
            directory_fd = os.open(path.parent, os.O_RDONLY | os.O_DIRECTORY)
            try:
                os.fsync(directory_fd)
            finally:
                os.close(directory_fd)
        if os.environ.get("PARKING_STATE_GIT") == "1":
            for command in (
                ["git", "add", "--", str(path)],
                ["git", "commit", "-m", "Record 106 progress"],
                ["git", "push", "origin", "HEAD"],
            ):
                # check=False: the return code is inspected below. A git that
                # cannot start (OSError) or a SubprocessError is the same
                # failure and maps to the same ReviewNeeded; its text is withheld.
                try:
                    result = subprocess.run(
                        command, cwd=path.parent, capture_output=True, check=False
                    )
                except (OSError, subprocess.SubprocessError):
                    result = None
                if result is None or result.returncode:
                    raise ReviewNeeded(
                        "Private state persistence failed; stop and reconcile before retrying."
                    )
    finally:
        Path(temporary).unlink(missing_ok=True)


def submit(
    api: Any,
    reporter: Any,
    report: Any,
    jpeg: Any,
    fingerprint: Any,
    state_dir: Any,
    retry_photo: Any = False,
    checkpoint: Any = None,
    initial_state: Any = None,
) -> Any:
    state_dir.mkdir(parents=True, exist_ok=True, mode=448)
    os.chmod(state_dir, 448)
    path = state_dir / (fingerprint + ".json")
    try:
        with FileLock(str(path) + ".lock", timeout=0, mode=384):
            state: dict[str, Any] = (
                dict(initial_state)
                if initial_state is not None
                else (
                    json_file(path)
                    if path.exists()
                    else {"version": 1, "status": "prepared"}
                )
            )
            state.setdefault("file_name", str(uuid.uuid4()) + ".jpg")

            def transition() -> None:
                if checkpoint is not None:
                    checkpoint(dict(state))
                save_state(path, state)

            if state.get("status") not in {
                "prepared",
                "creating",
                "created",
                "attaching",
                "complete",
            }:
                raise ReviewNeeded(
                    f"Unknown saved state; inspect {path} before continuing."
                )
            if state["status"] == "creating":
                raise ReviewNeeded(
                    f"A previous case creation has an uncertain result. Check the municipal app; do not delete {path} and resubmit."
                )
            if state["status"] == "complete":
                return {
                    "status": "already_submitted",
                    "case": state["case"],
                    "photo_attached": True,
                    "state_file": str(path),
                }
            if retry_photo and state["status"] == "prepared":
                raise InputError(
                    "No existing municipal case in the saved state. --retry-photo will not create one."
                )
            if state["status"] == "attaching" and (not retry_photo):
                raise ReviewNeeded(
                    f"The report exists but its photo was not confirmed. Use the same arguments with --retry-photo after checking the municipal app. State: {path}"
                )
            if state["status"] == "prepared":
                transition()
            token = api.login()
            if state["status"] == "prepared":
                contact = api.contact(token, reporter)
                state["status"] = "creating"
                transition()
                try:
                    result = api.create(token, contact, report)
                    if not result.get("WebCaseIdInCRM") or not result.get(
                        "CaseIdInCRM"
                    ):
                        raise APIError("Missing case reference.")
                    state["case"] = {
                        k: result.get(k)
                        for k in ("CaseIdInCRM", "WebCaseIdInCRM", "CaseNumberInCRM")
                    }
                    state["status"] = "created"
                    transition()
                except Exception as exc:
                    raise ReviewNeeded(
                        f"Case creation result is uncertain. No automatic retry. Check the municipal app. State: {path}"
                    ) from exc
            state["status"] = "attaching"
            transition()
            try:
                api.attach(token, state["case"], jpeg, state["file_name"])
            except Exception as exc:
                raise ReviewNeeded(
                    f"Report {state['case']['WebCaseIdInCRM']} exists; photo attachment was not confirmed. Use --retry-photo after checking the app. State: {path}"
                ) from exc
            state["status"] = "complete"
            transition()
            return {
                "status": "submitted",
                "case": state["case"],
                "photo_attached": True,
                "state_file": str(path),
            }
    except Timeout as exc:
        raise ReviewNeeded(
            "This same report is already being processed by another local process."
        ) from exc
