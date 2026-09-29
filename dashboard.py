"""
Link between the CV editor and the Rishi Jobs dashboard (rishijobs-workflow.web.app).

When a PM sends a PE's candidate to the Client Team, the dashboard opens this app
(embedded, or in a new tab) with the details in the query string:

    ?from=dashboard&origin=<dashboard address>&cv_url=<Drive link of the original CV>
     &candidate_name=...&job_title=...&current_salary=...&expected_salary=...&notice_period=...&recruiter_note=...

The form is pre-filled from that, the original CV is fetched from Google Drive,
and after "Generate edited CV" the PDF is posted back to the dashboard page with
window.postMessage - so the PM doesn't have to download and re-upload it.

With &auto=1 the dashboard loads this app out of sight and the CV is generated
straight away, without anyone clicking: the PDF (with any warnings about it)
comes back to the dashboard, or - if a field is missing or the CV can't be
processed - the problems do (send_problems), for the PM to fix there.

Only the dashboard's own addresses are accepted as `origin`, so the edited CV is
never posted to any other site, and only Google Drive links are fetched.
"""

from __future__ import annotations

import base64
import json
import re
import urllib.request
from dataclasses import dataclass, field
from urllib.parse import urlparse

import streamlit as st
import streamlit.components.v1 as components

from cv_processor import MAX_UPLOAD_MB

# Where the dashboard runs. The edited CV is only ever posted to one of these.
DASHBOARD_ORIGINS = {
    "https://rishijobs-workflow.web.app",
    "https://rishijobs-workflow.firebaseapp.com",
}
LOCAL_ORIGIN = re.compile(r"^http://(localhost|127\.0\.0\.1):\d{2,5}$")

# The query-string names the dashboard uses -> this app's form keys.
FIELDS = {
    "candidate_name": "candidate_name",
    "job_title": "job_title",
    "current_salary": "current_salary",
    "expected_salary": "expected_salary",
    "recruiter_note": "recruiter_note",
}

DRIVE_HOSTS = {"drive.google.com", "docs.google.com"}
DRIVE_ID = re.compile(r"(?:/file/d/|[?&]id=)([A-Za-z0-9_-]{10,})")


@dataclass
class Link:
    origin: str
    cv_url: str = ""
    notice_period: str = ""
    fields: dict[str, str] = field(default_factory=dict)
    # generate at once, without the recruiter clicking (the dashboard's hidden editor)
    auto: bool = False


def allowed_origin(origin: str) -> bool:
    return origin in DASHBOARD_ORIGINS or bool(LOCAL_ORIGIN.match(origin))


def read_link(params) -> Link | None:
    """The dashboard's details, or None when the app was opened on its own."""
    if params.get("from") != "dashboard":
        return None
    origin = params.get("origin", "")
    if not allowed_origin(origin):
        return None
    return Link(
        origin=origin,
        cv_url=params.get("cv_url", ""),
        notice_period=params.get("notice_period", "").strip(),
        auto=params.get("auto") == "1",
        fields={key: params.get(name, "").strip() for name, key in FIELDS.items() if params.get(name, "").strip()},
    )


@dataclass
class LinkedCv:
    name: str
    data: bytes


@st.cache_data(ttl=3600, show_spinner="Loading the original CV…", max_entries=20)
def fetch_cv(url: str) -> LinkedCv:
    """Downloads a CV the dashboard saved in Google Drive ("anyone with the link can view")."""
    parsed = urlparse(url)
    match = DRIVE_ID.search(url)
    if parsed.scheme != "https" or parsed.hostname not in DRIVE_HOSTS or not match:
        raise ValueError("it is not a Google Drive link")
    download = f"https://drive.google.com/uc?export=download&id={match.group(1)}"
    request = urllib.request.Request(download, headers={"User-Agent": "RishiJobs-CV-Editor"})
    limit = MAX_UPLOAD_MB * 1024 * 1024
    try:
        with urllib.request.urlopen(request, timeout=30) as response:
            data = response.read(limit + 1)
            disposition = response.headers.get("Content-Disposition", "")
    except Exception as exc:
        raise ValueError(f"Google Drive did not return it: {exc}") from exc
    if len(data) > limit:
        raise ValueError(f"it is larger than {MAX_UPLOAD_MB} MB")
    if not data.startswith(b"%PDF"):
        raise ValueError("it is not a PDF, or it is not shared as 'anyone with the link'")
    name = re.search(r'filename="?([^";]+)"?', disposition)
    return LinkedCv(name=name.group(1) if name else "original CV.pdf", data=data)


def send_back(origin: str, pdf: bytes, filename: str, details: dict[str, str], warnings: list[str] | None = None) -> None:
    """Posts the edited CV to the dashboard page that opened this app (embedding it, or in the tab that opened it)."""
    _post(
        origin,
        {
            "type": "rishijobs:revised-cv",
            "fileName": filename,
            "data": base64.b64encode(pdf).decode("ascii"),
            "details": details,
            "warnings": warnings or [],
        },
    )


def send_problems(origin: str, problems: list[str]) -> None:
    """Tells the dashboard why the CV could not be generated (auto mode), so the PM can fix it there."""
    _post(origin, {"type": "rishijobs:revise-problems", "problems": problems})


def _post(origin: str, message: dict) -> None:
    if not allowed_origin(origin):
        return
    # json.dumps output is valid JavaScript; "</" is escaped so the data can't close the <script> tag.
    payload = json.dumps(message).replace("</", "<\\/")
    target = json.dumps(origin)
    components.html(
        f"""<script>
        (function () {{
          var msg = {payload};
          var targets = [];
          try {{ targets.push(window.top); }} catch (e) {{}}
          try {{ if (window.top.opener) targets.push(window.top.opener); }} catch (e) {{}}
          targets.forEach(function (t) {{ try {{ t.postMessage(msg, {target}); }} catch (e) {{}} }});
        }})();
        </script>""",
        height=0,
    )
