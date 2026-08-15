import os
import subprocess
from urllib.parse import urlsplit

import pytest

from job_hunt.http_client import SafeHttpClient
from job_hunt.ollama import OllamaClient, OllamaSettings


@pytest.mark.local_model
def test_live_local_ollama_when_requested():
    if os.getenv("RUN_LOCAL_MODEL_TESTS") != "1":
        pytest.skip("set RUN_LOCAL_MODEL_TESTS=1")
    settings = OllamaSettings(base_url=os.getenv("OLLAMA_BASE_URL", "http://localhost:11434"))
    with OllamaClient(settings) as client:
        assert client.chat([{"role": "user", "content": "Reply LOCAL_OK"}], max_tokens=16).content


@pytest.mark.gpu
def test_nvidia_gpu_when_requested():
    if os.getenv("RUN_GPU_TESTS") != "1":
        pytest.skip("set RUN_GPU_TESTS=1")
    result = subprocess.run(["nvidia-smi", "-L"], capture_output=True, text=True, check=False)
    assert result.returncode == 0 and "GPU" in result.stdout


@pytest.mark.live_source
def test_public_source_when_explicitly_configured():
    url = os.getenv("LIVE_SOURCE_URL")
    if not url:
        pytest.skip("set LIVE_SOURCE_URL to an allowlisted public careers page")
    host = urlsplit(url).hostname
    assert host
    with SafeHttpClient(connector="live_source_test") as client:
        assert client.get_text(url, allowed_hosts={host}, cache_ttl_seconds=0)
