import argparse
import asyncio
import json

from app.admin.cli import create_operator, list_operators
from app.catalog.db_seed import refresh_availability, seed_database
from app.catalog.importer import import_trippass
from app.catalog.indexing import run_reindex
from app.catalog.seed import build_seed_catalog
from app.common.store import store
from app.content.jobs import backlog as translation_backlog
from app.content.jobs import drain_translations, enqueue_all


def main() -> None:
    parser = argparse.ArgumentParser(description="Seed or export the POC tourism catalog")
    parser.add_argument(
        "command",
        choices=[
            "seed",
            "seed-db",
            "refresh-availability",
            "import-trippass",
            "reindex",
            "enqueue-translations",
            "translate",
            "summary",
            "create-operator",
            "list-operators",
        ],
    )
    parser.add_argument("--email", help="Operator email address.")
    parser.add_argument("--name", default="", help="Operator display name.")
    parser.add_argument(
        "--role",
        default="analyst",
        help="admin, catalog_manager, merchandiser or analyst.",
    )
    parser.add_argument(
        "--rotate",
        action="store_true",
        help="Issue a new key for an existing operator, invalidating the old one.",
    )
    parser.add_argument("--force", action="store_true")
    parser.add_argument(
        "--locale",
        action="append",
        help="Restrict translation work to this locale; repeatable.",
    )
    parser.add_argument(
        "--limit",
        type=int,
        help="Translate at most this many fields, then stop. Omit to drain the queue.",
    )
    parser.add_argument(
        "--revive",
        action="store_true",
        help="Give up-to-date failed jobs their attempts back before draining.",
    )
    parser.add_argument(
        "--hold-for-review",
        action="store_true",
        help="Hold every field for review, not just the policy-bearing ones.",
    )
    parser.add_argument(
        "--days",
        type=int,
        default=30,
        help="How many days of availability to publish for imported products.",
    )
    args = parser.parse_args()
    if args.command == "create-operator":
        if not args.email:
            raise SystemExit("--email is required")
        result = asyncio.run(
            create_operator(args.email, args.name or args.email, args.role, args.rotate)
        )
        if result["api_key"]:
            print(f"{result['action']}: {result['email']} ({result['role']})")
            print(f"API key (shown once): {result['api_key']}")
        else:
            print(f"{result['action']}: {result['email']} already exists; pass --rotate to reissue")
        return
    if args.command == "list-operators":
        for row in asyncio.run(list_operators()):
            print(f"{row['email']:<40} {row['role']:<18} active={row['active']}")
        return
    if args.command == "import-trippass":
        result = asyncio.run(import_trippass(days=args.days))
        print(
            f"Trippass import: {result['fetched']} fetched, "
            f"{result['created']} created, {result['updated']} updated, "
            f"{result['needs_review']} need review"
        )
        return
    if args.command == "reindex":
        # Reconcile first: a release that changes how documents are built
        # touches no catalogue row, so nothing would be in the queue to drain.
        queued, count, backlog = asyncio.run(run_reindex())
        print(f"Reconciled {queued} stale locales; rebuilt {count} search documents")
        # A drain stops when it can build nothing, which is not the same as the
        # queue being empty - another replica may hold the rest, or a round of
        # transient failures may have returned everything to `queued`. Reporting
        # what is left and exiting non-zero is what turns "the job succeeded"
        # into a claim about the index rather than about the command.
        if backlog:
            summary = ", ".join(f"{n} {status}" for status, n in sorted(backlog.items()))
            print(f"Index backlog not empty: {summary}")
            raise SystemExit(1)
        return
    if args.command == "enqueue-translations":
        totals = asyncio.run(enqueue_all(locales=args.locale))
        print(
            f"Translation queue: {totals['enqueued']} fields enqueued "
            f"across {totals['experiences']} experiences"
        )
        return
    if args.command == "translate":
        totals = asyncio.run(
            drain_translations(
                limit=args.limit,
                hold_all=args.hold_for_review,
                revive=args.revive,
            )
        )
        print(
            f"Translated {totals['published']} fields "
            f"({totals['superseded']} superseded, {totals['retrying']} retrying, "
            f"{totals['failed']} failed, {totals['revived']} revived)"
        )
        remaining = asyncio.run(translation_backlog())
        if remaining:
            summary = ", ".join(f"{n} {status}" for status, n in sorted(remaining.items()))
            print(f"Translation backlog not empty: {summary}")
        # Only *terminal* failures are an error. A job that failed once and will
        # be retried is the queue working, and exiting non-zero for it would
        # make a transient provider blip fail the deployment.
        if totals["failed"]:
            raise SystemExit(1)
        return
    if args.command == "seed-db":
        count = asyncio.run(seed_database(force=args.force))
        print(f"Seeded {count} PostgreSQL experiences")
        return
    if args.command == "refresh-availability":
        result = asyncio.run(refresh_availability())
        print(f"Availability refreshed: {result['created']} created, {result['updated']} updated")
        return
    count = store.seed(force=True)
    if args.command == "seed":
        print(f"Seeded {count} demo experiences")
    else:
        print(
            json.dumps(
                {
                    "experiences": count,
                    "destinations": sorted(
                        {item["destination"] for item in build_seed_catalog(days=1)}
                    ),
                },
                indent=2,
            )
        )


if __name__ == "__main__":
    main()
