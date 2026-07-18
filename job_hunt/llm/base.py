"""Provider-neutral structured response contracts."""

from __future__ import annotations

from dataclasses import dataclass
from typing import Protocol, TypeVar

from pydantic import BaseModel

StructuredT = TypeVar("StructuredT", bound=BaseModel)


@dataclass(frozen=True)
class TokenUsage:
    input_tokens: int = 0
    output_tokens: int = 0
    estimated_cost_usd: float = 0


@dataclass(frozen=True)
class StructuredResponse:
    data: BaseModel
    provider: str
    model: str
    usage: TokenUsage
    duration_seconds: float


class StructuredProvider(Protocol):
    name: str
    model: str

    def generate(
        self,
        *,
        system_prompt: str,
        user_prompt: str,
        response_model: type[StructuredT],
        max_output_tokens: int,
    ) -> StructuredResponse: ...
