import pytest

from hawser.errors import (
    AdmissionError,
    BudgetExceeded,
    HawserError,
    PolicyViolation,
    ProviderError,
    ToolError,
)


def test_defaults() -> None:
    e = AdmissionError("too big")
    assert e.code == "admission_rejected"
    assert e.retryable is False
    assert e.details == {}
    assert str(e) == "[admission_rejected] too big"


def test_code_narrowing_and_retryable_override() -> None:
    e = AdmissionError("encrypted", code="pdf_encrypted", details={"pages": 3})
    assert e.code == "pdf_encrypted"
    p = ProviderError("boom")
    assert p.retryable is True
    assert ProviderError("bad request", retryable=False).retryable is False


@pytest.mark.parametrize(
    "error",
    [
        HawserError("base"),
        AdmissionError("a", code="pdf_corrupt"),
        PolicyViolation("p", details={"model": "x"}),
        BudgetExceeded("b", run_id="run_01J9X3K4N7Q2M8R5T6V7W8X9Y0"),
        ProviderError("r", retryable=False, step_id="step_01J9X3K4N7Q2M8R5T6V7W8X9Y0"),
        ToolError("t"),
    ],
)
def test_round_trip(error: HawserError) -> None:
    data = error.to_dict()
    back = HawserError.from_dict(data)
    assert type(back) is type(error)
    assert back.to_dict() == data


def test_unknown_type_falls_back_to_base() -> None:
    back = HawserError.from_dict({"type": "Mystery", "code": "x", "message": "m"})
    assert type(back) is HawserError
    assert back.code == "x"


def test_with_context_preserves_identity() -> None:
    e = ToolError("t")
    same = e.with_context(run_id="run_01J9X3K4N7Q2M8R5T6V7W8X9Y0")
    assert same is e
    assert e.run_id == "run_01J9X3K4N7Q2M8R5T6V7W8X9Y0"
