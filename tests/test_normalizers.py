"""Unit tests for the pure numeric normalizers in Engine.py."""

import pytest

from Engine import normalize_numeric_token, normalize_integer_token


class TestNormalizeNumericToken:
    @pytest.mark.parametrize(
        "token,expected",
        [
            ("15000", 15000.0),
            # Indonesian thousand separator: dot as thousands sep
            ("15.000", 15000.0),
            ("Rp 1.234.567", 1234567.0),
            # Decimal comma (last comma group != 3 digits)
            ("15,5", 15.5),
            # European format: dot thousands, comma decimal
            ("1.234,56", 1234.56),
            # US format: comma thousands, dot decimal
            ("1,234.56", 1234.56),
            # Multiple commas as thousands separators
            ("1,234,567", 1234567.0),
            ("500", 500.0),
            ("-500", -500.0),
        ],
    )
    def test_valid_tokens(self, token, expected):
        assert normalize_numeric_token(token) == expected

    @pytest.mark.parametrize(
        "token",
        [None, "", "   ", "abc", "Rp", "--"],
    )
    def test_invalid_tokens(self, token):
        assert normalize_numeric_token(token) is None


class TestNormalizeIntegerToken:
    @pytest.mark.parametrize(
        "token,expected",
        [("3", 3), ("03", 3), ("12.000", 12000)],
    )
    def test_valid_integers(self, token, expected):
        assert normalize_integer_token(token) == expected

    @pytest.mark.parametrize("token", ["3.5", "abc", None])
    def test_invalid_integers(self, token):
        assert normalize_integer_token(token) is None
