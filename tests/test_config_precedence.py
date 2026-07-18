"""config.json + environment composition with secret isolation."""

import json

import pytest

from job_hunt import main


@pytest.fixture
def workdir(tmp_path, monkeypatch):
    monkeypatch.chdir(tmp_path)
    for key in (
        "TINYFISH_API_KEY",
        "OPENROUTER_API_KEY",
        "OPENROUTER_MODEL",
        "ANTHROPIC_API_KEY",
        "ANTHROPIC_MODEL",
        "TELEGRAM_TOKEN",
        "TELEGRAM_CHAT_ID",
    ):
        monkeypatch.delenv(key, raising=False)
    monkeypatch.setenv("TINYFISH_API_KEY", "sk-real-env-key")
    return tmp_path


def _write_config(workdir, **overrides):
    (workdir / "config.json").write_text(json.dumps(overrides))


def test_placeholder_env_does_not_override_nonsecret_config(workdir, monkeypatch):
    _write_config(workdir, openrouter_model="configured/model")
    monkeypatch.setenv("OPENROUTER_MODEL", "your_openrouter_model_here")

    cfg = main.load_config()

    assert cfg["openrouter_model"] == "configured/model"


def test_real_env_overrides_nonsecret_config(workdir, monkeypatch):
    _write_config(workdir, openrouter_model="configured/model")
    monkeypatch.setenv("OPENROUTER_MODEL", "environment/model")

    cfg = main.load_config()

    assert cfg["openrouter_model"] == "environment/model"


def test_secret_in_config_is_rejected(workdir):
    _write_config(workdir, tinyfish_api_key="sk-must-not-be-in-json")

    with pytest.raises(SystemExit, match="Secrets are not allowed"):
        main.load_config()


def test_anthropic_key_bridged_from_env(workdir, monkeypatch):
    _write_config(workdir)
    monkeypatch.setenv("ANTHROPIC_API_KEY", "sk-ant-real")

    cfg = main.load_config()

    assert cfg["anthropic_api_key"] == "sk-ant-real"
