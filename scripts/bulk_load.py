"""Upload only Neptune CSVs; invoke the loader inside the VPC, poll, then validate."""

import argparse
import csv
import hashlib
import json
import time
from collections import defaultdict
from pathlib import Path
from tempfile import TemporaryDirectory

import boto3
from validate import invoke_utility, validate_files, validate_remote


def prepare_export(directory, staging, identities):
    """Reuse native Neptune IDs left by earlier MERGE-by-property imports."""
    native_by_logical = {}
    native_ids = {row["neptune_id"] for row in identities}
    groups = defaultdict(list)
    for row in identities:
        logical = row.get("logical_id")
        if logical is None:
            continue
        groups[logical].append(row)
    for logical, rows in groups.items():
        if len(rows) > 1:
            # Reuse the one Entity in our projection when a separate legacy
            # Disease node has the same source ID; preserve that legacy node.
            rows = [row for row in rows if "Entity" in row.get("labels", [])]
        if len(rows) != 1:
            raise ValueError(f"Neptune already has duplicate logical node ID: {logical}")
        native_by_logical[logical] = rows[0]["neptune_id"]
    remap = {}
    matched = 0
    with (directory / "nodes.csv").open(newline="", encoding="utf-8") as source:
        reader = csv.DictReader(source)
        with (staging / "nodes.csv").open("w", newline="", encoding="utf-8") as target:
            writer = csv.DictWriter(target, fieldnames=reader.fieldnames)
            writer.writeheader()
            for row in reader:
                logical = row["id:String"]
                native = native_by_logical.get(logical, row[":ID"])
                if logical not in native_by_logical and native in native_ids:
                    raise ValueError(f"Native Neptune ID collision for {logical}")
                matched += logical in native_by_logical
                remap[row[":ID"]] = native
                row[":ID"] = native
                writer.writerow(row)
    with (directory / "relationships.csv").open(newline="", encoding="utf-8") as source:
        reader = csv.DictReader(source)
        with (staging / "relationships.csv").open("w", newline="", encoding="utf-8") as target:
            writer = csv.DictWriter(target, fieldnames=reader.fieldnames)
            writer.writeheader()
            for row in reader:
                row[":START_ID"] = remap[row[":START_ID"]]
                row[":END_ID"] = remap[row[":END_ID"]]
                writer.writerow(row)
    validate_files(staging)
    return matched


def upload_and_load(directory, bucket, function, region, s3, client, timeout):
    files = [directory / "nodes.csv", directory / "relationships.csv"]
    version = hashlib.sha256(b"".join(p.read_bytes() for p in files)).hexdigest()[:24]
    for path in files:
        s3.upload_file(str(path), bucket, f"graph/{version}/{path.name}")
    # Never upload graph.json/manifest JSON into a prefix that the loader reads.
    result = invoke_utility(function, {"action": "load", "version": version}, client)
    load_id = result["payload"]["loadId"]
    print(json.dumps({"load_id": load_id, "version": version}), flush=True)
    deadline = time.monotonic() + timeout
    while time.monotonic() < deadline:
        status = invoke_utility(function, {"action": "status", "load_id": load_id}, client)
        state = status["payload"]["overallStatus"]["status"]
        print(state, flush=True)
        if state == "LOAD_COMPLETED":
            validated = validate_remote(invoke_utility(function, {"action": "validate"}, client))
            print(json.dumps(validated, indent=2))
            return validated
        if state not in {"LOAD_NOT_STARTED", "LOAD_IN_PROGRESS", "LOAD_IN_QUEUE"}:
            raise RuntimeError("Loader failed: " + json.dumps(status))
        time.sleep(5)
    raise TimeoutError(f"Load {load_id} still running; inspect with the utility status action")


def load(directory, bucket, function, region, timeout=900):
    validate_files(directory)
    s3 = boto3.client("s3", region_name=region)
    location = s3.get_bucket_location(Bucket=bucket).get("LocationConstraint") or "us-east-1"
    if location != region:
        raise ValueError("S3 bucket must be in the Neptune Region")
    client = boto3.client("lambda", region_name=region)
    identities = invoke_utility(function, {"action": "identity_map"}, client)
    with TemporaryDirectory(prefix="orion-neptune-load-") as temporary:
        staging = Path(temporary)
        matched = prepare_export(directory, staging, identities)
        print(json.dumps({"matched_existing_nodes": matched}), flush=True)
        return upload_and_load(staging, bucket, function, region, s3, client, timeout)


if __name__ == "__main__":
    p = argparse.ArgumentParser()
    p.add_argument("--directory", type=Path, default=Path("data/transformed"))
    p.add_argument("--bucket", required=True)
    p.add_argument("--function", required=True)
    p.add_argument("--region", required=True)
    a = p.parse_args()
    load(a.directory, a.bucket, a.function, a.region)
