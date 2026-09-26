"""
Rishi Jobs - CV branding and redaction.

Takes a candidate CV (PDF) and returns a branded copy:

  * personal contact details (email, phone, LinkedIn/URLs) are truly removed,
    not covered up - the text is deleted from the page, along with the icons
    that sat beside them
  * of the candidate's address only the area, city and state survive - the
    door, street, building and postcode go
  * a details box on the CV itself carries the recruiter's commercial details:
    salary, expected salary and notice period on one row, the note below
  * the logo sits in a header band on every page, with a faint watermark behind
  * the candidate's own CV design is left untouched - the page is placed whole,
    never re-typeset

The candidate's name is deliberately kept.
"""

from __future__ import annotations

import io
import os
import re
from collections import Counter
from dataclasses import dataclass, field
from typing import Iterable

import pymupdf

BASE_DIR = os.path.dirname(os.path.abspath(__file__))
LOGO_PATH = os.path.join(BASE_DIR, "templates", "logo.png")

#: Upload ceiling. Well above any real CV, low enough to keep a bad upload
#: from parking a multi-hundred-megabyte file in memory.
MAX_UPLOAD_MB = 25

# ---------------------------------------------------------------- brand ----

NAVY = (0x16 / 255, 0x21 / 255, 0x3E / 255)
TAN = (0xDD / 255, 0xA8 / 255, 0x78 / 255)
WHITE = (1, 1, 1)
INK = (0.13, 0.13, 0.13)

FONT = "helv"
FONT_BOLD = "hebo"

# The built-in base-14 fonts are Latin-1 only, so a rupee sign or an en dash
# comes out as "?". A system TrueType face is embedded when one is available.
_FONT_CANDIDATES = {
    "regular": [
        r"C:\Windows\Fonts\arial.ttf",
        r"C:\Windows\Fonts\segoeui.ttf",
        "/usr/share/fonts/truetype/dejavu/DejaVuSans.ttf",
        "/usr/share/fonts/truetype/liberation/LiberationSans-Regular.ttf",
        "/System/Library/Fonts/Supplemental/Arial.ttf",
    ],
    "bold": [
        r"C:\Windows\Fonts\arialbd.ttf",
        r"C:\Windows\Fonts\segoeuib.ttf",
        "/usr/share/fonts/truetype/dejavu/DejaVuSans-Bold.ttf",
        "/usr/share/fonts/truetype/liberation/LiberationSans-Bold.ttf",
        "/System/Library/Fonts/Supplemental/Arial Bold.ttf",
    ],
}

#: Applied only when no Unicode face could be found, so the cover degrades to
#: readable ASCII instead of a row of question marks.
_TRANSLITERATE = {
    "\u20b9": "INR ",
    "\u2014": "-",
    "\u2013": "-",
    "\u2018": "'",
    "\u2019": "'",
    "\u201c": '"',
    "\u201d": '"',
    "\u2026": "...",
    "\u2022": "-",
}


def _find_font(kind: str) -> str | None:
    for path in _FONT_CANDIDATES[kind]:
        if os.path.exists(path):
            return path
    return None


UNICODE_REGULAR = _find_font("regular")
UNICODE_BOLD = _find_font("bold")


class Typeface:
    """Resolves the best available fonts for a page, with an ASCII fallback."""

    def __init__(self, page: "pymupdf.Page"):
        self.unicode = bool(UNICODE_REGULAR and UNICODE_BOLD)
        if self.unicode:
            page.insert_font(fontname="rjreg", fontfile=UNICODE_REGULAR)
            page.insert_font(fontname="rjbold", fontfile=UNICODE_BOLD)
            self.regular, self.bold = "rjreg", "rjbold"
        else:
            self.regular, self.bold = FONT, FONT_BOLD

    def text(self, value: str) -> str:
        if self.unicode:
            return value
        for char, replacement in _TRANSLITERATE.items():
            value = value.replace(char, replacement)
        return value.encode("latin-1", "replace").decode("latin-1")

#: Height of the logo band added above every CV page, in points. The original
#: page is scaled to fit underneath it, so the candidate's layout is preserved
#: exactly - just very slightly smaller. Set to 0 to drop the band entirely.
HEADER_BAND_HEIGHT = 54.0

#: Watermark opacity. The logo is dark navy, so anything stronger than this
#: starts competing with the CV text.
WATERMARK_OPACITY = 0.10

#: Watermark width as a fraction of the page width.
WATERMARK_SCALE = 0.55

#: How small the CV's own text may be scaled, as a fraction of its original
#: size. Anything that would go below this is left at full size and allowed to
#: run onto another page instead, leaving white space rather than text the
#: reader has to zoom in on.
#:
#: At 0.97 an 11pt CV never drops below about 10.7pt - close enough to be
#: invisible - and the allowance only exists to save a whole extra page when
#: the content overruns by a line or two. It is not a licence to shrink.
SHRINK_TOLERANCE = 0.97

#: Breathing room between the logo band and the CV content beneath it.
CONTENT_TOP_GAP = 6.0

#: A page that continues from the previous one gets a deeper top margin. Its
#: content starts mid-flow rather than with the CV's own header, so text
#: running straight into the logo band reads as cramped.
CONTINUATION_TOP_GAP = 30.0

#: A leftover strip smaller than this is not worth a page of its own - it is
#: the bottom margin of the CV, not content.
BLANK_PAGE_TOLERANCE = 24.0

#: Vertical gaps taller than this are squeezed back down to it.
#:
#: Deleting a contact block leaves a hole exactly where the text was, and a
#: PDF does not reflow to close it, so a revised CV would otherwise carry
#: obvious empty patches. Sized to sit above normal section spacing (usually
#: 20-27pt), so the CV's own rhythm is left alone and only the holes shrink.
MAX_CONTENT_GAP = 28.0

#: A new source page carries on down the sheet already in progress when at
#: least this much of it is still free, instead of always starting a fresh one.
#:
#: A CV whose first page overruns by a line or two used to leave the rest of
#: that sheet blank, because the next source page always began a new one. Set
#: to a page height to go back to one source page per sheet.
SOURCE_PAGE_MIN_SPACE = 120.0

#: The breathing space left between one source page's content and the next
#: when the two share a sheet.
SOURCE_PAGE_GAP = 18.0

# ------------------------------------------------------------ redaction ----

# Deliberately anchored on unambiguous contact formats. Anything looser starts
# eating dates, salary figures and postcodes off real CVs.
EMAIL_RE = re.compile(r"[A-Za-z0-9._%+-]+@[A-Za-z0-9.-]+\.[A-Za-z]{2,}")

URL_RE = re.compile(
    r"(?:https?://|www\.)[^\s|]+"
    r"|(?:[A-Za-z0-9-]+\.)?(?:linkedin|github|gitlab|behance|dribbble|medium|x)"
    r"\.com/[^\s|]+",
    re.IGNORECASE,
)

# A phone number is only accepted once the digit count lands in a plausible
# range (see _phone_spans), which is what keeps "2012 - 2016" out of the net.
PHONE_RE = re.compile(
    r"(?:\+\d{1,3}[\s.\-]?)?"
    r"(?:\(\d{1,5}\)[\s.\-]?)?"
    r"\d[\d\s.\-]{7,16}\d"
)

PHONE_LABEL_RE = re.compile(
    r"\b(?:tel|telephone|phone|mob|mobile|cell|contact|whatsapp|ph|alt|alternate"
    r"|landline|direct|fax)\b[\s.:#]*",
    re.IGNORECASE,
)
EMAIL_LABEL_RE = re.compile(r"\b(?:e-?mail|mail)\b[\s.:]*", re.IGNORECASE)

#: What may sit between a contact label and the detail it introduces:
#: punctuation and short qualifiers, as in "Contact No (M):", "Email : -" or
#: "Mobile (Work) -". Each qualifier has to end at a non-letter, so a label
#: followed by ordinary prose ("Contact me at the office for ...") does not
#: match and the sentence is left alone.
_LABEL_GAP = r"[\s.:;,#()\[\]<>/|\-–—]"
LABEL_FILLER_RE = re.compile(
    rf"{_LABEL_GAP}*"
    rf"(?:(?:nos?|number|num|id|mob|mobile|cell|phone|tel|off|office|res"
    rf"|residence|work|home|personal|primary|alt|alternate|[a-z])"
    rf"(?={_LABEL_GAP}|$){_LABEL_GAP}*)*$",
    re.IGNORECASE,
)

#: A section heading that only introduces contact details. Once the details
#: underneath are gone the heading is left pointing at nothing, so it goes too.
#: Matched against a whole line, so "Contact" as a heading goes while
#: "contact" inside a sentence stays.
#:
#: "Personal Details" is deliberately absent: that heading also introduces the
#: date of birth and languages, which stay, so removing it would orphan them.
CONTACT_HEADING_RE = re.compile(
    r"^[\s|•·\-–—]*"
    r"(?:contact(?:\s*(?:details|info|information|me|us|nos?\.?|numbers?))?"
    r"|(?:phone|mobile|mob|cell|tel|telephone|whatsapp)\s*(?:nos?\.?|numbers?)"
    r"|e-?mail\s*(?:id|address)"
    r"|get\s*in\s*touch|reach\s*me|how\s*to\s*reach\s*me"
    r"|(?:current|present|permanent|residential|home|postal|mailing)?\s*"
    r"(?:address|location))"
    r"[\s:|•·\-–—]*$",
    re.IGNORECASE,
)

#: A contact heading that labels an address rather than a way of reaching the
#: candidate. When the area and city beside it survive, it still labels them.
ADDRESS_HEADING_RE = re.compile(r"(?:address|location)[\s:|•·\-–—]*$", re.IGNORECASE)

#: A bare label such as "Mobile" or "Email". Too ordinary a word to remove on
#: its own - "Mobile" is also a skill - so it only goes when the detail it
#: labelled, on the same row, has just been removed.
BARE_CONTACT_LABEL_RE = re.compile(
    r"^[\s|•·\-–—]*"
    r"(?:e-?mail|phone|mobile|mob|cell|tel|telephone|whatsapp|linkedin)"
    r"[\s:|•·\-–—]*$",
    re.IGNORECASE,
)


# ------------------------------------------------------------- location ----

#: Personal location details are taken out of the CV's header block only - the
#: top slice of page 1, where a candidate's address and city normally sit.
#: Cities mentioned further down are almost always employer locations
#: ("Global Marketing Director | London, UK"), and deleting those would gut
#: the work history. Flip REDACT_LOCATIONS_EVERYWHERE to strip those too.
HEADER_REGION_RATIO = 0.25
REDACT_LOCATIONS_EVERYWHERE = False

#: Off by default: area, city and state stay on the CV. Everything else on an
#: address line goes. Turn this on to strip place names as well.
REDACT_CITY_AND_AREA = False

#: Words that only ever turn up in an address. A piece carrying one of these is
#: treated as part of the building without further evidence.
#:
#: Note "nagar", "colony", "layout" and the like are absent: those name an
#: area, not a street, so they stay on the CV.
ADDRESS_STRONG_RE = re.compile(
    r"\b(?:flats?|apt|apartments?|h\.?\s*no\.?|house\s*no\.?|door\s*no\.?"
    r"|plot\s*no\.?|survey\s*no\.?|khasra|pin\s*code|pincode|p\.?o\.?\s*box"
    r"|post\s*box)\b",
    re.IGNORECASE,
)

#: Words that name a street or a building but also occur in ordinary CV prose,
#: so they only count when a number sits beside them: "12 Oakwood Road" is an
#: address, "experience building scalable systems" is not.
#:
#: "building", "house", "cross", "court" and "plot" are deliberately absent
#: even from this list - "hands-on experience building...", "in-house team",
#: "cross-functional", "plot the roadmap" are all normal CV language, and a
#: number nearby is not rare enough to save them.
ADDRESS_WEAK_RE = re.compile(
    r"\b(?:streets?|st|roads?|rd|lanes?|ln|avenues?|ave|marg|gali|galli"
    r"|towers?|villas?|residency|society|bldg|block|floor|nivas|bhavan)\b"
    # Run straight onto the building name, as in "AagamNavkarBldg". Case
    # sensitive, so only a capital after a lower-case letter counts.
    r"|(?-i:(?<=[a-z])(?:Bldg|Road|Rd|Marg|Lane|Towers?|Villas?|Residency"
    r"|Society|Apartments?|Nivas|Bhavan)\b)",
    re.IGNORECASE,
)

#: An address is written in short pieces. Anything longer is a sentence.
ADDRESS_MAX_WORDS_PER_PIECE = 5
ADDRESS_MAX_WORDS_PER_LINE = 14

#: A bare door or flat number standing as its own comma-separated piece.
DOOR_NUMBER_RE = re.compile(r"^[#no.\s-]*\d+\s*[A-Za-z]?(?:[-/]\s*\d+\s*[A-Za-z]?)?$", re.IGNORECASE)

#: A wing or block letter joined to the flat number: "E-202", "B/1204". At
#: least two digits, so a visa class such as "H-1B" or "F-1" is not taken.
WING_DOOR_NUMBER_RE = re.compile(r"^[:\s]*[A-Za-z]\s*[-/]\s*(\d{2,})\s*[A-Za-z]?$")

#: A comma between two digits groups a number ("1,25,000") rather than
#: separating two pieces of an address.
PIECE_SPLIT_RE = re.compile(r"(?<!\d),|,(?!\d)")


def _pieces(text: str) -> list[str]:
    """The comma-separated pieces of a line, leaving grouped numbers whole."""
    return PIECE_SPLIT_RE.split(text)

#: A full UK postcode narrows to a handful of houses, so it goes.
UK_POSTCODE_RE = re.compile(r"\b[A-Z]{1,2}\d[A-Z\d]?\s*\d[A-Z]{2}\b", re.IGNORECASE)

#: An Indian PIN or a similar 6-digit postal code. A detail of where the
#: candidate lives rather than the name of the city, so it goes with the rest
#: of the address - but only on a line already established as one, since six
#: digits on their own are just as likely to be a salary figure.
PIN_CODE_RE = re.compile(r"\b\d{6}\b")

#: A label that announces a location: "Address:", "Current Location -",
#: "Permanent Address". A line opening with one is an address beyond doubt,
#: wherever on the CV it sits, so everything but the place name comes off it.
ADDRESS_LABEL_RE = re.compile(
    r"^[\s|•·\-–—]*"
    r"(?:(?:current|present|permanent|residential|residence|home|local"
    r"|correspondence|mailing|postal)\s+)?"
    r"(?:addresses?|addr|locations?|residence|residing\s+at|based\s+(?:in|at)"
    r"|house\s+address|city)"
    r"\b[\s.:\-–—|]*",
    re.IGNORECASE,
)

_PLACES = """
india bengaluru bangalore mumbai bombay delhi new-delhi noida gurgaon gurugram
hyderabad chennai madras kolkata calcutta pune ahmedabad surat jaipur lucknow
kanpur nagpur indore thane bhopal visakhapatnam patna vadodara ghaziabad
ludhiana agra nashik faridabad meerut rajkot varanasi srinagar aurangabad
dhanbad amritsar navi-mumbai allahabad prayagraj ranchi howrah coimbatore
jabalpur gwalior vijayawada jodhpur madurai raipur kota chandigarh guwahati
solapur mysore mysuru trivandrum thiruvananthapuram kochi cochin ernakulam
bhubaneswar dehradun goa panaji shimla jammu udaipur tirupati salem warangal
karnataka maharashtra gujarat rajasthan punjab haryana kerala telangana odisha
bihar jharkhand assam uttarakhand chhattisgarh sikkim manipur meghalaya
tripura nagaland mizoram uttar-pradesh madhya-pradesh tamil-nadu west-bengal
andhra-pradesh himachal-pradesh arunachal-pradesh
london manchester birmingham leeds glasgow liverpool bristol sheffield
edinburgh cardiff belfast oxford cambridge brighton nottingham
newcastle southampton leicester coventry
new-york brooklyn manhattan boston chicago houston dallas austin
philadelphia phoenix denver seattle portland atlanta miami orlando detroit
minneapolis charlotte nashville baltimore washington los-angeles san-francisco
san-jose san-diego las-vegas
toronto vancouver montreal ottawa calgary sydney melbourne brisbane perth
adelaide auckland wellington singapore dubai abu-dhabi sharjah doha riyadh
jeddah kuwait manama muscat
paris berlin munich hamburg frankfurt amsterdam rotterdam brussels madrid
barcelona lisbon rome milan vienna zurich geneva stockholm oslo copenhagen
helsinki dublin warsaw prague budapest athens istanbul moscow
tokyo osaka seoul beijing shanghai shenzhen guangzhou hong-kong taipei bangkok
jakarta manila kuala-lumpur colombo kathmandu dhaka karachi lahore islamabad
johannesburg cape-town nairobi lagos cairo casablanca
usa u-s-a uk u-k uae england scotland wales ireland france germany spain italy
netherlands belgium switzerland sweden norway denmark finland poland portugal
greece turkey russia china japan korea singapore malaysia thailand vietnam
indonesia philippines australia canada mexico brazil argentina chile
ny nj tx fl il oh ga nc mi wa az va md tn mo wi mn sc ky
"""

#: Two-letter state codes and ordinary English words ("in", "reading") are
#: deliberately absent: matched on a header line they would eat normal prose.
#: Multi-word places are stored hyphenated above; the pattern accepts either a
#: space or a hyphen between the parts.
PLACE_RE = re.compile(
    r"\b(?:"
    + "|".join(
        sorted(
            (place.replace("-", r"[\s-]") for place in _PLACES.split()),
            key=len,
            reverse=True,
        )
    )
    + r")\b",
    re.IGNORECASE,
)

PHONE_MIN_DIGITS = 9
PHONE_MAX_DIGITS = 15

#: Separator glyphs swallowed alongside a hit so a redacted contact line does
#: not end up as a row of orphaned bullets.
SEPARATORS = set(" \t|•·∙●◦-–—/,;")

CATEGORY_LABELS = {
    "email": "Email addresses",
    "phone": "Phone numbers",
    "url": "LinkedIn / web profiles",
    "location": "Address / location",
    "heading": "Contact headings",
}


@dataclass
class RedactionReport:
    """What was taken off the CV, so the recruiter can sanity-check it."""

    removed: dict[str, list[str]] = field(default_factory=dict)
    pages_without_text: list[int] = field(default_factory=list)
    page_count: int = 0
    #: Paper colour per page, reused so the logo band matches the CV stock.
    page_backgrounds: list[tuple[float, float, float]] = field(default_factory=list)
    #: True when the recruiter note was too long for the box and got cut.
    note_truncated: bool = False

    def add(self, category: str, text: str) -> None:
        bucket = self.removed.setdefault(category, [])
        cleaned = text.strip().strip("".join(SEPARATORS))
        if cleaned and cleaned not in bucket:
            bucket.append(cleaned)

    @property
    def total(self) -> int:
        return sum(len(v) for v in self.removed.values())

    @property
    def looks_scanned(self) -> bool:
        """True when the CV has no usable text layer to redact."""
        return self.page_count > 0 and len(self.pages_without_text) == self.page_count

    def summary_lines(self) -> list[str]:
        return [
            f"{CATEGORY_LABELS.get(cat, cat)}: {', '.join(items)}"
            for cat, items in self.removed.items()
            if items
        ]


def _sample(pix: pymupdf.Pixmap, points: Iterable[tuple[int, int]]) -> tuple[float, float, float] | None:
    """Modal colour of the given pixels, quantised so anti-aliasing groups up."""
    counts: Counter = Counter()
    n = pix.n
    for x, y in points:
        if 0 <= x < pix.width and 0 <= y < pix.height:
            idx = (y * pix.width + x) * n
            counts[
                (
                    pix.samples[idx] & 0xFC,
                    pix.samples[idx + 1] & 0xFC,
                    pix.samples[idx + 2] & 0xFC,
                )
            ] += 1
    if not counts:
        return None
    r, g, b = counts.most_common(1)[0][0]
    return (r / 255, g / 255, b / 255)


def _page_background(pix: pymupdf.Pixmap) -> tuple[float, float, float]:
    """
    The page's dominant background colour.

    Plenty of CVs sit on cream or grey stock. Filling redactions - or the logo
    band - with pure white on one of those leaves obvious bright patches.
    """
    w, h = pix.width, pix.height
    margin = max(2, min(w, h) // 40)
    points = [(x, y) for x in range(0, w, 3) for y in (margin, h - margin)]
    points += [(x, y) for y in range(0, h, 3) for x in (margin, w - margin)]
    return _sample(pix, points) or WHITE


def _rect_background(
    pix: pymupdf.Pixmap, rect: pymupdf.Rect, fallback: tuple[float, float, float]
) -> tuple[float, float, float]:
    """Colour of the paper immediately around a redaction target."""
    pad = 3
    x0, y0 = int(rect.x0), int(rect.y0)
    x1, y1 = int(rect.x1), int(rect.y1)
    points = [(x, y0 - pad) for x in range(x0, x1, 2)]
    points += [(x, y1 + pad) for x in range(x0, x1, 2)]
    points += [(x0 - pad, y) for y in range(y0, y1, 2)]
    points += [(x1 + pad, y) for y in range(y0, y1, 2)]
    return _sample(pix, points) or fallback


def _phone_spans(text: str) -> Iterable[tuple[int, int]]:
    """Yield phone-number spans, filtered down to realistic digit counts."""
    for match in PHONE_RE.finditer(text):
        digits = sum(ch.isdigit() for ch in match.group())
        if PHONE_MIN_DIGITS <= digits <= PHONE_MAX_DIGITS:
            yield match.span()


def _widen(text: str, start: int, end: int) -> tuple[int, int]:
    """Grow a span outwards over separator characters and contact labels."""
    while end < len(text) and text[end] in SEPARATORS:
        end += 1
    while start > 0 and text[start - 1] in SEPARATORS:
        start -= 1
    prefix = text[:start]
    for label in (PHONE_LABEL_RE, EMAIL_LABEL_RE):
        for match in label.finditer(prefix):
            # The label counts when only punctuation and short qualifiers sit
            # between it and the detail, so "Contact No (M): +91..." goes whole
            # rather than leaving "Contact No (M):" pointing at nothing.
            if LABEL_FILLER_RE.match(prefix, match.end()):
                start = match.start()
                break

    # Punctuation left stranded at either end of the line goes too, so taking
    # the postcode off "Ahmedabad-380001." does not leave a full stop adrift.
    if not any(ch.isalnum() for ch in text[end:]):
        end = len(text)
    if not any(ch.isalnum() for ch in text[:start]):
        start = 0
    return start, end


#: A gap between two characters wider than this share of their height is read
#: as a word break. PDFs routinely space words by moving the cursor rather than
#: by writing a space character.
CHAR_WORD_GAP_RATIO = 0.25


def _line_groups(page: pymupdf.Page) -> Iterable[tuple[str, list[tuple[int, int, pymupdf.Rect]]]]:
    """
    Rebuild each visual line as a single string, character by character.

    Contact details are routinely split across several "words" ("+44", "20",
    "7946", "0912"), so matching word-by-word misses most phone numbers. This
    yields the joined line text plus a char-offset -> rect map.

    Built from individual characters rather than from whole words so that a
    match covering part of a word redacts only that part. "Ahmedabad-380001" is
    one word to the PDF, and taking the postcode off it must not take the city
    with it. Word breaks are inferred from the spacing where the PDF has no
    space character of its own.
    """
    try:
        blocks = page.get_text("rawdict").get("blocks", [])
    except Exception:  # a damaged text layer yields nothing to redact
        return

    for block in blocks:
        if block.get("type") != 0:
            continue
        for line in block.get("lines", []):
            text_bits: list[str] = []
            spans: list[tuple[int, int, pymupdf.Rect]] = []
            cursor = 0
            previous: pymupdf.Rect | None = None
            for span in line.get("spans", []):
                for char in span.get("chars", []):
                    rect = pymupdf.Rect(char["bbox"])
                    letter = char.get("c", "")
                    if (
                        previous is not None
                        and not letter.isspace()
                        and not text_bits[-1].isspace()
                        and rect.x0 - previous.x1
                        > max(1.0, previous.height * CHAR_WORD_GAP_RATIO)
                    ):
                        # The gap itself becomes the space's rectangle, so a
                        # redaction spanning it covers the whole run of text.
                        spans.append(
                            (
                                cursor,
                                cursor + 1,
                                pymupdf.Rect(previous.x1, rect.y0, rect.x0, rect.y1),
                            )
                        )
                        text_bits.append(" ")
                        cursor += 1
                    spans.append((cursor, cursor + 1, rect))
                    text_bits.append(letter)
                    cursor += 1
                    previous = rect
            text = "".join(text_bits)
            if text.strip():
                yield text, spans


def _rects_for_span(
    spans: list[tuple[int, int, pymupdf.Rect]], start: int, end: int
) -> list[pymupdf.Rect]:
    hits = [rect for w_start, w_end, rect in spans if w_start < end and w_end > start]
    if not hits:
        return []
    merged = pymupdf.Rect(hits[0])
    for rect in hits[1:]:
        merged |= rect
    return [merged]


#: A year, or a range of them. Identical in shape to a door number, and far
#: more common on a CV: "2021", "2021 - 2023".
YEAR_RE = re.compile(r"^(?:19|20)\d{2}(?:\s*[-–—/]\s*(?:(?:19|20)\d{2}|present))?$", re.IGNORECASE)


def _is_door_number(piece: str) -> bool:
    """A bare flat or door number - but not a year, which looks identical."""
    stripped = piece.strip()
    if YEAR_RE.match(stripped):
        return False
    wing = WING_DOOR_NUMBER_RE.match(stripped)
    if wing:
        return not YEAR_RE.match(wing.group(1))
    return bool(DOOR_NUMBER_RE.match(stripped))


def _has_address_context(text: str) -> bool:
    """
    Whether the line proves it is an address, independent of street words.

    A door number, an unmistakable address word or a postcode is enough. Once
    one is present, "Prestige Towers" or "MG Road" elsewhere on the line can
    be taken at face value without a number beside it.
    """
    if re.search(r"\b\d{6}\b", text) or UK_POSTCODE_RE.search(text):
        return True
    return any(
        _is_door_number(piece.strip()) or ADDRESS_STRONG_RE.search(piece)
        for piece in _pieces(text)
    )


def _is_address_piece(piece: str, context: bool = False) -> bool:
    """
    Whether one comma-separated piece names a building or a street.

    Prose is the thing to keep out. A professional summary sitting in the top
    quarter of page 1 is in scope for this check, so "hands-on experience
    building scalable systems" must not read as an address - which is why a
    street word on its own is never enough, and why long pieces are ignored.
    """
    if not piece:
        return False
    if _is_door_number(piece):
        return True
    if len(piece.split()) > ADDRESS_MAX_WORDS_PER_PIECE:
        return False
    if ADDRESS_STRONG_RE.search(piece):
        return True
    if not ADDRESS_WEAK_RE.search(piece):
        return False
    return context or bool(re.search(r"\d", piece))


def _without_postcodes(piece: str) -> str:
    """The piece with any postal code blanked out, for judging what is left.

    "New Delhi - 110017" is a city plus a postcode, not a building. The code is
    redacted separately, so it must not make the whole piece - city and all -
    look like an address detail.
    """
    return PIN_CODE_RE.sub(" ", UK_POSTCODE_RE.sub(" ", piece))


def _names_only_a_place(piece: str) -> bool:
    """
    Whether a piece of an address line carries nothing but a place name.

    This is the keep test on a line already known to be an address: the area,
    city, state and country stay, and anything else on the line - door and flat
    numbers, floors, street and building names, postcodes - comes off. Written
    as a keep test rather than a remove test on purpose: on a confirmed address
    line, anything unrecognised is a detail of where the candidate lives.
    """
    stripped = _without_postcodes(piece).strip(" \t.,;:|-–—/")
    if not stripped:
        return True  # nothing left to remove
    if len(stripped.split()) > ADDRESS_MAX_WORDS_PER_PIECE:
        return False
    if re.search(r"\d", stripped):
        return False  # a door, flat, floor, plot or sector number
    if ADDRESS_STRONG_RE.search(stripped) or ADDRESS_WEAK_RE.search(stripped):
        return False  # names a street or a building
    return True


def _is_confirmed_address(text: str) -> tuple[bool, int]:
    """
    Whether the line is an address beyond doubt, and where the address starts.

    True for a line that announces itself with an "Address:" label, and for one
    carrying unmistakable address structure - a door number, a flat or plot
    number, or a postcode. Such a line is stripped back to its place names
    wherever it sits on the CV, because a "Personal Details" block at the foot
    of a CV is every bit as identifying as one in the header.

    The second value is the offset the address begins at, so a label can be
    taken off along with the address it introduces.
    """
    label = ADDRESS_LABEL_RE.match(text)
    if label and label.end() < len(text):
        remainder = text[label.end():]
        # A bare "Location" as a column heading introduces nothing; require
        # something address-shaped after the label.
        if PLACE_RE.search(remainder) or _has_address_context(remainder):
            return True, label.start()
    return _has_address_context(text), 0


def _looks_like_an_address(text: str) -> bool:
    """
    Whether a header line reads as an address without proving it.

    "12 Oakwood Road, Indiranagar, Bengaluru" has no flat number and no
    postcode, so nothing on it is conclusive - but short comma-separated
    pieces, most of them place names, is the shape of an address and not of a
    sentence. Deliberately narrow: this runs over the top of page 1, where a
    professional summary also lives.
    """
    pieces = [p.strip() for p in _pieces(text)]
    if len(pieces) < 2 or len(text.split()) > ADDRESS_MAX_WORDS_PER_LINE:
        return False
    if any(len(p.split()) > ADDRESS_MAX_WORDS_PER_PIECE for p in pieces):
        return False
    if not PLACE_RE.search(text):
        return False
    # Mostly place names, with at least one piece that is not. A line where
    # every piece is a place has nothing to remove; one where most pieces are
    # not ("Managed 12 people, based in London, grew the brand") is prose.
    places = sum(1 for p in pieces if _names_only_a_place(p))
    return places * 2 >= len(pieces) and places < len(pieces)


#: How far apart two lines of one address may sit, as a multiple of the line's
#: own height, and the most lines a single address is allowed to run to.
ADDRESS_BLOCK_LINE_GAP = 1.8
ADDRESS_BLOCK_MAX_LINES = 5

#: A "Label: value" row, as a Personal Details block is written: "Date of
#: Birth: 14 March 1988", "Languages known------------English, Hindi". Those
#: sit directly under an address and must not be mistaken for more of it.
#:
#: The separator has to be a colon, a run of dashes, or a spaced dash, so
#: "Ahmedabad-380001" - a city joined to its PIN - is not read as a label.
DETAIL_ROW_RE = re.compile(
    r"^[\s|•·]*"
    r"[A-Za-z][A-Za-z'’&./]*(?:[ ][A-Za-z'’&./]+){0,3}"
    r"(?:\s*:|\s*[-–—]{2,}|\s+[-–—]\s+)"
    r"\s*\S",
)


def _continues_an_address(text: str, protect: str) -> bool:
    """
    Whether a line is fragmentary enough to be another line of an address.

    An address is often typed over three or four lines - "2278, Raipur Kot ni
    rang," / "Raipur Gate," / "Ahmedabad-380001." - and only the line carrying
    the door number or the PIN proves what it is. The lines above it have to be
    recognised by company rather than by content.

    Deliberately requires evidence: a comma, a digit or a place name. Without
    that the job title sitting directly above the address would be swallowed
    with it.
    """
    stripped = text.strip()
    if not stripped or len(stripped.split()) > ADDRESS_MAX_WORDS_PER_LINE:
        return False
    if CONTACT_HEADING_RE.match(stripped):
        return False
    if _protected_spans(stripped, protect):
        return False  # the candidate's own name line is never part of it
    # A line carrying a contact detail is a contact line, not an address one -
    # it would otherwise annex the address and take the city with it.
    if EMAIL_RE.search(stripped) or URL_RE.search(stripped):
        return False
    if any(True for _span in _phone_spans(stripped)):
        return False
    if _is_confirmed_address(stripped)[0]:
        return True  # proves itself, whatever shape the line is
    if DETAIL_ROW_RE.match(stripped):
        return False  # a Personal Details row, not more of the address
    return bool(
        "," in stripped or re.search(r"\d", stripped) or PLACE_RE.search(stripped)
    )


def _address_block_lines(
    lines: list[tuple[str, float, float]], protect: str
) -> set[int]:
    """
    The lines of a multi-line address that should come off whole.

    Grows a block outwards from every line that proves it is an address, then
    keeps only its **last** line - the one carrying the city - and marks the
    rest for removal. That is what reduces

        2278, Raipur Kot ni rang,
        Raipur Gate,
        Ahmedabad-380001.

    to "Ahmedabad": the earlier lines are the door and the street however they
    are worded, and the last line is where the place name lives. A one-line
    address is left to the piece-by-piece rules, which already keep its tail.
    """
    order = sorted(range(len(lines)), key=lambda i: (lines[i][1], i))

    def joins(candidate: int, neighbour: int) -> bool:
        text, top, bottom = lines[order[candidate]]
        _n_text, n_top, n_bottom = lines[order[neighbour]]
        height = max(1.0, min(bottom - top, n_bottom - n_top))
        gap = max(top, n_top) - min(bottom, n_bottom)
        if gap > height * ADDRESS_BLOCK_LINE_GAP:
            return False
        return _continues_an_address(text, protect)

    whole: set[int] = set()
    for position, index in enumerate(order):
        if not _is_confirmed_address(lines[index][0])[0]:
            continue
        block = [position]
        above = position - 1
        while above >= 0 and len(block) < ADDRESS_BLOCK_MAX_LINES:
            if not joins(above, block[0]):
                break
            block.insert(0, above)
            above -= 1
        below = position + 1
        while below < len(order) and len(block) < ADDRESS_BLOCK_MAX_LINES:
            if not joins(below, block[-1]):
                break
            block.append(below)
            below += 1
        whole.update(
            order[p]
            for p in block[:-1]
            if not _protected_spans(lines[order[p]][0], protect)
            and not _is_known_places(lines[order[p]][0])
        )
    return whole


def _is_known_places(text: str) -> bool:
    """
    Whether a line is nothing but recognised place names, as in "Surat" above
    "Gujarat". Such a line is the city of a block, not its street, so it stays.

    Only whole pieces the place list knows count: "Raipur Gate" is a locality
    that happens to start with a city's name, and it still goes.
    """
    pieces = [p.strip(" \t.,;:|-–—/") for p in _pieces(text)]
    pieces = [p for p in pieces if p]
    return bool(pieces) and all(PLACE_RE.fullmatch(p) for p in pieces)


def _location_spans(text: str, protect: str, header: bool) -> list[tuple[int, int, str]]:
    """
    The parts of a location line that identify where the candidate lives.

    Only the building, street and postcode go. The area, city and state stay,
    so the client still sees "Koramangala, Bengaluru" - just not the door the
    candidate lives behind.

    `header` says whether the line sits in the top slice of page 1. A confirmed
    address is stripped anywhere on the CV; the weaker "looks like an address"
    shape is only trusted in the header, where a candidate's own address sits.
    """
    protected = _protected_spans(text, protect)

    def clashes(start: int, end: int) -> bool:
        return any(start < p_end and end > p_start for p_start, p_end in protected)

    confirmed, address_start = _is_confirmed_address(text)
    if not confirmed and not (header and _looks_like_an_address(text)):
        if header and REDACT_CITY_AND_AREA:
            return [
                (m.start(), m.end(), "location")
                for m in PLACE_RE.finditer(text)
                if not clashes(m.start(), m.end())
            ]
        return []

    spans: list[tuple[int, int, str]] = [
        (m.start(), m.end(), "location")
        for pattern in (UK_POSTCODE_RE, PIN_CODE_RE)
        for m in pattern.finditer(text)
    ]

    # The label goes with the address it introduced, the same way a bare
    # "Contact" heading goes once the details under it have gone.
    label = ADDRESS_LABEL_RE.match(text) if confirmed else None
    if label:
        spans.append((label.start(), label.end(), "location"))

    if "," in text:
        # An address is written in comma-separated pieces. Keep the ones that
        # name an area, a city or a state; drop every other piece.
        loose = not confirmed
        context = _has_address_context(text)
        cursor = 0
        for piece in _pieces(text):
            start, end = cursor, cursor + len(piece)
            cursor = end + 1
            stripped = piece.strip()
            if _names_only_a_place(stripped):
                continue
            # On a line that only looks like an address, a piece has to be
            # positively address-shaped before it is removed; on a confirmed
            # one, everything that is not a place name goes.
            if loose and not _is_address_piece(stripped, context):
                continue
            spans.append(
                (
                    start + (len(piece) - len(piece.lstrip())),
                    end - (len(piece) - len(piece.rstrip())),
                    "location",
                )
            )
    elif confirmed and len(text.split()) <= ADDRESS_MAX_WORDS_PER_LINE:
        # One run of words with no commas to separate the address from the
        # place. Cut from the start of the address through the last street or
        # building word, which leaves the area and city that follow it.
        matches = list(ADDRESS_STRONG_RE.finditer(text)) + list(
            ADDRESS_WEAK_RE.finditer(text)
        )
        if matches:
            spans.append((address_start, max(m.end() for m in matches), "location"))

    if REDACT_CITY_AND_AREA:
        spans += [(m.start(), m.end(), "location") for m in PLACE_RE.finditer(text)]

    return [(s, e, cat) for s, e, cat in spans if not clashes(s, e)]


def _protected_spans(text: str, protect: str) -> list[tuple[int, int]]:
    """Where the candidate's name sits on this line - never redacted."""
    spans: list[tuple[int, int]] = []
    if not protect:
        return spans
    parts = [p for p in re.split(r"[^A-Za-z]+", protect) if len(p) > 2]
    for part in parts:
        for match in re.finditer(rf"\b{re.escape(part)}\b", text, re.IGNORECASE):
            spans.append(match.span())
    return spans


#: Largest thing treated as a contact icon, and how far above or below the
#: redacted text it may sit. Sized for a glyph beside a line of text, not a
#: photograph. The vertical reach is about one line, so an icon still goes when
#: its own link has wrapped onto the next line - a common CV header.
ICON_MAX_SIZE = 30.0
ICON_MAX_DISTANCE = 16.0

#: Fonts that ship nothing but icons. Any glyph drawn in one is an icon,
#: whatever character the PDF claims it is.
ICON_FONT_RE = re.compile(
    r"awesome|icomoon|glyphicons?|octicons?|entypo|ionicons?|typicons?|socicon"
    r"|themify|feather|materialicons|linearicons|simple[\s-]*line|elegant"
    r"|foundation[\s-]*icons|dripicons|fontello|iconfont|icons?[\s-]*font"
    r"|devicons?|academicons",
    re.IGNORECASE,
)

#: Characters that render as a bullet, a dash or a tick rather than a contact
#: icon. Separators are already swallowed with the text beside them, and these
#: must not pull an innocent line into the icon pass.
_NOT_ICON_CHARS = set("•·∙‣⁃–—‐‑‒―▪▫●◦○■□★☆✓✔✗✘→⇒»«※")


def _is_icon_glyph(char: str, font: str) -> bool:
    """
    Whether one character is a contact icon rather than real text.

    The GitHub, LinkedIn and envelope marks on a CV header are usually not
    pictures at all - they are glyphs from an icon font, mapped into Unicode's
    private use area or left unmapped entirely. Deleting the link text beside
    one leaves the mark behind, pointing at nothing.
    """
    if not char or char.isspace() or char.isalnum() or char in _NOT_ICON_CHARS:
        return False
    code = ord(char)
    return (
        0xE000 <= code <= 0xF8FF  # private use - where icon fonts live
        or 0xF0000 <= code <= 0x10FFFD  # supplementary private use
        or code == 0xFFFD  # a glyph the font could not map back to Unicode
        or 0x2600 <= code <= 0x27BF  # symbols and dingbats: envelope, phone
        or 0x1F300 <= code <= 0x1FAFF  # pictographs and emoji
        or bool(font and ICON_FONT_RE.search(font))
    )


def _page_characters(page: pymupdf.Page) -> list[tuple[str, str, pymupdf.Rect]]:
    """Every character on the page, with the font it is drawn in and its box."""
    chars: list[tuple[str, str, pymupdf.Rect]] = []
    try:
        blocks = page.get_text("rawdict").get("blocks", [])
    except Exception:  # a damaged text layer must not stop the redaction
        return chars
    for block in blocks:
        if block.get("type") != 0:
            continue
        for line in block.get("lines", []):
            for span in line.get("spans", []):
                font = span.get("font", "")
                for char in span.get("chars", []):
                    chars.append((char.get("c", ""), font, pymupdf.Rect(char["bbox"])))
    return chars


def _icon_glyph_rects(chars: list[tuple[str, str, pymupdf.Rect]]) -> list[pymupdf.Rect]:
    """Every character on the page that is really an icon."""
    return [rect for letter, font, rect in chars if _is_icon_glyph(letter, font)]


def _mostly_covered(rect: pymupdf.Rect, targets: list[pymupdf.Rect]) -> bool:
    """Whether a character already sits inside something being redacted."""
    area = rect.get_area()
    if area <= 0:
        return True
    return any((rect & target).get_area() > area * 0.5 for target in targets)


#: How far a redaction box is grown around an icon, so no fringe is left.
ICON_PADDING = 1.0

#: How far a redaction box may reach into a character that is staying, as a
#: share of that character's own size, and its ceiling in points.
#:
#: A box reaching about a tenth of the way into a glyph deletes the whole
#: glyph - which is how an icon sitting flush against "Pune/ Mumbai" took the
#: "P" with it. The allowance is a share rather than a fixed distance because
#: the threshold scales with the type: 0.45pt is enough to lose a letter at
#: 6pt, where it is nowhere near enough at 20pt.
ICON_SAFE_INTRUSION_RATIO = 0.05
ICON_SAFE_INTRUSION_MAX = 0.5


def _safe_icon_box(
    core: pymupdf.Rect, keepers: list[pymupdf.Rect]
) -> "pymupdf.Rect | None":
    """
    Pad an icon's box, then pull it back off any text that is staying.

    Padding is what makes the icon disappear cleanly, but on a tight header the
    pad reaches into the letter beside it and deletes it. The box is clipped to
    whichever side the letter sits on, so the icon still goes and the word does
    not.
    """
    box = pymupdf.Rect(core) + (
        -ICON_PADDING,
        -ICON_PADDING,
        ICON_PADDING,
        ICON_PADDING,
    )
    for other in keepers:
        if (box & other).is_empty:
            continue
        across = min(ICON_SAFE_INTRUSION_MAX, other.width * ICON_SAFE_INTRUSION_RATIO)
        down = min(ICON_SAFE_INTRUSION_MAX, other.height * ICON_SAFE_INTRUSION_RATIO)
        if other.x0 >= core.x1:  # the text sits to the right
            box.x1 = min(box.x1, other.x0 + across)
        elif other.x1 <= core.x0:  # to the left
            box.x0 = max(box.x0, other.x1 - across)
        elif other.y0 >= core.y1:  # below
            box.y1 = min(box.y1, other.y0 + down)
        elif other.y1 <= core.y0:  # above
            box.y0 = max(box.y0, other.y1 - down)
        elif other.x0 - core.x0 > core.x1 - other.x1:
            # The letter overlaps the icon's own box. The letter wins; clip to
            # whichever side leaves the most of the icon covered.
            box.x1 = min(box.x1, other.x0 + across)
        else:
            box.x0 = max(box.x0, other.x1 - across)

    if box.x1 - box.x0 <= 0.5 or box.y1 - box.y0 <= 0.5:
        return None  # clipped away to nothing - leave the page alone
    return box


#: A hyperlink that only ever leads to a way of contacting the candidate.
CONTACT_URI_RE = re.compile(
    r"^\s*(?:mailto:|tel:|callto:|skype:|whatsapp:|sms:)"
    r"|(?:linkedin|github|gitlab|behance|dribbble|medium|twitter|x)\.com"
    r"|wa\.me|t\.me",
    re.IGNORECASE,
)


def _contact_link_rects(page: pymupdf.Page) -> list[pymupdf.Rect]:
    """
    Where the page's contact hyperlinks sit.

    A header often hangs the link on the icon alone, with no address written
    out in text for the patterns above to match. The link's own rectangle is
    then the only thing that says where the GitHub mark is.
    """
    rects: list[pymupdf.Rect] = []
    for link in page.get_links():
        uri = link.get("uri") or ""
        rect = link.get("from")
        if rect and uri and CONTACT_URI_RE.search(uri):
            rects.append(pymupdf.Rect(rect))
    return rects


def _icon_redact_options() -> dict:
    """
    How the icon pass is allowed to treat artwork.

    Blanks the icon's pixels rather than deleting the whole image, so an icon
    that happens to be part of a larger sprite does not take the sprite with
    it, and removes vector art only when a redaction box covers it entirely.
    """
    options: dict = {
        "images": getattr(
            pymupdf, "PDF_REDACT_IMAGE_PIXELS", pymupdf.PDF_REDACT_IMAGE_REMOVE
        )
    }
    graphics = getattr(pymupdf, "PDF_REDACT_LINE_ART_REMOVE_IF_COVERED", None)
    if graphics is not None:
        options["graphics"] = graphics
    return options


_ICON_REDACT_OPTIONS = _icon_redact_options()


def _contact_icons(
    page: pymupdf.Page,
    targets: list[pymupdf.Rect],
    chars: list[tuple[str, str, pymupdf.Rect]],
) -> list[pymupdf.Rect]:
    """
    The icons belonging to the contact details that have just been removed.

    A CV header usually pairs each detail with an icon - an envelope, a phone,
    the LinkedIn and GitHub marks. Deleting the text on its own leaves a row of
    orphaned glyphs, which both looks wrong and still says where to find the
    candidate. Three kinds turn up and all three are collected: icon-font
    glyphs, small images, and small pieces of vector art.

    Anything icon-sized on, or within a line of, a row that lost contact
    details counts. Matching horizontally as well was too strict: a header
    routinely wraps, leaving the GitHub mark at the right-hand end of one row
    and the address it pointed at on the next.
    """
    if not targets:
        return []

    candidates: list[pymupdf.Rect] = _icon_glyph_rects(chars)
    try:
        for image in page.get_images(full=True):
            candidates.extend(page.get_image_rects(image[0]))
    except Exception:  # image geometry is best-effort only
        pass
    for drawing in page.get_drawings():
        rect = drawing.get("rect")
        if rect:
            candidates.append(rect)

    found: list[pymupdf.Rect] = []
    for rect in candidates:
        if rect.width > ICON_MAX_SIZE or rect.height > ICON_MAX_SIZE:
            continue
        if rect.width <= 0 or rect.height <= 0:
            continue
        middle = (rect.y0 + rect.y1) / 2
        if any(
            target.y0 - ICON_MAX_DISTANCE <= middle <= target.y1 + ICON_MAX_DISTANCE
            for target in targets
        ):
            found.append(pymupdf.Rect(rect))
    return found


def _icon_only_links(page: pymupdf.Page, link_rects: list[pymupdf.Rect]) -> list[pymupdf.Rect]:
    """
    Contact links that are drawn as an icon with no address written beside it.

    Nothing in the text layer gives these away, so the link's own rectangle is
    what gets redacted. Only taken when the rectangle is icon-sized and holds
    no readable text, so a link wrapped around a line of prose is left alone.
    """
    found: list[pymupdf.Rect] = []
    for rect in link_rects:
        if rect.width > ICON_MAX_SIZE * 2 or rect.height > ICON_MAX_SIZE * 2:
            continue
        try:
            text = page.get_textbox(rect)
        except Exception:
            continue
        if not any(ch.isalnum() for ch in text):
            found.append(pymupdf.Rect(rect))
    return found


def _values_on_row(
    number: int,
    geometry: list[tuple[str, float, float]],
    page_lines: list[tuple[str, list[tuple[int, int, pymupdf.Rect]]]],
) -> list[int]:
    """
    The lines sitting to the right of a label on the same row - its value.

    A Personal Details table is typed as "Address" in one column and
    ": E-202, ..." in the next, and the PDF keeps the two as separate lines.
    """
    _text, top, bottom = geometry[number]
    label_spans = page_lines[number][1]
    if not label_spans:
        return []
    label_right = max(rect.x1 for _s, _e, rect in label_spans)
    found: list[int] = []
    for other, (_o_text, o_top, o_bottom) in enumerate(geometry):
        o_spans = page_lines[other][1]
        if other == number or not o_spans:
            continue
        overlap = min(bottom, o_bottom) - max(top, o_top)
        if overlap <= 0.5 * min(bottom - top, o_bottom - o_top):
            continue
        if min(rect.x0 for _s, _e, rect in o_spans) >= label_right:
            found.append(other)
    return found


def _survives(text: str, hits: list[tuple[int, int, str]]) -> bool:
    """Whether anything readable is left on a line once its hits are removed."""
    gone: set[int] = set()
    for start, end, _category in hits:
        wide_start, wide_end = _widen(text, start, end)
        gone.update(range(wide_start, wide_end))
    return any(ch.isalnum() for i, ch in enumerate(text) if i not in gone)


def redact_contacts(doc: pymupdf.Document, candidate_name: str = "") -> RedactionReport:
    """
    Delete contact and personal location details from every page, in place.

    Uses real PDF redaction: `apply_redactions` strips the glyphs out of the
    content stream, so the text cannot be selected or copied back out. Drawing
    a white box would only hide it.
    """
    report = RedactionReport(page_count=doc.page_count)
    protect = candidate_name.strip()

    for page in doc:
        if not page.get_text().strip():
            report.pages_without_text.append(page.number + 1)

        # Rendered before redaction so we can match the paper colour. dpi=72
        # keeps pixel coordinates identical to PDF points.
        snapshot = page.get_pixmap(dpi=72)
        page_bg = _page_background(snapshot)
        report.page_backgrounds.append(page_bg)

        header_limit = (
            page.rect.height * HEADER_REGION_RATIO if page.number == 0 else 0.0
        )

        # Materialised rather than streamed: an address runs over several lines
        # and only one of them proves it, so the lines have to be seen together.
        page_lines = list(_line_groups(page))
        geometry = [
            (
                text,
                min((rect.y0 for _s, _e, rect in spans), default=0.0),
                max((rect.y1 for _s, _e, rect in spans), default=0.0),
            )
            for text, spans in page_lines
        ]
        address_block = _address_block_lines(geometry, protect)

        line_hits: list[list[tuple[int, int, str]]] = []
        for number, (text, spans) in enumerate(page_lines):
            found: list[tuple[int, int, str]] = []
            found += [(m.start(), m.end(), "email") for m in EMAIL_RE.finditer(text)]
            found += [(m.start(), m.end(), "url") for m in URL_RE.finditer(text)]
            found += [(s, e, "phone") for s, e in _phone_spans(text)]

            if number in address_block:
                # A street or door line of a multi-line address: nothing on it
                # is the city, so the whole line goes.
                found.append((0, len(text), "location"))
            else:
                line_top = min(rect.y0 for _s, _e, rect in spans) if spans else 0.0
                in_header = REDACT_LOCATIONS_EVERYWHERE or line_top <= header_limit
                found += _location_spans(text, protect, header=in_header)

            # An email contains an @ and dots; the URL pattern can also claim
            # part of it. Emails win, so drop overlapping URL/phone hits.
            emails = [(s, e) for s, e, cat in found if cat == "email"]
            kept = [
                (s, e, cat)
                for s, e, cat in found
                if cat == "email" or not any(s < ee and e > es for es, ee in emails)
            ]
            line_hits.append(kept)

        # Labels are judged once every detail is known, because in a table
        # ("Contact Number  : +91 ...") the label and its value are separate
        # lines of text on the same row.
        for number, (text, _spans) in enumerate(page_lines):
            if _protected_spans(text, protect):
                continue
            row = _values_on_row(number, geometry, page_lines)
            if CONTACT_HEADING_RE.match(text):
                # A bare "Contact" / "Address" heading goes wherever it sits -
                # it only ever introduced the details that have been removed.
                # An "Address" label stays while the city beside it does.
                if not (
                    ADDRESS_HEADING_RE.search(text)
                    and any(
                        _survives(page_lines[n][0], line_hits[n])
                        and any(cat == "location" for _s, _e, cat in line_hits[n])
                        for n in row
                    )
                ):
                    line_hits[number].append((0, len(text), "heading"))
            elif BARE_CONTACT_LABEL_RE.match(text) and any(
                cat in ("email", "phone", "url")
                for n in row
                for _s, _e, cat in line_hits[n]
            ):
                line_hits[number].append((0, len(text), "heading"))

        targets: list[pymupdf.Rect] = []
        for (text, spans), kept in zip(page_lines, line_hits):
            for start, end, category in kept:
                report.add(category, text[start:end])
                wide_start, wide_end = _widen(text, start, end)
                targets.extend(_rects_for_span(spans, wide_start, wide_end))

        # A contact link's own rectangle counts as a place a detail sat, so an
        # icon is still found when the link was never written out as text.
        chars = _page_characters(page)
        link_rects = _contact_link_rects(page)
        cores = _contact_icons(page, targets + link_rects, chars)
        cores += [
            rect
            for rect in _icon_only_links(page, link_rects)
            if not any(rect in found for found in cores)
        ]

        # Text that is staying, which an icon's redaction box must not reach
        # into. Characters already inside a target are going anyway.
        keepers = [
            rect
            for letter, font, rect in chars
            if not letter.isspace()
            and not _is_icon_glyph(letter, font)
            and not _mostly_covered(rect, targets)
        ]
        icons = [
            box for box in (_safe_icon_box(core, keepers) for core in cores) if box
        ]

        for rect in targets:
            # Filled with the surrounding paper colour rather than black bars,
            # so the redaction disappears into the CV's own design.
            page.add_redact_annot(rect, fill=_rect_background(snapshot, rect, page_bg))

        if targets:
            # Text only on this pass: a redaction box that happens to graze a
            # photograph must not damage it.
            page.apply_redactions(images=pymupdf.PDF_REDACT_IMAGE_NONE)

        # The little LinkedIn or GitHub glyph beside a link survives the text
        # being deleted, leaving an icon pointing at nothing, so it goes in a
        # second pass - the only one allowed to touch artwork.
        if icons:
            for rect in icons:
                page.add_redact_annot(rect, fill=_rect_background(snapshot, rect, page_bg))
            page.apply_redactions(**_ICON_REDACT_OPTIONS)

        # Hyperlinks survive text redaction - a mailto: or LinkedIn href would
        # otherwise still be sitting in the file.
        for link in page.get_links():
            page.delete_link(link)

    doc.set_metadata({})
    doc.del_xml_metadata()
    return report


# ------------------------------------------------------------- branding ----


#: A pixel this close to white, and reachable from the edge of the image, is
#: treated as background rather than part of the logo.
_WHITE_CUTOFF = 238


def _key_out_background(pix: pymupdf.Pixmap) -> bytearray:
    """
    Make the logo's white surround transparent.

    Flood-filled inwards from the border rather than thresholded globally, so
    the white lettering *inside* the logo stays opaque.
    """
    if pix.colorspace is None or pix.colorspace.n != 3:
        pix = pymupdf.Pixmap(pymupdf.csRGB, pix)
    if not pix.alpha:
        pix = pymupdf.Pixmap(pix, 1)

    w, h = pix.width, pix.height
    data = bytearray(pix.samples)
    stride = 4

    def is_bg(idx: int) -> bool:
        return (
            data[idx] >= _WHITE_CUTOFF
            and data[idx + 1] >= _WHITE_CUTOFF
            and data[idx + 2] >= _WHITE_CUTOFF
        )

    seen = bytearray(w * h)
    stack = []
    for x in range(w):
        stack.append((x, 0))
        stack.append((x, h - 1))
    for y in range(h):
        stack.append((0, y))
        stack.append((w - 1, y))

    while stack:
        x, y = stack.pop()
        if x < 0 or y < 0 or x >= w or y >= h:
            continue
        flat = y * w + x
        if seen[flat]:
            continue
        seen[flat] = 1
        idx = flat * stride
        if not is_bg(idx):
            continue
        # Zeroed as well as made transparent: MuPDF expects premultiplied RGBA.
        data[idx] = data[idx + 1] = data[idx + 2] = data[idx + 3] = 0
        stack.extend(((x + 1, y), (x - 1, y), (x, y + 1), (x, y - 1)))

    return data


def _source_pixmap(logo: "str | bytes") -> pymupdf.Pixmap:
    """Load the logo from a path or from raw uploaded bytes."""
    if isinstance(logo, (bytes, bytearray)):
        return pymupdf.Pixmap(io.BytesIO(bytes(logo)))
    return pymupdf.Pixmap(logo)


def _logo_pixmap(logo: "str | bytes") -> pymupdf.Pixmap:
    """The logo with a transparent background, for drawing over any colour."""
    pix = _source_pixmap(logo)
    data = _key_out_background(pix)
    return pymupdf.Pixmap(pymupdf.csRGB, pix.width, pix.height, bytes(data), True)


def _faded_logo(logo: "str | bytes", opacity: float) -> pymupdf.Pixmap:
    """
    The logo as a faint monochrome navy watermark.

    Recoloured rather than merely faded: the full-colour logo stays visually
    loud even at low opacity and fights the CV text for attention. Darker
    parts of the original keep more opacity, so the shape still reads.
    """
    pix = _source_pixmap(logo)
    data = _key_out_background(pix)
    navy = tuple(int(c * 255) for c in NAVY)

    for i in range(0, len(data), 4):
        if not data[i + 3]:
            continue
        lum = (data[i] * 299 + data[i + 1] * 587 + data[i + 2] * 114) // 1000
        data[i], data[i + 1], data[i + 2] = navy
        weight = 0.35 + 0.65 * (1 - lum / 255)
        alpha = int(data[i + 3] * opacity * weight)
        data[i + 3] = alpha
        # MuPDF stores RGBA pixmaps premultiplied; handing it straight colour
        # values renders the wrong hue entirely.
        scale = alpha / 255
        data[i] = int(data[i] * scale)
        data[i + 1] = int(data[i + 1] * scale)
        data[i + 2] = int(data[i + 2] * scale)

    return pymupdf.Pixmap(pymupdf.csRGB, pix.width, pix.height, bytes(data), True)


def _fit(rect: pymupdf.Rect, width: float, height: float) -> pymupdf.Rect:
    """Centre a width x height box inside rect, preserving aspect ratio."""
    scale = min(rect.width / width, rect.height / height)
    w, h = width * scale, height * scale
    x = rect.x0 + (rect.width - w) / 2
    y = rect.y0 + (rect.height - h) / 2
    return pymupdf.Rect(x, y, x + w, y + h)


def _draw_watermark(page: pymupdf.Page, faded: pymupdf.Pixmap) -> None:
    target_width = page.rect.width * WATERMARK_SCALE
    box = _fit(
        pymupdf.Rect(
            page.rect.x0,
            page.rect.y0,
            page.rect.x1,
            page.rect.y1,
        ),
        faded.width,
        faded.height,
    )
    scale = target_width / box.width
    centre = ((box.x0 + box.x1) / 2, (box.y0 + box.y1) / 2)
    half_w = box.width * scale / 2
    half_h = box.height * scale / 2
    rect = pymupdf.Rect(
        centre[0] - half_w, centre[1] - half_h, centre[0] + half_w, centre[1] + half_h
    )
    page.insert_image(rect, pixmap=faded, overlay=True, keep_proportion=True)


def _draw_header_band(
    page: pymupdf.Page, logo: pymupdf.Pixmap, paper: tuple[float, float, float]
) -> None:
    """
    A letterhead strip carrying the logo, closed by a tan rule.

    Filled with the CV's own paper colour rather than white, so it reads as
    part of the candidate's page instead of a banner stuck on top of it.
    """
    band = pymupdf.Rect(0, 0, page.rect.width, HEADER_BAND_HEIGHT)
    page.draw_rect(band, color=None, fill=paper)
    page.draw_line(
        pymupdf.Point(0, HEADER_BAND_HEIGHT),
        pymupdf.Point(page.rect.width, HEADER_BAND_HEIGHT),
        color=TAN,
        width=1.5,
    )

    logo_box = _fit(
        pymupdf.Rect(36, 5, 36 + 110, HEADER_BAND_HEIGHT - 8),
        logo.width,
        logo.height,
    )
    logo_box = pymupdf.Rect(36, logo_box.y0, 36 + logo_box.width, logo_box.y1)
    page.insert_image(logo_box, pixmap=logo, keep_proportion=True)

    page.insert_textbox(
        pymupdf.Rect(page.rect.width - 230, HEADER_BAND_HEIGHT / 2 - 7, page.rect.width - 28, HEADER_BAND_HEIGHT),
        "Presented by Rishi Jobs",
        fontname=FONT_BOLD,
        fontsize=9,
        color=NAVY,
        align=pymupdf.TEXT_ALIGN_RIGHT,
    )


# --------------------------------------------------------- details box ----

#: A hike above this much over the current salary is not printed as a figure.
SALARY_HIKE_THRESHOLD = 0.30

#: Shown in place of an expected salary that exceeds the threshold.
SALARY_AS_PER_NORMS = "As per industry norms"

#: Ceiling on the details box, as a fraction of page height. The recruiter
#: note is usually the long part, so it shrinks to fit rather than pushing the
#: CV itself down into the footer.
DETAILS_MAX_HEIGHT_RATIO = 0.42

_NOTE_SIZES = (10.5, 10, 9.5, 9, 8.5, 8)


#: Salary fields are entered in LPA (lakhs per annum) and nothing else, so
#: the figures stay comparable. Digits with an optional decimal part only.
SALARY_RE = re.compile(r"\d+(?:\.\d+)?")
SALARY_UNIT = "LPA"


def parse_salary(text: str) -> "float | None":
    """The LPA figure, or None when the field is blank or not a plain number."""
    text = text.strip()
    if not text or not SALARY_RE.fullmatch(text):
        return None
    return float(text)


def format_salary(text: str) -> str:
    """How a salary figure appears on the CV."""
    value = parse_salary(text)
    if value is None:
        return text.strip() or "Not specified"
    trimmed = f"{value:g}"
    return f"{trimmed} {SALARY_UNIT}"


def is_above_norm(current: str, expected: str) -> bool:
    """True when the expected salary is more than 30% above the current one."""
    current_value = parse_salary(current)
    expected_value = parse_salary(expected)
    if not current_value or expected_value is None:
        return False
    return expected_value > current_value * (1 + SALARY_HIKE_THRESHOLD)


@dataclass
class CvDetails:
    candidate_name: str
    current_salary: str = ""
    expected_salary: str = ""
    notice_period: str = ""
    recruiter_note: str = ""

    @classmethod
    def from_dict(cls, payload: dict) -> "CvDetails":
        errors = []
        name = str(payload.get("candidate_name", "")).strip()
        if not name:
            errors.append("Candidate name is required.")

        salaries = {}
        for key, label in (
            ("current_salary", "Current salary"),
            ("expected_salary", "Expected salary"),
        ):
            raw = str(payload.get(key, "")).strip()
            if raw and not SALARY_RE.fullmatch(raw):
                errors.append(
                    f"{label} must be a number in {SALARY_UNIT} - digits only, "
                    "with an optional decimal point (e.g. 18 or 18.5)."
                )
            salaries[key] = raw

        note = str(payload.get("recruiter_note", "")).strip()
        if not note:
            errors.append("A recruiter note is required.")
        elif note[0].islower():
            note = note[0].upper() + note[1:]

        if errors:
            raise ValueError(errors)
        return cls(
            candidate_name=name,
            current_salary=salaries["current_salary"],
            expected_salary=salaries["expected_salary"],
            notice_period=str(payload.get("notice_period", "")).strip(),
            recruiter_note=note,
        )

    @property
    def expected_salary_display(self) -> str:
        """
        The expected salary as it should appear on the branded CV.

        Left blank reads as "As per industry norms" as well: a recruiter who
        does not fill it in is declining to name a figure, which is the same
        thing the 30% rule is saying.
        """
        if not self.expected_salary or is_above_norm(
            self.current_salary, self.expected_salary
        ):
            return SALARY_AS_PER_NORMS
        return format_salary(self.expected_salary)

    def columns(self) -> list[tuple[str, str]]:
        return [
            ("Current Salary", format_salary(self.current_salary)),
            ("Expected Salary", self.expected_salary_display),
            ("Notice Period", self.notice_period or "Not specified"),
        ]


def _measure(
    size: pymupdf.Rect, rect: pymupdf.Rect, text: str, fontsize: float, bold: bool = False
) -> float:
    """
    Height the text actually needs, measured on a throwaway page.

    The scratch page gets its own Typeface: fonts are registered per page, so
    measuring with the real page's font name would silently miss.
    """
    scratch = pymupdf.open()
    try:
        page = scratch.new_page(width=size.width, height=size.height)
        type_ = Typeface(page)
        leftover = page.insert_textbox(
            rect,
            type_.text(text),
            fontname=type_.bold if bold else type_.regular,
            fontsize=fontsize,
        )
        if leftover < 0:  # did not fit at all
            return rect.height
        return rect.height - leftover
    finally:
        scratch.close()


def _fits(size: pymupdf.Rect, rect: pymupdf.Rect, text: str, fontsize: float) -> bool:
    """Whether the text can be drawn in the rect at all."""
    scratch = pymupdf.open()
    try:
        page = scratch.new_page(width=size.width, height=size.height)
        type_ = Typeface(page)
        return (
            page.insert_textbox(
                rect, type_.text(text), fontname=type_.regular, fontsize=fontsize
            )
            >= 0
        )
    finally:
        scratch.close()


def _truncate_to_fit(
    size: pymupdf.Rect, rect: pymupdf.Rect, text: str, fontsize: float
) -> tuple[str, bool]:
    """
    Cut the text at a word boundary until it fits, and say whether it was cut.

    PyMuPDF draws *nothing* when a textbox overflows, so an over-long recruiter
    note would otherwise vanish and leave an empty panel on the CV.
    """
    if _fits(size, rect, text, fontsize):
        return text, False

    low, high = 0, len(text)
    while low < high:
        middle = (low + high + 1) // 2
        if _fits(size, rect, text[:middle].rstrip() + " ...", fontsize):
            low = middle
        else:
            high = middle - 1

    cut = text[:low].rstrip()
    spaced = cut.rsplit(" ", 1)
    if len(spaced) == 2 and len(spaced[0]) > 40:
        cut = spaced[0]
    return cut.rstrip(" ,;:.") + " ...", True


def _draw_details_box(
    page: pymupdf.Page, details: CvDetails, type_: Typeface, top: float
) -> tuple[float, bool]:
    """
    Draw the recruiter's details on the CV page itself and return its bottom.

    Laid out as a single row of three columns - current salary, expected
    salary, notice period - with the recruiter note on its own full-width row
    underneath, since the note is normally far longer than the rest.
    """
    size = page.rect
    margin = 38.0
    pad = 16.0
    strip_height = 20.0

    box = pymupdf.Rect(margin, top, size.width - margin, top + 10)
    inner_width = box.width - 2 * pad

    # --- measure first, so the panel can be drawn behind the text ----------
    columns = details.columns()
    column_width = inner_width / len(columns)
    value_width = column_width - 12

    value_height = max(
        _measure(size, pymupdf.Rect(0, 0, value_width, 60), value, 11.5, bold=True)
        for _label, value in columns
    )
    columns_height = 11 + value_height + 6

    note_size = _NOTE_SIZES[0]
    note_height = 0.0
    if details.recruiter_note:
        budget = size.height * DETAILS_MAX_HEIGHT_RATIO - (
            strip_height + pad + columns_height + 28 + pad
        )
        for candidate_size in _NOTE_SIZES:
            note_size = candidate_size
            note_height = _measure(
                size,
                pymupdf.Rect(0, 0, inner_width, size.height),
                details.recruiter_note,
                candidate_size,
            )
            if note_height <= budget:
                break
        note_height = min(note_height, max(budget, 30.0)) + 14  # label above it

    total = strip_height + pad + columns_height + (note_height + 14 if note_height else 0) + pad
    box = pymupdf.Rect(box.x0, top, box.x1, top + total)

    # --- draw --------------------------------------------------------------
    page.draw_rect(box, color=(0.84, 0.84, 0.86), fill=(0.98, 0.98, 0.99), width=0.7)
    page.draw_rect(
        pymupdf.Rect(box.x0, box.y0, box.x1, box.y0 + strip_height), color=None, fill=NAVY
    )
    page.insert_textbox(
        pymupdf.Rect(box.x0 + pad, box.y0 + 5.5, box.x1 - pad, box.y0 + strip_height),
        "CANDIDATE DETAILS",
        fontname=type_.bold,
        fontsize=8.5,
        color=TAN,
    )

    cursor = box.y0 + strip_height + pad
    for index, (label, value) in enumerate(columns):
        x = box.x0 + pad + index * column_width
        if index:
            page.draw_line(
                pymupdf.Point(x - 6, cursor - 2),
                pymupdf.Point(x - 6, cursor + columns_height - 4),
                color=(0.87, 0.87, 0.89),
                width=0.7,
            )
        page.insert_textbox(
            pymupdf.Rect(x, cursor, x + value_width, cursor + 12),
            label.upper(),
            fontname=type_.bold,
            fontsize=7.5,
            color=(0.45, 0.45, 0.5),
        )
        page.insert_textbox(
            pymupdf.Rect(x, cursor + 11, x + value_width, cursor + 11 + value_height + 4),
            type_.text(value),
            fontname=type_.bold,
            fontsize=11.5,
            color=NAVY,
        )
    cursor += columns_height

    truncated = False
    if note_height:
        page.draw_line(
            pymupdf.Point(box.x0 + pad, cursor + 2),
            pymupdf.Point(box.x1 - pad, cursor + 2),
            color=(0.87, 0.87, 0.89),
            width=0.7,
        )
        cursor += 14
        page.insert_textbox(
            pymupdf.Rect(box.x0 + pad, cursor, box.x1 - pad, cursor + 12),
            "RECRUITER NOTE",
            fontname=type_.bold,
            fontsize=7.5,
            color=(0.45, 0.45, 0.5),
        )
        note_rect = pymupdf.Rect(box.x0 + pad, cursor + 14, box.x1 - pad, box.y1 - 4)
        note_text, truncated = _truncate_to_fit(
            size, note_rect, details.recruiter_note, note_size
        )
        page.insert_textbox(
            note_rect,
            type_.text(note_text),
            fontname=type_.regular,
            fontsize=note_size,
            color=INK,
        )

    return box.y1, truncated


# -------------------------------------------------------------- pipeline ---


@dataclass
class _Layout:
    """Where the next piece of CV goes, carried across every source page."""

    out: pymupdf.Document
    papers: list
    page: "pymupdf.Page | None" = None
    out_y: float = 0.0
    sheets: int = 0


def _flow_page(
    layout: _Layout,
    src: pymupdf.Document,
    src_page: pymupdf.Page,
    paper: tuple[float, float, float],
    is_first: bool,
    box_bottom: float,
) -> None:
    """
    Lay one source page out, continuing wherever the last one finished.

    The page is placed as a series of content runs rather than in one piece,
    which does two things: an oversized gap between runs - the hole left where
    a contact block used to be - is squeezed back to MAX_CONTENT_GAP, and a
    page break lands between runs instead of through a line of text.

    The sheet in progress carries over, so a source page that overruns by a few
    lines no longer leaves most of a sheet blank before the next one starts.
    """
    out = layout.out
    papers = layout.papers
    size = src_page.rect
    segments = _content_segments(src_page)

    if not segments:
        # Nothing measurable on the sheet - an image-only page, or a genuinely
        # blank one. Place it once, whole, rather than splitting empty space.
        page = out.new_page(width=size.width, height=size.height)
        page.draw_rect(page.rect, color=None, fill=paper)
        papers.append(paper)
        layout.sheets += 1
        layout.page = None  # it fills its own sheet; the next page starts clean
        top = box_bottom if is_first else HEADER_BAND_HEIGHT + CONTENT_TOP_GAP
        # Scaled to fit rather than split: there is no text to break between,
        # and clipping a scanned page would lose whatever is at the bottom of
        # it. SHRINK_TOLERANCE does not apply for that reason.
        plain = min(1.0, (size.y1 - top) / size.height)
        placed = size.width * plain
        page.show_pdf_page(
            pymupdf.Rect(
                (size.width - placed) / 2,
                top,
                (size.width - placed) / 2 + placed,
                top + size.height * plain,
            ),
            src,
            src_page.number,
        )
        return

    gaps = [
        min(segments[i][0] - segments[i - 1][1], MAX_CONTENT_GAP)
        for i in range(1, len(segments))
    ]

    used = sum(end - start for start, end in segments) + sum(gaps)
    first_top = box_bottom if is_first else HEADER_BAND_HEIGHT + CONTENT_TOP_GAP
    scale = _content_scale(size, first_top, used)

    width = size.width * scale
    left = (size.width - width) / 2
    fresh_top = HEADER_BAND_HEIGHT + CONTINUATION_TOP_GAP
    fresh_available = size.y1 - fresh_top

    # Carry on down the sheet already in progress when a useful amount of it is
    # still free; otherwise leave it and start clean.
    if layout.page is not None:
        if size.y1 - layout.out_y >= SOURCE_PAGE_MIN_SPACE:
            layout.out_y += SOURCE_PAGE_GAP
        else:
            layout.page = None

    def start_page():
        layout.page = out.new_page(width=size.width, height=size.height)
        layout.page.draw_rect(layout.page.rect, color=None, fill=paper)
        papers.append(paper)
        layout.out_y = first_top if layout.sheets == 0 else fresh_top
        layout.sheets += 1

    for index, (segment_start, segment_end) in enumerate(segments):
        if layout.page is not None and index:
            # Keep a run whole where it would otherwise be split for the sake
            # of a few points, but only when it fits on a page of its own.
            scaled = (segment_end - segment_start) * scale
            if (
                layout.out_y + gaps[index - 1] * scale + scaled > size.y1
                and scaled <= fresh_available
            ):
                layout.page = None
            else:
                layout.out_y += gaps[index - 1] * scale

        cursor = segment_start
        remaining = segment_end - cursor
        while remaining > 0.5:
            if layout.page is None:
                start_page()

            available = size.y1 - layout.out_y
            # Too little room left to be worth using - carry on overleaf. Only
            # when something already sits above, so a freshly started sheet is
            # never abandoned and the loop always makes progress.
            if available < BLANK_PAGE_TOLERANCE and layout.out_y > fresh_top:
                layout.page = None
                continue

            part = min(remaining, available / scale)
            if part < remaining - 0.5:
                part = _safe_split(src_page, cursor, part)

            layout.page.show_pdf_page(
                pymupdf.Rect(
                    left, layout.out_y, left + width, layout.out_y + part * scale
                ),
                src,
                src_page.number,
                clip=pymupdf.Rect(size.x0, cursor, size.x1, cursor + part),
            )

            layout.out_y += part * scale
            cursor += part
            remaining -= part
            if remaining > 0.5:
                layout.page = None  # the rest carries on overleaf

    if layout.sheets == 0:
        start_page()  # an empty source page still gets a sheet


def _details_box_height(size: pymupdf.Rect, details: CvDetails) -> float:
    """Height the details box will take, measured on a throwaway page."""
    scratch = pymupdf.open()
    try:
        page = scratch.new_page(width=size.width, height=size.height)
        return _draw_details_box(page, details, Typeface(page), 0.0)[0]
    finally:
        scratch.close()


def _content_bottom(page: pymupdf.Page) -> float:
    """
    How far down the page the CV's content actually reaches.

    Measured rather than assumed, so a half-empty page does not drag a sheet of
    blank paper along behind it. Full-page fills and images are skipped: a CV
    printed on cream stock has a background rectangle covering the whole page,
    which would otherwise make every CV look full to the last millimetre.
    """
    page_area = page.rect.width * page.rect.height
    bottom = page.rect.y0

    for word in page.get_text("words"):
        bottom = max(bottom, word[3])

    for drawing in page.get_drawings():
        rect = drawing.get("rect")
        if rect and rect.width * rect.height < page_area * 0.9:
            bottom = max(bottom, rect.y1)

    try:
        for image in page.get_images(full=True):
            for rect in page.get_image_rects(image[0]):
                if rect.width * rect.height < page_area * 0.9:
                    bottom = max(bottom, rect.y1)
    except Exception:  # image geometry is best-effort only
        pass

    if bottom <= page.rect.y0 + 1:
        return page.rect.y1  # nothing measurable - treat the page as full
    return min(bottom + 2, page.rect.y1)


def _content_segments(page: pymupdf.Page) -> list[tuple[float, float]]:
    """
    Contiguous runs of content, top to bottom.

    Runs are separated only where the page is empty for more than
    MAX_CONTENT_GAP, so ordinary line and section spacing stays inside a run
    and is reproduced exactly. The gaps between runs are the ones worth
    closing. Full-page background fills are ignored, as in _content_bottom.
    """
    page_area = page.rect.width * page.rect.height
    spans: list[list[float]] = [[word[1], word[3]] for word in page.get_text("words")]

    for drawing in page.get_drawings():
        rect = drawing.get("rect")
        if rect and rect.width * rect.height < page_area * 0.9:
            spans.append([rect.y0, rect.y1])

    try:
        for image in page.get_images(full=True):
            for rect in page.get_image_rects(image[0]):
                if rect.width * rect.height < page_area * 0.9:
                    spans.append([rect.y0, rect.y1])
    except Exception:  # image geometry is best-effort only
        pass

    if not spans:
        return []

    spans.sort()
    merged = [spans[0]]
    for start, end in spans[1:]:
        if start <= merged[-1][1] + MAX_CONTENT_GAP:
            merged[-1][1] = max(merged[-1][1], end)
        else:
            merged.append([start, end])
    return [(start, end) for start, end in merged]


def _content_scale(size: pymupdf.Rect, content_top: float, used: float) -> float:
    """
    How much to scale the CV's own content.

    `used` is how far the content actually reaches, so a page with room to
    spare simply fits. Otherwise the CV is only shrunk when that costs almost
    nothing - a page is kept at full size and allowed to run onto a second
    sheet instead, because shrinking a whole CV to clear the details box
    leaves the text too small to read.
    """
    available = size.height - content_top
    if used <= available:
        return 1.0
    fit = min(1.0, available / used)
    return fit if fit >= SHRINK_TOLERANCE else 1.0


def _safe_split(src_page: pymupdf.Page, top: float, height: float) -> float:
    """
    Trim a band so it ends between two lines of text, never through one.

    Splitting a page at a fixed height would cut a row of text in half, leaving
    its top on one page and its bottom on the next.
    """
    limit = top + height
    words = [w for w in src_page.get_text("words") if w[3] > top and w[1] < limit]
    if not words:
        return height

    floor = top + height * 0.35  # never shave off more than this
    boundary = limit
    for _attempt in range(200):
        crossing = [w for w in words if w[1] < boundary - 0.5 < w[3]]
        if not crossing:
            break
        highest = min(w[1] for w in crossing) - 0.5
        if highest <= floor:
            return height  # too crowded to split cleanly; take the straight cut
        boundary = highest

    return max(boundary - top, height * 0.35)


def generate_branded_cv(
    pdf_bytes: bytes,
    details: CvDetails,
    logo: "str | bytes" = LOGO_PATH,
) -> tuple[bytes, RedactionReport]:
    """Redact, brand and return the finished CV plus a report of what went."""
    try:
        src = pymupdf.open(stream=pdf_bytes, filetype="pdf")
    except Exception:
        raise ValueError("That file could not be read as a PDF.")
    try:
        if src.needs_pass:
            raise ValueError("This PDF is password protected - please supply an unlocked copy.")
        if src.page_count == 0:
            raise ValueError("This PDF has no pages.")

        report = redact_contacts(src, details.candidate_name)

        out = pymupdf.open()
        try:
            crest = _logo_pixmap(logo)
            faded = _faded_logo(logo, WATERMARK_OPACITY)

            gap = 10.0
            box_top = HEADER_BAND_HEIGHT + gap
            # Measured up front: the CV has to be placed below the box, but the
            # box is drawn last so it sits over the watermark cleanly.
            box_height = _details_box_height(src[0].rect, details)

            papers: list[tuple[float, float, float]] = []
            layout = _Layout(out=out, papers=papers)
            for src_page in src:
                paper = (
                    report.page_backgrounds[src_page.number]
                    if src_page.number < len(report.page_backgrounds)
                    else WHITE
                )
                _flow_page(
                    layout,
                    src,
                    src_page,
                    paper,
                    is_first=src_page.number == 0,
                    box_bottom=box_top + box_height + gap,
                )

            # Decoration happens once every page's content is in place: the
            # watermark sits over the CV, and the details box over both.
            for index, page in enumerate(out):
                _draw_watermark(page, faded)
                if HEADER_BAND_HEIGHT > 0:
                    _draw_header_band(page, crest, papers[index])

            _bottom, report.note_truncated = _draw_details_box(
                out[0], details, Typeface(out[0]), box_top
            )

            out.set_metadata(
                {
                    "title": f"{details.candidate_name} - CV (Rishi Jobs)",
                    "producer": "Rishi Jobs CV Tool",
                }
            )
            buffer = io.BytesIO()
            out.save(buffer, garbage=4, deflate=True)
            return buffer.getvalue(), report
        finally:
            out.close()
    finally:
        src.close()


#: Lines that are a document title rather than somebody's name.
_NOT_A_NAME = {
    "cv",
    "resume",
    "résumé",
    "curriculum",
    "vitae",
    "biodata",
    "confidential",
    # section headings, which are often the largest text after the name
    "profile",
    "summary",
    "objective",
    "experience",
    "education",
    "skills",
    "projects",
    "certifications",
    "achievements",
    "employment",
    "work",
    "career",
    "contact",
    "personal",
    "references",
    "languages",
    "interests",
    "hobbies",
    "declaration",
    "qualifications",
    "training",
    "awards",
    "publications",
}


def _looks_like_a_name(text: str) -> bool:
    if not 2 <= len(text) <= 60:
        return False
    if any(ch.isdigit() for ch in text) or "@" in text:
        return False
    words = [w for w in re.split(r"\s+", text.strip()) if w]
    if not 1 <= len(words) <= 5:
        return False
    if any(word.strip(".,").lower() in _NOT_A_NAME for word in words):
        return False
    return all(re.fullmatch(r"[A-Za-z][A-Za-z.'\-]*", word) for word in words)


def guess_candidate_name(pdf_bytes: bytes) -> str:
    """
    Best guess at the candidate's name, for pre-filling the form.

    Takes the largest piece of text in the top third of page 1, which is where
    virtually every CV puts the name. Returns "" when nothing looks right - the
    recruiter can always type it.
    """
    try:
        doc = pymupdf.open(stream=pdf_bytes, filetype="pdf")
    except Exception:
        return ""
    try:
        if doc.needs_pass or doc.page_count == 0:
            return ""
        page = doc[0]
        limit = page.rect.y0 + page.rect.height * 0.33

        best_size, best_top, best_text = 0.0, 0.0, ""
        for block in page.get_text("dict").get("blocks", []):
            for line in block.get("lines", []):
                spans = line.get("spans", [])
                text = "".join(span["text"] for span in spans).strip()
                top = line["bbox"][1]
                if not text or top > limit:
                    continue
                if not _looks_like_a_name(text):
                    continue
                size = max(span["size"] for span in spans)
                if size > best_size + 0.5 or (abs(size - best_size) <= 0.5 and top < best_top):
                    best_size, best_top, best_text = size, top, text

        if not best_text:
            return ""
        # Plenty of CVs set the name in capitals; title case reads better in
        # the form and in the file name.
        if best_text.isupper():
            best_text = " ".join(
                "-".join(part.capitalize() for part in word.split("-"))
                for word in best_text.split()
            )
        return best_text
    finally:
        doc.close()


def cv_filename(details: CvDetails) -> str:
    safe = re.sub(r"[^A-Za-z0-9]+", "_", details.candidate_name).strip("_")
    return f"{safe or 'Candidate'}_RishiJobs.pdf"
