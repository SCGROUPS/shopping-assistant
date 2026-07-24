import argparse
import asyncio
import json

from app.catalog.db_seed import seed_database
from app.catalog.seed import build_seed_catalog
from app.common.store import store


def main() -> None:
    parser = argparse.ArgumentParser(description="Seed or export the POC tourism catalog")
    parser.add_argument("command", choices=["seed", "seed-db", "summary"])
    parser.add_argument("--force", action="store_true")
    args = parser.parse_args()
    if args.command == "seed-db":
        count = asyncio.run(seed_database(force=args.force))
        print(f"Seeded {count} PostgreSQL experiences")
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
