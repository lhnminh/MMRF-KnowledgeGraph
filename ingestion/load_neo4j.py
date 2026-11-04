"""Load the Monarch associations and DisMech mechanism path into the graph.

Runs two ways with the same logic: locally against Neo4j (python ingestion/load_neo4j.py), or as
the kg-ingest Lambda, which reads the same three files from S3 and writes to Neptune.

`dismech_explanation` holds DisMech's curator commentary, kept separate from
graph-encoded edges (predicate/evidence_pmid) so the agent never presents it as fact.
"""
import argparse
import json
import os
import re
import time
from pathlib import Path

import yaml
from neo4j import GraphDatabase
from neo4j.exceptions import DriverError, Neo4jError

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
        if assoc.get("subject_category") == "biolink:Gene":
            load_gene_disease_association(tx, assoc, version)
            continue
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
            MERGE (d)-[r:ASSOCIATION {{predicate: $predicate, source: 'monarch'}}]->(o)
            SET r.source_version = $version,
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


def load_gene_disease_association(tx, assoc: dict, version: str) -> None:
    """A Monarch gene -> disease association. Monarch stores these with the gene as subject, so the
    gene is an Entity and the disease is the object, the reverse of every other ASSOCIATION edge.
    The gene keeps the specificity Monarch records (gene level)."""
    tx.run(
        """
        MERGE (s:Entity {id: $subject})
        SET s.label = coalesce(s.label, $subject_label),
            s.category = coalesce(s.category, $subject_category),
            s.source = coalesce(s.source, 'monarch'),
            s.source_version = coalesce(s.source_version, $version)
        MERGE (o:Disease {id: $object})
        SET o.name = coalesce(o.name, $object_label)
        MERGE (s)-[r:ASSOCIATION {predicate: $predicate, source: 'monarch'}]->(o)
        SET r.source_version = $version,
            r.edge_origin = 'recorded',
            r.primary_knowledge_source = $primary_knowledge_source,
            r.publications = $publications
        """,
        subject=assoc["subject"],
        subject_label=assoc.get("subject_label"),
        subject_category=assoc["subject_category"],
        object=assoc["object"],
        object_label=assoc.get("object_label"),
        predicate=assoc["predicate"],
        primary_knowledge_source=assoc.get("primary_knowledge_source"),
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

    for phenotype in dismech.get("phenotypes", []):
        term = phenotype["phenotype_term"]["term"]
        # The HP id joins this to any Monarch record of the same phenotype: one node, one edge per source.
        write_association(
            tx, disease_id, term["id"], term["label"], "biolink:PhenotypicFeature", "biolink:has_phenotype",
            phenotype, version, description=phenotype.get("description"), frequency=phenotype.get("frequency"),
        )

    for subtype in dismech.get("has_subtypes", []):
        write_association(
            tx, disease_id, subtype_id(disease_id, subtype["name"]), subtype.get("display_name") or subtype["name"],
            "dismech:DiseaseSubtype", "dismech:has_subtype", subtype, version,
            description=subtype.get("description"), association_type=subtype.get("classification"),
        )

    for factor in dismech.get("genetic", []):
        gene = factor.get("gene_term", {}).get("term")
        # Gene ids use the HGNC: form that other sources join on (AD-001). DisMech writes hgnc:.
        object_id = "HGNC:" + gene["id"].split(":")[1] if gene else f"dismech:genetic:{factor['name']}"
        write_association(
            tx, disease_id, object_id, gene["label"] if gene else factor["name"],
            "biolink:Gene" if gene else "dismech:GeneticFactor", "dismech:has_genetic_factor",
            factor, version, description=factor.get("notes"), association_type=factor.get("association"),
        )
        # DisMech names the subtype a driver gene defines (for example KRAS -> KRAS-mutated). Only that
        # recorded link is loaded; a gene with no `subtype` field gets no subtype edge.
        if gene and factor.get("subtype"):
            tx.run(
                """
                MATCH (s:Entity {id: $subtype})
                MATCH (g:Entity {id: $gene})
                MERGE (s)-[r:HAS_DRIVER_GENE]->(g)
                SET r.source = 'dismech', r.source_version = $version, r.edge_origin = 'recorded',
                    r.association_type = $association_type
                """,
                subtype=subtype_id(disease_id, factor["subtype"]),
                gene=object_id,
                association_type=factor.get("association"),
                version=version,
            )

    for treatment in dismech.get("treatments", []):
        term = treatment["treatment_term"]
        # Most treatments share one generic term (NCIT:C15986), so the record's name identifies the node.
        treatment_id = f"dismech:treatment:{treatment['name']}"
        write_association(
            tx, disease_id, treatment_id, treatment["name"], "biolink:Treatment", "dismech:has_recorded_treatment",
            treatment, version, description=treatment.get("description"), ontology_term=term["term"]["id"],
            therapeutic_modality=treatment.get("therapeutic_modality"),
        )
        for target in treatment.get("target_mechanisms", []):
            evidence = (target.get("evidence") or [{}])[0]
            tx.run(
                """
                MATCH (t:Entity {id: $treatment})
                MERGE (m:Mechanism {name: $mechanism})
                MERGE (t)-[r:TARGETS_MECHANISM]->(m)
                SET r.source = 'dismech',
                    r.source_version = $version,
                    r.edge_origin = 'recorded',
                    r.treatment_effect = $effect,
                    r.description = $description,
                    r.evidence_pmid = $pmid,
                    r.evidence_snippet = $snippet,
                    r.dismech_explanation = $explanation
                """,
                treatment=treatment_id,
                mechanism=target["target"],
                effect=target.get("treatment_effect"),
                description=target.get("description"),
                pmid=evidence.get("reference"),
                snippet=evidence.get("snippet"),
                explanation=evidence.get("explanation"),
                version=version,
            )
        for agent in term.get("therapeutic_agent", []):
            tx.run(
                """
                MERGE (t:Entity {id: $treatment})
                MERGE (a:Entity {id: $agent})
                SET a.label = coalesce(a.label, $agent_label),
                    a.category = coalesce(a.category, 'biolink:Drug'),
                    a.source = coalesce(a.source, 'dismech'),
                    a.source_version = coalesce(a.source_version, $version)
                MERGE (t)-[r:HAS_AGENT]->(a)
                SET r.source = 'dismech', r.source_version = $version, r.edge_origin = 'recorded'
                """,
                treatment=treatment_id,
                agent=agent["term"]["id"],
                agent_label=agent["preferred_term"],
                version=version,
            )


def subtype_id(disease_id: str, name: str) -> str:
    """Subtype names repeat across diseases, so the disease id is part of the node id."""
    return f"dismech:subtype:{disease_id}:{name}"


def write_association(
    tx, disease_id, object_id, object_label, object_category, predicate, record, version, *,
    description=None, frequency=None, association_type=None, ontology_term=None, therapeutic_modality=None,
) -> None:
    """One DisMech record as a Disease -> Entity edge, carrying the record's first evidence item."""
    evidence = (record.get("evidence") or [{}])[0]
    tx.run(
        """
        MERGE (d:Disease {id: $disease_id})
        MERGE (o:Entity {id: $object})
        SET o.label = coalesce(o.label, $object_label),
            o.category = coalesce(o.category, $object_category),
            o.source = coalesce(o.source, 'dismech'),
            o.source_version = coalesce(o.source_version, $version)
        MERGE (d)-[r:ASSOCIATION {predicate: $predicate, source: 'dismech'}]->(o)
        SET r.source_version = $version,
            r.edge_origin = 'recorded',
            r.record_name = $record_name,
            r.frequency = $frequency,
            r.association_type = $association_type,
            r.ontology_term = $ontology_term,
            r.therapeutic_modality = $therapeutic_modality,
            r.description = $description,
            r.evidence_pmid = $pmid,
            r.evidence_snippet = $snippet,
            r.dismech_explanation = $explanation
        """,
        disease_id=disease_id,
        object=object_id,
        object_label=object_label,
        object_category=object_category,
        predicate=predicate,
        record_name=record["name"],
        frequency=frequency,
        association_type=association_type,
        ontology_term=ontology_term,
        therapeutic_modality=therapeutic_modality,
        description=description,
        pmid=evidence.get("reference"),
        snippet=evidence.get("snippet"),
        explanation=evidence.get("explanation"),
        version=version,
    )


WRITE_RETRY_ATTEMPTS = 3
WRITE_RETRY_BASE_DELAY_SECONDS = 1.0


def _execute_write_with_retry(session, work, *args):
    """Retry a whole execute_write call, not just the driver's own per-transaction retry.
    Safe: load_monarch/load_dismech are all MERGEs, so rerunning one doesn't duplicate writes."""
    for attempt in range(1, WRITE_RETRY_ATTEMPTS + 1):
        try:
            return session.execute_write(work, *args)
        except (DriverError, Neo4jError):
            if attempt == WRITE_RETRY_ATTEMPTS:
                raise
            time.sleep(WRITE_RETRY_BASE_DELAY_SECONDS * attempt)


def load_graph(driver, monarch_text: str, dismech_text: str, provenance_text: str) -> dict:
    """Load one Monarch file and one DisMech record. Safe to rerun: every write is a MERGE."""
    return load_graphs(driver, [(monarch_text, dismech_text, provenance_text)])


def load_graphs(driver, sources: list[tuple[str, str, str]]) -> dict:
    """Load each (monarch_text, dismech_text, provenance_text) set, then report what the graph holds.

    Every set is parsed, and refused if unversioned, before anything is written."""
    parsed = [parse_sources(*texts) for texts in sources]
    monarch_versions, dismech_versions = set(), set()
    with driver.session() as session:
        for monarch, dismech, monarch_version, dismech_version in parsed:
            _execute_write_with_retry(session, load_monarch, monarch, monarch_version)
            monarch_versions.add(monarch_version)
            _execute_write_with_retry(session, load_dismech, dismech, dismech_version)
            dismech_versions.add(dismech_version)
        nodes = session.execute_read(lambda tx: tx.run("MATCH (n) RETURN count(n) AS c").single()["c"])
        edges = session.execute_read(lambda tx: tx.run("MATCH ()-[r]->() RETURN count(r) AS c").single()["c"])
    return {
        "nodes": nodes,
        "relationships": edges,
        "monarch_version": ", ".join(sorted(monarch_versions)),
        "dismech_version": ", ".join(sorted(dismech_versions)),
    }


SOURCE_NAME = re.compile(r"[a-z0-9_]+")


def source_names(event) -> list[str]:
    """Names to load from an invoke event, validated so only raw/monarch_<name>.json and
    raw/dismech_<name>.yaml (+ .provenance.json) can be read.

    {"sources": ["mm", "jmml"]} loads both sets. With no "sources" it loads multiple myeloma only, as before."""
    names = (event or {}).get("sources") or ["mm"]
    if not isinstance(names, list) or not all(isinstance(n, str) and SOURCE_NAME.fullmatch(n) for n in names):
        raise ValueError("sources must be a list of names made of lowercase letters, digits and '_'")
    return names


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

    result = load_graphs(
        get_driver(),
        [
            (read(f"monarch_{n}.json"), read(f"dismech_{n}.yaml"), read(f"dismech_{n}.yaml.provenance.json"))
            for n in source_names(event)
        ],
    )
    print(json.dumps({"event": "kg_ingest", **result}))
    return result


def main() -> None:
    def read(name: str) -> str:
        return (DATA_DIR / name).read_text(encoding="utf-8")

    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--sources", nargs="+", default=["mm"], help="names such as mm jmml")
    names = source_names({"sources": parser.parse_args().sources})
    driver = GraphDatabase.driver(NEO4J_URI, auth=NEO4J_AUTH)
    result = load_graphs(
        driver,
        [(read(f"monarch_{n}.json"), read(f"dismech_{n}.yaml"), read(f"dismech_{n}.yaml.provenance.json")) for n in names],
    )
    driver.close()
    print(f"Loaded Monarch associations and DisMech records into Neo4j: {result}")


if __name__ == "__main__":
    main()
