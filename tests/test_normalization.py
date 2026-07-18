from job_hunt.domain.models import WorkMode
from job_hunt.normalization import (
    canonicalize_url,
    description_hash,
    detect_work_mode,
    normalize_match_text,
    strip_html,
)


def test_canonicalize_url_removes_tracking_but_preserves_job_identifier():
    url = "https://Example.COM:443//jobs/1?utm_source=x&gh_jid=42&ref=mail#apply"
    assert canonicalize_url(url) == "https://example.com/jobs/1?gh_jid=42"


def test_description_hash_ignores_html_and_whitespace():
    assert description_hash("<p>Secure   endpoints</p>") == description_hash(
        "secure endpoints"
    )


def test_html_and_match_normalization():
    assert strip_html("<p>Identity &amp; Access</p>") == "Identity & Access"
    assert normalize_match_text("  Entra-ID  ") == "entra id"


def test_detect_work_modes():
    assert detect_work_mode("Remote in Brazil") == WorkMode.REMOTE
    assert detect_work_mode("Modelo híbrido") == WorkMode.HYBRID
    assert detect_work_mode("On-site Porto Alegre") == WorkMode.ONSITE

