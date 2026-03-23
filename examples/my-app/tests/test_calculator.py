"""Tests for myapp.calculator."""
import pytest

from myapp.calculator import add, clamp, divide, multiply, power, subtract


class TestAdd:
    def test_integers(self):
        assert add(2, 3) == 5

    def test_floats(self):
        assert add(1.1, 2.2) == pytest.approx(3.3)

    def test_negative(self):
        assert add(-1, -1) == -2

    def test_zero(self):
        assert add(0, 5) == 5


class TestSubtract:
    def test_basic(self):
        assert subtract(10, 3) == 7

    def test_negative_result(self):
        assert subtract(3, 10) == -7


class TestMultiply:
    def test_basic(self):
        assert multiply(4, 5) == 20

    def test_by_zero(self):
        assert multiply(99, 0) == 0

    def test_floats(self):
        assert multiply(2.5, 4.0) == pytest.approx(10.0)


class TestDivide:
    def test_basic(self):
        assert divide(10, 2) == 5

    def test_float_result(self):
        assert divide(7, 2) == pytest.approx(3.5)

    def test_divide_by_zero(self):
        with pytest.raises(ZeroDivisionError):
            divide(5, 0)


class TestPower:
    def test_square(self):
        assert power(3, 2) == 9

    def test_zero_exp(self):
        assert power(99, 0) == 1

    def test_fractional_exp(self):
        assert power(4, 0.5) == pytest.approx(2.0)


class TestClamp:
    def test_in_range(self):
        assert clamp(5, 0, 10) == 5

    def test_below_low(self):
        assert clamp(-1, 0, 10) == 0

    def test_above_high(self):
        assert clamp(15, 0, 10) == 10

    def test_invalid_range(self):
        with pytest.raises(ValueError, match="must be <="):
            clamp(5, 10, 0)
