"""Preview the Neptune CSV export in local Neo4j without changing the source dataset."""

import argparse
import csv
import json
import math
import os
import re
from collections import defaultdict
from pathlib import Path

from neo4j import GraphDatabase


def properties(row):
    result = {}
    for header, value in row.items():
        if header.startswith(":") or value == "":
            continue
        name, kind = header.rsplit(":", 1)
        if kind == "String":
            result[name] = value
        elif kind == "Bool":
            if value not in {"true", "false"}:
                raise ValueError(f"Invalid Boolean for {name}")
            result[name] = value == "true"
        elif kind == "Double":
            result[name] = float(value)
            if not math.isfinite(result[name]):
                raise ValueError(f"Non-finite number for {name}")
        else:
            raise ValueError(f"Unsupported CSV property type: {kind}")
    return result


def identifier(value):
    if not re.fullmatch(r"[A-Za-z_][A-Za-z0-9_]*", value):
        raise ValueError("Invalid graph label or relationship type")
    return value


def read_export(directory):
    # Preserve complete source/evidence JSON, with a bounded 8 MiB field limit.
    csv.field_size_limit(8 * 1024 * 1024)
    with (directory / "nodes.csv").open(newline="", encoding="utf-8") as f:
        nodes = list(csv.DictReader(f))
    with (directory / "relationships.csv").open(newline="", encoding="utf-8") as f:
        edges = list(csv.DictReader(f))
    if not nodes or not edges or len(nodes) > 10000 or len(edges) > 10000:
        raise ValueError("Expected a nonempty hackathon graph with at most 10,000 nodes/relationships")
    ids = {row[":ID"] for row in nodes}
    if len(ids) != len(nodes) or len({row[":ID"] for row in edges}) != len(edges):
        raise ValueError("Duplicate source IDs")
    node_groups, edge_groups = defaultdict(list), defaultdict(list)
    for row in nodes:
        props = properties(row)
        if props.get("id") != row[":ID"]:
            raise ValueError("Node property ID must match its export ID")
        labels = tuple(sorted({"Entity", *[identifier(v) for v in row[":LABEL"].split(";")]}))
        node_groups[labels].append({"id": row[":ID"], "properties": props})
    for row in edges:
        if row[":START_ID"] not in ids or row[":END_ID"] not in ids:
            raise ValueError("Relationship endpoint is missing from nodes.csv")
        props = properties(row)
        if props.get("id") != row[":ID"]:
            raise ValueError("Relationship property ID must match its export ID")
        edge_groups[identifier(row[":TYPE"])].append(
            {
                "id": row[":ID"],
                "subject": row[":START_ID"],
                "object": row[":END_ID"],
                "properties": props,
            }
        )
    return node_groups, edge_groups


def import_export(tx, node_groups, edge_groups):
    # Labels/types are checked identifiers; every ID and property value is parameterized.
    for labels, rows in node_groups.items():
        label_clause = ":".join(labels)
        tx.run(
            f"UNWIND $rows AS row MERGE (n:Entity {{id:row.id}}) SET n:{label_clause} SET n += row.properties",
            rows=rows,
        ).consume()
    for kind, rows in edge_groups.items():
        tx.run(
            f"UNWIND $rows AS row MATCH (s:Entity {{id:row.subject}}), (t:Entity {{id:row.object}}) "
            f"MERGE (s)-[r:{kind} {{id:row.id}}]->(t) SET r += row.properties",
            rows=rows,
        ).consume()


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--directory", type=Path, default=Path("data/transformed"))
    args = parser.parse_args()
    nodes, edges = read_export(args.directory)
    with GraphDatabase.driver(
        os.getenv("NEO4J_URI", "bolt://localhost:7687"),
        auth=(os.getenv("NEO4J_USER", "neo4j"), os.getenv("NEO4J_PASSWORD", "password")),
        connection_timeout=10,
    ) as driver:
        driver.verify_connectivity()
        with driver.session(database=os.getenv("NEO4J_DATABASE", "neo4j")) as session:
            session.execute_write(import_export, nodes, edges)
    print(
        json.dumps(
            {
                "loaded_nodes": sum(map(len, nodes.values())),
                "loaded_relationships": sum(map(len, edges.values())),
                "browser": "http://localhost:7474",
            }
        )
    )
