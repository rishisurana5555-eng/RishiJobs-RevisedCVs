# Rishi Jobs - CV Branding Tool

Takes a candidate's CV (PDF) and returns a client-ready version:

- **Contact details removed** - email, phone, LinkedIn/web profiles and the candidate's personal address, area and city are deleted from the page, not covered over
- **Recruiter details box on the CV itself** - current salary, expected salary and notice period across one row, with the recruiter's note full-width underneath
- **Logo on every page**, in a letterhead band matched to the CV's own paper colour
- **Faint navy watermark** behind the content on every page
- **Salaries in LPA** - digits only, validated on both the form and the API
- **Expected salary protected** - a hike of more than 30% shows as "As per industry norms" instead of the figure
- **The candidate's design is untouched** - the original page is placed whole at full size, never re-typeset or shrunk to fit
- **Emailed automatically** to whoever edited it, and downloads as `<Candidate_Name>_RishiJobs.pdf`

The candidate's **name is kept** and is read off the CV automatically to pre-fill the form. Their **area, city and state are kept** too - only the building and street go, so a client sees "Koramangala, Bengaluru" but not the door number.

---

## Running it

```bash
pip install -r requirements.txt
python -m streamlit run app.py     # http://localhost:8501
```

Or just double-click **`run.bat`**.

The Streamlit app calls the processing code directly, so that is the only process you need. `api.py` exposes the same thing as an HTTP API for scripts or another front end - start it with `run-api.bat` if you want it.

---

## Deploying to Streamlit Community Cloud

1. Push to GitHub (this repo).
2. At [share.streamlit.io](https://share.streamlit.io), **New app** → pick this repo, branch `main`, main file `app.py`.
3. Open **Advanced settings → Secrets** and paste:

   ```toml
   SMTP_HOST = "smtp.gmail.com"
   SMTP_PORT = "587"
   SMTP_USER = "rishisurana5555@gmail.com"
   SMTP_PASSWORD = "your16charapppassword"
   ```

   These are the same values as `.env`, in TOML form. The app copies them into the environment at startup.
4. Deploy.
5. **Settings → Sharing → set the app to private**, then invite each team member by their Google address. Without this the URL is public, and anyone who finds it can process CVs and email your heads.

`packages.txt` installs DejaVu fonts: the deploy runs on Linux, where Arial does not exist, and without a Unicode font the ₹ sign falls back to `?`.

To add team members on the deployed app, either edit `team.json` and push, or add a `TEAM_MEMBERS` secret:

```toml
TEAM_MEMBERS = "Rishi Surana <rishisurana5555@gmail.com>; Shubham Tyagi <shubhamtyagi.rj@gmail.com>"
```

---

## Project layout

| File | What it does |
|---|---|
| `cv_processor.py` | All the PDF work - redaction, details box, logo, watermark |
| `app.py` | Streamlit front end - the app itself |
| `api.py` | Optional: the same processing as an HTTP API |
| `mailer.py` | Sends the finished CV by email |
| `team.json` | The team members who use the tool |
| `templates/logo.png` | The Rishi Jobs logo |
| `samples/` | A test CV |

---

## API

| Endpoint | Purpose |
|---|---|
| `GET /api/health` | Service status |
| `POST /api/cv/process` | CV + details in, branded PDF out |
| `POST /api/cv/preview` | Same inputs, returns JSON listing what would be redacted |
| `POST /api/cv/detect-name` | Reads the candidate's name off a CV, to pre-fill the form |
| `GET /api/team` | The team members who may send CVs |

Send a multipart form with `cv_file` plus `candidate_name` (required), and optionally `current_salary`, `expected_salary`, `notice_period`, `recruiter_note` and `sender_name` (must match a name in `team.json`). A JSON body with the PDF in `cv_base64` works too. The logo is fixed and cannot be overridden through the API.

```bash
curl -X POST http://127.0.0.1:5001/api/cv/process \
  -F "cv_file=@samples/cv.pdf" \
  -F "candidate_name=Priya Raman" \
  -F "notice_period=60 days" \
  -o branded.pdf
```

The response carries `X-Redactions` (how many details were removed), `X-Looks-Scanned` and `X-Note-Truncated`.

---

## Email setup

Every CV that processes cleanly is emailed to the heads with the PDF attached, the commercial details and a summary of what was redacted. Until credentials are set the app still works - it just says the CV was not emailed, and you download it as usual.

Subject line: `Revised CV - <Candidate Name>`.

### Who it goes to

**Whoever edited the CV, and nobody else.** If Shubham edits a CV it goes to Shubham; if Rishi edits one it goes to Rishi. The name picked in the "Edited by" box decides it, and the address is looked up server side from `team.json` - a request supplies a name, never an address, so a CV cannot be emailed somewhere of the caller's choosing.

`FALLBACK_RECIPIENTS` in `mailer.py` is only used when no team member was identified, so a CV is never sent into the void. `MAIL_TO` overrides that fallback, comma-separated, capped at 4 addresses.

### Who it comes from, and who edited the CV

Every email goes out from **one shared mailbox** (the `SMTP_USER` in `.env` - Rishi's account). The team member picked on the form is whoever **edited** the CV, which is a separate thing from who sent the mail:

- **From** is always `Rishi Jobs CV Tool <rishisurana5555@gmail.com>`
- the body says `Edited by: Shubham Tyagi (shubhamtyagi.rj@gmail.com)`

Gmail will not let an account send as somebody else's address anyway, so a per-person `From` would need Google OAuth. This upgrades to that later without redoing anything else.

Members live in **`team.json`** - this is the "Edited by" picker, and it is also what decides where each CV is sent:

```json
{ "members": [ { "name": "Priya Raman", "email": "priya@example.com" } ] }
```

Add or remove people there and restart `api.py`. The front end shows them in the "Edited by" picker, and the API only accepts a name that is on the list - a request cannot supply its own address, so nobody can forge a Reply-To on a CV going to the heads.

Copy `.env.example` to `.env` and fill it in:

```
SMTP_HOST=smtp.gmail.com
SMTP_PORT=587
SMTP_USER=your.address@gmail.com
SMTP_PASSWORD=your16charapppassword
```

For Gmail this **must be an App Password**, not your normal password: Google Account → Security → 2-Step Verification → App passwords. `.env` is git-ignored, so the password never reaches the repo. Restart `api.py` after editing it.

Check the setup without processing a CV:

```bash
python mailer.py
```

It prints what it found and sends one test email, so you can confirm credentials before relying on it.

Turn sending off entirely with `SEND_EMAIL=0` - useful while testing, so repeated runs do not fill the inbox.

**A scanned CV is never emailed automatically.** Nothing can be redacted on one, so the contact details are still on the page - the app hands it to you to check and send by hand instead.

---

## The expected-salary rule

If the expected salary is more than **30%** above the current salary, the CV prints **"As per industry norms"** rather than the number. Both figures have to be readable for the rule to fire - if either is blank or non-numeric, whatever was typed is shown as-is.

Both salary fields are **in LPA** and accept digits only, with an optional decimal point - `18`, `18.5`, `24`. Anything else (`18,00,000`, `₹18L`, `eighteen`) is rejected by the form *and* by the API, so a stray value cannot slip through a direct API call. The CV prints them as `18 LPA`.

Change the threshold with `SALARY_HIKE_THRESHOLD` in `cv_processor.py`, and the wording with `SALARY_AS_PER_NORMS`.

---

## How the redaction works

Contact details are found by rebuilding each visual line of text and matching it, rather than checking word by word - a number like `+44 20 7946 0912` is four separate "words" in the PDF and would otherwise be missed.

Matches are removed with PyMuPDF's `apply_redactions()`, which **deletes the glyphs from the content stream**. Drawing a white box over text would leave it fully selectable and copyable. Document metadata and link annotations (a `mailto:` or LinkedIn href) are stripped too.

Each redaction is filled with the paper colour sampled from just around it, so on a cream or grey CV the gap disappears instead of leaving a bright white patch.

### Headings

A bare **"Contact"**, "Contact Details", "Personal Details" or "Address" heading is removed as well. Once the details under it are gone the heading points at nothing. It has to be the whole line, so a heading goes while "Please contact me for references" or "Contact Centre modernisation" stays.

### Locations

The candidate's **personal address, area and city** go too, but only from the CV's header block - the top 25% of page 1, where that information sits. Cities further down a CV are almost always employer locations (`Global Marketing Director | London, UK`), and deleting those would gut the work history.

A line that looks like a postal address, or carries a PIN/postcode/ZIP, is removed whole. Otherwise just the place names go, so `Elena Vance | London` keeps the name. The candidate's name is protected explicitly and never redacted, whatever else matches on that line.

To strip every location everywhere instead, set `REDACT_LOCATIONS_EVERYWHERE = True` in `cv_processor.py`. Widen or narrow the header block with `HEADER_REGION_RATIO`.

### Phones

Phone matching only accepts 9-15 digits, which keeps date ranges (`2012 - 2016`), money (`$420M`), Indian-format figures (`12,00,00,000`), postcodes and employee IDs out of the net. Labels like `Mobile:` or `Tel` immediately before a number are removed along with it, as are the separators around a hit, so a redacted contact line doesn't end up as a row of orphaned bullets.

---

## Page layout

The CV's own content is kept at **full size and never shrunk to make room** for the details box. Where it no longer fits, it continues onto another page instead - a 1-page CV normally comes back as 2 pages. Splits land between lines of text, never through one.

**An extra page is only added when there is content to put on it.** How far down the page the CV actually reaches is measured first, so a short CV that ends half way down stays a single page instead of dragging a blank sheet along behind it. Full-page background fills are ignored in that measurement - a CV printed on cream stock has a rectangle covering the whole page, which would otherwise make every CV look full to the last millimetre.

Pages that carry no details box (page 2 onward) lose only the logo band, so they are scaled by about 7% rather than being split - not enough to notice, and it avoids a 50pt sliver page after every sheet.

`SHRINK_TOLERANCE` sets where that choice tips: content is scaled if it would still be at least this fraction of full size, otherwise it keeps full size and runs on.

---

## Important limits

- **Scanned CVs cannot be redacted.** If a CV is a photo or a scan there is no text layer to search, so nothing is removed and the contact details are still visible. The tool detects this and shows a loud error - the app does not silently hand back a CV that only looks redacted. OCR would be the fix, and isn't built yet.
- **Always eyeball the result.** The app lists exactly what it removed. Phone and address formats vary endlessly; check before sending a CV to a client.
- **Dates of birth are not redacted.** Remove those by hand if needed.
- **Location redaction is header-only by default** (see above), so a city mentioned in the body of the CV stays. That is deliberate - check the "Removed from the CV" list and the page itself.
- Password-protected PDFs are rejected with a clear message; supply an unlocked copy.
- Uploads are capped at 25 MB.

---

## Tuning

Constants at the top of `cv_processor.py`:

| Constant | Effect |
|---|---|
| `HEADER_BAND_HEIGHT` | Height of the logo band. Set to `0` to drop it and leave CV pages completely untouched. |
| `WATERMARK_OPACITY` | Watermark strength. Higher is more visible; the logo is dark, so it competes with the text quickly. |
| `WATERMARK_SCALE` | Watermark size as a fraction of page width. |
| `DETAILS_MAX_HEIGHT_RATIO` | Ceiling on the details box as a fraction of page height. A long recruiter note shrinks to fit this, and is cut with an ellipsis if it still will not fit - the app warns when that happens. |
| `SALARY_HIKE_THRESHOLD` | The 30% rule. |
| `SHRINK_TOLERANCE` | How much shrinking is acceptable before the CV is split onto another page instead. |
| `HEADER_REGION_RATIO` | How much of page 1 counts as the header block for location redaction. |
| `REDACT_LOCATIONS_EVERYWHERE` | Strip locations from the whole CV, not just the header. |

The details box renders with a system TrueType font (Arial, Segoe UI, DejaVu or Liberation) so symbols like `₹` display correctly. If none is found it falls back to the built-in Helvetica and transliterates what that font can't show.

---

## Ideas to extend

- OCR (`pytesseract` / `ocrmypdf`) so scanned CVs can be redacted too
- Bulk processing - several CVs in one go, zipped up
- Optional redaction of dates of birth
- Emailing the client directly, with a CC to the recruiter
- Deploy the API (Render/Railway/Fly.io) and point `CV_API_URL` at it
