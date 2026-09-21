"""
Rishi Jobs - emails the finished CV out.

Configured entirely through environment variables so no credential ever lands
in the repository:

    SMTP_HOST       default smtp.gmail.com
    SMTP_PORT       default 587 (STARTTLS); use 465 for implicit SSL
    SMTP_USER       the mailbox doing the sending
    SMTP_PASSWORD   an app password, not the account password
    MAIL_FROM       defaults to SMTP_USER
    MAIL_TO         comma-separated; defaults to CV_RECIPIENTS below
    TEAM_MEMBERS    overrides team.json ("Name <address>; Name <address>")
    SEND_EMAIL      set to 0 to turn sending off entirely

Sending never blocks the download: if the mail fails, the caller still gets
the PDF and the reason is reported back.
"""

import json
import os
import smtplib
import ssl
from email.message import EmailMessage
from email.utils import formataddr, getaddresses

from cv_processor import format_salary

BASE_DIR = os.path.dirname(os.path.abspath(__file__))

# Credentials are read from a local .env when python-dotenv is installed, so
# they never have to be typed into a terminal. The file is git-ignored.
try:
    from dotenv import load_dotenv

    load_dotenv(os.path.join(BASE_DIR, ".env"))
except ImportError:  # env vars set by hand still work
    pass

#: The fixed heads who receive every revised CV. MAIL_TO overrides the list;
#: separate several addresses with commas. They go on one To: line so the
#: heads can see each other and reply-all.
#:
CV_RECIPIENTS = [
    "rishisurana5555@gmail.com",  # Rishi Surana
    "shubhamtyagi.rj@gmail.com",  # Shubham Tyagi
]

#: Deliberately small. Every revised CV carries a candidate's details, so the
#: distribution list stays at the handful of heads who need it - anything
#: longer is a sign the list has drifted.
MAX_RECIPIENTS = 4

DEFAULT_HOST = "smtp.gmail.com"
DEFAULT_PORT = 587

TEAM_FILE = os.path.join(BASE_DIR, "team.json")

#: Every email leaves from the one shared mailbox, so the From line names the
#: tool rather than a person. Who edited the CV is a separate fact, carried in
#: the body and in Reply-To.
FROM_DISPLAY = "Rishi Jobs CV Tool"


def team_members() -> list[dict]:
    """
    The people allowed to send through the tool.

    Read fresh each time so the list can be edited while the app is running.
    TEAM_MEMBERS ("Name <address>; Name <address>") overrides the file.
    """
    configured = os.environ.get("TEAM_MEMBERS", "").strip()
    if configured:
        parsed = getaddresses([configured.replace(";", ",")])
        return [{"name": name, "email": email} for name, email in parsed if email]

    try:
        with open(TEAM_FILE, encoding="utf-8") as handle:
            listed = json.load(handle).get("members", [])
    except (OSError, ValueError):
        return []
    return [
        {"name": str(m.get("name", "")).strip(), "email": str(m.get("email", "")).strip()}
        for m in listed
        if str(m.get("name", "")).strip()
    ]


def find_member(name: str) -> dict | None:
    """Look a member up by name - the client never supplies its own address."""
    wanted = (name or "").strip().casefold()
    if not wanted:
        return None
    for member in team_members():
        if member["name"].casefold() == wanted:
            return member
    return None


def recipients() -> list[str]:
    configured = os.environ.get("MAIL_TO", "")
    listed = [address.strip() for address in configured.split(",") if address.strip()]
    chosen = listed or list(CV_RECIPIENTS)

    # Deduplicated case-insensitively, keeping the order they were written in.
    seen: set[str] = set()
    unique = [
        address
        for address in chosen
        if not (address.casefold() in seen or seen.add(address.casefold()))
    ]
    return unique[:MAX_RECIPIENTS]


def recipient() -> str:
    """The recipients as one display string."""
    return ", ".join(recipients())


def is_enabled() -> bool:
    return os.environ.get("SEND_EMAIL", "1").strip() not in ("0", "false", "no")


def is_configured() -> bool:
    return bool(os.environ.get("SMTP_USER") and os.environ.get("SMTP_PASSWORD"))


def _body(
    details, filename: str, summary_lines: list[str], warnings: list[str], member: dict | None
) -> str:
    lines = [f"Revised CV attached for {details.candidate_name}.", ""]

    if member:
        who = member["name"]
        lines.append(
            f"Edited by:        {who} ({member['email']})"
            if member.get("email")
            else f"Edited by:        {who}"
        )
    lines += [
        f"Current salary:   {format_salary(details.current_salary)}",
        f"Expected salary:  {details.expected_salary_display}",
        f"Notice period:    {details.notice_period or 'Not specified'}",
    ]
    if details.recruiter_note:
        lines += ["", "Recruiter note:", f"  {details.recruiter_note}"]

    lines += ["", "Removed from the CV:"]
    lines += [f"  - {line}" for line in summary_lines] or ["  - nothing found"]

    if warnings:
        lines += ["", "Please check:"]
        lines += [f"  - {warning}" for warning in warnings]

    lines += ["", f"Attachment: {filename}", "", "Sent by the Rishi Jobs CV tool."]
    return "\n".join(lines)


def send_cv(
    pdf_bytes: bytes,
    filename: str,
    details,
    summary_lines: list[str] | None = None,
    warnings: list[str] | None = None,
    member: dict | None = None,
) -> tuple[bool, str]:
    """
    Email the finished CV as an attachment.

    Returns (sent, message). A failure is never raised: the CV download
    matters more than the notification.
    """
    if not is_enabled():
        return False, "Email sending is switched off (SEND_EMAIL=0)."
    if not is_configured():
        return False, (
            "Email is not configured - set SMTP_USER and SMTP_PASSWORD "
            "(see README) to have CVs sent automatically."
        )

    host = os.environ.get("SMTP_HOST", DEFAULT_HOST)
    port = int(os.environ.get("SMTP_PORT", DEFAULT_PORT))
    user = os.environ["SMTP_USER"]
    password = os.environ["SMTP_PASSWORD"]
    sender = os.environ.get("MAIL_FROM", user)
    to_addresses = recipients()

    message = EmailMessage()
    message["Subject"] = f"Revised CV - {details.candidate_name}"
    # Always from the one shared mailbox. The team member named on the form is
    # whoever *edited* the CV, not whoever sent the mail, so their name goes in
    # the body - and their address on Reply-To, so a head can reply to them.
    message["From"] = formataddr((FROM_DISPLAY, sender))
    if member and member.get("email"):
        message["Reply-To"] = member["email"]
    message["To"] = ", ".join(to_addresses)
    message.set_content(
        _body(details, filename, summary_lines or [], warnings or [], member)
    )
    message.add_attachment(
        pdf_bytes, maintype="application", subtype="pdf", filename=filename
    )

    context = ssl.create_default_context()
    try:
        if port == 465:
            with smtplib.SMTP_SSL(host, port, context=context, timeout=30) as server:
                server.login(user, password)
                server.send_message(message)
        else:
            with smtplib.SMTP(host, port, timeout=30) as server:
                server.starttls(context=context)
                server.login(user, password)
                server.send_message(message)
    except smtplib.SMTPAuthenticationError:
        return False, (
            "The mail server rejected the login. For Gmail this must be a "
            "16-character App Password, not the account password."
        )
    except Exception as exc:
        return False, f"Could not send the email: {exc}"

    return True, f"Sent to {', '.join(to_addresses)}."


if __name__ == "__main__":
    # python mailer.py - checks the settings and sends one test email.
    from cv_processor import CvDetails

    print(f"Recipients: {recipient()}")
    print(f"Enabled:    {is_enabled()}")
    print(f"Configured: {is_configured()}")
    if is_configured():
        print(f"SMTP:       {os.environ.get('SMTP_HOST', DEFAULT_HOST)}:"
              f"{os.environ.get('SMTP_PORT', DEFAULT_PORT)} as {os.environ['SMTP_USER']}")
    print()

    sent, message = send_cv(
        b"%PDF-1.4\n% test file from the Rishi Jobs CV tool\n",
        "Test_RishiJobs.pdf",
        CvDetails(
            candidate_name="Test Candidate",
            current_salary="18",
            expected_salary="20",
            notice_period="60 days",
            recruiter_note="Test message from the Rishi Jobs CV tool.",
        ),
        ["Email addresses: test@example.com"],
    )
    print("OK -", message) if sent else print("FAILED -", message)
