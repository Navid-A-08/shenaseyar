"""src/detect/legal.py: schema validation, inclusive->half-open validity, lookup by code and date."""
import copy
import json

import pytest

from src.detect.legal import UNITS_PATH, LegalUnits, LegalUnitsError

UNIT = {"id": "U1", "kind": "article", "title": "عنوان", "body": "متن", "valid_from": "1402-01-01",
        "valid_to": "1403-12-30", "source_url": "https://example.invalid/u1",
        "applies_to": ["T2"], "status": "reviewed"}


def _load(tmp_path, units, schema_version=1):
    p = tmp_path / "units.json"
    p.write_text(json.dumps({"schema_version": schema_version, "units": units},
                            ensure_ascii=False), encoding="utf-8")
    return LegalUnits.load(p)


def test_committed_file_loads_and_is_all_placeholders():
    units = LegalUnits.load()
    assert len(units.units) >= 2
    assert all(u.is_placeholder for u in units.units)          # Claude writes no legal text
    assert all(u.title.startswith("TODO(legal)") and u.body.startswith("TODO(legal)")
               for u in units.units)
    assert json.loads(UNITS_PATH.read_text(encoding="utf-8"))["_doc"]


def test_valid_to_is_inclusive_and_converted_once(tmp_path):
    u = _load(tmp_path, [UNIT]).get("U1")
    assert (u.valid_to, u.valid_to_excl) == ("1403-12-30", "1404-01-01")
    assert u.in_force("1402-01-01") and u.in_force("1403-12-30")
    assert not u.in_force("1404-01-01") and not u.in_force("1401-12-29")


def test_for_finding_filters_by_code_and_date_reviewed_first(tmp_path):
    later = dict(UNIT, id="U2", valid_from="1404-01-01", valid_to=None)
    todo = dict(UNIT, id="A0", status="TODO(legal)", valid_to=None)
    units = _load(tmp_path, [UNIT, later, todo])
    assert [u.id for u in units.for_finding("T2", "1403-06-01")] == ["U1", "A0"]
    assert [u.id for u in units.for_finding("T2", "1405-06-01")] == ["U2", "A0"]
    assert units.for_finding("T6", "1403-06-01") == []


@pytest.mark.parametrize("change", [
    {"kind": "chapter"}, {"status": "draft"}, {"applies_to": []}, {"applies_to": ["T9"]},
    {"valid_from": "1402/01/01"}, {"valid_to": "1401-01-01"}, {"title": " "},
    {"source_url": ""},                                  # reviewed needs a source
])
def test_invalid_units_are_rejected(tmp_path, change):
    with pytest.raises(LegalUnitsError):
        _load(tmp_path, [dict(UNIT, **change)])


def test_missing_or_extra_field_rejected(tmp_path):
    missing = copy.deepcopy(UNIT)
    del missing["body"]
    with pytest.raises(LegalUnitsError):
        _load(tmp_path, [missing])
    with pytest.raises(LegalUnitsError):
        _load(tmp_path, [dict(UNIT, superseded_by=None)])


def test_duplicate_ids_and_bad_schema_rejected(tmp_path):
    with pytest.raises(LegalUnitsError):
        _load(tmp_path, [UNIT, UNIT])
    with pytest.raises(LegalUnitsError):
        _load(tmp_path, [UNIT], schema_version=2)
