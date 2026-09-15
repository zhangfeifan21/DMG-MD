#!/usr/bin/env python3
"""Interactive terminal dashboard for the long-NVE acceptance runner."""

from __future__ import annotations

import io
import os
import shutil
import sys
import threading
import time
from pathlib import Path
from typing import Any, Dict, Iterable, List, Mapping, Optional, Sequence, Tuple


ConfigKey = Tuple[str, int, str, int]
RestartKey = Tuple[str, str, int, int]


def _format_duration(seconds: float) -> str:
    total = max(0, int(seconds))
    hours, remainder = divmod(total, 3600)
    minutes, seconds = divmod(remainder, 60)
    return f"{hours:02d}:{minutes:02d}:{seconds:02d}"


def _backend_label(backend: str) -> str:
    return "H" if backend == "HostStaged" else "C"


class LongNveDashboard:
    """Own the in-memory progress model and render it to one terminal screen."""

    SYMBOLS = {
        "pending": "·",
        "running": "▶",
        "revalidating": "↻",
        "retrying": "↻",
        "archived-incomplete": "↻",
        "reused": "✓",
        "adopted-existing": "✓",
        "passed": "✓",
        "failed": "✗",
    }

    def __init__(
        self,
        profile: str,
        cases: Sequence[str],
        seeds: Sequence[int],
        ranks: Sequence[int],
        backends: Sequence[str],
        expected_configs: Iterable[ConfigKey],
        expected_restarts: Iterable[RestartKey],
        stream: io.TextIOBase,
        terminal_fd: Optional[int],
        *,
        log_path: Optional[Path] = None,
        report_path: Optional[Path] = None,
        alternate_screen: bool = True,
        color: bool = True,
        refresh_interval: float = 1.0,
        start_thread: bool = True,
        close_stream: bool = False,
    ) -> None:
        self.profile = profile
        self.cases = list(cases)
        self.seeds = list(seeds)
        self.ranks = list(ranks)
        self.backends = list(backends)
        self.expected_configs = list(expected_configs)
        self.expected_restarts = list(expected_restarts)
        self.config_status: Dict[ConfigKey, str] = {
            key: "pending" for key in self.expected_configs
        }
        self.restart_status: Dict[RestartKey, str] = {
            key: "pending" for key in self.expected_restarts
        }
        self.completed_configs: set[ConfigKey] = set()
        self.completed_restarts: set[RestartKey] = set()
        self.current_config: Optional[ConfigKey] = None
        self.current_restart: Optional[RestartKey] = None
        self.current_stage = "environment validation"
        self.current_stage_status = "running"
        self.current_attempt: Optional[int] = None
        self.current_max_attempts: Optional[int] = None
        self.current_stage_elapsed: Optional[float] = None
        self.stage_started = time.monotonic()
        self.started = self.stage_started
        self.phase = "initializing"
        self.suite_status = "RUNNING"
        self.failure_reason: Optional[str] = None
        self.work_root: Optional[Path] = None
        self.log_path = log_path
        self.report_path = report_path
        self.stream = stream
        self.terminal_fd = terminal_fd
        self.alternate_screen = alternate_screen
        self.color = color
        self.refresh_interval = refresh_interval
        self.close_stream = close_stream
        self._closed = False
        self._lock = threading.Lock()
        self._render_lock = threading.Lock()
        self._wake = threading.Event()
        self._stop = threading.Event()
        self._thread: Optional[threading.Thread] = None

        if self.alternate_screen:
            self.stream.write("\x1b[?1049h\x1b[?25l")
            self.stream.flush()
        self.refresh()
        if start_thread:
            self._thread = threading.Thread(
                target=self._refresh_loop,
                name="long-nve-dashboard",
                daemon=True,
            )
            self._thread.start()

    def _refresh_loop(self) -> None:
        while not self._stop.is_set():
            self._wake.wait(self.refresh_interval)
            self._wake.clear()
            if not self._stop.is_set():
                self.refresh()

    def _changed(self) -> None:
        self.refresh()

    def set_plan(self, work_root: Path, completed: Iterable[ConfigKey]) -> None:
        completed_set = set(completed)
        with self._lock:
            self.work_root = work_root
            self.completed_configs = completed_set & set(self.expected_configs)
            for key in self.completed_configs:
                self.config_status[key] = "passed"
            self.phase = "reference preparation"
            self.current_stage = "preparing shared reference stages"
            self.current_stage_status = "running"
            self.current_attempt = None
            self.current_max_attempts = None
            self.current_stage_elapsed = None
            self.stage_started = time.monotonic()
        self._changed()

    def set_phase(self, phase: str, activity: Optional[str] = None) -> None:
        with self._lock:
            self.phase = phase
            if activity is not None:
                self.current_stage = activity
                self.current_stage_status = "running"
                self.current_attempt = None
                self.current_max_attempts = None
                self.current_stage_elapsed = None
                self.stage_started = time.monotonic()
        self._changed()

    def config_started(self, key: ConfigKey, revalidating: bool) -> None:
        with self._lock:
            self.current_config = key
            self.current_restart = None
            self.config_status[key] = "revalidating" if revalidating else "running"
            self.phase = "configuration matrix"
            self.current_stage = "starting configuration"
            self.current_stage_status = self.config_status[key]
            self.current_attempt = None
            self.current_max_attempts = None
            self.current_stage_elapsed = None
            self.stage_started = time.monotonic()
        self._changed()

    def config_passed(self, key: ConfigKey) -> None:
        with self._lock:
            self.config_status[key] = "passed"
            self.completed_configs.add(key)
            self.current_config = None
            self.current_stage_status = "passed"
        self._changed()

    def restart_started(self, key: RestartKey) -> None:
        with self._lock:
            self.current_config = None
            self.current_restart = key
            self.restart_status[key] = "running"
            self.phase = "restart validation"
            self.current_stage = "starting restart transition"
            self.current_stage_status = "running"
            self.current_attempt = None
            self.current_max_attempts = None
            self.current_stage_elapsed = None
            self.stage_started = time.monotonic()
        self._changed()

    def restart_passed(self, key: RestartKey) -> None:
        with self._lock:
            self.restart_status[key] = "passed"
            self.completed_restarts.add(key)
            self.current_restart = None
            self.current_stage_status = "passed"
        self._changed()

    def stage_event(self, status: str, fields: Mapping[str, Any]) -> None:
        path_value = fields.get("path")
        display_path = str(path_value) if path_value is not None else "MD stage"
        if path_value is not None and self.work_root is not None:
            try:
                display_path = str(Path(path_value).relative_to(self.work_root))
            except ValueError:
                pass
        now = time.monotonic()
        with self._lock:
            if status == "running":
                self.stage_started = now
                self.current_stage_elapsed = None
            else:
                elapsed = fields.get("elapsed_seconds")
                self.current_stage_elapsed = (
                    float(elapsed) if elapsed is not None else now - self.stage_started
                )
            self.current_stage = display_path
            self.current_stage_status = status
            attempt = fields.get("attempt")
            maximum = fields.get("max_attempts")
            self.current_attempt = int(attempt) if attempt is not None else None
            self.current_max_attempts = int(maximum) if maximum is not None else None
            if "/restart/" in f"/{display_path}/":
                self.phase = "restart validation"
            elif "reference-" in display_path and self.current_config is None:
                self.phase = "reference preparation"
        self._changed()

    def fail(self, reason: str) -> None:
        with self._lock:
            self.suite_status = "FAILED"
            self.failure_reason = " ".join(reason.splitlines())
            if self.current_config is not None:
                self.config_status[self.current_config] = "failed"
                self.completed_configs.discard(self.current_config)
            if self.current_restart is not None:
                self.restart_status[self.current_restart] = "failed"
                self.completed_restarts.discard(self.current_restart)
            self.current_stage_status = "failed"
        self.refresh()

    def finish(self) -> None:
        with self._lock:
            self.suite_status = "PASSED"
            self.phase = "complete"
            self.current_stage = "all validations passed"
            self.current_stage_status = "passed"
            self.current_attempt = None
            self.current_max_attempts = None
            self.current_stage_elapsed = 0.0
        self.refresh()

    def _terminal_size(self) -> Tuple[int, int]:
        if self.terminal_fd is not None:
            try:
                size = os.get_terminal_size(self.terminal_fd)
                return size.columns, size.lines
            except OSError:
                pass
        size = shutil.get_terminal_size((120, 40))
        return size.columns, size.lines

    def _progress_bar(self, completed: int, total: int, width: int) -> str:
        if total <= 0:
            return "".ljust(width, "░")
        filled = min(width, int(width * completed / total))
        return "█" * filled + "░" * (width - filled)

    def _status_symbol(self, status: str) -> str:
        return self.SYMBOLS.get(status, "?")

    def _current_label(self) -> str:
        if self.current_config is not None:
            case, seed, backend, ranks = self.current_config
            return f"{case} / seed-{seed} / {backend} / r{ranks}"
        if self.current_restart is not None:
            case, backend, source, destination = self.current_restart
            return f"{case} / {backend} / restart r{source}→r{destination}"
        return self.phase

    def _next_pending(self) -> str:
        for case, seed, backend, ranks in self.expected_configs:
            if self.config_status[(case, seed, backend, ranks)] == "pending":
                return f"{case}/seed-{seed}/{backend}-r{ranks}"
        for case, backend, source, destination in self.expected_restarts:
            if self.restart_status[(case, backend, source, destination)] == "pending":
                return f"{case}/{backend}/restart-r{source}-to-r{destination}"
        return "none"

    def _detailed_checklist(self) -> List[str]:
        combinations = [
            (backend, ranks) for backend in self.backends for ranks in self.ranks
        ]
        label_width = max(20, max((len(case) for case in self.cases), default=0) + 9)
        header = "  " + f"{'case / seed':<{label_width}} " + " ".join(
            f"{_backend_label(backend)}{ranks}".center(3)
            for backend, ranks in combinations
        )
        lines = ["Checklist · configuration matrix", header]
        for case in self.cases:
            for seed in self.seeds:
                label = f"{case} / s{seed}"
                symbols = " ".join(
                    self._status_symbol(
                        self.config_status[(case, seed, backend, ranks)]
                    ).center(3)
                    for backend, ranks in combinations
                )
                lines.append(f"  {label:<{label_width}} {symbols}")
        return lines

    def _compact_checklist(self, width: int) -> List[str]:
        lines = ["Checklist · configuration matrix (completed/total)"]
        per_seed_total = len(self.backends) * len(self.ranks)
        case_total = per_seed_total * len(self.seeds)
        label_width = min(34, max(20, max((len(case) for case in self.cases), default=0)))
        for case in self.cases:
            completed = sum(
                (case, seed, backend, ranks) in self.completed_configs
                for seed in self.seeds
                for backend in self.backends
                for ranks in self.ranks
            )
            completed_by_seed = {
                seed: sum(
                    (case, seed, backend, ranks) in self.completed_configs
                    for backend in self.backends
                    for ranks in self.ranks
                )
                for seed in self.seeds
            }
            seed_summary = " ".join(
                f"s{seed}:{completed_by_seed[seed]}/{per_seed_total}"
                for seed in self.seeds
            )
            detailed = f"  {case:<{label_width}} {seed_summary}  total:{completed}/{case_total}"
            if len(detailed) <= width:
                lines.append(detailed)
            else:
                bar_width = max(8, min(24, width - label_width - 15))
                lines.append(
                    f"  {case:<{label_width}} "
                    f"[{self._progress_bar(completed, case_total, bar_width)}] "
                    f"{completed}/{case_total}"
                )
        return lines

    def render_lines(self, width: int, height: int) -> List[str]:
        width = max(40, width)
        height = max(12, height)
        now = time.monotonic()
        config_done = len(self.completed_configs)
        restart_done = len(self.completed_restarts)
        total_configs = len(self.expected_configs)
        total_restarts = len(self.expected_restarts)
        total = total_configs + total_restarts
        completed = config_done + restart_done
        running = sum(
            status in ("running", "revalidating")
            for status in [*self.config_status.values(), *self.restart_status.values()]
        )
        failed = sum(
            status == "failed"
            for status in [*self.config_status.values(), *self.restart_status.values()]
        )
        pending = sum(
            status == "pending"
            for status in [*self.config_status.values(), *self.restart_status.values()]
        )
        percent = 100.0 if total == 0 else 100.0 * completed / total
        bar_width = max(4, min(36, width - 38))
        lines = [
            f"DMG-MD long-NVE · {self.profile.upper():<9} "
            f"suite={self.suite_status}  elapsed={_format_duration(now - self.started)}",
            "",
            f"Overall        [{self._progress_bar(completed, total, bar_width)}] "
            f"{completed}/{total} ({percent:5.1f}%)",
            f"Configurations {config_done}/{total_configs}    "
            f"Restarts {restart_done}/{total_restarts}    "
            f"Running {running}    Pending {pending}    Failed {failed}",
            "",
            f"Current  {self._current_label()}",
            f"Stage    {self._status_symbol(self.current_stage_status)} "
            f"{self.current_stage}",
        ]
        stage_elapsed = (
            self.current_stage_elapsed
            if self.current_stage_elapsed is not None
            else now - self.stage_started
        )
        if self.current_attempt is not None and self.current_max_attempts is not None:
            lines.append(
                f"Attempt  {self.current_attempt}/{self.current_max_attempts}    "
                f"stage elapsed={_format_duration(stage_elapsed)}"
            )
        else:
            lines.append(f"Stage elapsed={_format_duration(stage_elapsed)}")
        lines.extend((f"Next     {self._next_pending()}", ""))

        detailed = self._detailed_checklist()
        # On a conventional 24-line tmux pane, favor the exact nightly matrix
        # over the final report-path line; release still switches to aggregation.
        fixed_tail = 5 + (1 if self.failure_reason else 0)
        if len(lines) + len(detailed) + fixed_tail <= height:
            lines.extend(detailed)
        else:
            lines.extend(self._compact_checklist(width))

        if total_restarts:
            restart_by_case = []
            for case in self.cases:
                keys = [key for key in self.expected_restarts if key[0] == case]
                if keys:
                    restart_by_case.append(
                        f"{case}:{sum(key in self.completed_restarts for key in keys)}/{len(keys)}"
                    )
            lines.append("Restarts  " + "  ".join(restart_by_case))
        lines.extend(("", "✓ passed   ▶ running   ↻ revalidating/retrying   · pending   ✗ failed"))
        if self.failure_reason:
            lines.append(f"Error    {self.failure_reason}")
        if self.work_root is not None:
            lines.append(f"Work     {self.work_root}")
        if self.log_path is not None:
            lines.append(f"Log      {self.log_path}")
        if self.report_path is not None:
            lines.append(f"Report   {self.report_path}")

        visible = [line[:width] for line in lines[:height]]
        return visible

    def _decorate(self, text: str) -> str:
        if not self.color:
            return text
        replacements = {
            "✓": "\x1b[32m✓\x1b[0m",
            "▶": "\x1b[36m▶\x1b[0m",
            "↻": "\x1b[33m↻\x1b[0m",
            "✗": "\x1b[31m✗\x1b[0m",
            "suite=PASSED": "suite=\x1b[32mPASSED\x1b[0m",
            "suite=FAILED": "suite=\x1b[31mFAILED\x1b[0m",
        }
        for source, replacement in replacements.items():
            text = text.replace(source, replacement)
        return text

    def refresh(self) -> None:
        if self._closed:
            return
        with self._render_lock:
            width, height = self._terminal_size()
            with self._lock:
                rendered = "\n".join(self.render_lines(width, height))
            try:
                self.stream.write("\x1b[2J\x1b[H" + self._decorate(rendered) + "\n")
                self.stream.flush()
            except (BrokenPipeError, OSError):
                self._stop.set()

    def close(self, show_failure_summary: bool = False) -> None:
        if self._closed:
            return
        self._stop.set()
        self._wake.set()
        if self._thread is not None and self._thread is not threading.current_thread():
            self._thread.join(timeout=max(1.0, self.refresh_interval * 2.0))
        if self.alternate_screen:
            try:
                self.stream.write("\x1b[?25h\x1b[?1049l")
                self.stream.flush()
            except (BrokenPipeError, OSError):
                pass
        if show_failure_summary and self.failure_reason:
            with self._lock:
                summary = [
                    f"DMG-MD long-NVE · {self.profile.upper()} · FAILED",
                    f"Current: {self._current_label()}",
                    f"Stage:   {self.current_stage}",
                    f"Error:   {self.failure_reason}",
                ]
                if self.work_root is not None:
                    summary.append(f"Work:    {self.work_root}")
                if self.log_path is not None:
                    summary.append(f"Log:     {self.log_path}")
            try:
                self.stream.write(self._decorate("\n".join(summary)) + "\n")
                self.stream.flush()
            except (BrokenPipeError, OSError):
                pass
        self._closed = True
        if self.close_stream:
            self.stream.close()


def open_dashboard(
    mode: str,
    ui_fd: Optional[int],
    profile: str,
    cases: Sequence[str],
    seeds: Sequence[int],
    ranks: Sequence[int],
    backends: Sequence[str],
    expected_configs: Iterable[ConfigKey],
    expected_restarts: Iterable[RestartKey],
    *,
    log_path: Optional[Path] = None,
    report_path: Optional[Path] = None,
) -> Optional[LongNveDashboard]:
    """Open the dashboard stream, or return None for plain/non-interactive output."""
    if mode == "plain":
        return None
    term = os.environ.get("TERM", "")
    if mode == "auto" and ui_fd is None and (not sys.stdout.isatty() or term == "dumb"):
        return None

    stream: io.TextIOBase
    terminal_fd: Optional[int]
    close_stream = False
    if ui_fd is not None:
        duplicated = os.dup(ui_fd)
        stream = os.fdopen(
            duplicated, "w", buffering=1, encoding="utf-8", errors="replace"
        )
        terminal_fd = duplicated
        close_stream = True
    elif sys.stdout.isatty():
        stream = sys.stdout
        terminal_fd = sys.stdout.fileno()
    else:
        try:
            terminal_fd = os.open("/dev/tty", os.O_WRONLY)
        except OSError:
            if mode == "auto":
                return None
            raise
        stream = os.fdopen(
            terminal_fd, "w", buffering=1, encoding="utf-8", errors="replace"
        )
        close_stream = True

    return LongNveDashboard(
        profile,
        cases,
        seeds,
        ranks,
        backends,
        expected_configs,
        expected_restarts,
        stream,
        terminal_fd,
        log_path=log_path,
        report_path=report_path,
        alternate_screen=True,
        color="NO_COLOR" not in os.environ and term != "dumb",
        close_stream=close_stream,
    )
