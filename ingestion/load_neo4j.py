"""Load the Monarch associations and DisMech mechanism path into the graph.

Runs two ways with the same logic: locally against Neo4j (python ingestion/load_neo4j.py), or as
the kg-ingest Lambda, which reads the same three files from S3 and writes to Neptune.

`dismech_explanation` holds DisMech's curator commentary, kept separate from
graph-encoded edges (predicate/evidence_pmid) so the agent never presents it as fact.
"""
import json
import os
from pathlib import Path

import yaml
from neo4j import GraphDatabase

DATA_DIR = Path(__file__).resolve().parent.parent / "data" / "raw"
NEO4J_URI = os.environ.get("NEO4J_URI", "bolt://localhost:7687")
NEO4J_AUTH = ("neo4j", "password")

MONARCH_FILE = "monarch_mm.json"
DISMECH_FILE = "dismech_mm.yaml"
DISMECH_PROVENANCE_FILE = "dismech_mm.yaml.provenance.json"
S3_PREFIX = "raw/"


def parse_sources(monarch_text: str, dismech_text: str, provenance_text: str) -> tuple[dict, dict, str, str]:
    """Return (monarch, dismech, monarch_version, dismech_version), refusing unversioned data (AD-001)."""
    monarch = json.loads(monarch_text)
    monarch_version = monarch.get("source_version")
    if not monarch_version:
        raise ValueError(f"{MONARCH_FILE} has no source_version; re-run ingestion/fetch_monarch.py")
    dismech_version = json.loads(provenance_text).get("source_revision")
    if not dismech_version:
        raise ValueError(f"{DISMECH_PROVENANCE_FILE} has no source_revision; re-run ingestion/fetch_dismech.py")
    return monarch, yaml.safe_load(dismech_text), monarch_version, dismech_version


def load_monarch(tx, monarch: dict, version: str) -> None:
    entity = monarch["entity"]
    tx.run(
        "MERGE (d:Disease {id: $id}) SET d.name = $name",
        id=entity["id"],
        name=entity["name"],
    )
    for assoc in monarch["associations"]:
        # A disease stays one node whichever end of an edge it appears on. The label comes from
        # this fixed choice, never from the data, because Cypher cannot parameterize labels.
        is_disease = assoc.get("object_category") == "biolink:Disease"
        object_label = "Disease" if is_disease else "Entity"
        disease_name = ", o.name = coalesce(o.name, $object_label)" if is_disease else ""
        tx.run(
            f"""
            MERGE (d:Disease {{id: $subject}})
            SET d.name = coalesce(d.name, $subject_label)
            MERGE (o:{object_label} {{id: $object}})
            SET o.label = $object_label, o.category = $object_category,
                o.source = 'monarch', o.source_version = $version{disease_name}
            MERGE (d)-[r:ASSOCIATION {{predicate: $predicate}}]->(o)
            SET r.source = 'monarch',
                r.source_version = $version,
                r.edge_origin = 'recorded',
                r.primary_knowledge_source = $primary_knowledge_source,
                r.publications = $publications
            """,
            subject=assoc["subject"],
            subject_label=assoc.get("subject_label"),
            object=assoc["object"],
            object_label=assoc.get("object_label"),
            object_category=assoc.get("object_category"),
            predicate=assoc["predicate"],
            primary_knowledge_source=assoc.get("primary_knowledge_source"),
            # A delimited string, because Neptune openCypher has no list-valued properties.
            publications=";".join(assoc.get("publications") or []),
            version=version,
        )


def load_dismech(tx, dismech: dict, version: str) -> None:
    disease_id = dismech["disease_term"]["term"]["id"]
    tx.run("MERGE (d:Disease {id: $id}) SET d.name = $name", id=disease_id, name=dismech["name"])

    for step in dismech["pathophysiology"]:
        evidence = (step.get("evidence") or [{}])[0]
        tx.run(
            """
            MERGE (m:Mechanism {name: $name})
            SET m.description = $description, m.source = 'dismech', m.source_version = $version
            MERGE (d:Disease {id: $disease_id})
            MERGE (d)-[h:HAS_MECHANISM]->(m)
            SET h.source = 'dismech',
                h.source_version = $version,
                h.edge_origin = 'recorded',
                h.evidence_pmid = $pmid,
                h.evidence_snippet = $snippet,
                h.dismech_explanation = $explanation
            """,
            name=step["name"],
            description=step.get("description"),
            disease_id=disease_id,
            version=version,
            pmid=evidence.get("reference"),
            snippet=evidence.get("snippet"),
            explanation=evidence.get("explanation"),
        )
        for downstream in step.get("downstream", []):
            evidence = (downstream.get("evidence") or [{}])[0]
            tx.run(
                """
                MERGE (a:Mechanism {name: $from_name})
                MERGE (b:Mechanism {name: $to_name})
                MERGE (a)-[r:LEADS_TO]->(b)
                SET r.source = 'dismech',
                    r.source_version = $version,
                    r.edge_origin = 'recorded',
                    r.evidence_pmid = $pmid,
                    r.evidence_snippet = $snippet,
                    r.dismech_explanation = $explanation
                """,
                from_name=step["name"],
                to_name=downstream["target"],
                pmid=evidence.get("reference"),
                snippet=evidence.get("snippet"),
                explanation=evidence.get("explanation"),
                version=version,
            )


def load_graph(driver, monarch_text: str, dismech_text: str, provenance_text: str) -> dict:
    """Load both sources and return what the graph now holds. Safe to rerun: every write is a MERGE."""
    monarch, dismech, monarch_version, dismech_version = parse_sources(monarch_text, dismech_text, provenance_text)
    with driver.session() as session:
        session.execute_write(load_monarch, monarch, monarch_version)
        session.execute_write(load_dismech, dismech, dismech_version)
        nodes = session.execute_read(lambda tx: tx.run("MATCH (n) RETURN count(n) AS c").single()["c"])
        edges = session.execute_read(lambda tx: tx.run("MATCH ()-[r]->() RETURN count(r) AS c").single()["c"])
    return {
        "nodes": nodes,
        "relationships": edges,
        "monarch_version": monarch_version,
        "dismech_version": dismech_version,
    }


def lambda_handler(event, context) -> dict:
    """kg-ingest: read the raw files from the fixed bucket and prefix, then write them to Neptune."""
    # Both only exist or matter in the Lambda: boto3 ships with the runtime, and `handler` (the
    # kg-graph module, packaged in the same zip) holds the signed Neptune connection.
    import boto3
    from handler import get_driver

    s3 = boto3.client("s3")
    bucket = os.environ["INGEST_BUCKET"]

    def read(name: str) -> str:
        return s3.get_object(Bucket=bucket, Key=S3_PREFIX + name)["Body"].read().decode("utf-8")

    result = load_graph(get_driver(), read(MONARCH_FILE), read(DISMECH_FILE), read(DISMECH_PROVENANCE_FILE))
    print(json.dumps({"event": "kg_ingest", **result}))
    return result


def main() -> None:
    def read(name: str) -> str:
        return (DATA_DIR / name).read_text(encoding="utf-8")

    driver = GraphDatabase.driver(NEO4J_URI, auth=NEO4J_AUTH)
    result = load_graph(driver, read(MONARCH_FILE), read(DISMECH_FILE), read(DISMECH_PROVENANCE_FILE))
    driver.close()
    print(f"Loaded Monarch associations and DisMech mechanism path into Neo4j: {result}")


if __name__ == "__main__":
    main()
