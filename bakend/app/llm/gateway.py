"""The complete model-policy dependency surface; fake gateways need no application."""

from typing import Any, Protocol

from ..models import ChatRequest


class ModelGateway(Protocol):
    max_tokens: int

    @property
    def mode(self) -> str: ...
    async def complete(
        self, messages: list[dict[str, Any]], *, max_tokens: int | None = None
    ) -> str: ...
    async def json_response(
        self, messages, *, stage, validate=None, max_tokens=2000
    ): ...
    def system_message(self, prompt: str, request: ChatRequest) -> dict[str, str]: ...
    def extract_json_object(self, content: str) -> dict[str, Any]: ...
