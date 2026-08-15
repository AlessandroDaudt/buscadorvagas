"""Small persisted local task runner for long web operations."""

from __future__ import annotations

import threading
import time
from collections.abc import Callable
from dataclasses import dataclass
from datetime import datetime, timezone
from typing import Any, TypeVar

from sqlalchemy import select
from sqlalchemy.exc import OperationalError

from job_hunt.log import get_logger, redact_text
from job_hunt.persistence.database import Database
from job_hunt.persistence.models import WebTaskRecord

logger = get_logger("autopilot.web.tasks")
TaskHandler = Callable[["TaskContext", dict[str, Any]], dict[str, Any]]
RecurringCondition = Callable[[], bool]
ACTIVE_STATES = {"queued", "running", "cancel_requested"}
RESTARTABLE_TASK_TYPES = {"import_portal_catalog"}
DATABASE_LOCK_RETRY_DELAYS = (0.25, 0.5, 1.0, 2.0)
T = TypeVar("T")


class TaskConflictError(RuntimeError):
    pass


class TaskNotFoundError(LookupError):
    pass


class TaskCancellationError(RuntimeError):
    pass


class TaskCancelled(RuntimeError):
    pass


@dataclass(frozen=True)
class TaskContext:
    manager: "LocalTaskManager"
    task_id: str

    def progress(self, percent: int, message: str) -> None:
        self.manager.update_progress(self.task_id, percent, message)
        self.raise_if_cancelled()

    def raise_if_cancelled(self) -> None:
        if self.manager.is_cancel_requested(self.task_id):
            raise TaskCancelled("task cancelled at a safe checkpoint")


class LocalTaskManager:
    """Runs bounded daemon threads while SQLite remains the source of truth."""

    def __init__(self, database_url: str, handlers: dict[str, TaskHandler]) -> None:
        self.database_url = database_url
        self.handlers = handlers
        self._threads: dict[str, threading.Thread] = {}
        self._recurring_threads: dict[str, threading.Thread] = {}
        self._recurring_states: dict[str, dict[str, Any]] = {}
        self._recurring_stop = threading.Event()
        self._guard = threading.Lock()
        self.recover_interrupted()

    def _database(self) -> Database:
        return Database(self.database_url)

    @staticmethod
    def _is_database_locked(exc: OperationalError) -> bool:
        return "database is locked" in str(exc).casefold()

    def _retry_locked(self, operation: Callable[[], T], *, operation_name: str) -> T:
        for attempt in range(len(DATABASE_LOCK_RETRY_DELAYS) + 1):
            try:
                return operation()
            except OperationalError as exc:
                if not self._is_database_locked(exc) or attempt >= len(DATABASE_LOCK_RETRY_DELAYS):
                    raise
                delay = DATABASE_LOCK_RETRY_DELAYS[attempt]
                logger.warning(
                    "SQLite busy while updating a local task; retrying",
                    extra={"operation": operation_name, "retry_in_seconds": delay},
                )
                time.sleep(delay)
        raise RuntimeError("unreachable database retry state")

    def _task_thread_alive(self, task_id: str) -> bool:
        with self._guard:
            thread = self._threads.get(task_id)
            return bool(thread and thread.is_alive())

    def _mark_orphaned(self, task_id: str) -> None:
        def update() -> None:
            database = self._database()
            try:
                with database.session() as session:
                    record = session.get(WebTaskRecord, task_id)
                    if record is None or record.state not in ACTIVE_STATES:
                        return
                    record.state = "failed"
                    record.message = "Tarefa órfã recuperada automaticamente"
                    record.error = (
                        "A execução perdeu seu worker após uma falha transitória; "
                        "um novo lote será iniciado automaticamente."
                    )
                    record.completed_at = datetime.now(timezone.utc)
            finally:
                database.dispose()

        self._retry_locked(update, operation_name="mark_orphaned")

    def recover_interrupted(self) -> None:
        database = self._database()
        restartable: list[tuple[str, str]] = []
        try:
            with database.session() as session:
                records = session.scalars(
                    select(WebTaskRecord).where(WebTaskRecord.state.in_(ACTIVE_STATES))
                ).all()
                now = datetime.now(timezone.utc)
                for record in records:
                    if (
                        record.task_type in RESTARTABLE_TASK_TYPES
                        and record.task_type in self.handlers
                    ):
                        record.state = "queued"
                        record.progress = 0
                        record.message = "Retomando em background após reinício"
                        record.started_at = None
                        record.completed_at = None
                        record.error = None
                        restartable.append((record.id, record.task_type))
                        continue
                    record.state = "failed"
                    record.completed_at = now
                    record.error = (
                        "A aplicação foi reiniciada antes da conclusão. Execute novamente."
                    )
                    record.message = "Interrompida por reinício"
        finally:
            database.dispose()
        for task_id, task_type in restartable:
            self._start_thread(task_id, task_type)

    def _start_thread(self, task_id: str, task_type: str) -> None:
        thread = threading.Thread(
            target=self._execute,
            args=(task_id,),
            name=f"autopilot-{task_type}-{task_id[:8]}",
            daemon=task_type not in RESTARTABLE_TASK_TYPES,
        )
        self._threads[task_id] = thread
        thread.start()

    def submit(
        self,
        task_type: str,
        payload: dict[str, Any] | None = None,
        *,
        exclusive: bool = False,
        cancel_safe: bool = True,
    ) -> WebTaskRecord:
        if task_type not in self.handlers:
            raise ValueError(f"unknown task type: {task_type}")
        with self._guard:
            database = self._database()
            try:
                with database.session() as session:
                    if exclusive:
                        active = session.scalar(
                            select(WebTaskRecord).where(
                                WebTaskRecord.task_type == task_type,
                                WebTaskRecord.state.in_(ACTIVE_STATES),
                            )
                        )
                        if active is not None:
                            thread = self._threads.get(active.id)
                            if thread is not None and thread.is_alive():
                                raise TaskConflictError(f"task already active: {active.id}")
                            active.state = "failed"
                            active.message = "Tarefa órfã recuperada automaticamente"
                            active.error = (
                                "A execução perdeu seu worker; uma nova execução foi iniciada."
                            )
                            active.completed_at = datetime.now(timezone.utc)
                    record = WebTaskRecord(
                        task_type=task_type,
                        state="queued",
                        progress=0,
                        message="Na fila",
                        payload_data=payload or {},
                        cancel_safe=cancel_safe,
                    )
                    session.add(record)
                    session.flush()
                    task_id = record.id
            finally:
                database.dispose()
            self._start_thread(task_id, task_type)
        return self.get(task_id)

    def shutdown(self) -> None:
        """Wait for durable imports so a graceful web shutdown cannot cut them short."""
        self._recurring_stop.set()
        for thread in list(self._recurring_threads.values()):
            if thread is not threading.current_thread():
                thread.join(timeout=5)
        while True:
            with self._guard:
                threads = [thread for thread in self._threads.values() if not thread.daemon]
            if not threads:
                return
            for thread in threads:
                if thread is not threading.current_thread():
                    thread.join()

    def start_recurring_until_complete(
        self,
        task_type: str,
        should_continue: RecurringCondition,
        *,
        interval_seconds: float = 60,
        payload: dict[str, Any] | None = None,
    ) -> None:
        """Keep one exclusive task running until its durable backlog is empty."""
        if task_type not in self.handlers:
            raise ValueError(f"unknown task type: {task_type}")
        interval = max(0.01, min(float(interval_seconds), 3600))
        with self._guard:
            existing = self._recurring_threads.get(task_type)
            if existing is not None and existing.is_alive():
                return
            self._recurring_states[task_type] = {
                "enabled": True,
                "state": "starting",
                "interval_seconds": interval,
                "last_task_id": None,
                "error": None,
            }
            thread = threading.Thread(
                target=self._run_recurring,
                args=(task_type, should_continue, interval, dict(payload or {})),
                name=f"autopilot-recurring-{task_type}",
                daemon=True,
            )
            self._recurring_threads[task_type] = thread
            thread.start()

    def _set_recurring_state(self, task_type: str, **updates: Any) -> None:
        with self._guard:
            state = self._recurring_states.setdefault(task_type, {"enabled": True})
            state.update(updates)

    def _run_recurring(
        self,
        task_type: str,
        should_continue: RecurringCondition,
        interval_seconds: float,
        payload: dict[str, Any],
    ) -> None:
        last_task_id: str | None = None
        while not self._recurring_stop.is_set():
            try:
                if last_task_id is not None:
                    previous = self.get(last_task_id)
                    if previous.state in ACTIVE_STATES:
                        if self._task_thread_alive(last_task_id):
                            self._set_recurring_state(
                                task_type,
                                state="waiting_for_active",
                                last_task_id=last_task_id,
                                error=None,
                            )
                            self._recurring_stop.wait(interval_seconds)
                            continue
                        self._mark_orphaned(last_task_id)
                        self._set_recurring_state(
                            task_type,
                            state="retrying",
                            last_task_id=last_task_id,
                            error="worker órfão recuperado",
                        )
                    last_task_id = None
                if not should_continue():
                    self._set_recurring_state(task_type, state="completed", error=None)
                    return
                try:
                    record = self.submit(
                        task_type,
                        payload,
                        exclusive=True,
                        cancel_safe=True,
                    )
                except TaskConflictError:
                    self._set_recurring_state(task_type, state="waiting_for_active", error=None)
                else:
                    last_task_id = record.id
                    self._set_recurring_state(
                        task_type,
                        state="running",
                        last_task_id=record.id,
                        error=None,
                    )
            except Exception as exc:
                logger.exception("recurring local task coordinator failed")
                self._set_recurring_state(
                    task_type,
                    state="retrying",
                    error=redact_text(str(exc))[:500] or type(exc).__name__,
                )
            self._recurring_stop.wait(interval_seconds)

    def recurring_status(self, task_type: str) -> dict[str, Any]:
        with self._guard:
            state = dict(
                self._recurring_states.get(
                    task_type,
                    {"enabled": False, "state": "disabled"},
                )
            )
            thread = self._recurring_threads.get(task_type)
            state["coordinator_alive"] = bool(thread and thread.is_alive())
            return state

    def _execute(self, task_id: str) -> None:
        database = self._database()
        try:

            def mark_running() -> tuple[str, dict[str, Any]] | None:
                with database.session() as session:
                    record = session.get(WebTaskRecord, task_id)
                    if record is None or record.state == "cancelled":
                        return None
                    record.state = "running"
                    record.started_at = datetime.now(timezone.utc)
                    record.progress = max(record.progress, 1)
                    record.message = "Iniciando"
                    return record.task_type, dict(record.payload_data)

            started = self._retry_locked(mark_running, operation_name="task_start")
            if started is None:
                return
            task_type, payload = started
            handler = self.handlers[task_type]
            result = handler(TaskContext(self, task_id), payload)

            def mark_completed() -> None:
                with database.session() as session:
                    record = session.get(WebTaskRecord, task_id)
                    if record is None:
                        return
                    if record.state == "cancel_requested":
                        record.state = "cancelled"
                        record.message = "Cancelada"
                    else:
                        record.state = "completed"
                        record.progress = 100
                        record.message = "Concluída"
                        record.result_data = result
                    record.completed_at = datetime.now(timezone.utc)

            self._retry_locked(mark_completed, operation_name="task_complete")
        except TaskCancelled:

            def mark_cancelled() -> None:
                with database.session() as session:
                    record = session.get(WebTaskRecord, task_id)
                    if record:
                        record.state = "cancelled"
                        record.message = "Cancelada em ponto seguro"
                        record.completed_at = datetime.now(timezone.utc)

            self._retry_locked(mark_cancelled, operation_name="task_cancel")
        except (Exception, SystemExit) as exc:
            logger.exception("local web task failed", extra={"task_id": task_id})
            error_text = redact_text(str(exc))[:1000] or type(exc).__name__

            def mark_failed() -> None:
                with database.session() as session:
                    record = session.get(WebTaskRecord, task_id)
                    if record:
                        record.state = "failed"
                        record.message = "Falha"
                        record.error = error_text
                        record.completed_at = datetime.now(timezone.utc)

            try:
                self._retry_locked(mark_failed, operation_name="task_fail")
            except OperationalError:
                logger.exception(
                    "failed to persist terminal task state; recurring coordinator will recover it",
                    extra={"task_id": task_id},
                )
        finally:
            database.dispose()
            with self._guard:
                self._threads.pop(task_id, None)

    def update_progress(self, task_id: str, percent: int, message: str) -> None:
        def update() -> None:
            database = self._database()
            try:
                with database.session() as session:
                    record = session.get(WebTaskRecord, task_id)
                    if record is None:
                        raise TaskNotFoundError(task_id)
                    record.progress = max(0, min(99, int(percent)))
                    record.message = message[:1000]
            finally:
                database.dispose()

        self._retry_locked(update, operation_name="task_progress")

    def is_cancel_requested(self, task_id: str) -> bool:
        return self.get(task_id).state == "cancel_requested"

    def cancel(self, task_id: str) -> WebTaskRecord:
        database = self._database()
        try:
            with database.session() as session:
                record = session.get(WebTaskRecord, task_id)
                if record is None:
                    raise TaskNotFoundError(task_id)
                if record.state not in ACTIVE_STATES:
                    raise TaskCancellationError("task is no longer active")
                if record.state == "queued":
                    record.state = "cancelled"
                    record.message = "Cancelada antes de iniciar"
                    record.completed_at = datetime.now(timezone.utc)
                else:
                    if not record.cancel_safe:
                        raise TaskCancellationError(
                            "a execução já iniciou e não possui ponto seguro de cancelamento"
                        )
                    record.state = "cancel_requested"
                    record.message = "Cancelamento solicitado; aguardando ponto seguro"
        finally:
            database.dispose()
        return self.get(task_id)

    def get(self, task_id: str) -> WebTaskRecord:
        database = self._database()
        try:
            with database.session() as session:
                record = session.get(WebTaskRecord, task_id)
                if record is None:
                    raise TaskNotFoundError(task_id)
                session.expunge(record)
                return record
        finally:
            database.dispose()

    def list(self, *, limit: int = 100, task_type: str | None = None) -> list[WebTaskRecord]:
        database = self._database()
        try:
            with database.session() as session:
                statement = select(WebTaskRecord)
                if task_type:
                    statement = statement.where(WebTaskRecord.task_type == task_type)
                records = session.scalars(
                    statement.order_by(WebTaskRecord.created_at.desc()).limit(min(limit, 200))
                ).all()
                for record in records:
                    session.expunge(record)
                return list(records)
        finally:
            database.dispose()
