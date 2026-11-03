"""Upload only Neptune CSVs; invoke the loader inside the VPC, poll, then validate."""

import argparse
import hashlib
import json
import time
from pathlib import Path

import boto3
from validate import invoke_utility, validate_files, validate_remote


def load(directory, bucket, function, region, timeout=900):
    validate_files(directory)
    s3 = boto3.client("s3", region_name=region)
    location = s3.get_bucket_location(Bucket=bucket).get("LocationConstraint") or "us-east-1"
    if location != region:
        raise ValueError("S3 bucket must be in the Neptune Region")
    files = [directory / "nodes.csv", directory / "relationships.csv"]
    version = hashlib.sha256(b"".join(p.read_bytes() for p in files)).hexdigest()[:24]
    for path in files:
        s3.upload_file(str(path), bucket, f"graph/{version}/{path.name}")
    # Never upload graph.json/manifest JSON into a prefix that the loader reads.
    client = boto3.client("lambda", region_name=region)
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


if __name__ == "__main__":
    p = argparse.ArgumentParser()
    p.add_argument("--directory", type=Path, default=Path("data/transformed"))
    p.add_argument("--bucket", required=True)
    p.add_argument("--function", required=True)
    p.add_argument("--region", required=True)
    a = p.parse_args()
    load(a.directory, a.bucket, a.function, a.region)
