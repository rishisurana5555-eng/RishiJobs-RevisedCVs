"""
Rishi Jobs - CV Branding API (Flask)

Endpoints
    GET  /api/health       -> service status
    POST /api/cv/process   -> CV PDF + recruiter details in, branded PDF out
    POST /api/cv/preview   -> same, but reports what would be redacted (JSON)

Run:  python api.py   (serves on http://127.0.0.1:5001)
"""

import base64
import io
import os

from flask import Flask, jsonify, request, send_file

import mailer

from cv_processor import (
    CvDetails,
    LOGO_PATH,
    MAX_UPLOAD_MB,
    cv_filename,
    generate_branded_cv,
    guess_candidate_name,
)

app = Flask(__name__)
app.config["MAX_CONTENT_LENGTH"] = MAX_UPLOAD_MB * 1024 * 1024


def _read_request() -> tuple[bytes, CvDetails]:
    """
    Pull the CV and the recruiter fields out of the request.

    Accepts a multipart upload (cv_file + form fields) or a JSON body with the
    PDF base64-encoded, so the API is usable from a plain HTTP client too. The
    logo is fixed - it is never taken from the request.
    """
    if request.files.get("cv_file"):
        upload = request.files["cv_file"]
        pdf_bytes = upload.read()
        details = CvDetails.from_dict(request.form.to_dict())
    else:
        payload = request.get_json(silent=True)
        if not isinstance(payload, dict):
            raise ValueError(["Send a multipart upload with 'cv_file', or a JSON body."])
        encoded = payload.get("cv_base64")
        if not encoded:
            raise ValueError(["'cv_base64' is required when posting JSON."])
        try:
            pdf_bytes = base64.b64decode(encoded)
        except Exception:
            raise ValueError(["'cv_base64' is not valid base64."])
        details = CvDetails.from_dict(payload)

    if not pdf_bytes:
        raise ValueError(["The uploaded CV is empty."])
    return pdf_bytes, details


@app.get("/api/team")
def team():
    """The team members who may send CVs, for the front end's picker."""
    return jsonify(members=mailer.team_members())


@app.get("/api/health")
def health():
    return jsonify(
        status="ok",
        logo_found=os.path.exists(LOGO_PATH),
        email_enabled=mailer.is_enabled(),
        email_configured=mailer.is_configured(),
        email_recipient=mailer.recipient(),
    )


@app.post("/api/cv/detect-name")
def detect_name():
    """Best guess at the candidate's name, used to pre-fill the form."""
    upload = request.files.get("cv_file")
    if not upload:
        return jsonify(errors=["Send a multipart upload with 'cv_file'."]), 422
    try:
        return jsonify(candidate_name=guess_candidate_name(upload.read()))
    except Exception:
        app.logger.exception("Name detection failed")
        return jsonify(candidate_name="")


@app.post("/api/cv/process")
def process_cv():
    try:
        pdf_bytes, details = _read_request()
    except ValueError as exc:
        return jsonify(errors=exc.args[0]), 422

    try:
        output, report = generate_branded_cv(pdf_bytes, details)
    except ValueError as exc:
        return jsonify(errors=[str(exc)]), 422
    except Exception as exc:  # surface rendering problems to the client
        app.logger.exception("CV processing failed")
        return jsonify(errors=[f"Could not process the CV: {exc}"]), 500

    # Looked up server side: the request supplies a name, never an address, so
    # nobody can put an arbitrary Reply-To on a CV going to the heads.
    sender_name = (request.form.get("sender_name") or "").strip()
    member = mailer.find_member(sender_name)
    if sender_name and member is None:
        return jsonify(errors=[f"'{sender_name}' is not in the team list."]), 422

    filename = cv_filename(details)
    sent, mail_message = mailer.deliver(output, filename, details, report, member)

    response = send_file(
        io.BytesIO(output),
        mimetype="application/pdf",
        as_attachment=True,
        download_name=filename,
    )
    # Headers, so a client can warn the recruiter without a second round trip.
    response.headers["X-Redactions"] = str(report.total)
    response.headers["X-Looks-Scanned"] = "1" if report.looks_scanned else "0"
    response.headers["X-Note-Truncated"] = "1" if report.note_truncated else "0"
    response.headers["X-Email-Sent"] = "1" if sent else "0"
    response.headers["X-Email-Message"] = mail_message
    return response


@app.post("/api/cv/preview")
def preview_cv():
    """Dry run: report what would be stripped, without returning the PDF."""
    try:
        pdf_bytes, details = _read_request()
    except ValueError as exc:
        return jsonify(errors=exc.args[0]), 422

    try:
        _output, report = generate_branded_cv(pdf_bytes, details)
    except ValueError as exc:
        return jsonify(errors=[str(exc)]), 422
    except Exception as exc:
        app.logger.exception("CV preview failed")
        return jsonify(errors=[f"Could not read the CV: {exc}"]), 500

    return jsonify(
        removed=report.removed,
        total=report.total,
        looks_scanned=report.looks_scanned,
        note_truncated=report.note_truncated,
        email_recipient=mailer.recipient(),
        email_ready=mailer.is_enabled() and mailer.is_configured(),
        team_size=len(mailer.team_members()),
        pages_without_text=report.pages_without_text,
        page_count=report.page_count,
        filename=cv_filename(details),
    )


if __name__ == "__main__":
    app.run(
        host=os.environ.get("API_HOST", "127.0.0.1"),
        port=int(os.environ.get("API_PORT", "5001")),
        debug=False,
    )
