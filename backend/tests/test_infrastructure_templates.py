"""Static checks on the Bicep templates for mistakes that still deploy.

The alerting rules shipped in round 44 could never fire. `${appName}` was
written inside a Bicep multi-line string, which is *literal* - so the compiled
KQL asked for a container app named `${appName}`, matched nothing, and would
have stayed silent through the very outage it was added for. `az bicep build`
passed, `az deployment sub validate` passed, `what-if` showed the rules being
created, and the query ran fine when tested by hand because the hand-substituted
name was correct. Every check said yes.

That is the whole point of this file: these are errors of the kind that produce
healthy-looking green infrastructure, so they have to be caught by reading the
template rather than by deploying it.
"""

import re
from pathlib import Path

import pytest

BICEP_DIR = Path(__file__).resolve().parents[2] / "infra" / "bicep"
TEMPLATES = sorted(BICEP_DIR.glob("*.bicep"))

DELIMITER = "'" * 3
# A multi-line block is verbatim: no ${...} substitution happens inside it.
MULTILINE_BLOCK = re.compile(rf"{DELIMITER}(.*?){DELIMITER}", re.DOTALL)
QUERY_VARIABLE = re.compile(rf"var (\w+) = {DELIMITER}(.*?){DELIMITER}", re.DOTALL)
QUERY_ASSIGNMENT = re.compile(r"^\s*query: (.+)$", re.M)
SUBSTITUTED = re.compile(r"replace\(\w+, '__APP__', appName\)")


def _without_comments(source: str) -> str:
    """Strip Bicep comments before any block parsing.

    Learned the hard way, twice. A stray delimiter inside a `//` comment
    desynchronises the block pairing, and the desync does not merely produce
    false alarms - review demonstrated that it silently disarms the scan while
    the real bug sits in the file untouched. A checker that fails open is worse
    than no checker, because it is credited for the check it stopped doing.

    The lookbehind keeps `https://...` intact, which is otherwise the obvious
    way to break this file while fixing it.
    """
    return re.sub(r"(?<!:)//[^\n]*", "", source)


def _blocks(source: str) -> list[str]:
    return MULTILINE_BLOCK.findall(_without_comments(source))


def test_there_are_templates_to_check():
    """A glob that silently matches nothing would make every test below pass."""
    assert TEMPLATES, f"no Bicep templates found under {BICEP_DIR}"


@pytest.mark.parametrize("template", TEMPLATES, ids=lambda path: path.name)
def test_no_multi_line_string_pretends_to_interpolate(template: Path):
    """`${x}` inside a multi-line block is six literal characters, not a value.

    Nothing rejects it - not the compiler, not ARM, not the deployment. The
    resource is created with the placeholder still in it and simply never does
    what it was written to do.
    """
    source = _without_comments(template.read_text())
    # An odd count means the pairing below is nonsense: blocks would be read
    # from the *gaps* between the real ones, so the scan inspects the wrong text
    # and reports success.
    assert source.count(DELIMITER) % 2 == 0, (
        f"{template.name} has an odd number of multi-line string delimiters, so nothing "
        "reading this file can tell which text is inside a literal and which is code"
    )
    for block in MULTILINE_BLOCK.findall(source):
        assert "${" not in block, (
            f"{template.name} has a multi-line block containing '${{...}}', which Bicep "
            "does not interpolate - it ships the literal text. Substitute with "
            "replace(body, '__PLACEHOLDER__', value) instead.\n"
            f"block:\n{block.strip()}"
        )


def test_the_alert_queries_name_the_container_app_they_watch():
    """A log alert scoped to the workspace sees every app in it.

    The workspace also carries the catalogue job's logs, so a query that forgets
    to filter by application is not merely broader than intended - it can be
    triggered by a component the rule says nothing about.
    """
    resources = (BICEP_DIR / "resources.bicep").read_text()
    queries = [block for block in _blocks(resources) if "ContainerAppConsoleLogs_CL" in block]
    assert queries, "no container-app log queries found; have the alert rules been removed?"
    for query in queries:
        assert "__APP__" in query, (
            "an alert query does not filter on the container app name placeholder, so it "
            f"watches everything writing to the workspace:\n{query.strip()}"
        )
        assert "replace(" not in query, (
            "the placeholder is substituted outside the literal, not inside it"
        )


def test_every_placeholder_in_a_query_is_actually_substituted():
    """The placeholder is only useful if something replaces it.

    Renaming the variable, or copying a query into a new rule and forgetting
    the `replace()`, reproduces the original bug exactly - a rule that deploys
    green and matches nothing.
    """
    resources = _without_comments((BICEP_DIR / "resources.bicep").read_text())
    watching = [name for name, body in QUERY_VARIABLE.findall(resources) if "__APP__" in body]
    assert watching, "no query variables carry the app-name placeholder"

    # Every use site, not merely somewhere in the file. Review broke the first
    # version of this check by adding a fourth rule that passed the raw variable
    # while the original rule's correct replace() stayed put to satisfy a
    # file-wide search - which is exactly the "copy a rule and forget the
    # replace()" mistake this exists to prevent.
    uses = [use.strip() for use in QUERY_ASSIGNMENT.findall(resources)]
    assert len(uses) >= len(watching), (
        f"found {len(uses)} query assignments for {len(watching)} query variables carrying "
        "a placeholder; a rule that never uses its query variable is a rule watching nothing"
    )
    for use in uses:
        assert SUBSTITUTED.fullmatch(use), (
            f"an alert rule is assigned {use!r}. A query has to reach the rule through "
            "replace(<var>, '__APP__', appName); passing the variable itself ships the "
            "placeholder, and the rule then looks for a container app named '__APP__'."
        )
