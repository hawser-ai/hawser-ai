import contextlib

import pytest
from hypothesis import given
from hypothesis import strategies as st
from pydantic import BaseModel, ValidationError

from hawser.ids import PREFIXES, InvalidId, RunId, new_id, parse_id, validate_id


@pytest.mark.parametrize("prefix", sorted(PREFIXES))
def test_new_id_round_trips(prefix: str) -> None:
    value = new_id(prefix)
    found, ulid = parse_id(value)
    assert found == prefix
    assert value == f"{prefix}_{ulid}"
    assert validate_id(value, prefix=prefix) == value


def test_unknown_prefix_rejected() -> None:
    with pytest.raises(InvalidId):
        new_id("nope")
    with pytest.raises(InvalidId):
        parse_id("nope_01J9X3K4N7Q2M8R5T6V7W8X9Y0")


def test_wrong_prefix_rejected() -> None:
    with pytest.raises(InvalidId, match="expected a 'run' id"):
        parse_id(new_id("asset"), prefix="run")


@pytest.mark.parametrize(
    "bad",
    [
        "",
        "run_",
        "run-01J9X3K4N7Q2M8R5T6V7W8X9Y0",
        "run_01J9X3K4N7Q2M8R5T6V7W8X9Y",  # 25 chars
        "run_01J9X3K4N7Q2M8R5T6V7W8X9Y0Z",  # 27 chars
        "run_01J9X3K4N7Q2M8R5T6V7W8X9IL",  # I and L are not Crockford
        "RUN_01J9X3K4N7Q2M8R5T6V7W8X9Y0",
        "run_81J9X3K4N7Q2M8R5T6V7W8X9Y0",  # first char > 7 overflows 128 bits
    ],
)
def test_malformed_rejected(bad: str) -> None:
    with pytest.raises(InvalidId):
        parse_id(bad)


@given(st.text())
def test_random_text_never_crashes_with_other_errors(text: str) -> None:
    with contextlib.suppress(InvalidId):
        parse_id(text)


def test_ids_sort_by_creation() -> None:
    ids = [new_id("evt") for _ in range(50)]
    assert ids == sorted(ids)


def test_typed_alias_in_model() -> None:
    class M(BaseModel):
        run_id: RunId

    assert M(run_id=new_id("run")).run_id.startswith("run_")
    with pytest.raises(ValidationError):
        M(run_id=new_id("asset"))
