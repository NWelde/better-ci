"""Console output formatting for BetterCI.

Uses ANSI colors when the terminal supports them (isatty check).
Respects the NO_COLOR environment variable convention.
"""
from __future__ import annotations

import os
import sys
from typing import Optional


# ---------------------------------------------------------------------------
# ANSI color helpers
# ---------------------------------------------------------------------------

def _supports_color() -> bool:
    """Return True if the terminal supports ANSI color codes."""
    if os.environ.get("NO_COLOR"):
        return False
    if os.environ.get("FORCE_COLOR"):
        return True
    return hasattr(sys.stdout, "isatty") and sys.stdout.isatty()


_COLOR = _supports_color()

# Styles
_RESET  = "\033[0m"  if _COLOR else ""
_BOLD   = "\033[1m"  if _COLOR else ""
_DIM    = "\033[2m"  if _COLOR else ""
# Foreground colors
_GREEN  = "\033[32m" if _COLOR else ""
_YELLOW = "\033[33m" if _COLOR else ""
_RED    = "\033[31m" if _COLOR else ""
_CYAN   = "\033[36m" if _COLOR else ""
_BLUE   = "\033[34m" if _COLOR else ""
_GRAY   = "\033[90m" if _COLOR else ""
_WHITE  = "\033[97m" if _COLOR else ""


def _c(color: str, text: str) -> str:
    return f"{color}{text}{_RESET}" if _COLOR else text


def _fmt_elapsed(seconds: float) -> str:
    if seconds < 60:
        return f"{seconds:.1f}s"
    m = int(seconds // 60)
    s = seconds % 60
    return f"{m}m{s:.0f}s"


# ---------------------------------------------------------------------------
# Console class
# ---------------------------------------------------------------------------

class Console:
    """Centralized, color-aware console output for BetterCI."""

    def __init__(self, debug: bool = False, verbose: bool = False):
        """
        Args:
            debug:   Show full stack traces and detailed internal state.
            verbose: Stream step output in real-time (passed to runner).
        """
        self.debug = debug
        self.verbose = verbose

    # ------------------------------------------------------------------
    # Run lifecycle
    # ------------------------------------------------------------------

    def print_run_started(
        self,
        repository: str,
        workflow: str,
        job_count: int,
    ) -> None:
        bar = _c(_BOLD + _CYAN, "=" * 56)
        print(bar)
        print(_c(_BOLD + _WHITE, f"  BetterCI — {repository}"))
        print(f"  Workflow : {workflow}")
        print(f"  Jobs     : {job_count}")
        print(bar)
        print()

    # ------------------------------------------------------------------
    # Plan (git-diff selection)
    # ------------------------------------------------------------------

    def print_plan_header(
        self,
        compare_ref: Optional[str] = None,
        changed_count: Optional[int] = None,
    ) -> None:
        print(_c(_BOLD, "\nPLAN"))
        if compare_ref and changed_count is not None:
            print(_c(_GRAY, f"  Comparing against {compare_ref} — {changed_count} file(s) changed"))

    def print_plan_job(self, name: str, reason: str) -> None:
        print(f"  {_c(_GREEN, '✓')} {_c(_BOLD, name)}  {_c(_GRAY, reason)}")

    def print_plan_job_skipped(self, name: str, reason: str) -> None:
        print(f"  {_c(_GRAY, '–')} {_c(_GRAY, name)}  {_c(_GRAY, '(skipped: ' + reason + ')')}")

    # ------------------------------------------------------------------
    # Job lifecycle
    # ------------------------------------------------------------------

    def print_job_start(self, name: str) -> None:
        print(f"\n{_c(_BOLD + _CYAN, '▶')} {_c(_BOLD, name)}")

    def print_job_done(self, name: str, *, elapsed: float = 0.0) -> None:
        print(
            f"  {_c(_GREEN, '✓')} {_c(_GREEN, 'Done')}  "
            f"{_c(_GRAY, _fmt_elapsed(elapsed))}"
        )

    def print_job_skipped(self, name: str, reason: str, *, elapsed: float = 0.0) -> None:
        print(
            f"  {_c(_YELLOW, '⊘')} {_c(_YELLOW, 'Skipped')} "
            f"{_c(_GRAY, '(' + reason + ')')}  "
            f"{_c(_GRAY, _fmt_elapsed(elapsed))}"
        )

    # ------------------------------------------------------------------
    # Step lifecycle
    # ------------------------------------------------------------------

    def print_step(self, name: str) -> None:
        print(f"  {_c(_GRAY, '·')} {name}")

    def print_success(self, name: str, *, elapsed: float = 0.0) -> None:
        print(
            f"  {_c(_GREEN, '✓')} {name}  "
            f"{_c(_GRAY, _fmt_elapsed(elapsed))}"
        )

    def print_failure(
        self,
        name: str,
        reason: str,
        *,
        exit_code: Optional[int] = None,
        hint: Optional[str] = None,
        elapsed: float = 0.0,
        is_job: bool = False,
    ) -> None:
        label = "JOB FAILED" if is_job else "FAILED"
        print(
            f"\n  {_c(_RED + _BOLD, '✗')} {_c(_RED + _BOLD, label + ': ' + name)}  "
            f"{_c(_GRAY, _fmt_elapsed(elapsed))}",
            file=sys.stderr,
        )
        if exit_code is not None:
            print(f"    exit code : {exit_code}", file=sys.stderr)

        # Always show a concise error message; full detail only in debug mode
        if self.debug:
            print(f"    details   : {reason}", file=sys.stderr)
        else:
            first_line = reason.split("\n")[0] if reason else ""
            if first_line:
                print(f"    error     : {first_line}", file=sys.stderr)

        if hint:
            print(f"    hint      : {_c(_YELLOW, hint)}", file=sys.stderr)

    # ------------------------------------------------------------------
    # Cache
    # ------------------------------------------------------------------

    def print_cache_hit(self, job: str, reason: str) -> None:
        print(f"  {_c(_CYAN, '◉')} cache hit  {_c(_GRAY, reason)}")

    def print_cache_miss(self, job: str) -> None:
        print(f"  {_c(_GRAY, '○')} cache miss")

    def print_cache_saved(self, job: str, key: str) -> None:
        short = key[:12] + "…" if len(key) > 12 else key
        print(f"  {_c(_CYAN, '◉')} cache saved  {_c(_GRAY, short)}")

    # ------------------------------------------------------------------
    # Summary table
    # ------------------------------------------------------------------

    def print_results(self, results: dict[str, str]) -> None:
        print()
        print(_c(_BOLD, "─" * 56))
        print(_c(_BOLD, "RESULTS"))
        print(_c(_BOLD, "─" * 56))

        counts = {"ok": 0, "failed": 0, "skipped": 0}
        for job_name, status in results.items():
            if status == "ok":
                marker = _c(_GREEN, "✓")
                label  = _c(_GREEN, "ok")
                counts["ok"] += 1
            elif status == "failed":
                marker = _c(_RED, "✗")
                label  = _c(_RED, "failed")
                counts["failed"] += 1
            else:
                marker = _c(_YELLOW, "⊘")
                label  = _c(_YELLOW, status)
                counts["skipped"] += 1

            print(f"  {marker} {job_name:<30} {label}")

        print(_c(_BOLD, "─" * 56))
        parts = []
        if counts["ok"]:
            parts.append(_c(_GREEN, f"{counts['ok']} passed"))
        if counts["failed"]:
            parts.append(_c(_RED, f"{counts['failed']} failed"))
        if counts["skipped"]:
            parts.append(_c(_YELLOW, f"{counts['skipped']} skipped"))
        print("  " + ",  ".join(parts))
        print()

    # ------------------------------------------------------------------
    # Errors and general output
    # ------------------------------------------------------------------

    def print_error(
        self,
        title: str,
        message: str,
        details: Optional[list[str]] = None,
        suggestion: Optional[str] = None,
    ) -> None:
        print(f"\n{_c(_RED + _BOLD, 'ERROR')}: {_c(_BOLD, title)}", file=sys.stderr)
        print(f"  {message}", file=sys.stderr)
        if details:
            for d in details:
                print(f"  {d}", file=sys.stderr)
        if suggestion:
            print(f"\n  {_c(_YELLOW, 'Hint')}: {suggestion}", file=sys.stderr)

    def print_warning(self, message: str) -> None:
        print(f"{_c(_YELLOW, 'WARNING')}: {message}", file=sys.stderr)

    def print_info(self, message: str) -> None:
        print(message)

    def print_debug(self, message: str) -> None:
        if self.debug:
            print(f"{_c(_GRAY, '[debug]')} {message}", file=sys.stderr)

    def print_exception(self, exc: Exception) -> None:
        if self.debug:
            import traceback
            traceback.print_exc()
        else:
            print(
                f"{_c(_RED, 'Error')}: {exc}  "
                f"{_c(_GRAY, '(run with --debug for full traceback)')}",
                file=sys.stderr,
            )

    # ------------------------------------------------------------------
    # Agent output
    # ------------------------------------------------------------------

    def print_agent_started(
        self, agent_id: str, api: str, poll_interval: int
    ) -> None:
        print(_c(_BOLD + _CYAN, "\nBetterCI Agent"))
        print(f"  agent-id : {agent_id}")
        print(f"  api      : {api}")
        print(f"  polling  : every {poll_interval}s")
        print()

    def print_lease_acquired(self, job_name: str, run_id: str) -> None:
        print(f"\n{_c(_BOLD + _CYAN, '▶')} lease acquired: {_c(_BOLD, job_name)}")
        print(f"  run-id: {_c(_GRAY, run_id)}")

    def print_execution_complete(
        self, status: str, duration: Optional[float] = None
    ) -> None:
        if status in ("ok", "success"):
            marker = _c(_GREEN, "✓")
            label  = _c(_GREEN, "complete")
        else:
            marker = _c(_RED, "✗")
            label  = _c(_RED, "failed")
        dur = f"  {_c(_GRAY, _fmt_elapsed(duration))}" if duration else ""
        print(f"  {marker} {label}{dur}")


# ---------------------------------------------------------------------------
# Global console singleton
# ---------------------------------------------------------------------------

_console: Optional[Console] = None


def get_console() -> Console:
    global _console
    if _console is None:
        _console = Console()
    return _console


def set_console(console: Console) -> None:
    global _console
    _console = console
