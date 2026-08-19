"""Thread-safe UI controllers."""

from __future__ import annotations

from dataclasses import dataclass, replace
from queue import Empty, Queue
from threading import Event, Lock, Thread, current_thread
from typing import Callable

from planning_ping.contracts import SearchEvent, SearchRequest, SearchService, SearchSummary


@dataclass(frozen=True, slots=True)
class SearchViewState:
    running: bool = False
    search_enabled: bool = True
    cancel_enabled: bool = False
    status_message: str = "Ready"
    current_council: str = ""
    completed_councils: int = 0
    total_councils: int = 0
    saved_count: int = 0
    warnings: tuple[str, ...] = ()
    summary: SearchSummary | None = None
    error_message: str = ""


@dataclass(frozen=True, slots=True)
class _Finished:
    summary: SearchSummary


@dataclass(frozen=True, slots=True)
class _Failed:
    error: Exception


class SearchController:
    """Runs a search off-thread and applies all observable state on poll()."""

    def __init__(self, service: SearchService, on_state: Callable[[SearchViewState], object]) -> None:
        self._service = service
        self._on_state = on_state
        self._messages: Queue[SearchEvent | _Finished | _Failed] = Queue()
        self._cancel_event: Event | None = None
        self._worker: Thread | None = None
        self._lock = Lock()
        self._closed = False
        self.state = SearchViewState()

    def _publish(self, **changes: object) -> None:
        self.state = replace(self.state, **changes)
        self._on_state(self.state)

    def start(self, request: SearchRequest) -> None:
        with self._lock:
            if self._closed:
                raise RuntimeError("Search controller is closed")
            if self._worker is not None and self._worker.is_alive():
                raise RuntimeError("A search is already running")
            self._cancel_event = Event()
            cancel_event = self._cancel_event
            self._publish(
                running=True,
                search_enabled=False,
                cancel_enabled=True,
                status_message="Starting search…",
                current_council="",
                completed_councils=0,
                total_councils=0,
                saved_count=0,
                warnings=(),
                summary=None,
                error_message="",
            )

            def work() -> None:
                try:
                    result = self._service.run(request, self._messages.put, cancel_event)
                except Exception as error:
                    self._messages.put(_Failed(error))
                else:
                    self._messages.put(_Finished(result))

            self._worker = Thread(target=work, name="PlanningPing search", daemon=True)
            self._worker.start()

    def cancel(self) -> None:
        if self._cancel_event is not None and self.state.running:
            self._cancel_event.set()
            self._publish(cancel_enabled=False, status_message="Cancelling…")

    def poll(self) -> bool:
        changed = False
        while True:
            try:
                message = self._messages.get_nowait()
            except Empty:
                break
            changed = True
            if isinstance(message, SearchEvent):
                self._apply_event(message)
            elif isinstance(message, _Finished):
                summary = message.summary
                cancelled = summary.status == "cancelled"
                self._publish(
                    running=False,
                    search_enabled=True,
                    cancel_enabled=False,
                    status_message=("Search cancelled" if cancelled else self._format_summary(summary)),
                    summary=summary,
                )
            else:
                error_text = str(message.error) or message.error.__class__.__name__
                self._publish(
                    running=False,
                    search_enabled=True,
                    cancel_enabled=False,
                    status_message="Search failed",
                    error_message=error_text,
                )
        return changed

    def _apply_event(self, event: SearchEvent) -> None:
        changes: dict[str, object] = {}
        if event.kind == "started":
            changes.update(status_message="Search running", total_councils=event.total)
        elif event.kind == "council_started":
            status_message = event.message or f"Searching {event.council or 'council'}…"
            changes.update(
                current_council=event.council or "",
                completed_councils=event.completed,
                total_councils=event.total,
                status_message=status_message,
            )
        elif event.kind == "council_finished":
            changes.update(completed_councils=event.completed, total_councils=event.total)
        elif event.kind == "application_saved":
            changes["saved_count"] = event.saved_count
        elif event.kind == "warning":
            warning = event.message or "Warning"
            if event.council:
                warning = f"{event.council}: {warning}"
            changes["warnings"] = (*self.state.warnings, warning)
        elif event.kind == "cancelled":
            changes["status_message"] = "Search cancelling…"
        elif event.kind == "completed":
            changes.update(
                completed_councils=event.completed,
                total_councils=event.total,
                saved_count=event.saved_count,
                status_message="Finalising search…",
            )
        if changes:
            self._publish(**changes)

    @staticmethod
    def _format_summary(summary: SearchSummary) -> str:
        return (
            f"Finished: {summary.saved_applications} saved across "
            f"{summary.searched_councils}/{summary.total_councils} councils; "
            f"{summary.failed_councils} failed"
        )

    def close(self, timeout_seconds: float = 0.0) -> bool:
        with self._lock:
            self._closed = True
            if self._cancel_event is not None:
                self._cancel_event.set()
            worker = self._worker
        if worker is not None and worker is not current_thread():
            worker.join(max(0.0, timeout_seconds))
        drained = worker is None or not worker.is_alive()
        if drained:
            while True:
                try:
                    self._messages.get_nowait()
                except Empty:
                    break
            with self._lock:
                if self._worker is worker:
                    self._worker = None
            self.state = replace(
                self.state,
                running=False,
                search_enabled=False,
                cancel_enabled=False,
            )
        return drained


class BackgroundTaskController:
    """Runs a local query off-thread and delivers completion during UI polling."""

    def __init__(self, on_finished: Callable[[], object]) -> None:
        self._on_finished = on_finished
        self._messages: Queue[Exception | None] = Queue()
        self._closed = False
        self._worker: Thread | None = None
        self.running = False
        self.last_error: Exception | None = None

    def start(self, action: Callable[[], object]) -> None:
        if self._closed:
            raise RuntimeError("Task controller is closed")
        if self.running:
            raise RuntimeError("A task is already running")
        self.running = True
        self.last_error = None

        def work() -> None:
            try:
                action()
            except Exception as error:
                self._messages.put(error)
            else:
                self._messages.put(None)

        self._worker = Thread(target=work, name="PlanningPing query", daemon=True)
        self._worker.start()

    def poll(self) -> bool:
        if not self.running:
            return False
        try:
            self.last_error = self._messages.get_nowait()
        except Empty:
            return False
        self.running = False
        self._worker = None
        if not self._closed:
            self._on_finished()
        return True

    def close(self, timeout_seconds: float = 0.0) -> bool:
        self._closed = True
        worker = self._worker
        if worker is not None and worker is not current_thread():
            worker.join(max(0.0, timeout_seconds))
        drained = worker is None or not worker.is_alive()
        if drained:
            while True:
                try:
                    self.last_error = self._messages.get_nowait()
                except Empty:
                    break
            self.running = False
            self._worker = None
        return drained
