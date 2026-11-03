"""Check the integrity of the local CSV export."""

import argparse
import csv
import json
from pathlib import Path


def validate_files(directory):
    # A drug's complete clinical-report list can exceed Python's 128 KiB default.
    csv.field_size_limit(8 * 1024 * 1024)
    with (directory / "nodes.csv").open(newline="") as f:
        nodes = list(csv.DictReader(f))
    with (directory / "relationships.csv").open(newline="") as f:
        edges = list(csv.DictReader(f))
    assert nodes and edges, "Empty graph export"
    ids = {n[":ID"] for n in nodes}
    assert len(ids) == len(nodes), "Duplicate node ID"
    assert len({r[":ID"] for r in edges}) == len(edges), "Duplicate relationship ID"
    for r in edges:
        assert r[":START_ID"] in ids and r[":END_ID"] in ids, "Dangling relationship"
        assert all(
            r[k + ":String"]
            for k in [
                "id",
                "predicate",
                "source",
                "source_url",
                "retrieved_at",
                "review_status",
                "confidence",
            ]
        ), "Missing provenance"
        json.loads(r["evidence_json:String"])
        json.loads(r["original_json:String"])
    return {"nodes": len(nodes), "relationships": len(edges)}


if __name__ == "__main__":
    p = argparse.ArgumentParser()
    p.add_argument("--directory", type=Path, default=Path("data/transformed"))
    a = p.parse_args()
    print(json.dumps(validate_files(a.directory), indent=2))
