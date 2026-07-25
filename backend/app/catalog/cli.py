import argparse
import asyncio
import json

from app.catalog.db_seed import refresh_availability, seed_database
from app.catalog.importer import import_trippass
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
            "summary",
        ],
    )
    parser.add_argument("--force", action="store_true")
    parser.add_argument(
        "--days",
        type=int,
        default=30,
        help="How many days of availability to publish for imported products.",
    )
    args = parser.parse_args()
    if args.command == "import-trippass":
        result = asyncio.run(import_trippass(days=args.days))
        print(
            f"Trippass import: {result['fetched']} fetched, "
            f"{result['created']} created, {result['updated']} updated, "
            f"{result['needs_review']} need review"
        )
        return
    if args.command == "seed-db":
        count = asyncio.run(seed_database(force=args.force))
        print(f"Seeded {count} PostgreSQL experiences")
        return
    if args.command == "refresh-availability":
        result = asyncio.run(refresh_availability())
        print(
            f"Availability refreshed: {result['created']} created, "
            f"{result['updated']} updated"
        )
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
