"""Text normalisation and verbatim-quote verification.

Quote verification is a core anti-hallucination control: whenever the model
claims to be quoting the source document (or a retrieved abstract), we check
that the quote actually occurs there.  Because PDF text extraction introduces
ligatures, soft hyphens and irregular whitespace, exact matching alone is too
brittle -- so we normalise aggressively first and fall back to a fuzzy
partial-ratio match with a high threshold.
"""

from __future__ import annotations

import re
import unicodedata

from rapidfuzz import fuzz

from app.core.enums import QuoteVerification

#: Characters PDF producers emit that should collapse to ASCII equivalents.
_TRANSLATIONS = {
    "ﬀ": "ff",
    "ﬁ": "fi",
    "ﬂ": "fl",
    "ﬃ": "ffi",
    "ﬄ": "ffl",
    "­": "",  # soft hyphen
    "‐": "-",
    "‑": "-",
    "‒": "-",
    "–": "-",
    "—": "-",
    "―": "-",
    "‘": "'",
    "’": "'",
    "‚": "'",
    "“": '"',
    "”": '"',
    "„": '"',
    "…": "...",
    " ": " ",
    " ": " ",
    " ": " ",
    "−": "-",
    "·": ".",
    "•": " ",
}
_TRANS_TABLE = str.maketrans(_TRANSLATIONS)

_WS_RE = re.compile(r"\s+")
_PUNCT_RE = re.compile(r"[^\w\s%<>=+./-]", re.UNICODE)
_HYPHEN_BREAK_RE = re.compile(r"(\w)-\s*\n\s*(\w)")

#: Minimum fuzzy score (0-100) accepted as a genuine quote.
FUZZY_QUOTE_THRESHOLD = 88.0
#: Quotes shorter than this are too generic to verify meaningfully.
MIN_VERIFIABLE_QUOTE_CHARS = 12


def normalize_unicode(text: str) -> str:
    if not text:
        return ""
    text = unicodedata.normalize("NFKC", text)
    return text.translate(_TRANS_TABLE)


def dehyphenate(text: str) -> str:
    """Join words split across a line break by a hyphen."""
    return _HYPHEN_BREAK_RE.sub(r"\1\2", text)


def clean_extracted_text(text: str) -> str:
    """Normalise PDF-extracted text while preserving paragraph structure."""
    text = normalize_unicode(text)
    text = dehyphenate(text)
    text = re.sub(r"[ \t]+", " ", text)
    text = re.sub(r"\n{3,}", "\n\n", text)
    return text.strip()


def normalize_for_match(text: str) -> str:
    """Aggressive normalisation used only for comparison, never for display."""
    text = normalize_unicode(text).lower()
    text = _PUNCT_RE.sub(" ", text)
    return _WS_RE.sub(" ", text).strip()


def collapse_whitespace(text: str) -> str:
    return _WS_RE.sub(" ", normalize_unicode(text)).strip()


def verify_quote(quote: str, haystack: str) -> tuple[QuoteVerification, float]:
    """Check that ``quote`` genuinely occurs in ``haystack``.

    Returns the verification outcome and a 0-1 match score.
    """
    if not quote or not quote.strip():
        return QuoteVerification.NOT_APPLICABLE, 0.0
    if not haystack or not haystack.strip():
        return QuoteVerification.NOT_FOUND, 0.0

    needle = normalize_for_match(quote)
    hay = normalize_for_match(haystack)
    if not needle:
        return QuoteVerification.NOT_APPLICABLE, 0.0

    if needle in hay:
        return QuoteVerification.EXACT, 1.0

    if len(needle) < MIN_VERIFIABLE_QUOTE_CHARS:
        # Too short for a trustworthy fuzzy verdict.
        return QuoteVerification.NOT_FOUND, 0.0

    score = fuzz.partial_ratio(needle, hay)
    if score >= FUZZY_QUOTE_THRESHOLD:
        return QuoteVerification.FUZZY, round(score / 100.0, 4)
    return QuoteVerification.NOT_FOUND, round(score / 100.0, 4)


def find_quote_span(quote: str, haystack: str) -> tuple[int, int] | None:
    """Best-effort character span of ``quote`` inside the original haystack."""
    if not quote or not haystack:
        return None
    idx = haystack.find(quote)
    if idx >= 0:
        return idx, idx + len(quote)
    collapsed_quote = collapse_whitespace(quote)
    collapsed_hay = collapse_whitespace(haystack)
    idx = collapsed_hay.lower().find(collapsed_quote.lower())
    if idx >= 0:
        return idx, idx + len(collapsed_quote)
    return None


def truncate(text: str, limit: int, suffix: str = "...") -> str:
    if len(text) <= limit:
        return text
    return text[: max(0, limit - len(suffix))].rstrip() + suffix


def token_estimate(text: str) -> int:
    """Cheap token estimate (~4 chars/token) for budgeting, not billing."""
    return max(1, len(text) // 4)


_SENTENCE_RE = re.compile(r"(?<=[.!?])\s+(?=[A-Z0-9])")


def split_sentences(text: str) -> list[str]:
    text = collapse_whitespace(text)
    if not text:
        return []
    return [s.strip() for s in _SENTENCE_RE.split(text) if s.strip()]


def normalize_entity_key(name: str) -> str:
    """Deduplication key for entity names.

    Case-folds, strips punctuation and common decorations so that
    ``"KRAS G12C"``, ``"KRAS-G12C"`` and ``"kras g12c"`` collapse together.
    """
    key = normalize_for_match(name)
    key = re.sub(r"\b(the|a|an)\b", " ", key)
    key = re.sub(r"[-_/]+", " ", key)
    return _WS_RE.sub(" ", key).strip()
