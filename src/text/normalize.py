"""Persian text normalization. The ONE function applied to both queries and catalog titles.

Apply normalize() to BOTH sides, never one side only: a query normalized against raw titles (or
the reverse) silently breaks matching.

What it does, in order:
  1. Arabic letters -> Persian: ي -> ی, ى -> ی, ك -> ک
  2. Alef/waw variants: آ, أ, إ -> ا ; ؤ -> و
     DELIBERATE recall-over-precision trade: آ -> ا makes some distinct words collide
     (e.g. آب "water" and اب). The catalog mostly writes the madda-less form (اچار,
     ازمایشگاهی) while sellers type آ, so without this, BM25 misses those words. Revisit if
     precision suffers.
  3. Persian (۰-۹) and Arabic-Indic (٠-٩) digits -> Latin 0-9 (project convention)
  4. Remove kashida (ـ) and Arabic diacritics (U+064B-U+0652)
  5. ZWNJ (U+200C) -> space, so بسته‌بندی matches the catalog's بسته بندی.
     ZWJ, LRM, RLM are removed; NBSP and other whitespace become a plain space.
  6. casefold (Latin brand and model names)
  7. collapse runs of spaces, strip ends

No stemming, no synonyms, no affix handling.

Known limitation (unsolved, on purpose): the joined form بستهبندی (no space, no ZWNJ) does NOT
match بسته بندی. Fixing it needs a word list or affix rules, which is out of scope here.
"""
import re

_TRANSLATE = str.maketrans({
    "ي": "ی", "ى": "ی", "ك": "ک",
    "آ": "ا", "أ": "ا", "إ": "ا", "ؤ": "و",
    **{d: str(i) for i, d in enumerate("۰۱۲۳۴۵۶۷۸۹")},
    **{d: str(i) for i, d in enumerate("٠١٢٣٤٥٦٧٨٩")},
    "ـ": None,              # kashida
    **{chr(c): None for c in range(0x064B, 0x0653)},  # diacritics
    "‌": " ",               # ZWNJ -> space
    "‍": None,              # ZWJ
    "‎": None, "‏": None,  # LRM, RLM
})
_SPACES = re.compile(r"\s+")


def normalize(text):
    """Return the normalized form of `text` (see module docstring). Idempotent."""
    return _SPACES.sub(" ", text.translate(_TRANSLATE).casefold()).strip()
