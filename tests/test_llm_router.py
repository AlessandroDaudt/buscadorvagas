import pytest
from pydantic import BaseModel

from job_hunt.llm.base import StructuredResponse, TokenUsage
from job_hunt.llm.config import LLMSettings, ProviderSettings
from job_hunt.llm.router import LLMBudgetExceeded, LLMRouter


class Result(BaseModel):
    answer: str


class FakeProvider:
    name = "fake"
    model = "fixture"

    def __init__(self, outcome):
        self.outcome = outcome

    def generate(self, **_kwargs):
        if isinstance(self.outcome, Exception):
            raise self.outcome
        return self.outcome


def _response(cost: float = 0.1) -> StructuredResponse:
    return StructuredResponse(Result(answer="ok"), "fake", "fixture", TokenUsage(10, 5, cost), 0.01)


def test_router_retries_then_succeeds(monkeypatch):
    outcomes = iter([TimeoutError(), _response()])
    monkeypatch.setattr(
        "job_hunt.llm.router.build_provider",
        lambda *_args, **_kwargs: FakeProvider(next(outcomes)),
    )
    settings = LLMSettings(
        enabled=True,
        primary=ProviderSettings(provider="openai", model="fixture"),
        max_retries=1,
    )
    router = LLMRouter(settings, sleep=lambda _seconds: None)
    result = router.generate(system_prompt="s", user_prompt="u", response_model=Result)
    assert result.data == Result(answer="ok")
    assert router.run_cost_usd == 0.1


def test_router_uses_fallback(monkeypatch):
    def factory(settings, **_kwargs):
        return FakeProvider(RuntimeError("down") if settings.provider == "openai" else _response())

    monkeypatch.setattr("job_hunt.llm.router.build_provider", factory)
    settings = LLMSettings(
        enabled=True,
        primary=ProviderSettings(provider="openai", model="one"),
        fallback=[ProviderSettings(provider="gemini", model="two")],
        max_retries=0,
    )
    result = LLMRouter(settings).generate(system_prompt="s", user_prompt="u", response_model=Result)
    assert result.data == Result(answer="ok")


def test_router_rejects_disabled_and_budget_exhaustion():
    disabled = LLMRouter(LLMSettings(enabled=False))
    with pytest.raises(RuntimeError, match="disabled"):
        disabled.generate(system_prompt="s", user_prompt="u", response_model=Result)

    settings = LLMSettings(enabled=True, run_cost_limit_usd=0)
    with pytest.raises(LLMBudgetExceeded, match="Per-run"):
        LLMRouter(settings).generate(system_prompt="s", user_prompt="u", response_model=Result)


def test_monthly_budget_is_enforced_before_call():
    settings = LLMSettings(enabled=True, monthly_cost_limit_usd=10)
    with pytest.raises(LLMBudgetExceeded, match="Monthly"):
        LLMRouter(settings, monthly_cost=lambda: 10).generate(
            system_prompt="s", user_prompt="u", response_model=Result
        )


def test_configured_maximum_request_cost_is_enforced():
    settings = LLMSettings(
        enabled=True,
        run_cost_limit_usd=0.01,
        max_output_tokens=1000,
        primary=ProviderSettings(
            provider="openai",
            model="expensive",
            output_cost_per_million=100,
        ),
    )
    with pytest.raises(LLMBudgetExceeded, match="Per-run"):
        LLMRouter(settings).generate(system_prompt="s", user_prompt="u", response_model=Result)
