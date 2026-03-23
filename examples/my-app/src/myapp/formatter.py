"""
Result formatting utilities.

Demonstrates that BetterCI can scope jobs to specific subdirectories:
changes here only trigger the 'lint' and 'test' jobs, not a hypothetical
'build-docs' job scoped to docs/.
"""
from __future__ import annotations


def format_result(value: float, precision: int = 2) -> str:
    """Format a numeric result for display."""
    return f"{value:.{precision}f}"


def format_equation(a: float, op: str, b: float, result: float) -> str:
    """Format a full equation string, e.g. '3.00 + 4.00 = 7.00'."""
    return f"{format_result(a)} {op} {format_result(b)} = {format_result(result)}"
