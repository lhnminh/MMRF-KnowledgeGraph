"""Load the Monarch associations and DisMech mechanism path into Neo4j.

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


def load_monarch(tx, monarch: dict) -> None:
    entity = monarch["entity"]
    tx.run(
        "MERGE (d:Disease {id: $id}) SET d.name = $name",
        id=entity["id"],
        name=entity["name"],
    )
    for assoc in monarch["associations"]:
        tx.run(
            """
            MERGE (d:Disease {id: $subject})
            MERGE (o:Entity {id: $object}) SET o.label = $object_label, o.category = $object_category
            MERGE (d)-[r:ASSOCIATION {predicate: $predicate}]->(o)
            SET r.source = 'monarch',
                r.primary_knowledge_source = $primary_knowledge_source,
                r.publications = $publications
            """,
            subject=assoc["subject"],
            object=assoc["object"],
            object_label=assoc.get("object_label"),
            object_category=assoc.get("object_category"),
            predicate=assoc["predicate"],
            primary_knowledge_source=assoc.get("primary_knowledge_source"),
            publications=assoc.get("publications") or [],
        )


def load_dismech(tx, dismech: dict) -> None:
    disease_id = dismech["disease_term"]["term"]["id"]
    tx.run("MERGE (d:Disease {id: $id}) SET d.name = $name", id=disease_id, name=dismech["name"])

    for step in dismech["pathophysiology"]:
        tx.run(
            """
            MERGE (m:Mechanism {name: $name})
            SET m.description = $description, m.source = 'dismech'
            MERGE (d:Disease {id: $disease_id})
            MERGE (d)-[:HAS_MECHANISM {source: 'dismech'}]->(m)
            """,
            name=step["name"],
            description=step.get("description"),
            disease_id=disease_id,
        )
        for downstream in step.get("downstream", []):
            evidence = (downstream.get("evidence") or [{}])[0]
            tx.run(
                """
                MERGE (a:Mechanism {name: $from_name})
                MERGE (b:Mechanism {name: $to_name})
                MERGE (a)-[r:LEADS_TO]->(b)
                SET r.source = 'dismech',
                    r.evidence_pmid = $pmid,
                    r.evidence_snippet = $snippet,
                    r.dismech_explanation = $explanation
                """,
                from_name=step["name"],
                to_name=downstream["target"],
                pmid=evidence.get("reference"),
                snippet=evidence.get("snippet"),
                explanation=evidence.get("explanation"),
            )


def main() -> None:
    monarch = json.loads((DATA_DIR / "monarch_mm.json").read_text())
    dismech = yaml.safe_load((DATA_DIR / "dismech_mm.yaml").read_text())

    driver = GraphDatabase.driver(NEO4J_URI, auth=NEO4J_AUTH)
    with driver.session() as session:
        session.execute_write(load_monarch, monarch)
        session.execute_write(load_dismech, dismech)
    driver.close()
    print("Loaded Monarch associations and DisMech mechanism path into Neo4j.")


if __name__ == "__main__":
    main()
