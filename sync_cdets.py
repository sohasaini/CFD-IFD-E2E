
from __future__ import annotations

import argparse
import json
from datetime import datetime
from pathlib import Path

from services.cfd_sync_service import CdetsSyncService

BASE_DIR = Path(__file__).resolve().parent

def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("--from-date", default="")
    parser.add_argument("--to-date", default="")
    args = parser.parse_args()

    from_date = (
        datetime.strptime(args.from_date, "%Y-%m-%d").date()
        if args.from_date else None
    )
    to_date = (
        datetime.strptime(args.to_date, "%Y-%m-%d").date()
        if args.to_date else None
    )

    service = CdetsSyncService(
        components_file=BASE_DIR / "data" / "cdets_components.json",
        cache_file=BASE_DIR / "data" / "cfd_cache.json",
        state_file=BASE_DIR / "data" / "cfd_sync_state.json",
    )

    print(json.dumps(
        service.sync(force_start_date=from_date, end_date=to_date),
        indent=2,
    ))

if __name__ == "__main__":
    main()
