import threading
import time

import pytest

from job_hunt.persistence.migration import upgrade_database
from job_hunt.persistence.models import WebTaskRecord
from job_hunt.web.tasks import LocalTaskManager, TaskConflictError


def _wait(manager, task_id):
    for _ in range(200):
        record = manager.get(task_id)
        if record.state not in {"queued", "running", "cancel_requested"}:
            return record
        time.sleep(0.01)
    raise AssertionError("task did not finish")


def test_persisted_tasks_complete_fail_and_recover(tmp_path):
    url = f"sqlite:///{(tmp_path / 'tasks.db').as_posix()}"
    upgrade_database(url)

    def success(context, payload):
        context.progress(50, "metade")
        return {"answer": payload["value"] * 2}

    def failure(_context, _payload):
        raise RuntimeError("token=secret should be redacted")

    manager = LocalTaskManager(url, {"success": success, "failure": failure})
    completed = _wait(manager, manager.submit("success", {"value": 21}).id)
    assert completed.state == "completed"
    assert completed.result_data == {"answer": 42}
    failed = _wait(manager, manager.submit("failure").id)
    assert failed.state == "failed"
    assert "secret" not in (failed.error or "")
    assert len(manager.list()) == 2


def test_exclusive_task_conflict_and_safe_cancel(tmp_path):
    url = f"sqlite:///{(tmp_path / 'tasks.db').as_posix()}"
    upgrade_database(url)
    started = threading.Event()

    def bounded(context, _payload):
        started.set()
        for value in range(2, 99):
            time.sleep(0.005)
            context.progress(value, "working")
        return {}

    manager = LocalTaskManager(url, {"bounded": bounded})
    first = manager.submit("bounded", exclusive=True, cancel_safe=True)
    assert started.wait(1)
    with pytest.raises(TaskConflictError):
        manager.submit("bounded", exclusive=True)
    manager.cancel(first.id)
    assert _wait(manager, first.id).state == "cancelled"


def test_catalog_import_is_durable_and_resumes_after_restart(tmp_path):
    url = f"sqlite:///{(tmp_path / 'tasks.db').as_posix()}"
    upgrade_database(url)
    first_manager = LocalTaskManager(url, {})
    database = first_manager._database()
    try:
        with database.session() as session:
            interrupted = WebTaskRecord(
                task_type="import_portal_catalog",
                state="running",
                progress=45,
                message="Importando",
                payload_data={"batch": 1},
                cancel_safe=True,
            )
            session.add(interrupted)
            session.flush()
            task_id = interrupted.id
    finally:
        database.dispose()

    handled = threading.Event()

    def resumed(context, payload):
        assert payload == {"batch": 1}
        context.progress(80, "Retomada")
        handled.set()
        return {"resumed": True}

    manager = LocalTaskManager(url, {"import_portal_catalog": resumed})
    assert handled.wait(1)
    record = _wait(manager, task_id)
    assert record.state == "completed"
    assert record.result_data == {"resumed": True}


def test_catalog_import_thread_is_non_daemon(tmp_path):
    url = f"sqlite:///{(tmp_path / 'tasks.db').as_posix()}"
    upgrade_database(url)
    started = threading.Event()
    finish = threading.Event()

    def import_catalog(_context, _payload):
        started.set()
        assert finish.wait(1)
        return {}

    manager = LocalTaskManager(url, {"import_portal_catalog": import_catalog})
    task = manager.submit("import_portal_catalog")
    assert started.wait(1)
    assert not manager._threads[task.id].daemon
    finish.set()
    manager.shutdown()
    assert _wait(manager, task.id).state == "completed"


def test_recurring_task_runs_sequential_batches_until_backlog_is_empty(tmp_path):
    url = f"sqlite:///{(tmp_path / 'tasks.db').as_posix()}"
    upgrade_database(url)
    remaining = {"batches": 3}
    completed = threading.Event()
    execution_guard = threading.Lock()
    simultaneous = {"value": 0, "maximum": 0}

    def import_batch(_context, payload):
        assert payload == {"automatic": True}
        with execution_guard:
            simultaneous["value"] += 1
            simultaneous["maximum"] = max(simultaneous["maximum"], simultaneous["value"])
        time.sleep(0.01)
        remaining["batches"] -= 1
        with execution_guard:
            simultaneous["value"] -= 1
        if remaining["batches"] == 0:
            completed.set()
        return {"pending": remaining["batches"]}

    manager = LocalTaskManager(url, {"import_portal_catalog": import_batch})
    manager.start_recurring_until_complete(
        "import_portal_catalog",
        lambda: remaining["batches"] > 0,
        interval_seconds=0.01,
        payload={"automatic": True},
    )

    assert completed.wait(2)
    for _ in range(100):
        if manager.recurring_status("import_portal_catalog")["state"] == "completed":
            break
        time.sleep(0.01)
    records = manager.list(task_type="import_portal_catalog")
    manager.shutdown()

    assert len(records) == 3
    assert all(record.state == "completed" for record in records)
    assert simultaneous["maximum"] == 1
    assert manager.recurring_status("import_portal_catalog")["state"] == "completed"


def test_recurring_task_retries_a_failed_batch_while_work_remains(tmp_path):
    url = f"sqlite:///{(tmp_path / 'tasks.db').as_posix()}"
    upgrade_database(url)
    attempts = {"value": 0}
    pending = {"value": True}
    completed = threading.Event()

    def flaky(_context, _payload):
        attempts["value"] += 1
        if attempts["value"] == 1:
            raise RuntimeError("temporary failure")
        pending["value"] = False
        completed.set()
        return {"pending": 0}

    manager = LocalTaskManager(url, {"import_portal_catalog": flaky})
    manager.start_recurring_until_complete(
        "import_portal_catalog",
        lambda: pending["value"],
        interval_seconds=0.01,
    )

    assert completed.wait(2)
    manager.shutdown()
    states = [record.state for record in manager.list(task_type="import_portal_catalog")]
    assert attempts["value"] == 2
    assert sorted(states) == ["completed", "failed"]


def test_exclusive_submit_recovers_an_orphaned_active_task(tmp_path):
    url = f"sqlite:///{(tmp_path / 'tasks.db').as_posix()}"
    upgrade_database(url)
    manager = LocalTaskManager(url, {"import_portal_catalog": lambda _c, _p: {"ok": True}})
    database = manager._database()
    try:
        with database.session() as session:
            orphan = WebTaskRecord(
                task_type="import_portal_catalog",
                state="running",
                progress=20,
                message="Importando",
                payload_data={},
                cancel_safe=True,
            )
            session.add(orphan)
            session.flush()
            orphan_id = orphan.id
    finally:
        database.dispose()

    replacement = manager.submit("import_portal_catalog", exclusive=True)
    assert _wait(manager, replacement.id).state == "completed"
    recovered = manager.get(orphan_id)
    assert recovered.state == "failed"
    assert "órfã" in recovered.message
    manager.shutdown()
