"""Pull the multiple myeloma entity + its associations from the Monarch API.

This legacy fetcher is retained for compatibility. Prefer scripts/extract.py's
bounded calls and retrieval manifest. Check individual upstream data terms;
Monarch's preferred-license guidance is not a blanket CC0 grant.
"""
import json
from pathlib import Path

import requests

API = "https://api.monarchinitiative.org/v3/api"
MONDO_ID = "MONDO:0009693"  # plasma cell myeloma / multiple myeloma
OUT_PATH = Path(__file__).resolve().parent.parent / "data" / "raw" / "monarch_mm.json"


def fetch_all_associations(subject: str, limit: int = 100) -> list[dict]:
    associations: list[dict] = []
    offset = 0
    while True:
        resp = requests.get(
            f"{API}/association",
            params={"subject": subject, "limit": limit, "offset": offset},
            timeout=30,
        )
        resp.raise_for_status()
        page = resp.json()
        associations.extend(page["items"])
        offset += limit
        if offset >= page["total"]:
            break
    return associations


def fetch_source_version() -> str:
    resp = requests.get(f"{API}/version", timeout=30)
    resp.raise_for_status()
    return resp.json()["monarch_kg_version"]


def main() -> None:
    entity = requests.get(f"{API}/entity/{MONDO_ID}", timeout=30).json()
    associations = fetch_all_associations(MONDO_ID)
    source_version = fetch_source_version()

    OUT_PATH.parent.mkdir(parents=True, exist_ok=True)
    OUT_PATH.write_text(
        json.dumps({"source_version": source_version, "entity": entity, "associations": associations}, indent=2),
        encoding="utf-8",
    )
    print(f"Wrote {len(associations)} associations for {MONDO_ID} (Monarch KG {source_version}) -> {OUT_PATH}")


if __name__ == "__main__":
    main()
