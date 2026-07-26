"""What we actually send Azure OpenAI, checked without calling it.

Intent extraction failed on every request in production for days. The cause was
one string: `reasoning.effort` was `"minimal"`, a value the retired gpt-5-nano
accepted and no deployment we run does. Both current models answer it with a
400. The service caught the exception, fell back to deterministic parsing and
returned HTTP 200 with a full page of products, so the whole offline suite, the
browser journeys and every health check stayed green while the product silently
stopped understanding anything.

Nothing offline could have caught it, because nothing offline looked at the
request. The Azure provider had no test at all - it was covered only by the
opt-in live suite, which is exactly the suite you do not run when you believe
you have changed nothing important.
"""

import ast
from pathlib import Path
from typing import Any

import pytest

from app.assistant.provider import AzureOpenAIProvider
from app.common.config import Settings

# Accepted by both gpt-5.4-nano and gpt-5.4-mini, verified against the live
# endpoint. "minimal" is absent deliberately: it is the value that broke
# production, and the API's own error names these as the alternatives.
SUPPORTED_EFFORTS = {"none", "low", "medium", "high", "xhigh"}


class _CapturingResponses:
    def __init__(self) -> None:
        self.kwargs: dict[str, Any] = {}

    async def create(self, **kwargs: Any) -> Any:
        self.kwargs = kwargs
        raise _StopHere


class _StopHere(Exception):
    """Raised once the request is captured; the response is not what is under test."""


class _CapturingClient:
    def __init__(self) -> None:
        self.responses = _CapturingResponses()


def _provider() -> tuple[AzureOpenAIProvider, _CapturingClient]:
    provider = AzureOpenAIProvider(
        Settings(
            azure_openai_endpoint="https://example.openai.azure.com/",
            azure_openai_api_key="not-a-real-key",
        )
    )
    client = _CapturingClient()
    provider.client = client  # type: ignore[assignment]
    return provider, client


async def test_intent_extraction_asks_for_an_effort_the_models_accept():
    """The exact defect: a `reasoning.effort` no deployment supports."""
    provider, client = _provider()

    with pytest.raises(_StopHere):
        await provider.extract_intent("hoi an lantern")

    effort = client.responses.kwargs["reasoning"]["effort"]
    assert effort in SUPPORTED_EFFORTS, (
        f"reasoning.effort={effort!r} is rejected with a 400 by every deployment we run. "
        f"Intent extraction would fall back to deterministic parsing on every request, "
        f"which still returns HTTP 200 and a full page of results. Supported: "
        f"{sorted(SUPPORTED_EFFORTS)}"
    )


def test_no_call_site_anywhere_asks_for_an_unsupported_effort():
    """Every `reasoning={"effort": ...}` in the provider, including ones not yet written.

    The test above stubs one method and so only covers the paths a test author
    remembered to stub - which is the same bet that lost last time, since the
    broken call site had no test. Reading the source instead covers every call
    site by construction, so a new one added with a bad value fails here even if
    nobody writes a test for it.
    """
    source = (Path(__file__).parents[1] / "app" / "assistant" / "provider.py").read_text()
    tree = ast.parse(source)

    found: list[tuple[int, str]] = []
    for node in ast.walk(tree):
        if not isinstance(node, ast.keyword) or node.arg != "reasoning":
            continue
        if not isinstance(node.value, ast.Dict):
            continue
        for key, value in zip(node.value.keys, node.value.values, strict=True):
            if isinstance(key, ast.Constant) and key.value == "effort":
                if isinstance(value, ast.Constant):
                    found.append((value.lineno, value.value))

    assert found, "no reasoning.effort call sites found - has the provider been restructured?"

    bad = [(line, effort) for line, effort in found if effort not in SUPPORTED_EFFORTS]
    assert not bad, (
        "provider.py asks for a reasoning.effort no deployment accepts: "
        + ", ".join(f"line {line}: {effort!r}" for line, effort in bad)
        + f". Every such call 400s and is silently swallowed. Supported: {sorted(SUPPORTED_EFFORTS)}"
    )
