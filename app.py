"""
Rishi Jobs - CV editing tool (Streamlit front end)

Collects the CV and the recruiter's details, edits the PDF and offers it for
download, emailing a copy to the heads.

Runs on its own - it calls the processing code directly rather than going over
HTTP, so it works as a single process on Streamlit Community Cloud. api.py is
still there for anything that wants the same thing as an HTTP API.

Run:  streamlit run app.py
"""

import os
import re

import streamlit as st

# Streamlit Cloud supplies configuration through st.secrets rather than a .env
# file. Copied into the environment before mailer reads it, and with
# setdefault so a real environment variable still wins.
try:
    for _key, _value in st.secrets.items():
        if isinstance(_value, str):
            os.environ.setdefault(_key, _value)
except Exception:  # no secrets configured - .env or plain env vars are used
    pass

import cv_processor  # noqa: E402  (must follow the secrets bridge)
import mailer  # noqa: E402

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


members = [m["name"] for m in mailer.team_members() if m.get("name")]
if members:
    editor = st.selectbox(
        "Edited by *",
        ["— select —"] + members,
        key="editor_name",
        help=(
            "Who is editing this CV. The email always goes out from the Rishi Jobs "
            "account - your name is recorded in it, and replies come back to you."
        ),
    )
    editor = "" if editor == "— select —" else editor
else:
    editor = ""
    st.warning(
        "No team members are configured, so the email will not say who edited the CV. "
        "Add them to team.json, or set TEAM_MEMBERS in the app's secrets."
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
        try:
            st.session_state["candidate_name"] = cv_processor.guess_candidate_name(
                cv_file.getvalue()
            )
        except Exception:
            st.session_state["candidate_name"] = ""

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
    "The CV shows “As per industry norms” instead of the figure when the expected "
    "salary is left blank, or is more than 30% above the current one."
)

col3, col4 = st.columns(2)
notice_choice = col3.selectbox("Notice period", NOTICE_PERIODS, index=2)
notice_other = col4.text_input(
    "If other, specify",
    placeholder="e.g. 45 days",
    disabled=notice_choice != "Other",
)

recruiter_note = st.text_area(
    "Recruiter note *",
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

    if members and not editor:
        st.error("Please select who is editing this CV.")
    elif not cv_file:
        st.error("Please upload the candidate's CV.")
    elif not candidate_name.strip():
        st.error("Please enter the candidate's name - it could not be read from the CV.")
    elif notice_choice == "Other" and not notice_other.strip():
        st.error("Please specify the notice period.")
    elif not recruiter_note.strip():
        st.error("Please write a recruiter note - it appears on the CV and in the email.")
    elif salary_problems:
        for problem in salary_problems:
            st.error(problem)
    else:
        try:
            details = cv_processor.CvDetails.from_dict(
                {
                    "candidate_name": candidate_name.strip(),
                    "current_salary": current_salary.strip(),
                    "expected_salary": expected_salary.strip(),
                    "notice_period": notice_period,
                    "recruiter_note": recruiter_note.strip(),
                }
            )
        except ValueError as exc:
            for problem in exc.args[0]:
                st.error(problem)
            st.stop()

        with st.spinner("Editing the CV…"):
            try:
                output, report = cv_processor.generate_branded_cv(
                    cv_file.getvalue(), details
                )
            except ValueError as exc:
                st.error(str(exc))
                st.stop()
            except Exception as exc:
                st.error(f"Could not process the CV: {exc}")
                st.stop()

            filename = cv_processor.cv_filename(details)
            sent, mail_message = mailer.deliver(
                output, filename, details, report, mailer.find_member(editor)
            )

        if report.looks_scanned:
            st.error(
                "This CV has no readable text layer - it looks like a scan or a set of "
                "images. **Nothing could be redacted**, so the contact details are still "
                "on the page. Check it by hand before sending it out."
            )
        elif report.pages_without_text:
            pages = ", ".join(str(p) for p in report.pages_without_text)
            st.warning(f"No text found on page(s) {pages} - check those by hand.")

        if report.note_truncated:
            st.warning(
                "The recruiter note was too long for the box and has been shortened on "
                "the CV. Trim it and regenerate if the full text matters."
            )

        st.success("Done. Review the details below, then download.")

        if sent:
            st.info(f"📧 {mail_message}")
        else:
            st.warning(f"Not emailed - {mail_message}")

        if report.total:
            st.markdown("**Removed from the CV**")
            for category, items in report.removed.items():
                label = cv_processor.CATEGORY_LABELS.get(category, category)
                for item in items:
                    st.markdown(f"- {label}: `{item}`")
        else:
            st.warning(
                "No contact details were found to remove. If the CV does show an email "
                "or a phone number, check it by hand before sending it."
            )

        st.download_button(
            "Download edited CV",
            data=output,
            file_name=filename,
            mime="application/pdf",
        )
