"""Start the manual source -> S3 -> Neptune workflow and optionally wait for its validated result."""

import argparse
import hashlib
import json
import uuid
from urllib.parse import urlsplit

import boto3
from botocore.config import Config

ACCOUNT = "548574235556"


def verify_saved_result(s3, output):
    uri = urlsplit(output["manifest_uri"])
    if uri.scheme != "s3" or not uri.netloc or not uri.path.startswith("/raw/source-pipeline/runs/") or not uri.path.endswith("/manifest.json"):
        raise ValueError("Workflow returned an unexpected source-manifest URI")
    key = uri.path.lstrip("/")

    def read(name):
        with s3.get_object(Bucket=uri.netloc, Key=name)["Body"] as body:
            return body.read()

    if hashlib.sha256(read(key)).hexdigest() != output["manifest_sha256"]:
        raise ValueError("Stored S3 manifest does not match the validated workflow receipt")
    report = json.loads(read(key.removesuffix("manifest.json") + "result.json"))
    if not isinstance(report, dict) or report != output:
        raise ValueError("Stored S3 result does not match the validated workflow output")


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--profile", default="mmrf-hackathon")
    parser.add_argument("--region", default="us-west-2")
    parser.add_argument("--function-name", default="mmrf-kg-source-pipeline-fetch")
    parser.add_argument("--run-id", help="Reuse a failed run's committed snapshot, or read a completed result")
    parser.add_argument("--no-wait", action="store_true")
    args = parser.parse_args()
    run_id = args.run_id or str(uuid.uuid4())
    try:
        if str(uuid.UUID(run_id)) != run_id:
            raise ValueError("Use a canonical lowercase UUID")
    except ValueError as error:
        parser.error(str(error))
    session = boto3.Session(profile_name=args.profile, region_name=args.region)
    if session.client("sts").get_caller_identity()["Account"] != ACCOUNT:
        parser.error("Expected the hackathon sandbox account")
    client = session.client("lambda", config=Config(connect_timeout=5, read_timeout=910,
                                                   retries={"total_max_attempts": 1}))
    print("Run ID: " + run_id, flush=True)
    response = client.invoke(
        FunctionName=args.function_name, InvocationType="Event" if args.no_wait else "RequestResponse",
        Payload=json.dumps({"run_id": run_id}).encode(),
    )
    if args.no_wait:
        if response["StatusCode"] != 202:
            raise SystemExit("Lambda did not accept the asynchronous invocation")
        print("Queued. Check result.json under this run ID in S3 and the Lambda logs.")
        return
    with response["Payload"] as payload:
        output = json.loads(payload.read())
    if response.get("FunctionError"):
        print(json.dumps(output, indent=2))
        raise SystemExit("Pipeline failed. Check Lambda logs and retry with the same --run-id.")
    if output.get("status") != "SUCCEEDED" or not output.get("validation") or output.get("run_id") != run_id:
        raise SystemExit("Pipeline ended without a matching validated ingestion result")
    verify_saved_result(session.client("s3"), output)
    print("S3 manifest and saved result verified.", flush=True)
    print(json.dumps(output, indent=2))


if __name__ == "__main__":
    main()
