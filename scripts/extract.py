"""Bounded optional Monarch refresh. No complete-graph downloads or gene guessing."""

import argparse
import hashlib
import json
from datetime import datetime, timezone
from pathlib import Path

import requests

API = "https://api.monarchinitiative.org/v3/api"
DISEASE = "MONDO:0009693"
GENES = {"TP53": "HGNC:11998", "KRAS": "HGNC:6407", "NRAS": "HGNC:7989", "BRAF": "HGNC:1097"}


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("--output", type=Path, default=Path("data/raw/monarch_mm.json"))
    args = parser.parse_args()
    session = requests.Session()

    def get(path, params=None):
        r = session.get(API + path, params=params, timeout=(5, 30))
        r.raise_for_status()
        return r.json()

    entity = get("/entity/" + DISEASE)
    associations = {}
    calls = []
    # One bounded disease page and two directions for each requested gene.
    # Empty gene results remain empty; curated publication facts are a separate source.
    queries = [{"subject": DISEASE, "limit": 100}]
    for gene in GENES.values():
        queries.extend(
            [
                {"subject": gene, "object": DISEASE, "limit": 20},
                {"subject": DISEASE, "object": gene, "limit": 20},
            ]
        )
    for query in queries:
        page = get("/association", query)
        calls.append(
            {
                "url": API + "/association",
                "parameters": query,
                "total_available": page.get("total"),
                "fetched": len(page["items"]),
            }
        )
        for a in page["items"]:
            associations[a["id"]] = a
    args.output.parent.mkdir(parents=True, exist_ok=True)
    args.output.write_text(
        json.dumps({"entity": entity, "associations": list(associations.values())}, indent=2)
    )
    provenance = {
        "retrieved_at": datetime.now(timezone.utc).isoformat(),
        "source_revision": "live-api",
        "calls": calls,
        "sha256": hashlib.sha256(args.output.read_bytes()).hexdigest(),
        "truncated_by_design": True,
    }
    args.output.with_suffix(args.output.suffix + ".provenance.json").write_text(
        json.dumps(provenance, indent=2)
    )
    print(f"Saved {len(associations)} bounded associations, original IDs/metadata, and retrieval manifest")


if __name__ == "__main__":
    main()
