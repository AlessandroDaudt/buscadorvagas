from job_hunt.connectors.ashby import AshbyConnector
from job_hunt.connectors.base import ConnectorContext
from job_hunt.connectors.generic_html import GenericHtmlConnector
from job_hunt.connectors.jsonld import parse_job_postings
from job_hunt.connectors.registry import build_connector, detect_connector, normalize_company
from job_hunt.connectors.smartrecruiters import SmartRecruitersConnector
from job_hunt.connectors.workable import WorkableConnector


class FakeClient:
    def __init__(self, payloads):
        self.payloads = payloads

    def get_json(self, url, *, allowed_hosts=None):
        for marker, payload in self.payloads.items():
            if marker in url:
                return payload
        raise AssertionError(url)


def test_ashby_connector_parses_public_fixture():
    client = FakeClient({"posting-api": {"jobs": [{"id": "a1", "title": "IAM Engineer", "jobUrl": "https://jobs.ashbyhq.com/acme/a1", "location": "Remote", "descriptionHtml": "<p>Entra ID</p>"}]}})
    result = AshbyConnector("acme", "Acme", client).collect(ConnectorContext())
    assert result.jobs[0].title == "IAM Engineer"
    assert result.jobs[0].description == "Entra ID"


def test_smartrecruiters_connector_fetches_details():
    client = FakeClient({
        "?limit=100": {"content": [{"id": "s1"}]},
        "/s1": {"id": "s1", "name": "Security Engineer", "ref": "https://jobs.smartrecruiters.com/acme/s1", "location": {"city": "Porto Alegre", "country": "BR"}, "jobAd": {"sections": {"desc": {"text": "<p>Defender</p>"}}}},
    })
    result = SmartRecruitersConnector("acme", "Acme", client).collect(ConnectorContext())
    assert result.jobs[0].location == "Porto Alegre, BR"


def test_workable_connector_parses_public_fixture():
    client = FakeClient({"/jobs": {"results": [{"shortcode": "W1", "title": "Cloud Support", "url": "https://apply.workable.com/acme/j/W1/", "location": {"city": "Remote"}, "description": "<b>Linux</b>"}]}})
    result = WorkableConnector("acme", "Acme", client).collect(ConnectorContext())
    assert result.jobs[0].description == "Linux"


def test_jsonld_jobposting_fixture():
    markup = '''<script type="application/ld+json">{"@type":"JobPosting","title":"IAM Lead","url":"https://acme.example/jobs/1","description":"<p>OAuth</p>","hiringOrganization":{"name":"Acme"},"jobLocationType":"TELECOMMUTE"}</script>'''
    jobs = parse_job_postings(markup, company_fallback="Fallback", source_url="https://acme.example/careers", context=ConnectorContext())
    assert len(jobs) == 1
    assert jobs[0].company == "Acme"
    assert jobs[0].location == "Remote"


class FakeTextClient:
    def __init__(self, text):
        self.text = text

    def get_text(self, *_args, **_kwargs):
        return self.text

    def get_json(self, *_args, **_kwargs):
        return {}


class AllowRobots:
    def require_allowed(self, *_args, **_kwargs):
        return None


def test_generic_html_collects_static_links_and_detects_blocks():
    company = normalize_company({"name": "Acme", "careers_url": "https://acme.example/careers", "location": "Remote"})
    html = '<a href="/jobs/1234">Senior IAM Engineer</a>'
    result = GenericHtmlConnector(company, FakeTextClient(html), AllowRobots()).collect(ConnectorContext())
    assert result.jobs[0].title == "Senior IAM Engineer"
    assert result.jobs[0].source_name == "generic_html"
    captcha = GenericHtmlConnector(company, FakeTextClient("g-recaptcha"), AllowRobots()).collect(ConnectorContext())
    assert captcha.errors[0].code == "captcha_detected"
    login = GenericHtmlConnector(company, FakeTextClient("sign in to continue"), AllowRobots()).collect(ConnectorContext())
    assert login.errors[0].code == "authentication_required"


def test_registry_detects_and_builds_supported_connectors():
    cases = [
        ("https://boards.greenhouse.io/acme", "greenhouse"),
        ("https://jobs.lever.co/acme", "lever"),
        ("https://jobs.ashbyhq.com/acme", "ashby"),
        ("https://jobs.smartrecruiters.com/acme", "smartrecruiters"),
        ("https://apply.workable.com/acme", "workable"),
        ("https://acme.example/careers", "generic_html"),
    ]
    for url, expected in cases:
        company = normalize_company({"name": "Acme", "careers_url": url})
        assert detect_connector(company) == expected
        assert build_connector(company, FakeTextClient(""), AllowRobots()).source_name == expected


def test_registry_rejects_invalid_and_disabled_shapes():
    try:
        normalize_company({"name": "Acme", "careers_url": "http://unsafe.example"})
    except ValueError as exc:
        assert "HTTPS" in str(exc)
    company = normalize_company({"name": "Acme", "careers_url": "https://acme.example", "connector": "unknown"})
    try:
        detect_connector(company)
    except ValueError as exc:
        assert "Unsupported" in str(exc)
