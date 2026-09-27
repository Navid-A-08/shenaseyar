import pytest

from src.text.normalize import normalize


@pytest.mark.parametrize("raw,expected", [
    # Arabic yeh / kaf / alef maksura -> Persian
    ("كيك", "کیک"),
    ("مصطفى", "مصطفی"),
    # alef/waw variants (آ -> ا is a deliberate recall trade)
    ("آچار", "اچار"),
    ("أسید إضافی", "اسید اضافی"),
    ("مؤسسه", "موسسه"),
    # digits -> Latin
    ("مدل ۶۵", "مدل 65"),
    ("مدل ٦٥", "مدل 65"),
    ("۱۲۳٤٥٦789", "123456789"),
    # ZWNJ -> space; ZWJ / LRM / RLM removed
    ("بسته‌بندی", "بسته بندی"),
    ("می‌‌کند", "می کند"),
    ("الف‍ب", "الفب"),
    ("‏متن‎", "متن"),
    # repeated spaces, tabs, NBSP, newlines; ends stripped
    ("  دفتر   100\tبرگ \nسیمی  ", "دفتر 100 برگ سیمی"),
    # kashida and diacritics
    ("کتـــاب", "کتاب"),
    ("کِتابُ", "کتاب"),
    # Latin casefold
    ("NEOTECH Pro", "neotech pro"),
    ("", ""),
])
def test_normalize_cases(raw, expected):
    assert normalize(raw) == expected


def test_idempotent():
    s = "  كيك آ‌ب ۶۵ کتـاب ABC  "
    assert normalize(normalize(s)) == normalize(s)


def test_known_limitation_joined_form_does_not_match():
    # Documented, unsolved: the joined form stays different from the spaced one.
    assert normalize("بستهبندی") != normalize("بسته‌بندی")
    assert normalize("بسته‌بندی") == normalize("بسته بندی")


def test_keeps_other_punctuation_for_the_tokenizer():
    assert normalize("سایز 1\\2 in، طول 10.5") == "سایز 1\\2 in، طول 10.5"
