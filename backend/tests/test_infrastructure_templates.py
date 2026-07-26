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

# A ''' ... ''' block is verbatim: no ${...} substitution happens inside it.
MULTILINE_BLOCK = re.compile(r"'''(.*?)'''", re.DOTALL)


def test_there_are_templates_to_check():
    """A glob that silently matches nothing would make every test below pass."""
    assert TEMPLATES, f"no Bicep templates found under {BICEP_DIR}"


@pytest.mark.parametrize("template", TEMPLATES, ids=lambda path: path.name)
def test_no_multi_line_string_pretends_to_interpolate(template: Path):
    """`${x}` inside a ''' block is six literal characters, not a value.

    Nothing rejects it - not the compiler, not ARM, not the deployment. The
    resource is created with the placeholder still in it and simply never does
    what it was written to do.
    """
    for block in MULTILINE_BLOCK.findall(template.read_text()):
        assert "${" not in block, (
            f"{template.name} has a ''' block containing '${{...}}', which Bicep does "
            "not interpolate - it ships the literal text. Substitute with "
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
    queries = [
        block
        for block in MULTILINE_BLOCK.findall(resources)
        if "ContainerAppConsoleLogs_CL" in block
    ]
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
    resources = (BICEP_DIR / "resources.bicep").read_text()
    query_vars = re.findall(r"var (\w+) = '''(.*?)'''", resources, re.DOTALL)
    watching = [name for name, body in query_vars if "__APP__" in body]
    assert watching, "no query variables carry the app-name placeholder"
    for name in watching:
        assert re.search(rf"replace\({name}, '__APP__', appName\)", resources), (
            f"{name} contains __APP__ but nothing substitutes it, so the rule using it "
            "would look for a container app literally named '__APP__' and never fire"
        )
