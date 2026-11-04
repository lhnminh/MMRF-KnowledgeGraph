"""API Gateway HTTP API v2 adapter. Never accepts caller-supplied queries or tool names."""

import hashlib
import json
import logging
import os
import re
from datetime import datetime, timezone
from pathlib import Path
from urllib.parse import urlsplit

DISEASE = "MONDO:0009693"
MAX_EDGES = 100  # Existing kg_run_readonly_query returns at most 100 rows.
MAX_TARGET_PAGES = 2
MAX_FETCH_EDGES = MAX_EDGES * (1 + MAX_TARGET_PAGES)
QUERY = """MATCH (d:Disease {id: $disease_id})-[r]->(b)
RETURN d AS a, r, b
UNION
MATCH (:Disease {id: $disease_id})-[:HAS_MECHANISM|ASSOCIATION]->(a)-[r:LEADS_TO|HAS_AGENT]->(b)
RETURN a, r, b"""
TARGET_QUERY = """MATCH (:Disease {id: $disease_id})
-[:ASSOCIATION {source: 'dismech', predicate: 'dismech:has_recorded_treatment'}]->(:Entity)
-[:HAS_AGENT]->(a:Entity)-[r:TARGETS_GENE {source: 'opentargets'}]->(b:Entity)
RETURN DISTINCT a, r, b
ORDER BY a.id, b.id
SKIP {offset}
LIMIT 101"""
CLINICAL_IDS = {
    "HP:0002653", "HP:0002797", "HP:0000938", "HP:0002756", "HP:0002953",
    "HP:0000112", "HP:0001919", "HP:0000100", "HP:0003259", "HP:0003072", "HP:0001903",
}
VIEWS = {"all", "clinical", "phenotypes", "mechanisms", "genetics", "treatments"}
PARAMETERS = {"disease_id", "view", "source", "entity_type", "predicate", "q", "node_id", "limit"}
METADATA = (
    "confidence", "review_status", "retrieved_at", "synthetic", "source_url", "frequency",
    "association_type", "ontology_term", "therapeutic_modality", "record_name",
    "chembl_id", "action_type", "mechanism_of_action",
)
ASSETS = {
    "/": ("index.html", "text/html; charset=utf-8"),
    "/assets/app.js": ("app.js", "text/javascript; charset=utf-8"),
    "/assets/app.css": ("app.css", "text/css; charset=utf-8"),
    "/assets/cytoscape.min.js": ("cytoscape.min.js", "text/javascript; charset=utf-8"),
    "/assets/CYTOSCAPE-LICENSE.txt": ("CYTOSCAPE-LICENSE.txt", "text/plain; charset=utf-8"),
}
LOGGER = logging.getLogger(__name__)
_client = None


class InputError(ValueError):
    pass


class UpstreamError(RuntimeError):
    pass


def response(status, body, content_type="application/json; charset=utf-8"):
    return {
        "statusCode": status,
        "headers": {
            "Content-Type": content_type,
            "Cache-Control": "no-store",
            "X-Content-Type-Options": "nosniff",
            "Referrer-Policy": "no-referrer",
            "Strict-Transport-Security": "max-age=31536000",
            "Content-Security-Policy": "default-src 'none'; script-src 'self'; "
            "style-src 'self' 'unsafe-inline'; connect-src 'self'; img-src 'self' data: blob:; "
            "base-uri 'none'; frame-ancestors 'none'; form-action 'self'",
        },
        "body": json.dumps(body, ensure_ascii=False) if content_type.startswith("application/json") else body,
    }


def invoke_tool(tool, **arguments):
    global _client
    if _client is None:
        import boto3
        from botocore.config import Config

        _client = boto3.client(
            "lambda", config=Config(connect_timeout=3, read_timeout=23, retries={"total_max_attempts": 1})
        )
    result = _client.invoke(
        FunctionName=os.environ["GRAPH_FUNCTION_NAME"],
        InvocationType="RequestResponse",
        Payload=json.dumps({"tool": tool, **arguments}).encode(),
    )
    with result["Payload"] as payload:
        data = json.loads(payload.read())
    if result.get("FunctionError") or data.get("result_type") not in {"graph", "table"}:
        raise UpstreamError("The graph tool did not return a successful result.")
    return data


def reference_url(reference):
    value = str(reference or "")
    if re.fullmatch(r"PMID:\d+", value):
        return "https://pubmed.ncbi.nlm.nih.gov/" + value.split(":")[1] + "/"
    if value.startswith("https://") and urlsplit(value).hostname:
        return value
    # Open Targets keeps the source prefix, for example FDA:https://... .
    candidate = value.partition(":")[2]
    if candidate.startswith("https://") and urlsplit(candidate).hostname:
        return candidate
    return None


def source_url(dataset, version, record_id=None):
    if dataset == "monarch":
        return "https://monarchinitiative.org/"
    if dataset == "dismech" and re.fullmatch(r"[0-9a-f]{40}", str(version)):
        return f"https://github.com/monarch-initiative/dismech/blob/{version}/kb/disorders/Multiple_Myeloma.yaml"
    if dataset == "opentargets":
        if re.fullmatch(r"CHEMBL\d+", str(record_id)):
            return f"https://platform.opentargets.org/drug/{record_id}"
        return "https://platform.opentargets.org/"
    return None


def references(value):
    if isinstance(value, list):
        return [str(item) for item in value if item]
    return [part.strip() for part in re.split(r"[;|,]", str(value or "")) if part.strip()]


def graph_from_table(result):
    """Keep identifiers and citation metadata, omitting article snippets and curator descriptions."""
    table = result.get("table", {})
    if table.get("columns") != ["a", "r", "b"]:
        raise UpstreamError("Unexpected graph tool response schema.")
    nodes, edges, evidence = {}, {}, {}

    def add_node(raw, relation):
        props = raw["properties"]
        labels = raw.get("labels", [])
        identifier = props.get("id")
        if not identifier and "Mechanism" in labels and props.get("name"):
            identifier = "dismech:mechanism:" + props["name"]
        if not identifier:
            raise UpstreamError("A graph node has no stable identifier.")
        node_type = props.get("category") or props.get("entity_type")
        if not node_type:
            node_type = next((label for label in labels if label != "Entity"), "Entity")
        node_type = node_type.removeprefix("biolink:")
        display_label = (props.get("name") if "Disease" in labels else props.get("label"))
        display_label = display_label or props.get("label") or props.get("name") or identifier
        node = nodes.setdefault(identifier, {
            "id": identifier, "label": display_label,
            "type": node_type, "labels": [], "sources": [], "aliases": [],
            **{key: props[key] for key in METADATA if key in props},
        })
        node["labels"] = sorted(set(node["labels"] + labels))
        for alias in (props.get("name"), props.get("label")):
            if alias and alias != node["label"] and alias not in node["aliases"]:
                node["aliases"].append(alias)
        for record, basis in ((props, "node"), (relation, "relationship")):
            if not record.get("source"):
                continue
            dataset, version = record["source"], record.get("source_version") or "unrecorded"
            if not any(item["source_dataset"] == dataset and item["source_version"] == version
                       for item in node["sources"]):
                node["sources"].append({
                    "source_dataset": dataset, "source_version": version,
                    "url": source_url(dataset, version), "basis": basis,
                })
        if not node["sources"]:
            node["sources"].append({
                "source_dataset": "unrecorded", "source_version": "unrecorded", "url": None, "basis": "node",
            })
        return identifier

    rows = table.get("rows", [])
    if len(rows) > MAX_FETCH_EDGES:
        raise UpstreamError("Graph tool exceeded the response bound.")
    for raw_a, raw_r, raw_b in rows:
        props = raw_r["properties"]
        a, b = add_node(raw_a, props), add_node(raw_b, props)
        kind = raw_r["type"]
        predicate = props.get("predicate") or {"HAS_MECHANISM": "has_mechanism", "LEADS_TO": "leads_to"}.get(
            kind, kind
        )
        dataset, version = props.get("source", "unrecorded"), props.get("source_version", "unrecorded")
        raw_subject = raw_a["properties"].get("id") or raw_a["properties"].get("name")
        raw_object = raw_b["properties"].get("id") or raw_b["properties"].get("name")
        tool_edge_id = f"{dataset}:{raw_subject}|{predicate}|{raw_object}"
        identifier = props.get("id") or tool_edge_id
        # Dataset/version/type are part of the identity; keep independently recorded edges separate.
        key = json.dumps([identifier, version, kind], ensure_ascii=False)
        eid = "edge:" + hashlib.sha256(key.encode()).hexdigest()[:24]
        refs = (references(props.get("evidence_pmid")) + references(props.get("publications"))
                + references(props.get("references")))
        if not refs:
            refs = references(props.get("primary_knowledge_source"))
        evidence_ids = []
        for ref in dict.fromkeys(refs):
            rid = eid + "#" + ref
            evidence_ids.append(rid)
            evidence[rid] = {
                "id": rid, "reference": ref, "url": reference_url(ref),
                "source_dataset": dataset, "source_version": version,
            }
        edges[eid] = {
            "id": eid, "original_id": props.get("id"), "tool_edge_id": tool_edge_id,
            "identifier_origin": "recorded" if props.get("id") else "derived from recorded endpoints",
            "source": a, "target": b,
            "predicate": predicate, "relationship_type": kind,
            "source_dataset": dataset, "source_version": version,
            "source_url": source_url(dataset, version, props.get("chembl_id")),
            "edge_origin": props.get("edge_origin", "unrecorded"),
            "evidence_ids": evidence_ids, **{key: props[key] for key in METADATA if key in props},
        }
    return {"nodes": list(nodes.values()), "edges": sorted(edges.values(), key=lambda edge: edge["id"])}, evidence


def validate_filters(params):
    if set(params) - PARAMETERS:
        raise InputError("Unknown parameter: " + ", ".join(sorted(set(params) - PARAMETERS)))
    if any(not isinstance(value, str) or len(value) > 200 for value in params.values()):
        raise InputError("Filter values must be strings of at most 200 characters.")
    if params.get("disease_id", DISEASE) != DISEASE:
        raise InputError("This prototype supports Multiple Myeloma (MONDO:0009693) only.")
    if params.get("view", "all") not in VIEWS:
        raise InputError("view must be one of: " + ", ".join(sorted(VIEWS)))
    try:
        limit = int(params.get("limit", str(MAX_EDGES)))
    except ValueError as error:
        raise InputError("limit must be an integer between 1 and 100.") from error
    if not 1 <= limit <= MAX_EDGES:
        raise InputError("limit must be an integer between 1 and 100.")
    return {**params, "view": params.get("view", "all"), "limit": limit}


def filter_graph(graph, filters):
    nodes = {node["id"]: node for node in graph["nodes"]}
    selected = []
    view = filters["view"]
    for edge in graph["edges"]:
        endpoints = [nodes[edge["source"]], nodes[edge["target"]]]
        if view == "phenotypes" and edge["predicate"] != "biolink:has_phenotype":
            continue
        if view == "mechanisms" and edge["relationship_type"] not in {"HAS_MECHANISM", "LEADS_TO"}:
            continue
        if view == "genetics" and edge["predicate"] != "dismech:has_genetic_factor":
            continue
        if view == "treatments" and edge["predicate"] != "dismech:has_recorded_treatment" and edge[
            "relationship_type"
        ] not in {"HAS_AGENT", "TARGETS_GENE"}:
            continue
        if view == "clinical" and not all(
            node["id"] in CLINICAL_IDS or node["id"] == DISEASE or node["type"] == "Mechanism"
            for node in endpoints
        ):
            continue
        if filters.get("source") and edge["source_dataset"] != filters["source"]:
            continue
        if filters.get("entity_type") and not any(node["type"] == filters["entity_type"] for node in endpoints):
            continue
        if filters.get("predicate") and edge["predicate"] != filters["predicate"]:
            continue
        if filters.get("node_id") and not any(node["id"] == filters["node_id"] for node in endpoints):
            continue
        query = filters.get("q", "").strip().casefold()
        if query and not any(query in f"{node['id']} {node['label']}".casefold() for node in endpoints):
            continue
        selected.append(edge)
    truncated = len(selected) > filters["limit"]
    selected = selected[:filters["limit"]]
    connected = {edge[key] for edge in selected for key in ("source", "target")}
    return {"nodes": [node for node in graph["nodes"] if node["id"] in connected], "edges": selected}, truncated


def fetch_graph():
    """Fetch only the fixed disease slice and its recorded drug targets, with bounded pagination."""
    result = invoke_tool("kg_run_readonly_query", query=QUERY, parameters={"disease_id": DISEASE})
    if result.get("table", {}).get("columns") != ["a", "r", "b"]:
        raise UpstreamError("Unexpected graph tool response schema.")
    rows = list(result["table"]["rows"])
    if len(rows) > MAX_EDGES:
        raise UpstreamError("Graph tool exceeded the response bound.")
    limitations = list(result.get("limitations", []))
    trace = dict(result.get("trace", {}))
    calls = list(trace.get("tool_calls", []))
    request_ids = [trace["request_id"]] if trace.get("request_id") else []
    for page in range(MAX_TARGET_PAGES):
        target = invoke_tool("kg_run_readonly_query", query=TARGET_QUERY.replace("{offset}", str(page * MAX_EDGES)),
                             parameters={"disease_id": DISEASE})
        if target.get("table", {}).get("columns") != ["a", "r", "b"]:
            raise UpstreamError("Unexpected drug-target response schema.")
        target_rows = target["table"]["rows"]
        if len(target_rows) > MAX_EDGES:
            raise UpstreamError("Graph tool exceeded the response bound.")
        if any(row[1].get("type") != "TARGETS_GENE" or row[1].get("properties", {}).get("source") != "opentargets"
               for row in target_rows):
            raise UpstreamError("Unexpected drug-target relationship.")
        rows.extend(target_rows)
        target_limits = target.get("limitations", [])
        truncated = any("cut off" in text.lower() for text in target_limits)
        limitations.extend(text for text in target_limits if "cut off" not in text.lower())
        calls.extend(target.get("trace", {}).get("tool_calls", []))
        if target.get("trace", {}).get("request_id"):
            request_ids.append(target["trace"]["request_id"])
        if not truncated:
            break
        if len(target_rows) != MAX_EDGES:
            raise UpstreamError("Incomplete drug-target page.")
        if page == MAX_TARGET_PAGES - 1:
            limitations.append("Drug-target results are cut off at 200 relationships for this disease.")
    if calls:
        trace["tool_calls"] = calls
    trace["upstream_request_ids"] = list(dict.fromkeys(request_ids))
    return {"table": {"columns": ["a", "r", "b"], "rows": rows},
            "limitations": list(dict.fromkeys(limitations)), "trace": trace}


def get_graph(params):
    filters = validate_filters(params)
    result = fetch_graph()
    graph, evidence = graph_from_table(result)
    selected, clipped = filter_graph(graph, filters)
    used_evidence = {rid for edge in selected["edges"] for rid in edge["evidence_ids"]}
    limitations = list(result.get("limitations", []))
    limitations.extend([
        "This is a disease-centred graph with a third hop limited to recorded drug targets, capped at 100 displayed relationships; it is not a full database export.",
        "Open Targets drug-to-gene mechanisms are a separate source from DisMech treatment and trial evidence.",
        "Phenotype associations and source-reported mechanisms do not establish links between mechanisms and phenotypes.",
        "Article snippets and curator descriptions are omitted; citation identifiers and source versions are retained.",
        "Recorded treatments are published knowledge, not treatment recommendations. No patient or cohort data is returned.",
    ])
    if clipped:
        limitations.append("The requested limit clipped the filtered relationships.")
    if not selected["edges"]:
        limitations.append("No relationships matching these filters are recorded in the returned graph.")
    return {
        "contract_version": "0.2", "result_type": "graph",
        "summary": f"{len(selected['nodes'])} nodes and {len(selected['edges'])} relationships match the filters.",
        "graph": selected, "evidence": [value for key, value in evidence.items() if key in used_evidence],
        "limitations": limitations,
        "facets": {
            "sources": sorted({edge["source_dataset"] for edge in graph["edges"]}),
            "entity_types": sorted({node["type"] for node in graph["nodes"]}),
            "predicates": sorted({edge["predicate"] for edge in graph["edges"]}),
        },
        "meta": {
            "disease_id": DISEASE, "filters": filters, "maximum_depth": 3,
            "total_nodes": len(graph["nodes"]), "total_edges": len(graph["edges"]),
            "truncated": clipped or any("cut off" in text.lower() for text in result.get("limitations", [])),
            "fetched_at": datetime.now(timezone.utc).isoformat(), "backend": "AWS Neptune",
        },
        "trace": result.get("trace", {}),
    }


def handler(event, context):
    method = event.get("requestContext", {}).get("http", {}).get("method", "")
    path = event.get("rawPath", "")
    request_id = getattr(context, "aws_request_id", None)
    if method != "GET":
        return response(405, {"error": "Only GET is supported.", "request_id": request_id})
    if path in ASSETS:
        name, content_type = ASSETS[path]
        return response(200, (Path(__file__).parent / "static" / name).read_text(), content_type)
    if path != "/api/graph":
        return response(404, {"error": "Endpoint not found.", "request_id": request_id})
    try:
        return response(200, get_graph(event.get("queryStringParameters") or {}))
    except InputError as error:
        return response(400, {"error": str(error), "request_id": request_id})
    except Exception as error:
        # Never log the request event, source text, or upstream response.
        LOGGER.error("Graph API failure request_id=%s kind=%s", request_id, type(error).__name__)
        return response(502, {
            "error": "The graph service could not complete the request. Try again shortly.",
            "request_id": request_id,
        })
