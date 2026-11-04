"""VPC Lambda: verify a committed snapshot, atomically upsert its small graph, then read it back."""

import json
import os
from collections import Counter
from datetime import datetime, timezone

from source_pipeline.common import (
    DATA_FILES,
    DISEASE,
    MAX_ASSOCIATIONS,
    MAX_EDGES,
    digest,
    in_disease_subset,
    read_object,
    run_prefix,
    validate_manifest,
)

MATCHES = {
    "association": """MATCH (a {id: $start})-[r:ASSOCIATION {source: $source, predicate: $predicate}]->(b)
                      WHERE b.id = $end""",
    "mechanism": """MATCH (a:Disease {id: $start})-[r:HAS_MECHANISM {source: $source}]->(b:Mechanism {name: $end})""",
    "leads_to": """MATCH (a:Mechanism {name: $start})-[r:LEADS_TO {source: $source}]->(b:Mechanism {name: $end})""",
    "agent": """MATCH (a:Entity {id: $start})-[r:HAS_AGENT {source: $source}]->(b:Entity {id: $end})""",
    "driver": """MATCH (a:Entity {id: $start})-[r:HAS_DRIVER_GENE {source: $source}]->(b:Entity {id: $end})""",
    "target": """MATCH (a:Entity {id: $start})-[r:TARGETS_MECHANISM {source: $source}]->(b:Mechanism {name: $end})""",
}
STAMP = """
SET r.ingestion_run_id = $run_id, r.retrieved_at = $retrieved_at, r.source_url = $source_url,
    r.raw_s3_uri = $raw_s3_uri, r.raw_sha256 = $raw_sha256,
    r.source_manifest_uri = $source_manifest_uri, r.source_manifest_sha256 = $manifest_sha256,
    r.source_record_pointer = $record_pointer, r.source_record_id = $record_id,
    r.evidence_json = $evidence_json
"""
VALIDATE = """MATCH ()-[r]->() WHERE r.ingestion_run_id = $run_id
RETURN r.source AS source, r.source_version AS version, count(r) AS relationships"""


def read_snapshot(s3, bucket, event):
    run_id = event["run_id"]
    key = run_prefix(run_id) + "manifest.json"
    if event.get("manifest_key") != key or event.get("bucket") != bucket:
        raise ValueError("Ingestion must use this run's manifest in the configured bucket")
    content = read_object(s3, bucket, key)
    if digest(content) != event.get("manifest_sha256"):
        raise ValueError("Manifest checksum does not match the fetch receipt")
    manifest = json.loads(content)
    validate_manifest(manifest, run_id)
    files = {}
    for name, descriptor in manifest["files"].items():
        body = read_object(s3, bucket, descriptor["key"])
        if digest(body) != descriptor["sha256"] or len(body) != descriptor["bytes"]:
            raise ValueError("Source artifact checksum or size mismatch: " + name)
        if name in DATA_FILES:
            files[name] = body.decode("utf-8")
    return manifest, files


def expected_records(monarch, dismech):
    """The same fixed record identities used by the existing loader, including source-specific parallel edges."""
    if monarch.get("entity", {}).get("id") != DISEASE:
        raise ValueError("Monarch disease identity is invalid")
    if dismech.get("disease_term", {}).get("term", {}).get("id") != DISEASE:
        raise ValueError("DisMech disease identity is invalid")
    if not 0 < len(monarch.get("associations", [])) <= MAX_ASSOCIATIONS:
        raise ValueError("Monarch associations are empty or exceed the incremental limit")
    records = {}

    def add(kind, start, predicate, end, source, record, pointer):
        if not all(isinstance(value, str) and value for value in (start, predicate, end)):
            raise ValueError("Source relationship has a missing identifier or predicate")
        if "LOINC:" in json.dumps(record).upper():
            raise ValueError("LOINC-bearing records are outside this permitted sandbox subset")
        key = kind, start, predicate, end, source
        if key in records:
            raise ValueError("Source contains repeated graph identities; review before merging evidence")
        records[key] = {"record": record, "pointer": pointer}

    for index, record in enumerate(monarch["associations"]):
        if not in_disease_subset(record) or record.get("negated"):
            raise ValueError("Unsupported or out-of-scope Monarch association")
        if record.get("subject_category") not in {"biolink:Disease", "biolink:Gene"}:
            raise ValueError("The loader supports disease/gene-subject Monarch records only")
        add("association", record["subject"], record["predicate"], record["object"], "monarch", record,
            f"/associations/{index}")
    steps = dismech.get("pathophysiology", [])
    if not isinstance(steps, list) or not steps or len(steps) > 30:
        raise ValueError("DisMech mechanism list is empty or exceeds the small-refresh limit")
    names = {step["name"] for step in steps}
    if len(names) != len(steps):
        raise ValueError("DisMech mechanism names must be unique")
    for index, step in enumerate(steps):
        add("mechanism", DISEASE, "has_mechanism", step["name"], "dismech", step, f"/pathophysiology/{index}")
        for downstream_index, downstream in enumerate(step.get("downstream", [])):
            if downstream["target"] not in names:
                raise ValueError("DisMech downstream target has no mechanism definition")
            add("leads_to", step["name"], "leads_to", downstream["target"], "dismech", downstream,
                f"/pathophysiology/{index}/downstream/{downstream_index}")
    for index, record in enumerate(dismech.get("phenotypes", [])):
        term = record["phenotype_term"]["term"]
        add("association", DISEASE, "biolink:has_phenotype", term["id"], "dismech", record, f"/phenotypes/{index}")
    subtypes = {record["name"] for record in dismech.get("has_subtypes", [])}
    for index, record in enumerate(dismech.get("has_subtypes", [])):
        add("association", DISEASE, "dismech:has_subtype", f"dismech:subtype:{DISEASE}:{record['name']}",
            "dismech", record, f"/has_subtypes/{index}")
    for index, record in enumerate(dismech.get("genetic", [])):
        gene = record.get("gene_term", {}).get("term")
        identifier = "HGNC:" + gene["id"].split(":")[1] if gene else "dismech:genetic:" + record["name"]
        add("association", DISEASE, "dismech:has_genetic_factor", identifier, "dismech", record, f"/genetic/{index}")
        if gene and record.get("subtype"):
            if record["subtype"] not in subtypes:
                raise ValueError("DisMech driver gene references an undefined subtype")
            add("driver", f"dismech:subtype:{DISEASE}:{record['subtype']}", "HAS_DRIVER_GENE", identifier,
                "dismech", record, f"/genetic/{index}")
    for index, record in enumerate(dismech.get("treatments", [])):
        identifier = "dismech:treatment:" + record["name"]
        add("association", DISEASE, "dismech:has_recorded_treatment", identifier, "dismech", record, f"/treatments/{index}")
        for target_index, target in enumerate(record.get("target_mechanisms", [])):
            if target["target"] not in names:
                raise ValueError("Recorded treatment references an undefined mechanism")
            add("target", identifier, "TARGETS_MECHANISM", target["target"], "dismech", target,
                f"/treatments/{index}/target_mechanisms/{target_index}")
        for agent_index, agent in enumerate(record["treatment_term"].get("therapeutic_agent", [])):
            add("agent", identifier, "HAS_AGENT", agent["term"]["id"], "dismech", agent,
                f"/treatments/{index}/treatment_term/therapeutic_agent/{agent_index}")
    if len(records) > MAX_EDGES:
        raise ValueError("Graph exceeds 300 relationships; use the bulk-loading workflow for larger datasets")
    return records


def verify_counts(tx, run_id, expected):
    actual = {(row["source"], row["version"]): row["relationships"] for row in tx.run(VALIDATE, run_id=run_id)}
    if actual != expected:
        raise ValueError("Neptune validation did not match the expected per-source, per-version relationship counts")
    return [
        {"source": source, "source_version": version, "relationships": count}
        for (source, version), count in sorted(actual.items())
    ]


def ingest_snapshot(driver, manifest, files, bucket, manifest_sha256, loader):
    monarch, dismech, monarch_version, dismech_version = loader.parse_sources(
        files["monarch_mm.json"], files["dismech_mm.yaml"], files["dismech_mm.yaml.provenance.json"]
    )
    versions = {"monarch": monarch_version, "dismech": dismech_version}
    if versions != {key: value["source_version"] for key, value in manifest["sources"].items()}:
        raise ValueError("Parsed source versions differ from the committed manifest")
    provenance = json.loads(files["dismech_mm.yaml.provenance.json"])
    if provenance.get("sha256") != digest(files["dismech_mm.yaml"].encode()):
        raise ValueError("DisMech's own provenance checksum does not match its YAML")
    records = expected_records(monarch, dismech)
    expected = dict(Counter((key[-1], versions[key[-1]]) for key in records))
    run_id = manifest["run_id"]
    manifest_uri = f"s3://{bucket}/{run_prefix(run_id)}manifest.json"

    def write(tx):
        # One transaction for both sources, metadata and validation: source-shape errors cannot leave half a refresh.
        loader.load_monarch(tx, monarch, monarch_version)
        loader.load_dismech(tx, dismech, dismech_version)
        for (kind, start, predicate, end, source), value in records.items():
            descriptor = manifest["files"]["monarch_mm.json" if source == "monarch" else "dismech_mm.yaml"]
            metadata = manifest["sources"][source]
            record = value["record"]
            tx.run(MATCHES[kind] + STAMP, start=start, end=end, predicate=predicate, source=source,
                   run_id=run_id, retrieved_at=metadata["retrieved_at"], source_url=metadata["source_url"],
                   raw_s3_uri=f"s3://{bucket}/{descriptor['key']}", raw_sha256=descriptor["sha256"],
                   source_manifest_uri=manifest_uri, manifest_sha256=manifest_sha256,
                   record_pointer=value["pointer"], record_id=record.get("id"),
                   evidence_json=json.dumps(record.get("evidence") or record.get("publications") or [],
                                            ensure_ascii=False, default=str))
        verify_counts(tx, run_id, expected)

    with driver.session() as session:
        session.execute_write(write)
        verified = session.execute_read(lambda tx: verify_counts(tx, run_id, expected))
        nodes = session.execute_read(lambda tx: tx.run("MATCH (n) RETURN count(n) AS c").single()["c"])
        relationships = session.execute_read(lambda tx: tx.run("MATCH ()-[r]->() RETURN count(r) AS c").single()["c"])
    return {
        "status": "SUCCEEDED", "run_id": run_id, "manifest_uri": manifest_uri,
        "manifest_sha256": manifest_sha256, "completed_at": datetime.now(timezone.utc).isoformat(),
        "validation": verified, "nodes_after": nodes, "relationships_after": relationships,
        "ingest_mode": "small_incremental_upsert", "expected_relationships": len(records),
        "limitations": [
            "Upsert only: records removed upstream are not deleted from Neptune; inspect source_version and ingestion_run_id.",
            "Only the current small Monarch/DisMech subset is supported; bulk loading is required for initial or larger datasets.",
            "Source confidence/review fields and all original records remain in the S3 snapshots; no confidence is inferred.",
        ],
    }


def handler(event, context):
    import boto3
    import load_neo4j
    from handler import get_driver

    bucket = os.environ["INGEST_BUCKET"]
    manifest, files = read_snapshot(boto3.client("s3"), bucket, event)
    return ingest_snapshot(get_driver(), manifest, files, bucket, event["manifest_sha256"], load_neo4j)
