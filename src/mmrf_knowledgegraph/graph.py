"""Bounded graph operations. The model never supplies query text."""

import json
import os
from collections import deque
from pathlib import Path

import boto3
from botocore.config import Config

NODE_RETURN = "n.id AS id, n.name AS name, n.entity_type AS entity_type, n.aliases_json AS aliases_json, n.provenance_json AS provenance_json"
EDGE_RETURN = "r.id AS id, s.id AS subject, t.id AS object, r.predicate AS predicate, r.source AS source, r.source_url AS source_url, r.retrieved_at AS retrieved_at, r.review_status AS review_status, r.confidence AS confidence, r.synthetic AS synthetic, r.evidence_json AS evidence_json, r.original_json AS original_json, r.provenance_json AS provenance_json"


def decode(row):
    out = dict(row)
    for key in list(out):
        if key.endswith("_json"):
            out[key[:-5]] = json.loads(
                out.pop(key)
                or ("[]" if key in {"aliases_json", "evidence_json", "provenance_json"} else "{}")
            )
    return out


class Neptune:
    def __init__(self, endpoint=None, client=None):
        endpoint = endpoint or os.environ["NEPTUNE_ENDPOINT"]
        if not endpoint.startswith("https://"):
            raise ValueError("NEPTUNE_ENDPOINT must use HTTPS")
        self.client = client or boto3.client(
            "neptunedata",
            endpoint_url=endpoint,
            config=Config(
                connect_timeout=5, read_timeout=25, retries={"max_attempts": 2, "mode": "standard"}
            ),
        )

    def query(self, statement, **parameters):
        response = self.client.execute_open_cypher_query(
            openCypherQuery="USING QUERY:TIMEOUTMILLISECONDS 15000 " + statement,
            parameters=json.dumps(parameters),
        )
        return [decode(r) for r in response.get("results", [])]

    def search(self, query, entity_types):
        return self.query(
            f"MATCH (n:Entity) WHERE (toLower(n.name) CONTAINS $query OR toLower(n.id) = $query OR toLower(n.aliases_json) CONTAINS $query) AND (size($types) = 0 OR n.entity_type IN $types) RETURN {NODE_RETURN} ORDER BY n.name LIMIT 20",
            query=query.lower(),
            types=entity_types,
        )

    def nodes(self, ids):
        return self.query(f"MATCH (n:Entity) WHERE n.id IN $ids RETURN {NODE_RETURN} LIMIT 100", ids=ids)

    def edges(self, ids):
        return self.query(
            f"MATCH (s:Entity)-[r:RELATIONSHIP]->(t:Entity) WHERE r.id IN $ids RETURN {EDGE_RETURN} LIMIT 50",
            ids=ids,
        )

    def paths(self, source, target, depth):
        # Depth is a validated integer selected by code; data values are parameters.
        return self.query(
            f"MATCH p=(s:Entity)-[:RELATIONSHIP*1..{depth}]-(t:Entity) WHERE s.id=$source AND t.id=$target RETURN [n IN nodes(p) | n.id] AS node_ids, [r IN relationships(p) | r.id] AS relationship_ids LIMIT 20",
            source=source,
            target=target,
        )

    def related(self, entity_id, predicates, limit):
        return self.query(
            f"MATCH (s:Entity)-[r:RELATIONSHIP]->(t:Entity) WHERE (s.id=$id OR t.id=$id) AND (size($predicates)=0 OR r.predicate IN $predicates) RETURN {EDGE_RETURN} ORDER BY r.id LIMIT {limit}",
            id=entity_id,
            predicates=predicates,
        )


class Local:
    """Offline rehearsal backend; same operation/result contract, never labeled Neptune."""

    def __init__(self, path=None):
        g = json.loads(Path(path or os.getenv("ORION_GRAPH_PATH", "data/transformed/graph.json")).read_text())
        self._nodes = {n["id"]: n for n in g["nodes"]}
        self._edges = {r["id"]: r for r in g["relationships"]}

    def search(self, query, entity_types):
        q = query.lower()
        return sorted(
            [
                n
                for n in self._nodes.values()
                if (not entity_types or n["entity_type"] in entity_types)
                and any(q in str(v).lower() for v in [n["id"], n["name"], *n.get("aliases", [])])
            ],
            key=lambda n: n["name"],
        )[:20]

    def nodes(self, ids):
        return [self._nodes[i] for i in ids if i in self._nodes][:100]

    def edges(self, ids):
        return [self._edges[i] for i in ids if i in self._edges][:50]

    def related(self, entity_id, predicates, limit):
        return sorted(
            [
                r
                for r in self._edges.values()
                if entity_id in (r["subject"], r["object"])
                and (not predicates or r["predicate"] in predicates)
            ],
            key=lambda r: r["id"],
        )[:limit]

    def paths(self, source, target, depth):
        results, queue = [], deque([([source], [])])
        examined = 0
        while queue and len(results) < 20 and examined < 5000:
            nodes, edges = queue.popleft()
            examined += 1
            if len(edges) >= depth:
                continue
            for r in self.related(nodes[-1], [], 50):
                other = r["object"] if r["subject"] == nodes[-1] else r["subject"]
                if other in nodes:
                    continue
                ns, es = nodes + [other], edges + [r["id"]]
                if other == target:
                    results.append({"node_ids": ns, "relationship_ids": es})
                else:
                    queue.append((ns, es))
        return results[:20]


class GraphTools:
    def __init__(self, backend):
        self.backend = backend

    def resolve(self, value, entity_type=None):
        hits = self.backend.search(value, [entity_type] if entity_type else [])
        exact = [
            n
            for n in hits
            if value.casefold()
            in [str(n[k]).casefold() for k in ("id", "name")] + [a.casefold() for a in n.get("aliases", [])]
        ]
        hits = exact or hits
        if len(hits) != 1:
            raise ValueError(
                "Entity not found or ambiguous; use search_entities and choose an exact identifier"
            )
        return hits[0]["id"]

    def subgraph(self, edges, paths=None):
        ids = sorted({r[end] for r in edges for end in ("subject", "object")})
        return {
            "nodes": self.backend.nodes(ids),
            "relationships": edges,
            "paths": paths or [],
            "empty": not edges,
            "interpretation": "Edges preserve direction and source predicate. Undirected paths show connections, not proof of causation.",
        }

    def search_entities(self, query, entity_types):
        nodes = self.backend.search(query, entity_types)
        return {"nodes": nodes, "empty": not nodes}

    def find_mechanism_path(self, source_entity, target_entity, maximum_depth):
        s, t = self.resolve(source_entity), self.resolve(target_entity)
        paths = self.backend.paths(s, t, maximum_depth)
        ids = list(dict.fromkeys(i for p in paths for i in p["relationship_ids"]))[:50]
        # Keep only paths whose complete evidence fits the response cap.
        paths = [p for p in paths if set(p["relationship_ids"]) <= set(ids)]
        return self.subgraph(self.backend.edges(ids), paths)

    def find_gene_disease_connections(self, gene, disease):
        return self.find_mechanism_path(self.resolve(gene, "Gene"), self.resolve(disease, "Disease"), 3)

    def get_supporting_evidence(self, relationship_ids):
        edges = self.backend.edges(relationship_ids)
        return {
            "relationships": edges,
            "missing_ids": sorted(set(relationship_ids) - {r["id"] for r in edges}),
            "empty": not edges,
        }

    def find_related_entities(self, entity_id, relationship_types, limit):
        id = self.resolve(entity_id)
        return self.subgraph(self.backend.related(id, relationship_types, limit))

    def compare_entities(self, entity_ids, disease):
        id = self.resolve(disease, "Disease")
        comparisons = [
            {"entity_id": self.resolve(e, "Gene"), **self.find_gene_disease_connections(e, id)}
            for e in entity_ids
        ]
        return {
            "comparisons": comparisons,
            "caution": "Shared disease associations do not establish shared gene-level phenotypes.",
            "empty": not any(c["relationships"] for c in comparisons),
        }

    def get_graph_neighborhood(self, entity_id, maximum_depth):
        center = self.resolve(entity_id)
        seen, frontier, edges = {center}, {center}, {}
        for _ in range(maximum_depth):
            next_frontier = set()
            for id in sorted(frontier):
                for r in self.backend.related(id, [], 50 - len(edges)):
                    edges[r["id"]] = r
                    next_frontier.update({r["subject"], r["object"]} - seen)
                if len(edges) >= 50:
                    break
            seen |= next_frontier
            frontier = next_frontier
            if len(edges) >= 50 or not frontier:
                break
        return self.subgraph(list(edges.values()))
