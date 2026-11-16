from app.modules.m2_paths.core import describe, normalize, partition_values, run


def test_supplier_escapes_decode():
    assert normalize("lake/dt=2026/hour=01/a%20b", "supplier") == "lake/dt=2026/hour=01/a b"


def test_native_keeps_percent_literal():
    assert normalize("lake/dt=2026/hour=01/a%20b", "native") == "lake/dt=2026/hour=01/a%20b"
    assert normalize("lake/dt=2026/hour=01/a b", "native") == "lake/dt=2026/hour=01/a b"


def test_native_keeps_backslash_literal():
    assert normalize("lake\\hour=01\\a", "native") == "lake\\hour=01\\a"
    assert partition_values("lake\\hour=01\\a", "native") == {}


def test_native_keeps_double_slash_literal():
    assert normalize("lake//dt=2026/hour=01/a", "native") == "lake//dt=2026/hour=01/a"


def test_supplier_folds_double_slash():
    assert normalize("lake//dt=2026/hour=01/a", "supplier") == "lake/dt=2026/hour=01/a"


def test_describe_preserves_raw_key():
    described = describe("lake//dt=2026/hour=01/a%20b", "supplier")
    assert described["raw_key"] == "lake//dt=2026/hour=01/a%20b"
    assert described["target"] == "lake/dt=2026/hour=01/a b"


def test_run_row_source_override():
    out = run({"paths": [
        {"key": "lake/dt=2026/hour=01/a%20b", "source": "native"},
        {"key": "lake/dt=2026/hour=01/a%20b", "source": "supplier"},
    ], "source": "supplier"})
    assert out["paths"][0] == "lake/dt=2026/hour=01/a%20b"
    assert out["paths"][1] == "lake/dt=2026/hour=01/a b"


def test_unknown_source_rejected():
    from app.errors import DomainError
    import pytest
    with pytest.raises(DomainError):
        run({"paths": ["a"], "source": "mystery"})
