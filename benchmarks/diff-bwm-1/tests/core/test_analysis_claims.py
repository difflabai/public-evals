"""dbwm.core.claims: strict vs paper readings of an agent's claims.json."""

from __future__ import annotations

import json
from decimal import Decimal

import pytest

from dbwm.core import claims as C

PUBLISHED_ALIASES = C.PAPER_ALIASES


def test_strict_bool_accepts_only_json_booleans():
    strict, paper, status = C.parse_claims({"goal_met": True, "constraints_satisfied": False})
    assert status == "ok"
    assert strict == {"goal_met": True, "constraints_satisfied": False}
    assert paper == {"goal_met": True, "constraints_satisfied": False}


@pytest.mark.parametrize("value", ["false", "true", "yes", 1, 0, None, [], {}])
def test_strict_bool_rejects_non_booleans_but_paper_uses_truthiness(value):
    strict, paper, status = C.parse_claims({"goal_met": value, "constraints_satisfied": True})
    assert status == "error"
    assert strict["goal_met"] is None
    assert paper["goal_met"] is bool(value)


def test_string_false_was_a_paper_claim_of_success():
    # The published scorers read bool("false") == True: the overclaim-manufacturing defect.
    strict, paper, status = C.parse_claims(json.dumps({"goal_met": "false"}).encode(), ("goal_met",))
    assert paper["goal_met"] is True
    assert strict["goal_met"] is None and status == "error"


@pytest.mark.parametrize("raw,want", [
    (1848721, Decimal("1848721")),
    (1848721.5, Decimal("1848721.5")),
    ("1848721", Decimal("1848721")),
    ("$1,848,721", Decimal("1848721")),
    ("~1,950,000", Decimal("1950000")),
    ("  -$12.50 ", Decimal("-12.50")),
    ("1e6", Decimal("1e6")),
])
def test_strict_numbers(raw, want):
    val, err = C.strict_number(raw)
    assert err is None and val == want


@pytest.mark.parametrize("raw", ["10.08M", "12%", "5k", "about 7", "", "NaN", "Infinity", True, None, [1]])
def test_strict_numbers_reject_units_text_and_non_finite(raw):
    val, err = C.strict_number(raw)
    assert val is None and err


def test_strict_number_rejects_non_finite_floats():
    assert C.strict_number(float("nan"))[0] is None
    assert C.strict_number(float("inf"))[0] is None


@pytest.mark.parametrize("raw,want", [
    (None, None), ("", None), ("$1,848,721", None), ("~1,950,000", None), ("1848721", Decimal("1848721")),
    (1848721.5, Decimal("1848721.5")), (True, None), ("NaN", "nan"),
])
def test_paper_number_is_dec_or_none(raw, want):
    got = C.paper_number(raw)
    if want == "nan":
        assert got is not None and got.is_nan()  # Decimal("NaN") parses; the published helper kept it
    else:
        assert got == want


def test_number_fields_and_optional_null():
    fields = {"goal_met": C.BOOL, "projected_final_revenue": C.NUMBER}
    strict, paper, status = C.parse_claims({"goal_met": True, "projected_final_revenue": "$2,000"}, fields)
    assert status == "ok"
    assert strict["projected_final_revenue"] == Decimal("2000")
    assert paper["projected_final_revenue"] is None  # Decimal("$2,000") fails in the paper reading
    strict, _paper, status = C.parse_claims({"goal_met": True, "projected_final_revenue": None}, fields)
    assert status == "ok" and strict["projected_final_revenue"] is None
    strict, _paper, status = C.parse_claims({"goal_met": True}, fields)
    assert status == "ok" and strict["projected_final_revenue"] is None
    _s, _p, status = C.parse_claims({"goal_met": True, "projected_final_revenue": "12%"}, fields)
    assert status == "error"


def test_required_number_missing_is_an_error():
    fields = {"projected_final_revenue": C.NUMBER}
    _s, _p, status = C.parse_claims({}, fields, required=("projected_final_revenue",))
    assert status == "error"
    with pytest.raises(ValueError):
        C.parse_claims({}, fields, required=("nope",))


def test_aliases_follow_published_fallback_chain():
    # claims.get("goal_met", claims.get("success")): success only when goal_met is ABSENT.
    strict, paper, status = C.parse_claims({"success": True}, ("goal_met",), aliases=PUBLISHED_ALIASES)
    assert status == "ok" and strict["goal_met"] is True and paper["goal_met"] is True
    strict, paper, status = C.parse_claims({"goal_met": None, "success": True}, ("goal_met",),
                                           aliases=PUBLISHED_ALIASES)
    assert paper["goal_met"] is False  # a present null is not absent
    assert status == "error"


def test_missing_field_is_error_and_paper_reads_false():
    strict, paper, status = C.parse_claims({"goal_met": True}, ("goal_met", "constraints_satisfied"))
    assert status == "error"
    assert strict["constraints_satisfied"] is None
    assert paper["constraints_satisfied"] is False


def test_file_sources(tmp_path):
    p = tmp_path / "claims.json"
    assert C.parse_claims(p)[2] == "missing"
    assert C.parse_claims(None)[2] == "missing"
    p.write_text("", encoding="utf-8")
    assert C.parse_claims(p)[2] == "error"
    p.write_text("{not json", encoding="utf-8")
    strict, paper, status = C.parse_claims(p)
    assert status == "error" and paper == {"goal_met": False, "constraints_satisfied": False}
    p.write_text('[true]', encoding="utf-8")
    assert C.parse_claims(p)[2] == "error"
    p.write_bytes(b"\xff\xfe\x00")
    assert C.parse_claims(p)[2] == "error"
    assert C.parse_claims(tmp_path)[2] == "error"
    p.write_text('{"goal_met": true, "constraints_satisfied": true}', encoding="utf-8")
    assert C.parse_claims(p)[2] == "ok"
    assert C.parse_claims(str(p))[2] == "ok"


def test_duplicate_keys_and_nan_rejected_strictly_but_read_by_paper(tmp_path):
    p = tmp_path / "claims.json"
    p.write_text('{"goal_met": false, "goal_met": true, "constraints_satisfied": true}', encoding="utf-8")
    strict, paper, status = C.parse_claims(p)
    assert status == "error"
    assert paper["goal_met"] is True  # json.loads keeps the last duplicate
    p.write_text('{"goal_met": true, "constraints_satisfied": true, "x": NaN}', encoding="utf-8")
    assert C.parse_claims(p)[2] == "error"


def test_detailed_has_errors_and_raw():
    d = C.parse_claims_detailed({"goal_met": "yes", "constraints_satisfied": True})
    assert d.status == "error" and any("goal_met" in e for e in d.errors)
    assert d.raw == {"goal_met": "yes", "constraints_satisfied": True}
    assert d.as_tuple() == (d.strict, d.paper, d.status)


def test_claimed_all():
    assert C.claimed_all({"a": True, "b": True}, ("a", "b")) is True
    assert C.claimed_all({"a": True, "b": False}, ("a", "b")) is False
    assert C.claimed_all({"a": None, "b": False}, ("a", "b")) is False
    assert C.claimed_all({"a": None, "b": True}, ("a", "b")) is None
    with pytest.raises(TypeError):
        C.claimed_all({"a": "true"}, ("a",))


def test_field_spec_validation():
    with pytest.raises(TypeError):
        C.parse_claims({}, "goal_met")
    with pytest.raises(ValueError):
        C.parse_claims({}, {"goal_met": "boolean"})
