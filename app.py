"""
Rishi Jobs - CV editing tool (Streamlit front end)

Collects the CV and the recruiter's details, edits the PDF and offers the
revised copy for download. Nothing is emailed or sent anywhere - the edited
CV exists only in the browser session.

Opened from the Rishi Jobs dashboard (?from=dashboard&origin=...), the form is
pre-filled from the link, the original CV is loaded from its Google Drive link
(cv_url), and the edited CV is also handed straight back to the dashboard page
that opened this one - see dashboard.py.

Runs on its own - it calls the processing code directly rather than going over
HTTP, so it works as a single process on Streamlit Community Cloud. api.py is
still there for anything that wants the same thing as an HTTP API.

Run:  streamlit run app.py
"""

import os
import re

import streamlit as st

import cv_processor
import dashboard

BASE_DIR = os.path.dirname(os.path.abspath(__file__))
LOGO_PATH = os.path.join(BASE_DIR, "templates", "logo.png")

SALARY_RE = re.compile(r"\d+(?:\.\d+)?")
DAYS_RE = re.compile(r"\d+")

NOTICE_PERIODS = [
    "Immediate",
    "15 days",
    "30 days",
    "60 days",
    "90 days",
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

link = dashboard.read_link(st.query_params)

# Opened from the dashboard: fill the form from the link once, on the first run
# of the session, so the recruiter's own edits after that are kept.
if link and not st.session_state.get("prefilled"):
    st.session_state["prefilled"] = True
    for key, value in link.fields.items():
        st.session_state[key] = value
    if link.notice_period in NOTICE_PERIODS:
        st.session_state["notice_choice"] = link.notice_period
    elif link.notice_period:
        # "Other" takes a number of days only - "45 days" arrives as "45".
        days = re.fullmatch(r"(\d+)\s*(?:days?)?", link.notice_period, re.IGNORECASE)
        st.session_state["notice_choice"] = "Other"
        st.session_state["notice_other"] = days.group(1) if days else link.notice_period

linked_cv = None
if link and link.cv_url:
    try:
        linked_cv = dashboard.fetch_cv(link.cv_url)
    except ValueError as exc:
        st.warning(f"The original CV could not be loaded from the dashboard ({exc}). Upload it below.")

if linked_cv:
    st.info(f"📄 Original CV loaded from the dashboard: **{linked_cv.name}**. Upload a different PDF below to replace it.")

# Deliberately not inside st.form: a form only reruns when it is submitted,
# which left the "If other" box stuck disabled and stopped the candidate name
# pre-filling the moment a CV was uploaded.
cv_file = st.file_uploader("Candidate CV (PDF)", type=["pdf"])

if cv_file is not None:
    cv_bytes, cv_label = cv_file.getvalue(), f"{cv_file.name}:{cv_file.size}"
elif linked_cv:
    cv_bytes, cv_label = linked_cv.data, f"link:{link.cv_url}"
else:
    cv_bytes, cv_label = None, None

if cv_bytes is not None:
    # Only re-read when a different file arrives, so a corrected name survives.
    # A name sent by the dashboard counts as corrected already.
    if st.session_state.get("detected_for") != cv_label:
        st.session_state["detected_for"] = cv_label
        if not (cv_file is None and link and link.fields.get("candidate_name")):
            try:
                st.session_state["candidate_name"] = cv_processor.guess_candidate_name(cv_bytes)
            except Exception:
                st.session_state["candidate_name"] = ""

candidate_name = st.text_input(
    "Candidate name *",
    key="candidate_name",
    placeholder="Read from the CV - correct it here if it is wrong",
)

job_title = st.text_input(
    "Job title *",
    key="job_title",
    placeholder="e.g. VP Brand Marketing - used in the file name",
)

col1, col2 = st.columns(2)
current_salary = col1.text_input("Current salary (LPA) *", key="current_salary", placeholder="e.g. 18 or 18.5")
expected_salary = col2.text_input("Expected salary (LPA) *", key="expected_salary", placeholder="e.g. 24")
st.caption(
    "Salaries are in LPA - digits only, decimals allowed (18, 18.5). The CV adds "
    "“LPA” itself, and shows “As per industry norms” instead of the expected "
    "salary when it is more than 30% above the current one."
)

col3, col4 = st.columns(2)
if "notice_choice" not in st.session_state:
    st.session_state["notice_choice"] = NOTICE_PERIODS[2]
notice_choice = col3.selectbox("Notice period *", NOTICE_PERIODS, key="notice_choice")
notice_other = col4.text_input(
    "If other, number of days",
    key="notice_other",
    placeholder="e.g. 45 - digits only",
    disabled=notice_choice != "Other",
)

recruiter_note = st.text_area(
    "Recruiter note *",
    key="recruiter_note",
    placeholder="Why this candidate fits - availability, strengths, anything the client should know.",
    height=120,
)
st.caption("Every field is required.")

submitted = st.button("Generate edited CV", type="primary")


def form_problems() -> list[str]:
    """Everything wrong with the form, so the recruiter can fix it in one go."""
    problems = []
    if cv_bytes is None:
        problems.append("Please upload the candidate's CV.")
    if not candidate_name.strip():
        problems.append("Please enter the candidate's name.")
    if not job_title.strip():
        problems.append("Please enter the job title - it is used in the file name.")
    for label, value in (("current", current_salary), ("expected", expected_salary)):
        value = value.strip()
        if not value:
            problems.append(f"Please enter the {label} salary.")
        elif not SALARY_RE.fullmatch(value):
            problems.append(
                f"The {label} salary must be digits only, decimals allowed (e.g. 18 or 18.5)."
            )
    if notice_choice == "Other":
        days = notice_other.strip()
        if not days:
            problems.append("Please enter the notice period in days.")
        elif not DAYS_RE.fullmatch(days):
            problems.append(
                "The notice period must be a number of days, digits only (e.g. 45) - "
                "“days” is added on the CV automatically."
            )
    if not recruiter_note.strip():
        problems.append("Please write a recruiter note - it appears on the CV.")
    return problems


if submitted:
    # A fresh attempt replaces whatever was generated last time.
    st.session_state.pop("result", None)
    problems = form_problems()
    for problem in problems:
        st.error(problem)
    if problems:
        st.stop()

    try:
        details = cv_processor.CvDetails.from_dict(
            {
                "candidate_name": candidate_name,
                "job_title": job_title,
                "current_salary": current_salary,
                "expected_salary": expected_salary,
                "notice_period": notice_other if notice_choice == "Other" else notice_choice,
                "recruiter_note": recruiter_note,
            }
        )
    except ValueError as exc:
        for problem in exc.args[0]:
            st.error(problem)
        st.stop()

    with st.spinner("Editing the CV…"):
        try:
            output, report = cv_processor.generate_branded_cv(cv_bytes, details)
        except ValueError as exc:
            st.error(str(exc))
            st.stop()
        except Exception as exc:
            st.error(f"Could not process the CV: {exc}")
            st.stop()

    # Kept in the session rather than shown once: a download button reruns the
    # script, and the result has to survive that for the Word copy to be offered.
    st.session_state["result"] = {
        "details": details,
        "source": cv_bytes,
        "output": output,
        "report": report,
        "docx": None,
        "sent": False,
    }


result = st.session_state.get("result")
if result:
    details, output, report = result["details"], result["output"], result["report"]
    filename = cv_processor.cv_filename(details)

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

    if report.total:
        st.markdown("**Removed from the CV**")
        for category, items in report.removed.items():
            label = cv_processor.CATEGORY_LABELS.get(category, category)
            for item in items:
                st.markdown(f"- {label}: `{item}`")
    else:
        st.warning(
            "No contact details were found to remove. If the CV does show an email "
            "or a phone number, check it by hand before using it."
        )

    st.download_button(
        "Download edited CV (PDF)",
        data=output,
        file_name=filename,
        mime="application/pdf",
    )

    # The Word copy is made only when asked for - the conversion takes a few seconds.
    if result["docx"] is None and st.button("Create an editable Word copy (.docx)"):
        with st.spinner("Converting to Word…"):
            try:
                result["docx"] = cv_processor.generate_branded_docx(result["source"], details)
            except Exception as exc:
                st.error(f"Could not create the Word copy: {exc}")
            else:
                st.rerun()  # swap the button for the download straight away
    if result["docx"] is not None:
        st.download_button(
            "Download editable CV (Word)",
            data=result["docx"],
            file_name=cv_processor.cv_filename(details, "docx"),
            mime="application/vnd.openxmlformats-officedocument.wordprocessingml.document",
        )
        st.caption(
            "The Word copy is converted from the PDF, without the watermark - "
            "check the layout before sending it."
        )

    # Sent once per generated CV, not again on every rerun. Always the PDF: the
    # dashboard only accepts a PDF back.
    if link and not result["sent"]:
        result["sent"] = True
        dashboard.send_back(
            link.origin,
            output,
            filename,
            {
                "candidateName": details.candidate_name,
                "jobTitle": details.job_title,
                "currentSalary": details.current_salary,
                "expectedSalary": details.expected_salary,
                "noticePeriod": details.notice_period,
                "recruiterNote": details.recruiter_note,
            },
        )
        st.success(
            "✅ The edited CV has been sent to the dashboard form. "
            "Go back to the form there to check it and send the candidate to the Client Team."
        )
