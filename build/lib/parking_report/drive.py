"""Read-only download of an intake folder from Google Drive into a local directory.

Uses a read-only OAuth access token from the PARKING_DRIVE_TOKEN environment variable.
Only talks to the Drive API host (or localhost for tests). Never uploads, edits or deletes.
"""

from __future__ import annotations

import json
import os
import re
import urllib.error
import urllib.parse
import urllib.request
from pathlib import Path
from urllib.parse import urlparse

from .intake import IMAGE_SUFFIXES, RECORD_NAME

API = "https://www.googleapis.com/drive/v3"
TOKEN_ENV = "PARKING_DRIVE_TOKEN"
FOLDER_MIME = "application/vnd.google-apps.folder"
_ID = re.compile(r"[A-Za-z0-9_-]{5,100}")
_LOCAL_HOSTS = {"localhost", "127.0.0.1", "::1"}
MAX_BYTES = 20 * 1024 * 1024


def check_api(base: str) -> str:
    parsed = urlparse(base)
    if base == API or (parsed.scheme == "http" and parsed.hostname in _LOCAL_HOSTS):
        return base.rstrip("/")
    raise ValueError(
        "Drive API URL must be the official one or a localhost test server"
    )


class _Client:
    def __init__(self, base: str, token: str) -> None:
        self.base, self.token = base, token

    def _open(self, url: str):
        request = urllib.request.Request(
            url, headers={"Authorization": f"Bearer {self.token}"}
        )
        try:
            return urllib.request.urlopen(request, timeout=30)  # GET only, no retries
        except urllib.error.HTTPError as error:
            raise ValueError(
                f"Drive API error {error.code} for {urlparse(url).path}"
            ) from None
        except urllib.error.URLError as error:
            raise ValueError(f"cannot reach Drive API: {error.reason}") from None

    def children(self, folder_id: str) -> list[dict]:
        if not _ID.fullmatch(folder_id):
            raise ValueError("invalid Drive folder id")
        query = urllib.parse.urlencode(
            {
                "q": f"'{folder_id}' in parents and trashed = false",
                "fields": "files(id,name,mimeType,size)",
                "pageSize": "200",
            }
        )
        with self._open(f"{self.base}/files?{query}") as response:
            return json.loads(response.read().decode("utf-8")).get("files", [])

    def download(self, file_id: str, dest: Path) -> None:
        with self._open(
            f"{self.base}/files/{urllib.parse.quote(file_id)}?alt=media"
        ) as response:
            data = response.read(MAX_BYTES + 1)
        if len(data) > MAX_BYTES:
            raise ValueError("file too large")
        dest.write_bytes(data)


def download_folder(
    folder_id: str, dest: str | Path, base: str = API, token: str | None = None
) -> Path:
    """Copy report.json and photos of each report subfolder into dest. Read-only on Drive."""
    token = token or os.environ.get(TOKEN_ENV, "")
    if not token:
        raise ValueError(f"set {TOKEN_ENV} to a read-only Google Drive access token")
    client = _Client(check_api(base), token)
    out = Path(dest)
    for sub in client.children(folder_id):
        if sub.get("mimeType") != FOLDER_MIME:
            continue
        name = Path(str(sub.get("name", ""))).name
        if not name or name.startswith("."):
            continue
        target = out / name
        target.mkdir(parents=True, exist_ok=True)
        for item in client.children(sub["id"]):
            file_name = Path(str(item.get("name", ""))).name
            if (
                file_name == RECORD_NAME
                or Path(file_name).suffix.lower() in IMAGE_SUFFIXES
            ):
                client.download(item["id"], target / file_name)
    return out
