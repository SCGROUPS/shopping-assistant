"""Prove the deployed stack still interprets a request, not merely that it answers one.

A deploy went out in which intent extraction failed on *every* request. The
model rejected our `reasoning.effort` value with a 400, the service caught it,
logged a warning and fell back to deterministic parsing - which by design says
almost nothing - so the API returned HTTP 200 with a full page of results and
every external check passed. Destination filtering, category filtering and
assistant routing were all dead and nothing said so.

Liveness is the wrong question for this system. A search endpoint that has
stopped understanding anything still returns twenty products, because returning
twenty products is what it does when it understands nothing. So this checks the
one thing the fallback cannot fake: that a query naming a city comes back with
that city resolved.

Run against any deployed environment:

    python scripts/smoke_deployment.py https://<host>
"""

from __future__ import annotations

import json
import sys
import unicodedata
import urllib.error
import urllib.request

# Unambiguous on purpose. Each names a city plainly, in a different language,
# so a pass means extraction ran *and* the multilingual path works. If a model
# change breaks Chinese but not English, one of these still fails.
CASES = [
    ("hoi an lantern making workshop", "Hoi An"),
    ("things to do in hanoi", "Hanoi"),
    ("đi thuyền ở hội an", "Hoi An"),
]

TIMEOUT_SECONDS = 60


def _fold(value: str) -> str:
    """Compare cities without letting accents or case decide the outcome.

    The catalogue stores unaccented names, but a model asked in Vietnamese may
    answer "Hội An", which is the same city and must not fail the gate.
    """
    stripped = unicodedata.normalize("NFD", value)
    return "".join(c for c in stripped if not unicodedata.combining(c)).casefold().strip()


def _search(base: str, query: str) -> dict:
    request = urllib.request.Request(
        f"{base.rstrip('/')}/api/v1/search",
        data=json.dumps({"query": query}).encode(),
        headers={"Content-Type": "application/json"},
        method="POST",
    )
    with urllib.request.urlopen(request, timeout=TIMEOUT_SECONDS) as response:
        if response.status != 200:
            raise SystemExit(f"FAIL {query!r}: HTTP {response.status}")
        return json.loads(response.read())


def main() -> int:
    if len(sys.argv) < 2:
        raise SystemExit("usage: smoke_deployment.py <base-url>")
    base = sys.argv[1]

    failures: list[str] = []
    for query, expected in CASES:
        try:
            payload = _search(base, query)
        except (urllib.error.URLError, TimeoutError) as error:
            failures.append(f"{query!r}: request failed: {error}")
            continue

        resolved = (payload.get("intent") or {}).get("destination", {}).get("name")
        items = payload.get("items") or []
        status = f"items={len(items):<3} destination={resolved!r}"

        if not resolved:
            failures.append(
                f"{query!r}: no destination resolved ({status}). Intent extraction is "
                "not running - the service is falling back to deterministic parsing, "
                "which cannot filter. Check the container logs for 'Intent extraction "
                "failed' and confirm the intent deployment accepts our request."
            )
        elif _fold(resolved) != _fold(expected):
            # Checking only that *a* city came back would accept "đi thuyền ở
            # hội an" resolving to Hanoi. This is the one gate standing between
            # a broken deployment and shoppers, so it asserts the answer, not
            # the shape of the answer.
            failures.append(
                f"{query!r}: resolved the wrong city ({status}, expected {expected!r}). "
                "Extraction ran but understood the place incorrectly, so the page is "
                "filtered to somewhere the shopper did not ask for."
            )
        else:
            print(f"ok   {query!r} -> {status}")

    if failures:
        print("\nDeployment smoke check FAILED:", file=sys.stderr)
        for failure in failures:
            print(f"  - {failure}", file=sys.stderr)
        return 1

    print(f"\nAll {len(CASES)} checks passed: the deployment still understands requests.")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
