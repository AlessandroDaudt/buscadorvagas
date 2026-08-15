"""Local config composition and external-secret rejection."""

import json

import pytest

from job_hunt import main


def write_local_config(path):
    path.write_text(json.dumps({
        "local_only": True,
        "llm_provider": "ollama",
        "ollama": {"base_url": "http://localhost:11434"},
    }), encoding="utf-8")


def test_candidate_environment_overrides_local_config(tmp_path, monkeypatch, clean_env):
    monkeypatch.chdir(tmp_path)
    write_local_config(tmp_path / "config.json")
    monkeypatch.setenv("CANDIDATE_NAME", "Ada")
    monkeypatch.setenv("MIN_SCORE", "70")
    config = main.load_config()
    assert config["candidate"]["name"] == "Ada"
    assert config["candidate"]["min_score"] == 70


def test_external_secret_in_environment_is_rejected(tmp_path, monkeypatch, clean_env):
    monkeypatch.chdir(tmp_path)
    write_local_config(tmp_path / "config.json")
    monkeypatch.setenv("ANTHROPIC_API_KEY", "real-secret")
    with pytest.raises(SystemExit, match="ANTHROPIC_API_KEY is configured"):
        main.load_config()


def test_external_secret_in_config_is_rejected(tmp_path, monkeypatch, clean_env):
    monkeypatch.chdir(tmp_path)
    write_local_config(tmp_path / "config.json")
    data = json.loads((tmp_path / "config.json").read_text())
    data["tinyfish_api_key"] = "real-secret"
    (tmp_path / "config.json").write_text(json.dumps(data), encoding="utf-8")
    with pytest.raises(SystemExit, match="tinyfish_api_key is configured"):
        main.load_config()
