"""A/B two intent models end-to-end against the live catalogue.

Validity is guaranteed by the schema on any model, so passing the live suite
says nothing about whether a cheaper model can do the job. What matters is
judgement: the same destination, a category that does not throw the shopper's
results away, prose routed to the assistant, and dates only when given.
"""

from __future__ import annotations

import asyncio
import os
import statistics
import time
from collections import Counter

from app.api.schemas import SearchRequest
from app.assistant.provider import AzureOpenAIProvider
from app.common.config import get_settings
from app.common.persistence import catalog_products
from app.search.service import SearchService

RUNS = 5
MODELS = ["gpt-5.4-mini", "gpt-5.4-nano"]

CASES = [
    {"q": "hoi an lantern", "dest": "Hoi An", "mode": None, "dated": False},
    {"q": "da nang cable car", "dest": "Da Nang", "mode": None, "dated": False},
    {"q": "things to do in hanoi", "dest": "Hanoi", "mode": None, "dated": False},
    {
        "q": "food tour in ho chi minh city",
        "dest": "Ho Chi Minh City",
        "mode": None,
        "dated": False,
    },
    {"q": "\u0111i thuy\u1ec1n \u1edf h\u1ed9i an", "dest": "Hoi An", "mode": None, "dated": False},
    {"q": "\u4f1a\u5b89\u706f\u7b3c\u4e4b\u65c5", "dest": "Hoi An", "mode": None, "dated": False},
    {
        "q": "we are a family of four with a toddler, looking for something gentle and mostly indoors in Hue, ideally with free cancellation",
        "dest": "Hue",
        "mode": "assistant",
        "dated": False,
    },
    {"q": "cooking class in hoi an on 2026-09-14", "dest": "Hoi An", "mode": None, "dated": True},
]


async def main() -> None:
    products = await catalog_products()
    print(
        f"catalogue: {len(products)} products, "
        f"{len({p['category'] for p in products})} categories\n"
    )
    # An empty catalogue scores 100% on routing and dates because nothing is
    # ever contradicted. The first run of this harness reported exactly that
    # and looked like a pass. Refuse to grade a run that cannot fail.
    if len(products) < 100:
        raise SystemExit(
            f"catalogue has {len(products)} products - set DEMO_MODE=false and "
            "DATABASE_URL before importing, or every score below is meaningless"
        )

    report = {}
    for model in MODELS:
        os.environ["AZURE_OPENAI_INTENT_DEPLOYMENT"] = model
        get_settings.cache_clear()
        service = SearchService(ai_provider=AzureOpenAIProvider(get_settings()))
        rows = {"dest": [], "items": [], "mode": [], "date": [], "cats": [], "lat": [], "zero": 0}

        print(f"=== {model} ===")
        for case in CASES:
            hits, dests, modes, dates, cats, lats = [], [], [], [], [], []
            for _ in range(RUNS):
                t0 = time.perf_counter()
                r = await service.search(SearchRequest(query=case["q"]))
                lats.append(time.perf_counter() - t0)
                hits.append(len(r.items))
                dests.append(r.effective_filters.destination)
                modes.append(r.interaction_mode)
                dates.append(r.effective_filters.visit_start is not None)
                cats.append(r.effective_filters.category)

            dest_ok = sum(1 for d in dests if d == case["dest"])
            mode_ok = sum(1 for m in modes if m == case["mode"]) if case["mode"] else RUNS
            date_ok = sum(1 for d in dates if d == case["dated"])
            zero = sum(1 for h in hits if h == 0)
            rows["zero"] += zero

            rows["dest"].append(dest_ok / RUNS)
            rows["items"].append(statistics.mean(hits))
            rows["mode"].append(mode_ok / RUNS)
            rows["date"].append(date_ok / RUNS)
            rows["cats"].extend(c for c in cats if c)
            rows["lat"].append(statistics.mean(lats))

            flag = f"  <-- {zero}/{RUNS} EMPTY" if zero else ""
            print(
                f"  {case['q'][:42]:42} items={statistics.mean(hits):5.1f} "
                f"dest={dest_ok}/{RUNS} mode={mode_ok}/{RUNS} date={date_ok}/{RUNS} "
                f"lat={statistics.mean(lats):.2f}s{flag}"
            )
        report[model] = rows
        print()

    print("=== summary ===")
    for model, rows in report.items():
        print(
            f"{model:14} destination {statistics.mean(rows['dest']):.0%} | "
            f"assistant-routing {statistics.mean(rows['mode']):.0%} | "
            f"dates {statistics.mean(rows['date']):.0%} | "
            f"mean items {statistics.mean(rows['items']):.1f} | "
            f"empty responses {rows['zero']}/{len(CASES) * RUNS} | "
            f"latency {statistics.mean(rows['lat']):.2f}s"
        )
        print(f"{'':14} categories: {Counter(rows['cats']).most_common()}")


asyncio.run(main())
