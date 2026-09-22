# Rishi Jobs - CV Branding Tool

Takes a candidate's CV (PDF) and returns a client-ready version:

- **Contact details removed** - email, phone, LinkedIn/web profiles, their icons, and the candidate's street address are deleted from the page, not covered over
- **Recruiter details box on the CV itself** - current salary, expected salary and notice period across one row, with the recruiter's note full-width underneath
- **Logo on every page**, in a letterhead band matched to the CV's own paper colour
- **Faint navy watermark** behind the content on every page
- **Salaries in LPA** - digits only, validated on both the form and the API
- **Expected salary protected** - a hike of more than 30% shows as "As per industry norms" instead of the figure
- **The candidate's design is untouched** - the original page is placed whole at full size, never re-typeset or shrunk to fit
- **Downloads straight from the browser** as `<Candidate_Name>_RishiJobs.pdf` - nothing is emailed or sent anywhere

The candidate's **name is kept** and is read off the CV automatically to pre-fill the form. Of their address, **only the area, city and state are kept** - the flat and door number, floor, street, building and postcode all go, so a client sees "Koramangala, Bengaluru" and nothing that narrows it further.

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
3. Deploy. There is nothing to configure - the app needs no secrets and no credentials.
4. **Settings → Sharing → set the app to private**, then invite each team member by their Google address. Without this the URL is public, and anyone who finds it can process CVs through your app.

`packages.txt` installs DejaVu fonts: the deploy runs on Linux, where Arial does not exist, and without a Unicode font the ₹ sign falls back to `?`.

---

## Project layout

| File | What it does |
|---|---|
| `cv_processor.py` | All the PDF work - redaction, details box, logo, watermark |
| `app.py` | Streamlit front end - the app itself |
| `api.py` | Optional: the same processing as an HTTP API |
| `templates/logo.png` | The Rishi Jobs logo |
| `samples/` | A test CV |

---

## API

| Endpoint | Purpose |
|---|---|
| `GET /api/health` | Service status |
| `POST /api/cv/process` | CV + details in, revised PDF out |
| `POST /api/cv/preview` | Same inputs, returns JSON listing what would be redacted |
| `POST /api/cv/detect-name` | Reads the candidate's name off a CV, to pre-fill the form |

Send a multipart form with `cv_file` plus `candidate_name` (required), and optionally `current_salary`, `expected_salary`, `notice_period` and `recruiter_note`. A JSON body with the PDF in `cv_base64` works too. The logo is fixed and cannot be overridden through the API.

```bash
curl -X POST http://127.0.0.1:5001/api/cv/process \
  -F "cv_file=@samples/cv.pdf" \
  -F "candidate_name=Priya Raman" \
  -F "notice_period=60 days" \
  -o branded.pdf
```

The response carries `X-Redactions` (how many details were removed), `X-Looks-Scanned` and `X-Note-Truncated`.

---

## The expected-salary rule

The CV prints **"As per industry norms"** instead of the expected salary when it is **left blank**, or when it is more than **30%** above the current salary. A recruiter who does not fill it in is declining to name a figure, which is the same thing the 30% rule says. Both figures have to be readable for the rule to fire - if either is blank or non-numeric, whatever was typed is shown as-is.

Both salary fields are **in LPA** and accept digits only, with an optional decimal point - `18`, `18.5`, `24`. Anything else (`18,00,000`, `₹18L`, `eighteen`) is rejected by the form *and* by the API, so a stray value cannot slip through a direct API call. The CV prints them as `18 LPA`.

Change the threshold with `SALARY_HIKE_THRESHOLD` in `cv_processor.py`, and the wording with `SALARY_AS_PER_NORMS`.

---

## How the redaction works

Contact details are found by rebuilding each visual line of text and matching it, rather than checking word by word - a number like `+44 20 7946 0912` is four separate "words" in the PDF and would otherwise be missed.

Each line is rebuilt **character by character**, so a match covering part of a word takes only that part. `Ahmedabad-380001` is a single word to the PDF, and stripping the PIN off it must not take the city with it. Word breaks are inferred from the spacing, because a PDF usually separates words by moving the cursor rather than by writing a space.

Matches are removed with PyMuPDF's `apply_redactions()`, which **deletes the glyphs from the content stream**. Drawing a white box over text would leave it fully selectable and copyable. Document metadata and link annotations (a `mailto:` or LinkedIn href) are stripped too.

Each redaction is filled with the paper colour sampled from just around it, so on a cream or grey CV the gap disappears instead of leaving a bright white patch.

Deleting text leaves a hole exactly where it was, and a PDF does not reflow to close it. So the CV is placed as a series of **content runs** rather than in one piece: any vertical gap wider than `MAX_CONTENT_GAP` (28pt) is squeezed back down to it, which closes the hole a removed contact block leaves behind. Ordinary line and section spacing sits below that threshold and is reproduced exactly, so the CV's own rhythm is untouched.

The same runs give page breaks somewhere sensible to land - a break falls between runs rather than through a line of text, and a run that would only just overflow is moved to the next page whole.

The **recruiter note is required**, and its first letter is capitalised automatically.

### Icons

A CV header usually pairs each contact detail with a small icon. Deleting the text on its own leaves a row of orphaned envelope, LinkedIn and GitHub marks, which both looks wrong and still says where to find the candidate. Three kinds turn up and all three are removed:

- **Icon-font glyphs** - the commonest by far. The GitHub and LinkedIn marks are usually not pictures at all but characters from an icon font (Font Awesome and the like), mapped into Unicode's private use area or left unmapped entirely. They are matched by character range and by font name, and ordinary bullets and dashes are excluded by name so a normal line is never dragged in.
- **Small images**, and
- **small pieces of vector art**.

Anything icon-sized (up to `ICON_MAX_SIZE`, 30pt) on a row that lost contact details, or within `ICON_MAX_DISTANCE` (16pt - about one line) above or below it, is taken. Matching horizontally as well was too strict: a header routinely wraps, leaving the GitHub mark at the right-hand end of one row and the address it pointed at on the next.

**Contact hyperlinks are used as evidence too.** A header often hangs the link on the icon alone, with no address written out in text for the patterns to match - nothing in the text layer gives that away, so the link's own rectangle is what gets redacted. Only when it is icon-sized and holds no readable text, so a link wrapped around a line of prose is left alone.

Artwork goes in a second pass, the only one allowed to touch it, and it blanks the icon's pixels rather than deleting a whole image - an icon that is part of a larger sprite does not take the sprite with it. Vector art is removed only when a redaction box covers it entirely.

### Headings

A bare **"Contact"**, "Contact Details", "Get in Touch", "Address" or "Permanent Address" heading is removed as well. Once the details under it are gone the heading points at nothing. It has to be the whole line, so a heading goes while "Please contact me for references" or "Contact Centre modernisation" stays. **"Personal Details" is deliberately kept** - that heading also introduces the date of birth and languages, which stay, so removing it would orphan them.

### Locations

**Only the area, city, state and country survive.** Everything else on an address line comes off - flat and door numbers, floors, plot and survey numbers, street and building names, PIN codes and postcodes - so `Flat 3B, Prestige Towers, Koramangala, Bengaluru 560034` is reduced to `Koramangala, Bengaluru`. A client should see roughly where the candidate is, and nothing that narrows it to a door.

It is written as a **keep** test rather than a remove test, which is what makes that "everything else" hold: on a line already established as an address, a piece is kept only when it names nothing but a place - no digits, no street or building word, five words or fewer. Anything unrecognised is treated as a detail of where the candidate lives and goes.

A line has to earn that treatment, because the top of page 1 is also where a professional summary lives. Two things qualify it:

- **A confirmed address** - one that announces itself with an `Address:` / `Current Location:` label, or carries unmistakable structure (a door, flat or plot number, a postcode or a PIN). These are stripped back to their place names **wherever they sit on the CV**, because a "Personal Details" block at the foot of a CV is every bit as identifying as one in the header.
- **A line that merely looks like an address** - short comma-separated pieces, most of them place names, with at least one that is not: `12 Oakwood Road, Indiranagar, Bengaluru` proves nothing on its own but is the shape of an address, not of a sentence. Trusted **only in the header block** (the top 25% of page 1), and only pieces that are positively address-shaped are removed, not everything that is not a place.

That split is what protects real prose. `Managed 12 people, based in London, grew the brand 40%` is mostly not place names, so it reads as a sentence and is left alone; `Senior Manager, 15 years experience, Mumbai` keeps the middle piece because nothing about it is address-shaped. Cities further down a CV are almost always employer locations (`Global Marketing Director | London, UK`) and deleting those would gut the work history, which is why the weaker test is header-only.

**An address typed over several lines is handled as a block.** Only one of its lines usually proves what it is, so the block is grown outwards from that line and everything above the last line goes:

```
2278, Raipur Kot ni rang,        ->  (removed)
Raipur Gate,                     ->  (removed)
Ahmedabad-380001.                ->  Ahmedabad
```

The earlier lines are the door and the street however they happen to be worded - a locality name no keyword would ever catch - and the last line is where the place name lives. Lines only join a block when they are fragmentary: short, close by, and carrying a comma, a digit or a place name. A job title directly above the address has none of those, so it is not swallowed; a line carrying an email, a phone number or a URL is a contact line and never joins; and a `Label: value` row is recognised as a Personal Details entry, so `Date of Birth: 14 March 1988` and `Languages known------------English, Hindi` sitting under an address are left alone.

The candidate's name is protected explicitly and never redacted, whatever else matches on that line, and a line carrying it never joins a block. A postal code inside a piece is judged separately, so `New Delhi - 110017` loses the code and keeps the city.

To strip the area and city as well, set `REDACT_CITY_AND_AREA = True` in `cv_processor.py`; to run the weaker test over the whole CV rather than the header, set `REDACT_LOCATIONS_EVERYWHERE = True`. Widen or narrow the header block with `HEADER_REGION_RATIO`.

### Address words

Address words fall in two tiers. "Flat", "Plot No" and "Pincode" only ever appear in an address, so they count on their own. "Road", "Street", "Tower" and the like also turn up in ordinary CV prose, so they only count when a number sits beside them or the line has already proved it is an address. Words like **"building", "house", "cross", "court" and "plot"** are in neither list - "hands-on experience **building** scalable systems", "**in-house** team", "**cross**-functional" are all normal CV language, and a number nearby is not rare enough to save them. Pieces longer than five words are ignored: an address is written in short pieces, a sentence is not.

### Phones

Phone matching only accepts 9-15 digits, which keeps date ranges (`2012 - 2016`), money (`$420M`), Indian-format figures (`12,00,00,000`), postcodes and employee IDs out of the net.

**Labels go with the detail they introduce**, so a line does not end up as a lone `Contact No (M):` pointing at nothing. The label counts when only punctuation and short qualifiers separate it from the number or address - `Contact No (M):`, `Email : -`, `Mobile (Work) -` - and each qualifier has to end at a non-letter, so a label followed by ordinary prose (`Contact me at the office for details`) is left alone. Separators around a hit go too, along with punctuation left stranded at either end of the line, so a redacted contact line doesn't end up as a row of orphaned bullets or a full stop on its own.

---

## Page layout

The CV's own content is kept at **full size and never shrunk to make room** for the details box. Where it no longer fits, it continues onto another page instead - a 1-page CV normally comes back as 2 pages. Splits land between lines of text, never through one.

**Source pages share a sheet rather than each starting a new one.** A CV whose first page overruns by a line or two used to leave the rest of that sheet blank, because the next source page always began a fresh one - the result was a page carrying two paragraphs and nothing else. A new source page now carries on down the sheet in progress whenever at least `SOURCE_PAGE_MIN_SPACE` (120pt) of it is free, with `SOURCE_PAGE_GAP` between the two. Set `SOURCE_PAGE_MIN_SPACE` to a page height to go back to one source page per sheet.

**An extra page is only added when there is content to put on it.** How far down the page the CV actually reaches is measured first, so a short CV that ends half way down stays a single page instead of dragging a blank sheet along behind it. Full-page background fills are ignored in that measurement - a CV printed on cream stock has a rectangle covering the whole page, which would otherwise make every CV look full to the last millimetre.

A page that continues from the previous one gets a deeper top margin (`CONTINUATION_TOP_GAP`), since its content starts mid-sentence rather than with the CV's own header and would otherwise run straight into the logo band.

`SHRINK_TOLERANCE` (0.97) sets where that choice tips. An 11pt CV never drops below about 10.7pt - close enough to be invisible - and that small allowance exists only to save a whole extra page when the content overruns by a line or two. Anything that would shrink further keeps full size and runs onto the next page, leaving white space rather than text the reader has to zoom in on.

The cost is pages: a dense two-page CV may come back as three, the last carrying only the few lines that would not fit. That is the intended trade - readable text over a tidy page count. Lower `SHRINK_TOLERANCE` if you would rather have fewer pages and slightly smaller text.

A scanned or image-only page is the exception: it is scaled to fit rather than split, because there is no text to break between and clipping it would lose whatever sits at the bottom.

---

## Important limits

- **Scanned CVs cannot be redacted.** If a CV is a photo or a scan there is no text layer to search, so nothing is removed and the contact details are still visible. The tool detects this and shows a loud error - the app does not silently hand back a CV that only looks redacted. OCR would be the fix, and isn't built yet.
- **Always eyeball the result.** The app lists exactly what it removed. Phone and address formats vary endlessly; check before sending a CV to a client.
- **Dates of birth are not redacted.** Remove those by hand if needed.
- **The area and city are kept on purpose**, so a client can see roughly where the candidate is. Only the street and building go. Set `REDACT_CITY_AND_AREA = True` to strip those too.
- **A city mentioned in the body of the CV stays**, unless the line is a confirmed address. That is deliberate - those are nearly always employer locations. Check the "Removed from the CV" list and the page itself.
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
| `CONTINUATION_TOP_GAP` | Top margin on a page that continues from the previous one. |
| `MAX_CONTENT_GAP` | Vertical gaps wider than this are squeezed down to it, closing the holes left by redaction. |
| `SOURCE_PAGE_MIN_SPACE` | How much of a sheet must be free for the next source page to carry on down it. Raise to a page height for one source page per sheet. |
| `SOURCE_PAGE_GAP` | The gap left between two source pages sharing a sheet. |
| `ADDRESS_BLOCK_MAX_LINES` | How many lines one address is allowed to run to. |
| `HEADER_REGION_RATIO` | How much of page 1 counts as the header block for location redaction. |
| `REDACT_LOCATIONS_EVERYWHERE` | Apply the weaker "looks like an address" test to the whole CV, not just the header. |
| `REDACT_CITY_AND_AREA` | Strip the area, city and state as well, leaving no location at all. |
| `ICON_MAX_SIZE` / `ICON_MAX_DISTANCE` | How big a contact icon may be, and how far above or below the removed text it may sit. |

The details box renders with a system TrueType font (Arial, Segoe UI, DejaVu or Liberation) so symbols like `₹` display correctly. If none is found it falls back to the built-in Helvetica and transliterates what that font can't show.

---

## Ideas to extend

- OCR (`pytesseract` / `ocrmypdf`) so scanned CVs can be redacted too
- Bulk processing - several CVs in one go, zipped up
- Optional redaction of dates of birth
- Deploy the API (Render/Railway/Fly.io) and point `CV_API_URL` at it
