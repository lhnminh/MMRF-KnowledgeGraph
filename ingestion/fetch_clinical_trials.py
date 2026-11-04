"""Resolve bounded DisMech treatment citations through PubMed into ClinicalTrials.gov.

No fuzzy/name matching: only PubMed's structured ClinicalTrials.gov accessions
are accepted. Raw sources stay in an ignored local folder and private sandbox S3.
"""

import argparse
import hashlib
import json
import re
import time
import uuid
import xml.etree.ElementTree as ET
from datetime import datetime, timezone
from pathlib import Path
from urllib.parse import urlencode

import requests
import yaml

API = "https://clinicaltrials.gov/api/v2"
PUBMED = "https://eutils.ncbi.nlm.nih.gov/entrez/eutils/efetch.fcgi"
MAX_BYTES = 4 * 1024 * 1024
MAX_CITATIONS = 30
MAX_STUDIES = 20
RAW = Path(__file__).resolve().parent.parent / "data/raw"
DISEASES = {"MONDO:0009693", "MONDO:0011908"}


def encode(value):
    return json.dumps(value, ensure_ascii=False, sort_keys=True, separators=(",", ":")).encode()


def sha256(value):
    return hashlib.sha256(value).hexdigest()


def download(url):
    for attempt in range(3):
        response = requests.get(url, timeout=(5, 35), headers={"User-Agent": "ORION-public-trials/1.0"})
        if response.status_code in {429, 500, 502, 503, 504} and attempt < 2:
            time.sleep(2 ** attempt)
            continue
        response.raise_for_status()
        if len(response.content) > MAX_BYTES:
            raise ValueError("Registry response exceeds the small-subset limit")
        return response.content
    raise RuntimeError("Public source failed")


def pubmed_accessions(xml, requested):
    root = ET.fromstring(xml)
    found = {}
    for article in root.findall("PubmedArticle"):
        pmid = article.findtext("MedlineCitation/PMID")
        if pmid not in requested or pmid in found:
            raise ValueError("PubMed returned an unexpected or repeated PMID")
        accessions = set()
        for bank in article.findall("MedlineCitation/Article/DataBankList/DataBank"):
            if bank.findtext("DataBankName") != "ClinicalTrials.gov":
                continue
            for accession in bank.findall("AccessionNumberList/AccessionNumber"):
                identifier = accession.text or ""
                if not re.fullmatch(r"NCT\d{8}", identifier):
                    raise ValueError("PubMed supplied an invalid registry accession")
                accessions.add(identifier)
        found[pmid] = sorted(accessions)
    if set(found) != set(requested):
        raise ValueError("PubMed omitted requested citations; no snapshot will be published")
    return found


def collect(sources, get=download):
    citations, excluded = [], []
    for source in sources:
        record = source["record"]
        disease = record["disease_term"]["term"]["id"]
        if disease not in DISEASES or not source["source_version"]:
            raise ValueError("Only the pinned MM and JMML subset is supported")
        for treatment in record.get("treatments", []):
            for evidence in treatment.get("evidence") or []:
                ref = evidence.get("reference", "")
                item = {"disease_id": disease, "treatment_id": f"dismech:treatment:{treatment['name']}",
                        "treatment_name": treatment["name"], "reference": ref,
                        "dismech_version": source["source_version"]}
                if evidence.get("evidence_source") != "HUMAN_CLINICAL" or not re.fullmatch(r"PMID:\d+", ref):
                    excluded.append({**item, "reason": "not_a_human_clinical_pubmed_citation"})
                    continue
                if item not in citations:
                    citations.append(item)
    pmids = sorted({c["reference"].split(":")[1] for c in citations})
    if not 0 < len(pmids) <= MAX_CITATIONS:
        raise ValueError("Clinical citation subset is empty or too large")
    before = json.loads(get(API + "/version"))
    version = before.get("dataTimestamp")
    if not version:
        raise ValueError("Registry did not report its data timestamp")
    pubmed_url = PUBMED + "?" + urlencode({"db": "pubmed", "id": ",".join(pmids), "retmode": "xml"})
    xml = get(pubmed_url)
    cross_refs = pubmed_accessions(xml, pmids)
    identifiers = sorted({nct for values in cross_refs.values() for nct in values})
    if not 0 < len(identifiers) <= MAX_STUDIES:
        raise ValueError("Resolved registry subset is empty or exceeds the small-refresh bound")
    studies = []
    for identifier in identifiers:
        record = json.loads(get(API + "/studies/" + identifier + "?format=json"))
        if record.get("protocolSection", {}).get("identificationModule", {}).get("nctId") != identifier:
            raise ValueError("Registry returned a different trial identifier")
        studies.append(record)
    if json.loads(get(API + "/version")).get("dataTimestamp") != version:
        raise ValueError("Registry version changed during retrieval; retry the fetch")
    links, unmatched = [], []
    for citation in citations:
        ids = cross_refs[citation["reference"].split(":")[1]]
        if not ids:
            unmatched.append({**citation, "reason": "no_structured_pubmed_registry_accession"})
        for nct in ids:
            links.append({**citation, "nct_id": nct, "match_basis": "pubmed_structured_registry_accession"})
    now = datetime.now(timezone.utc).isoformat()
    snapshot = {
        "schema_version": "1", "source": "clinicaltrials_gov", "source_version": version,
        "api_version": before.get("apiVersion"), "source_url": API, "retrieved_at": now,
        "dismech_sources": [{k: v for k, v in source.items() if k != "record"} for source in sources],
        "pubmed": {"source_url": pubmed_url, "retrieved_at": now, "sha256": sha256(xml),
                   "cross_references": cross_refs},
        "studies": studies, "links": links, "unmatched": unmatched, "excluded": excluded,
        "limitations": ["Only registered studies linked by structured PubMed accessions from the selected DisMech treatment evidence are included.",
                        "Registry status and enrollment are source-reported metadata, not efficacy findings or treatment recommendations.",
                        "A registry accession in a publication does not identify the specific treatment arm; no arm-level efficacy is inferred."],
    }
    if len(encode(snapshot)) > MAX_BYTES:
        raise ValueError("Snapshot exceeds the small incremental-load bound")
    return snapshot, xml


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--sources", nargs="+", default=["mm", "jmml"])
    parser.add_argument("--profile", default="mmrf-hackathon")
    parser.add_argument("--region", default="us-west-2")
    parser.add_argument("--bucket", help="Read the pinned DisMech records from this sandbox S3 bucket")
    parser.add_argument("--upload", action="store_true", help="Upload immutable sources and the fixed loader snapshot")
    args = parser.parse_args()
    if not args.sources or not all(re.fullmatch(r"[a-z0-9_]+", n) for n in args.sources):
        parser.error("Use short source names such as mm jmml")
    if args.upload and not args.bucket:
        parser.error("--upload requires --bucket")
    s3 = None
    if args.bucket:
        import boto3
        session = boto3.Session(profile_name=args.profile, region_name=args.region)
        if session.client("sts").get_caller_identity()["Account"] != "548574235556":
            parser.error("Expected the hackathon sandbox account")
        if session.client("s3").get_bucket_location(Bucket=args.bucket).get("LocationConstraint") != args.region:
            parser.error("Bucket must be in the configured Neptune Region")
        s3 = session.client("s3")

    def read(name):
        if s3:
            with s3.get_object(Bucket=args.bucket, Key="raw/" + name)["Body"] as body:
                value = body.read(MAX_BYTES + 1)
            if len(value) > MAX_BYTES:
                raise ValueError("Staged source exceeds the small-subset limit")
            return value
        return (RAW / name).read_bytes()

    sources = []
    for name in dict.fromkeys(args.sources):
        key = f"dismech_{name}.yaml"
        raw, provenance = read(key), json.loads(read(key + ".provenance.json"))
        if provenance.get("sha256") != sha256(raw) or not provenance.get("source_revision"):
            raise ValueError(f"Unversioned or invalid DisMech source: {name}")
        sources.append({"name": name, "disease_id": yaml.safe_load(raw)["disease_term"]["term"]["id"],
                        "record": yaml.safe_load(raw), "source_version": provenance["source_revision"],
                        "source_url": provenance.get("source_url"), "sha256": sha256(raw),
                        "raw_uri": f"s3://{args.bucket}/raw/{key}" if s3 else str(RAW / key)})
    snapshot, xml = collect(sources)
    content = encode(snapshot)
    prefix = "raw/clinical_trials/snapshots/" + str(uuid.uuid4()) + "/"
    provenance = {"sha256": sha256(content), "source_version": snapshot["source_version"],
                  "retrieved_at": snapshot["retrieved_at"], "source_url": API,
                  "snapshot_s3_uri": f"s3://{args.bucket}/{prefix}snapshot.json" if s3 else None,
                  "pubmed_s3_uri": f"s3://{args.bucket}/{prefix}pubmed.xml" if s3 else None}
    RAW.mkdir(parents=True, exist_ok=True)
    (RAW / "clinical_trials.json").write_bytes(content)
    (RAW / "clinical_trials.json.provenance.json").write_bytes(encode(provenance))
    (RAW / "clinical_trials_pubmed.xml").write_bytes(xml)
    if args.upload:
        for key, body, content_type in [
            (prefix + "pubmed.xml", xml, "application/xml"),
            (prefix + "snapshot.json", content, "application/json"),
            (prefix + "provenance.json", encode(provenance), "application/json"),
        ]:
            s3.put_object(Bucket=args.bucket, Key=key, Body=body, ServerSideEncryption="AES256",
                          ContentType=content_type, IfNoneMatch="*")
        # Hash-checking ingestion fails closed if these two pointer objects race with another upload.
        for key, body in [("raw/clinical_trials.json", content),
                          ("raw/clinical_trials.json.provenance.json", encode(provenance))]:
            s3.put_object(Bucket=args.bucket, Key=key, Body=body, ServerSideEncryption="AES256", ContentType="application/json")
    print(json.dumps({"studies": len(snapshot["studies"]), "treatment_links": len(snapshot["links"]),
                      "unmatched_citations": snapshot["unmatched"], "excluded_citations": snapshot["excluded"],
                      "uploaded": args.upload, "provenance": provenance}, indent=2))


if __name__ == "__main__":
    main()
