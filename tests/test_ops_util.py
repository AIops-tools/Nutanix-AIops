"""Unit tests for the shared ops helpers (_util).

Pure functions — no connection needed. Proves the v4 payload normalisers
(``as_list`` / ``as_obj``) unwrap both the ``{"data": ...}`` envelope and bare
shapes, drop non-dict rows, that ``_seg`` URL-encodes hostile path segments so an
id can never traverse endpoints, and that ``ext_id`` falls back across the
legacy id field names.
"""

import pytest

from nutanix_aiops.ops._util import _seg, as_list, as_obj, ext_id, s


@pytest.mark.unit
def test_as_list_unwraps_envelope_bare_array_and_none():
    assert as_list({"data": [{"a": 1}, "junk", {"b": 2}]}) == [{"a": 1}, {"b": 2}]
    assert as_list([{"a": 1}, 5]) == [{"a": 1}]  # bare array, non-dict dropped
    assert as_list(None) == []
    assert as_list({"data": None}) == []


@pytest.mark.unit
def test_as_obj_unwraps_data_or_returns_shape_or_empty():
    assert as_obj({"data": {"extId": "x"}}) == {"extId": "x"}
    assert as_obj({"extId": "y"}) == {"extId": "y"}  # no data key → the dict itself
    assert as_obj({"data": [1, 2]}) == {"data": [1, 2]}  # data not a dict → whole thing
    assert as_obj("not-a-dict") == {}


@pytest.mark.unit
def test_seg_encodes_traversal_and_slashes():
    assert _seg("../other") == "..%2Fother"
    assert _seg("a b/c") == "a%20b%2Fc"


@pytest.mark.unit
def test_ext_id_prefers_extid_then_legacy_fallbacks():
    assert ext_id({"extId": "e1"}) == "e1"
    assert ext_id({"uuid": "u1"}) == "u1"
    assert ext_id({"ext_id": "legacy"}) == "legacy"
    assert ext_id({}) == ""


@pytest.mark.unit
def test_s_bounds_and_stringifies_none():
    assert s(None) == ""
    assert s(123) == "123"
    assert len(s("x" * 500, 10)) == 10
