import pytest
from pydantic import ValidationError

from specter.schema import Query


def test_at_least_one_required():
    with pytest.raises(ValidationError):
        Query()


def test_phone_normalized_to_e164():
    q = Query(phone="+1 415-555-1234")
    assert q.phone == "+14155551234"


def test_phone_kept_when_unparseable():
    q = Query(phone="555-555-5555")  # no country code, can't normalize
    assert q.phone == "555-555-5555"


def test_strips_whitespace():
    q = Query(name="  Jane Doe  ")
    assert q.name == "Jane Doe"


def test_empty_strings_treated_as_none():
    with pytest.raises(ValidationError):
        Query(name="   ", username="")
