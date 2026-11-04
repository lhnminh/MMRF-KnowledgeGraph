"""Compare complete DisMech treatment evidence in Neptune with the staged S3 records.

Uses the existing read-only graph Lambda; no laptop-to-Neptune connection is needed.
Prints counts and citation identifiers, not article quotations or curator commentary.
"""

import argparse
import hashlib
import json
import re
from collections import Counter

import boto3
import yaml
from botocore.config import Config

ACCOUNT = "548574235556"
QUERY = """MATCH (d:Disease)-[r:ASSOCIATION]->(t:Entity)
WHERE d.id IN $disease_ids AND r.source = 'dismech'
AND r.predicate = 'dismech:has_recorded_treatment'
RETURN d.id AS disease, t.id AS treatment, r.source_version AS version,
r.evidence_json AS evidence_json, r.evidence_count AS evidence_count,
r.publications AS publications, r.evidence_pmid AS first_reference
LIMIT 100"""


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--profile", default="mmrf-hackathon")
    parser.add_argument("--region", default="us-west-2")
    parser.add_argument("--sources", nargs="+", default=["mm", "jmml"])
    parser.add_argument("--ingest-function", default="mmrf-kg-ingest")
    parser.add_argument("--graph-function", default="mmrf-kg-kg-graph")
    args = parser.parse_args()
    if not all(re.fullmatch(r"[a-z0-9_]+", name) for name in args.sources):
        parser.error("Source names must contain lowercase letters, digits or underscores")
    session = boto3.Session(profile_name=args.profile, region_name=args.region)
    if session.client("sts").get_caller_identity()["Account"] != ACCOUNT:
        parser.error("Expected the hackathon sandbox account")
    client = session.client("lambda", config=Config(connect_timeout=5, read_timeout=70,
                                                   retries={"total_max_attempts": 1}))
    config = client.get_function_configuration(FunctionName=args.ingest_function)
    bucket = config["Environment"]["Variables"]["INGEST_BUCKET"]
    s3 = session.client("s3")

    def read(key):
        with s3.get_object(Bucket=bucket, Key=key)["Body"] as body:
            return body.read()

    expected, reports, disease_ids = {}, {}, set()
    for name in dict.fromkeys(args.sources):
        key = f"raw/dismech_{name}.yaml"
        raw = read(key)
        provenance = json.loads(read(key + ".provenance.json"))
        if not provenance.get("source_revision") or provenance.get("sha256") != hashlib.sha256(raw).hexdigest():
            raise ValueError(f"Missing version or invalid source checksum: {key}")
        record = yaml.safe_load(raw)
        disease = record["disease_term"]["term"]["id"]
        disease_ids.add(disease)
        classifications = Counter()
        citations = []
        for treatment in record.get("treatments", []):
            items = treatment.get("evidence") or []
            references = list(dict.fromkeys(item["reference"] for item in items if item.get("reference")))
            expected[disease, f"dismech:treatment:{treatment['name']}"] = {
                "version": provenance["source_revision"], "evidence_json": items,
                "evidence_count": len(items), "publications": ";".join(references),
                "first_reference": (items or [{}])[0].get("reference"),
            }
            classifications.update(item.get("evidence_source", "unrecorded") for item in items)
            citations.extend(references)
        reports[name] = {"disease_id": disease, "treatments": len(record.get("treatments", [])),
                         "evidence_items": sum(classifications.values()),
                         "evidence_classifications": dict(classifications), "references": citations,
                         "source_version": provenance["source_revision"],
                         "source_url": provenance.get("source_url"), "raw_s3_uri": f"s3://{bucket}/{key}"}
    if len(expected) >= 100:
        raise ValueError("This validator supports fewer than 100 treatment relationships")
    response = client.invoke(FunctionName=args.graph_function, Payload=json.dumps({
        "tool": "kg_run_readonly_query", "query": QUERY,
        "parameters": {"disease_ids": sorted(disease_ids)},
    }).encode())
    with response["Payload"] as body:
        output = json.loads(body.read())
    if response.get("FunctionError") or output.get("result_type") != "table":
        raise RuntimeError("The read-only graph query did not return a table")
    table = output["table"]
    actual = {}
    for values in table["rows"]:
        row = dict(zip(table["columns"], values, strict=True))
        key = (row.pop("disease"), row.pop("treatment"))
        if key in actual:
            raise ValueError(f"Duplicate treatment relationship: {key}")
        row["evidence_json"] = json.loads(row["evidence_json"] or "null")
        actual[key] = row
    if set(actual) != set(expected):
        raise ValueError("Treatment relationships in Neptune differ from the staged source records")
    for key, fields in expected.items():
        for field, value in fields.items():
            if actual[key][field] != value:
                raise ValueError(f"Neptune evidence differs from S3: {key}, property {field}")
    print(json.dumps({"status": "VERIFIED", "sources": reports}, indent=2))


if __name__ == "__main__":
    main()
