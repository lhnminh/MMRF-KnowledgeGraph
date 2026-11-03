"""kg-graph: agent-facing query tool over the Neptune myeloma knowledge graph.
r.dismech_explanation is curator commentary, not a graph-encoded fact — callers must not present it as one.
"""
import os

from neo4j import GraphDatabase

NEPTUNE_URI = os.environ["NEPTUNE_URI"]

_driver = None


def get_driver():
    global _driver
    if _driver is None:
        _driver = GraphDatabase.driver(NEPTUNE_URI, auth=("username", "password"), encrypted=True)
    return _driver


def overview(tx, disease_id):
    record = tx.run(
        "MATCH (d:Disease {id: $id}) RETURN d.id AS id, d.name AS name",
        id=disease_id,
    ).single()
    return dict(record) if record else None


def associations(tx, disease_id):
    result = tx.run(
        """
        MATCH (d:Disease {id: $id})-[r:ASSOCIATION]->(o:Entity)
        RETURN o.id AS object_id, o.label AS object_label, o.category AS object_category,
               r.predicate AS predicate, r.primary_knowledge_source AS primary_knowledge_source,
               r.publications AS publications, r.source AS source
        """,
        id=disease_id,
    )
    return [dict(record) for record in result]


def mechanism(tx, disease_id):
    nodes = [
        dict(record)
        for record in tx.run(
            """
            MATCH (d:Disease {id: $id})-[:HAS_MECHANISM]->(start:Mechanism)
            MATCH (start)-[:LEADS_TO*0..10]->(m:Mechanism)
            RETURN DISTINCT m.name AS name, m.description AS description
            """,
            id=disease_id,
        )
    ]
    names = [n["name"] for n in nodes]
    edges = [
        dict(record)
        for record in tx.run(
            """
            MATCH (a:Mechanism)-[r:LEADS_TO]->(b:Mechanism)
            WHERE a.name IN $names AND b.name IN $names
            RETURN a.name AS from_name, b.name AS to_name,
                   r.evidence_pmid AS evidence_pmid, r.evidence_snippet AS evidence_snippet,
                   r.dismech_explanation AS dismech_explanation
            """,
            names=names,
        )
    ]
    return {"nodes": nodes, "edges": edges}


_QUERIES = {"overview": overview, "associations": associations, "mechanism": mechanism}


def handler(event, context):
    disease_id = event.get("disease_id")
    query_type = event.get("query_type", "overview")

    if not disease_id:
        return {"error": "disease_id is required"}
    if query_type not in _QUERIES:
        return {"error": f"unknown query_type: {query_type}, must be one of {list(_QUERIES)}"}

    with get_driver().session() as session:
        result = session.execute_read(_QUERIES[query_type], disease_id)

    return {"disease_id": disease_id, "query_type": query_type, "result": result}
