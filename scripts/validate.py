"""Check CSV integrity locally and/or run fixed queries through the VPC utility Lambda."""

import argparse
import csv
import json
from pathlib import Path

import boto3


def validate_files(directory):
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


def invoke_utility(function, event, client=None):
    client = client or boto3.client("lambda")
    response = client.invoke(FunctionName=function, Payload=json.dumps(event).encode())
    result = json.loads(response["Payload"].read())
    if response.get("FunctionError"):
        raise RuntimeError("Graph utility failed: " + json.dumps(result))
    return result


def validate_remote(result):
    assert result["nodes"][0]["count"] > 0, "Neptune is empty"
    assert result["relationships"][0]["count"] > 0, "No relationships in Neptune"
    assert result["tp53"], "Missing TP53/myeloma association"
    assert result["missing_provenance"][0]["count"] == 0, "Missing provenance in Neptune"
    return result


if __name__ == "__main__":
    p = argparse.ArgumentParser()
    p.add_argument("--directory", type=Path, default=Path("data/transformed"))
    p.add_argument("--function", help="VPC utility Lambda name for remote validation")
    a = p.parse_args()
    result = (
        validate_remote(invoke_utility(a.function, {"action": "validate"}))
        if a.function
        else validate_files(a.directory)
    )
    print(json.dumps(result, indent=2))
