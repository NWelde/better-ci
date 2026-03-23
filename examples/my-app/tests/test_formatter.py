"""Tests for myapp.formatter."""
from myapp.formatter import format_equation, format_result


def test_format_result_default_precision():
    assert format_result(3.14159) == "3.14"


def test_format_result_custom_precision():
    assert format_result(3.14159, precision=4) == "3.1416"


def test_format_result_integer():
    assert format_result(7.0) == "7.00"


def test_format_equation():
    result = format_equation(3, "+", 4, 7)
    assert result == "3.00 + 4.00 = 7.00"
