import argparse
import asyncio
import json

from app.admin.cli import create_operator, list_operators
from app.catalog.db_seed import refresh_availability, seed_database
from app.catalog.importer import import_trippass
from app.catalog.indexing import drain_index_queue, run_reconcile
from app.catalog.seed import build_seed_catalog
from app.common.store import store


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
        queued = asyncio.run(run_reconcile())
        count = asyncio.run(drain_index_queue(limit=None))
        print(f"Reconciled {queued} stale locales; rebuilt {count} search documents")
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
