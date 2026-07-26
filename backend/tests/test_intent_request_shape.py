"""What we actually send Azure OpenAI, checked without calling it.

Intent extraction failed on every request in production for days. The cause was
one string: `reasoning.effort` was `"minimal"`, which gpt-5.4-nano and
gpt-5.4-mini both answer with a 400. The service caught the exception, fell back
to deterministic parsing and returned HTTP 200 with a full page of products, so
the whole offline suite, the browser journeys and every health check stayed
green while the product silently stopped understanding anything.

Nothing offline could have caught it, because nothing offline looked at the
request. The Azure provider had no test at all - it was covered only by the
opt-in live suite, which is exactly the suite you do not run when you believe
you have changed nothing important.

Fixing that string did not restore production, and the reason is the second test
here. `"minimal"` was never universally wrong: gpt-5-nano accepts it and rejects
`"none"`, and the 5.4 models do the exact opposite. Which value is correct is
therefore a property of the deployment name in the Bicep template, not of this
file. The deploy that shipped `"none"` also reapplied that template, which
declared gpt-5-nano, so the code and the configuration crossed in mid-air and
the outage continued with the error message inverted. Two files that must agree
and no test that read both.
"""

import ast
import re
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
        await provider.extract_intent(
            "hoi an lantern",
            categories=["Culture"],
            destinations=["Hoi An"],
        )

    effort = client.responses.kwargs["reasoning"]["effort"]
    assert effort in SUPPORTED_EFFORTS, (
        f"reasoning.effort={effort!r} is rejected with a 400 by every deployment we run. "
        f"Intent extraction would fall back to deterministic parsing on every request, "
        f"which still returns HTTP 200 and a full page of results. Supported: "
        f"{sorted(SUPPORTED_EFFORTS)}"
    )


PROVIDER_SOURCE = (
    Path(__file__).resolve().parents[1] / "app" / "assistant" / "provider.py"
).read_text()


def _reasoning_efforts(source: str) -> list[tuple[int, str]]:
    """Every literal `reasoning={"effort": ...}` in the provider, with line numbers.

    Read from the source rather than by stubbing methods, so a call site added
    later is covered whether or not anyone writes a test for it. The broken one
    had no test.
    """
    found: list[tuple[int, str]] = []
    for node in ast.walk(ast.parse(source)):
        if not isinstance(node, ast.keyword) or node.arg != "reasoning":
            continue
        if not isinstance(node.value, ast.Dict):
            continue
        for key, value in zip(node.value.keys, node.value.values, strict=True):
            if isinstance(key, ast.Constant) and key.value == "effort":
                if isinstance(value, ast.Constant):
                    found.append((value.lineno, value.value))
    assert found, "no reasoning.effort call sites found - has the provider been restructured?"
    return found


def test_no_call_site_anywhere_asks_for_an_unsupported_effort():
    """Every `reasoning={"effort": ...}` in the provider, including ones not yet written.

    The test above stubs one method and so only covers the paths a test author
    remembered to stub - which is the same bet that lost last time, since the
    broken call site had no test. Reading the source instead covers every call
    site by construction, so a new one added with a bad value fails here even if
    nobody writes a test for it.
    """
    found = _reasoning_efforts(PROVIDER_SOURCE)
    bad = [(line, effort) for line, effort in found if effort not in SUPPORTED_EFFORTS]
    assert not bad, (
        "provider.py asks for a reasoning.effort no deployment accepts: "
        + ", ".join(f"line {line}: {effort!r}" for line, effort in bad)
        + f". Every such call 400s and is silently swallowed. Supported: {sorted(SUPPORTED_EFFORTS)}"
    )


# Which efforts each deployment accepts, established against the live endpoint
# rather than from documentation. The disagreement between the two rows is the
# entire hazard: there is no value that is safe on both.
EFFORTS_BY_MODEL = {
    "gpt-5-nano": {"minimal", "low", "medium", "high"},
    "gpt-5.4-nano": {"none", "low", "medium", "high", "xhigh"},
    "gpt-5.4-mini": {"none", "low", "medium", "high", "xhigh"},
}

BICEP = Path(__file__).resolve().parents[2] / "infra" / "bicep" / "main.bicep"


def _bicep_param(name: str) -> str:
    """A string parameter's default, read from the template production deploys.

    Not from Settings: the container's environment is written by Bicep, so the
    default in `config.py` is what a developer gets locally and has no bearing
    on what production sends. Reading the wrong one of those two is how this was
    missed the first time. scripts/deploy.sh passes no model parameters, so
    these defaults are what production runs.
    """
    pattern = re.compile(rf"^param\s+{re.escape(name)}\s+string\s*=\s*'([^']+)'")
    for line in BICEP.read_text().splitlines():
        match = pattern.match(line.strip())
        if match:
            return match.group(1)
    raise AssertionError(
        f"no string parameter {name!r} with a literal default found in {BICEP}. "
        "If it moved to a parameters file or is now passed by deploy.sh, this test is "
        "reading the wrong source and must be updated - it fails rather than guessing."
    )


def _declared_intent_deployment() -> str:
    return _bicep_param("intentDeployment")


def test_the_intent_deployment_is_one_the_template_actually_creates():
    """`intentDeployment` is a free parameter; nothing tied it to a real model.

    infra/bicep/ai-integration.bicep creates exactly two chat deployments,
    chatDeployment and nanoDeployment. intentDeployment merely happened to equal
    the first. Point it at a name that is not deployed and every intent call
    404s - and the fallback answers HTTP 200 with a full page, so it looks
    exactly like the two outages this file already exists for.
    """
    intent = _declared_intent_deployment()
    available = {_bicep_param("chatDeployment"), _bicep_param("nanoDeployment")}
    assert intent in available, (
        f"infra/bicep/main.bicep sets intentDeployment to {intent!r}, which is not one of "
        f"the deployments ai-integration.bicep creates ({sorted(available)}). Every intent "
        f"call would 404 and silently fall back to deterministic parsing."
    )


def test_the_effort_we_send_is_accepted_by_the_deployment_we_declare():
    """The pairing that took production down twice in one day.

    Changing either file alone is a valid-looking change that passes review and
    every other test, and takes intent extraction down the moment it deploys -
    without failing anything, because the fallback answers 200.
    """
    deployment = _declared_intent_deployment()
    assert deployment in EFFORTS_BY_MODEL, (
        f"infra/bicep/main.bicep declares intentDeployment '{deployment}', which this "
        f"test has no verified effort list for. Add it to EFFORTS_BY_MODEL by checking "
        f"against the live endpoint - do not guess, the models genuinely disagree."
    )

    accepted = EFFORTS_BY_MODEL[deployment]
    for line_number, effort in _reasoning_efforts(PROVIDER_SOURCE):
        assert effort in accepted, (
            f"provider.py:{line_number} sends reasoning.effort={effort!r}, which "
            f"{deployment} rejects with a 400. That deployment accepts {sorted(accepted)}. "
            f"Either this call site or the intentDeployment parameter in "
            f"infra/bicep/main.bicep is wrong; they are only ever correct together."
        )


def test_the_local_default_is_the_deployment_production_declares():
    """Otherwise the live suite validates a model production does not run.

    `Settings.azure_openai_intent_deployment` is what the live intent tests and
    the comparison harness talk to when nothing overrides it. If it names a
    different model from the Bicep parameter, those tests can pass against one
    deployment while production 400s on another - which is the failure this file
    exists for, wearing a disguise.
    """
    declared = _declared_intent_deployment()
    default = Settings(
        azure_openai_endpoint="https://example.openai.azure.com/",
        azure_openai_api_key="not-a-real-key",
    ).azure_openai_intent_deployment
    assert default == declared, (
        f"config.py defaults intent extraction to {default!r} but "
        f"infra/bicep/main.bicep deploys {declared!r}. Local and live runs would "
        f"exercise a different model from the one serving shoppers."
    )
