"""Verify registry metadata and publication cross-reference paths in the sandbox Neptune graph."""

import argparse
import hashlib
import json
from collections import Counter, defaultdict
from urllib.parse import urlsplit

import boto3
from botocore.config import Config


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--profile", default="mmrf-hackathon")
    parser.add_argument("--region", default="us-west-2")
    args = parser.parse_args()
    session = boto3.Session(profile_name=args.profile, region_name=args.region)
    if session.client("sts").get_caller_identity()["Account"] != "548574235556":
        parser.error("Expected the hackathon sandbox account")
    client = session.client("lambda", config=Config(connect_timeout=5, read_timeout=70,
                                                   retries={"total_max_attempts": 1}))
    bucket = client.get_function_configuration(FunctionName="mmrf-kg-ingest")["Environment"]["Variables"]["INGEST_BUCKET"]
    s3 = session.client("s3")

    def read(key):
        with s3.get_object(Bucket=bucket, Key=key)["Body"] as body:
            return body.read()

    raw = read("raw/clinical_trials.json")
    provenance = json.loads(read("raw/clinical_trials.json.provenance.json"))
    if hashlib.sha256(raw).hexdigest() != provenance["sha256"]:
        raise ValueError("Clinical trial snapshot checksum mismatch")
    data = json.loads(raw)
    for field in ("snapshot_s3_uri", "pubmed_s3_uri"):
        uri = urlsplit(provenance[field])
        if uri.scheme != "s3" or uri.netloc != bucket or not uri.path.startswith("/raw/clinical_trials/snapshots/"):
            raise ValueError("Unexpected immutable source URI")
        content = read(uri.path.lstrip("/"))
        expected = raw if field == "snapshot_s3_uri" else None
        if expected is not None and content != expected:
            raise ValueError("Fixed source snapshot differs from its immutable archive")
        if field == "pubmed_s3_uri" and hashlib.sha256(content).hexdigest() != data["pubmed"]["sha256"]:
            raise ValueError("Archived PubMed source checksum mismatch")

    def query(text):
        response = client.invoke(FunctionName="mmrf-kg-kg-graph", Payload=json.dumps({
            "tool": "kg_run_readonly_query", "query": text, "parameters": {},
        }).encode())
        with response["Payload"] as body:
            result = json.loads(body.read())
        if response.get("FunctionError") or result.get("result_type") != "table":
            raise RuntimeError("Read-only graph query failed")
        return [dict(zip(result["table"]["columns"], row, strict=True)) for row in result["table"]["rows"]]

    expected_diseases, expected_treatments = defaultdict(set), defaultdict(set)
    versions = {}
    for link in data["links"]:
        expected_diseases[link["disease_id"], link["nct_id"]].add(link["reference"])
        expected_treatments[link["disease_id"], link["treatment_id"], link["nct_id"]].add(link["reference"])
        versions[link["disease_id"]] = link["dismech_version"]
    studies = {s["protocolSection"]["identificationModule"]["nctId"]: s for s in data["studies"]}

    def check_relation(props, disease, refs):
        for key, expected in {
            "source_version": data["source_version"], "publications": ";".join(sorted(refs)),
            "mapping_basis": "pubmed_structured_registry_accession", "dismech_source_version": versions[disease],
            "raw_s3_uri": provenance["snapshot_s3_uri"], "raw_sha256": provenance["sha256"],
            "pubmed_s3_uri": provenance["pubmed_s3_uri"], "pubmed_sha256": data["pubmed"]["sha256"],
        }.items():
            if props.get(key) != expected:
                raise ValueError(f"Trial relationship differs from the source: {key}")

    disease_rows = query("""MATCH (d:Disease)-[r:HAS_CLINICAL_TRIAL {source:'clinicaltrials_gov'}]->(t:ClinicalTrial)
        RETURN d.id AS disease, t.id AS trial, properties(r) AS relationship, properties(t) AS trial_properties LIMIT 100""")
    if len(disease_rows) != len(expected_diseases) or {(r["disease"], r["trial"]) for r in disease_rows} != set(expected_diseases):
        raise ValueError("Disease/trial relationships differ from the source")
    for row in disease_rows:
        check_relation(row["relationship"], row["disease"], expected_diseases[row["disease"], row["trial"]])
        protocol = studies[row["trial"]]["protocolSection"]
        design, status = protocol.get("designModule", {}), protocol.get("statusModule", {})
        enrollment = design.get("enrollmentInfo", {})
        expected = {"name": protocol["identificationModule"]["briefTitle"],
                    "study_phase": ";".join(design.get("phases", [])), "study_status": status.get("overallStatus"),
                    "study_type": design.get("studyType"), "enrollment_count": enrollment.get("count"),
                    "enrollment_type": enrollment.get("type"), "has_results": studies[row["trial"]].get("hasResults"),
                    "source_version": data["source_version"], "retrieved_at": data["retrieved_at"],
                    "source_url": f"https://clinicaltrials.gov/study/{row['trial']}",
                    "registry_last_update": status.get("lastUpdatePostDateStruct", {}).get("date"),
                    "raw_s3_uri": provenance["snapshot_s3_uri"], "raw_sha256": provenance["sha256"]}
        for key, value in expected.items():
            if row["trial_properties"].get(key) != value:
                raise ValueError(f"Registry metadata differs from the source: {row['trial']}, {key}")
    treatment_rows = query("""MATCH (a:Entity)-[r:HAS_CLINICAL_TRIAL {source:'clinicaltrials_gov'}]->(t:ClinicalTrial)
        RETURN r.disease_id AS disease, a.id AS treatment, t.id AS trial, properties(r) AS relationship LIMIT 100""")
    if (len(treatment_rows) != len(expected_treatments)
            or {(r["disease"], r["treatment"], r["trial"]) for r in treatment_rows} != set(expected_treatments)):
        raise ValueError("Treatment/trial relationships differ from the source")
    for row in treatment_rows:
        check_relation(row["relationship"], row["disease"], expected_treatments[row["disease"], row["treatment"], row["trial"]])
    counts = Counter(row["disease"] for row in disease_rows)
    print(json.dumps({"status": "VERIFIED", "studies": len(studies), "trials_by_disease": dict(counts),
                      "disease_links": len(disease_rows), "treatment_links": len(treatment_rows),
                      "source_version": data["source_version"], "immutable_sources_verified": True}, indent=2))


if __name__ == "__main__":
    main()
