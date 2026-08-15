import errno
import json

from job_hunt.state_store import atomic_write_json, load_json_state


def test_atomic_write_creates_backup(tmp_path):
    path = tmp_path / "state.json"
    atomic_write_json(path, {"one": 1})
    atomic_write_json(path, {"two": 2})
    assert json.loads(path.read_text(encoding="utf-8")) == {"two": 2}
    assert json.loads((tmp_path / "state.json.bak").read_text(encoding="utf-8")) == {"one": 1}


def test_corrupt_state_is_preserved_and_recovers(tmp_path):
    path = tmp_path / "state.json"
    path.write_text("{broken", encoding="utf-8")
    assert load_json_state(path, []) == []
    assert list(tmp_path.glob("state.json.corrupt-*"))


def test_bind_mount_fallback_keeps_persisted_recovery_backup(tmp_path, monkeypatch):
    monkeypatch.chdir(tmp_path)
    path = tmp_path / "config.json"
    path.write_text('{"before": true}\n', encoding="utf-8")

    def busy_replace(_source, _target):
        raise OSError(errno.EBUSY, "mount point is busy")

    monkeypatch.setattr("job_hunt.state_store.os.replace", busy_replace)
    atomic_write_json(path, {"after": True})
    assert json.loads(path.read_text(encoding="utf-8")) == {"after": True}
    backups = list((tmp_path / "state" / "config_backups").glob("config.json.*.bak"))
    assert len(backups) == 1
    assert json.loads(backups[0].read_text(encoding="utf-8")) == {"before": True}
