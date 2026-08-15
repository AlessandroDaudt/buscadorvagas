import json

import pytest

from job_hunt.web.application_services import CompanyConfigService


def test_company_crud_is_atomic_and_rejects_private_resolution(tmp_path):
    path = tmp_path / "companies.json"
    path.write_text("[]\n", encoding="utf-8")
    public = CompanyConfigService(path, resolver=lambda _host: ["8.8.8.8"])
    company = public.add(
        {
            "name": "Example",
            "careers_url": "https://careers.example/jobs",
            "connector": "generic_html",
            "enabled": True,
        }
    )
    assert json.loads(path.read_text(encoding="utf-8"))[0]["name"] == "Example"
    duplicate = public.duplicate(company["id"])
    assert duplicate["enabled"] is False
    assert path.with_suffix(".json.bak").exists()
    public.delete(duplicate["id"])
    assert len(public.list()) == 1

    private = CompanyConfigService(path, resolver=lambda _host: ["127.0.0.1"])
    with pytest.raises(ValueError, match="private"):
        private.add(
            {
                "name": "Unsafe",
                "careers_url": "https://unsafe.example/jobs",
                "connector": "generic_html",
            }
        )
