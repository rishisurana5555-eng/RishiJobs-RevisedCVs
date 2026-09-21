"""
Rishi Jobs - CV editing tool (Streamlit front end)

Collects the CV and the recruiter's details, sends them to the Flask API and
offers the edited, redacted PDF for download.

Run:  streamlit run app.py      (the Flask API must be running: python api.py)
"""

import os
import re

import requests
import streamlit as st

API_URL = os.environ.get("CV_API_URL", "http://127.0.0.1:5001")
BASE_DIR = os.path.dirname(os.path.abspath(__file__))
LOGO_PATH = os.path.join(BASE_DIR, "templates", "logo.png")

SALARY_RE = re.compile(r"\d+(?:\.\d+)?")

NOTICE_PERIODS = [
    "Immediate",
    "15 days",
    "30 days",
    "60 days",
    "90 days",
    "1 month",
    "2 months",
    "3 months",
    "Other",
]

CATEGORY_LABELS = {
    "email": "Email addresses",
    "phone": "Phone numbers",
    "url": "LinkedIn / web profiles",
    "location": "Address / location",
    "heading": "Contact headings",
}

st.set_page_config(page_title="Rishi Jobs | CV Editor", page_icon="📄", layout="centered")

st.markdown(
    """
    <style>
      .rj-header {background:#16213E;color:#fff;padding:18px 22px;border-radius:12px;
                  border-left:8px solid #DDA878;margin-bottom:18px}
      .rj-header h1 {font-size:1.55rem;margin:0;color:#fff}
      .rj-header p {margin:4px 0 0;color:#e6d3bf;font-size:.95rem}
      div.stButton > button, div.stDownloadButton > button, div.stFormSubmitButton > button
        {background:#16213E;color:#fff;border:0}
      div.stButton > button:hover, div.stDownloadButton > button:hover,
      div.stFormSubmitButton > button:hover {background:#DDA878;color:#16213E}
    </style>
    """,
    unsafe_allow_html=True,
)

header_cols = st.columns([1, 5]) if os.path.exists(LOGO_PATH) else None
if header_cols:
    header_cols[0].image(LOGO_PATH, width=80)
    target = header_cols[1]
else:
    target = st
target.markdown(
    '<div class="rj-header"><h1>CV Editor</h1>'
    "<p>Strip the candidate's contact details, add the recruiter box and Rishi Jobs branding.</p></div>",
    unsafe_allow_html=True,
)


def api_available() -> bool:
    try:
        return requests.get(f"{API_URL}/api/health", timeout=3).ok
    except requests.RequestException:
        return False


if not api_available():
    st.error(
        f"Cannot reach the processing API at {API_URL}. "
        "Start it in a second terminal with:  python api.py"
    )
    st.stop()


def team_members() -> list[str]:
    """
    Names of the people who may send CVs, from the API's team list.

    Deliberately uncached: it is a cheap local call, and caching it meant an
    edit to team.json took a minute to show up in the picker.
    """
    try:
        response = requests.get(f"{API_URL}/api/team", timeout=5)
        if response.ok:
            return [m["name"] for m in response.json().get("members", []) if m.get("name")]
    except requests.RequestException:
        pass
    return []


def detect_name(file_bytes: bytes, filename: str) -> str:
    """Ask the API for the candidate's name, so the form can pre-fill it."""
    try:
        response = requests.post(
            f"{API_URL}/api/cv/detect-name",
            files={"cv_file": (filename, file_bytes, "application/pdf")},
            timeout=30,
        )
        if response.ok:
            return response.json().get("candidate_name", "")
    except requests.RequestException:
        pass
    return ""


members = team_members()
if members:
    sender = st.selectbox(
        "Edited by *",
        ["— select —"] + members,
        key="sender_name",
        help=(
            "Who is editing this CV. The email always goes out from the Rishi Jobs "
            "account - your name is recorded in it, and replies come back to you."
        ),
    )
    sender = "" if sender == "— select —" else sender
else:
    sender = ""
    st.warning(
        "No team members are configured, so the email will not say who sent it. "
        "Add them to team.json and restart the API."
    )

st.divider()

# Deliberately not inside st.form: a form only reruns when it is submitted,
# which left the "If other" box stuck disabled and stopped the candidate name
# pre-filling the moment a CV was uploaded.
cv_file = st.file_uploader("Candidate CV (PDF)", type=["pdf"])

if cv_file is not None:
    # Only re-read when a different file arrives, so a corrected name survives.
    fingerprint = f"{cv_file.name}:{cv_file.size}"
    if st.session_state.get("detected_for") != fingerprint:
        st.session_state["detected_for"] = fingerprint
        st.session_state["candidate_name"] = detect_name(cv_file.getvalue(), cv_file.name)

candidate_name = st.text_input(
    "Candidate name *",
    key="candidate_name",
    placeholder="Read from the CV - correct it here if it is wrong",
)

col1, col2 = st.columns(2)
current_salary = col1.text_input("Current salary (LPA)", placeholder="e.g. 18 or 18.5")
expected_salary = col2.text_input("Expected salary (LPA)", placeholder="e.g. 24")
st.caption(
    "Salaries are in LPA - digits only, decimals allowed (18, 18.5). "
    "If the expected salary is more than 30% above the current one, the CV shows "
    "“As per industry norms” instead of the figure."
)

col3, col4 = st.columns(2)
notice_choice = col3.selectbox("Notice period", NOTICE_PERIODS, index=2)
notice_other = col4.text_input(
    "If other, specify",
    placeholder="e.g. 45 days",
    disabled=notice_choice != "Other",
)

recruiter_note = st.text_area(
    "Recruiter note",
    placeholder="Why this candidate fits - availability, strengths, anything the client should know.",
    height=120,
)

submitted = st.button("Generate edited CV", type="primary")


if submitted:
    notice_period = notice_other.strip() if notice_choice == "Other" else notice_choice

    salary_problems = [
        f"{label} must be digits only, decimals allowed (e.g. 18 or 18.5)."
        for label, value in (
            ("Current salary", current_salary.strip()),
            ("Expected salary", expected_salary.strip()),
        )
        if value and not SALARY_RE.fullmatch(value)
    ]

    if members and not sender:
        st.error("Please select who is editing this CV.")
    elif not cv_file:
        st.error("Please upload the candidate's CV.")
    elif not candidate_name.strip():
        st.error("Please enter the candidate's name - it could not be read from the CV.")
    elif notice_choice == "Other" and not notice_other.strip():
        st.error("Please specify the notice period.")
    elif salary_problems:
        for problem in salary_problems:
            st.error(problem)
    else:
        files = {"cv_file": (cv_file.name, cv_file.getvalue(), "application/pdf")}

        data = {
            "candidate_name": candidate_name.strip(),
            "current_salary": current_salary.strip(),
            "expected_salary": expected_salary.strip(),
            "notice_period": notice_period,
            "recruiter_note": recruiter_note.strip(),
            "sender_name": sender,
        }

        with st.spinner("Editing the CV…"):
            try:
                # The preview call reports what was found, so the recruiter can
                # check the redaction before sending the CV to a client.
                summary = requests.post(
                    f"{API_URL}/api/cv/preview", files=files, data=data, timeout=120
                )
                response = requests.post(
                    f"{API_URL}/api/cv/process", files=files, data=data, timeout=120
                )
            except requests.RequestException as exc:
                st.error(f"Could not reach the API: {exc}")
                st.stop()

        if not response.ok:
            try:
                problems = response.json().get("errors", [response.text])
            except ValueError:
                problems = [response.text]
            for problem in problems:
                st.error(problem)
            st.stop()

        report = summary.json() if summary.ok else {}

        if report.get("looks_scanned"):
            st.error(
                "This CV has no readable text layer - it looks like a scan or a set of "
                "images. **Nothing could be redacted**, so the contact details are still "
                "on the page. Check it by hand before sending it out."
            )
        elif report.get("pages_without_text"):
            pages = ", ".join(str(p) for p in report["pages_without_text"])
            st.warning(f"No text found on page(s) {pages} - check those by hand.")

        if report.get("note_truncated"):
            st.warning(
                "The recruiter note was too long for the box and has been shortened on "
                "the CV. Trim it and regenerate if the full text matters."
            )

        st.success("Done. Review the details below, then download.")

        if response.headers.get("X-Email-Sent") == "1":
            st.info(f"📧 {response.headers.get('X-Email-Message', 'Emailed.')}")
        else:
            st.warning(
                "Not emailed - " + response.headers.get("X-Email-Message", "unknown reason")
            )

        removed = report.get("removed") or {}
        if removed:
            st.markdown("**Removed from the CV**")
            for category, items in removed.items():
                for item in items:
                    st.markdown(f"- {CATEGORY_LABELS.get(category, category)}: `{item}`")
        else:
            st.warning(
                "No contact details were found to remove. If the CV does show an email "
                "or a phone number, check it by hand before sending it."
            )

        st.download_button(
            "Download edited CV",
            data=response.content,
            file_name=report.get("filename")
            or f"{candidate_name.strip().replace(' ', '_')}_RishiJobs.pdf",
            mime="application/pdf",
        )
