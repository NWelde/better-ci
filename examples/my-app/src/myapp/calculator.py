"""
Simple calculator module.

This is the source code that BetterCI will lint and test.
Try changing something here and run:

    betterci run --git-diff

Only the jobs whose paths match the changed files will execute.
"""
from __future__ import annotations


def add(a: float, b: float) -> float:
    """Return a + b."""
    return a + b


def subtract(a: float, b: float) -> float:
    """Return a - b."""
    return a - b


def multiply(a: float, b: float) -> float:
    """Return a * b."""
    return a * b


def divide(a: float, b: float) -> float:
    """Return a / b. Raises ZeroDivisionError if b is zero."""
    if b == 0:
        raise ZeroDivisionError("Cannot divide by zero")
    return a / b


def power(base: float, exp: float) -> float:
    """Return base ** exp."""
    return base ** exp


def clamp(value: float, low: float, high: float) -> float:
    """Clamp value to the range [low, high]."""
    if low > high:
        raise ValueError(f"low ({low}) must be <= high ({high})")
    return max(low, min(high, value))
