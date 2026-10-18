import io

import pytest
from hypothesis import given
from hypothesis import strategies as st

from hawser.hashing import content_hash, hex_of, scoped_hash


def test_content_hash_known_value() -> None:
    assert content_hash(b"") == (
        "sha256:e3b0c44298fc1c149afbf4c8996fb92427ae41e4649b934ca495991b7852b855"
    )


@given(st.binary(max_size=1 << 16))
def test_stream_and_bytes_agree(data: bytes) -> None:
    assert content_hash(data) == content_hash(io.BytesIO(data))


def test_scoped_hash_differs_across_tenants_and_is_stable() -> None:
    h = content_hash(b"invoice")
    a1 = scoped_hash("ten_a", h)
    a2 = scoped_hash("ten_a", h)
    b = scoped_hash("ten_b", h)
    assert a1 == a2
    assert a1 != b
    assert a1 != h
    assert a1.startswith("sha256:")


def test_scoped_hash_rejects_bad_input() -> None:
    with pytest.raises(ValueError, match="non-empty"):
        scoped_hash("", content_hash(b"x"))
    with pytest.raises(ValueError, match="unsupported hash format"):
        scoped_hash("ten_a", "md5:abc")
    with pytest.raises(ValueError, match="malformed"):
        scoped_hash("ten_a", "sha256:zz")


def test_hex_of() -> None:
    assert hex_of(content_hash(b"x")) == content_hash(b"x")[len("sha256:") :]
