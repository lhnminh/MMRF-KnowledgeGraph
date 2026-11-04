"""kg-graph: named knowledge-graph tools over Neptune, returning the AD-004 envelope.

One Lambda backs every kg tool. Through AgentCore Gateway the tool name arrives in
context.client_context.custom["bedrockAgentCoreToolName"] as "<target>___<tool>" and the
event holds the tool arguments. For direct invocation (AD-004 decision 1 fallback) pass
{"tool": "<tool>", ...arguments} instead.

Envelope rules applied here (AD-001, AD-004):
- observations hold only what a loaded source record states; refs point at evidence ids.
- DisMech curator commentary (r.dismech_explanation) is never an observation. It goes in
  interpretation with basis "source_curator_commentary".
- Every node and edge carries source_dataset and source_version; edges carry edge_origin.
  Values not recorded on the loaded data are reported as "unrecorded" and named in
  limitations rather than guessed.
- Empty or partial results are reported in limitations: "none recorded" in the loaded
  graph, never "none exists".

The same code runs against Neptune and the local Neo4j stand-in, so the queries keep to
openCypher that Neptune supports (no CALL, no list-valued property predicates).
"""
import json
import os
import re
import time
from urllib.parse import urlsplit

from neo4j import Auth, GraphDatabase
from neo4j.graph import Node, Path, Relationship

CONTRACT_VERSION = "0.2"
UNRECORDED = "unrecorded"

NEPTUNE_URI = os.environ["NEPTUNE_URI"]
# Neptune requires TLS on Bolt and ignores the credentials while IAM DB auth is off. The
# overrides exist so the handler can be pointed at the local Neo4j stand-in.
NEPTUNE_TLS = os.environ.get("NEPTUNE_TLS", "true").lower() != "false"
NEPTUNE_AUTH = (os.environ.get("NEPTUNE_USER", "username"), os.environ.get("NEPTUNE_PASSWORD", "password"))
# Sign connections with the Lambda role. Safe to leave on before the cluster enforces IAM auth.
NEPTUNE_IAM_AUTH = os.environ.get("NEPTUNE_IAM_AUTH", "false").lower() == "true"
# A SigV4 signature expires after about 5 minutes and is only checked when a connection
# opens. Neptune speaks Bolt 4.0 at most, so the driver cannot re-authenticate a connection;
# it is rebuilt with a fresh signature before the old one could be rejected.
SIGNATURE_MAX_AGE_SECONDS = 240
LOG_INPUTS_MAX_CHARS = 2000

DEFAULT_LIMIT = 50
MAX_LIMIT = 200
QUERY_MAX_ROWS = 100
QUERY_MAX_HOPS = 5

# Default dataset by node label, used when the node itself has no `source` property.
# Disease nodes are merged from both Monarch and DisMech, so they get no default.
LABEL_DATASET = {"Entity": "monarch", "Mechanism": "dismech"}

_driver = None
_driver_created_at = 0.0


def neptune_iam_auth():
    """Bolt auth token signed with the Lambda role, per the Neptune Bolt IAM documentation."""
    # botocore ships with the Lambda runtime; imported here so local runs don't need it.
    from botocore.auth import SigV4Auth
    from botocore.awsrequest import AWSRequest
    from botocore.session import get_session

    credentials = get_session().get_credentials()
    if credentials is None:
        raise RuntimeError("No AWS credentials available to sign the Neptune connection")
    region = os.environ.get("AWS_REGION")
    if not region:
        raise RuntimeError("AWS_REGION is not set; it is needed to sign the Neptune connection")

    request = AWSRequest(method="GET", url=NEPTUNE_URI + "/opencypher")
    request.headers.add_header("Host", urlsplit(request.url).netloc)
    SigV4Auth(credentials.get_frozen_credentials(), "neptune-db", region).add_auth(request)

    token = {name: request.headers[name] for name in ("Authorization", "X-Amz-Date", "X-Amz-Security-Token", "Host")}
    token["HttpMethod"] = request.method
    return Auth("basic", "username", json.dumps(token), "realm")


def get_driver():
    global _driver, _driver_created_at
    if _driver is not None and NEPTUNE_IAM_AUTH and time.time() - _driver_created_at > SIGNATURE_MAX_AGE_SECONDS:
        _driver.close()
        _driver = None
    if _driver is None:
        auth = neptune_iam_auth() if NEPTUNE_IAM_AUTH else NEPTUNE_AUTH
        _driver = GraphDatabase.driver(NEPTUNE_URI, auth=auth, encrypted=NEPTUNE_TLS)
        _driver_created_at = time.time()
    return _driver


class ToolInputError(ValueError):
    pass


# ---------------------------------------------------------------- envelope


class Envelope:
    """Accumulates the parts of one AD-004 response envelope."""

    def __init__(self, tool, inputs, request_id=None):
        self.tool = tool
        self.inputs = inputs
        self.request_id = request_id
        self.nodes = {}
        self.edges = {}
        self.evidence = {}
        self.observations = []
        self.interpretation = []
        self.limitations = []
        self.query = None
        self._unversioned = set()
        self._edges_without_evidence = []

    def version(self, dataset, value):
        if value:
            return value
        if dataset != UNRECORDED:  # an unknown dataset is reported by the caller instead
            self._unversioned.add(dataset)
        return UNRECORDED

    def add_node(self, node_id, label, node_type, dataset, version=None, **extra):
        if node_id in self.nodes:
            return
        node = {
            "id": node_id,
            "label": label,
            "type": node_type,
            "source_dataset": dataset,
            "source_version": self.version(dataset, version),
        }
        node.update({k: v for k, v in extra.items() if v is not None})
        self.nodes[node_id] = node

    def add_evidence(self, evidence_id, reference, dataset, version, snippet=None):
        self.evidence.setdefault(evidence_id, {
            "id": evidence_id,
            "reference": reference,
            "snippet": snippet,
            "source_dataset": dataset,
            "source_version": version,
        })
        return evidence_id

    def set_query(self, text, parameters):
        self.query = {"language": "openCypher", "text": _dedent(text), "parameters": parameters}

    def limit(self, text):
        if text not in self.limitations:
            self.limitations.append(text)

    def build(self, result_type, summary, table=None):
        for dataset in sorted(self._unversioned):
            self.limit(
                f"source_version is not recorded on the loaded {dataset} data, so it is shown as "
                f"'{UNRECORDED}'. The dataset release used cannot be stated from the graph."
            )
        if self._edges_without_evidence:
            self.limit("No evidence reference is recorded for: " + ", ".join(self._edges_without_evidence) + ".")
        envelope = {
            "contract_version": CONTRACT_VERSION,
            "result_type": result_type,
            "summary": summary,
            "observations": self.observations,
            "interpretation": self.interpretation,
            "graph": {"nodes": list(self.nodes.values()), "edges": list(self.edges.values())},
            "evidence": list(self.evidence.values()),
            "query": self.query,
            "limitations": self.limitations,
            "trace": {
                "request_id": self.request_id,
                "plan": [],
                "tool_calls": [{"tool": self.tool, "inputs": self.inputs}],
            },
        }
        if table is not None:
            envelope["table"] = table
        return envelope


def _dedent(text):
    return "\n".join(line.strip() for line in text.strip().splitlines())


def _as_list(value):
    """Publications are a list in Neo4j but a delimited string in Neptune, which has no
    list-valued properties. Accept both."""
    if value is None:
        return []
    if isinstance(value, (list, tuple)):
        return [str(v) for v in value if v]
    return [part.strip() for part in re.split(r"[;|,]", str(value)) if part.strip()]


def _humanize(predicate):
    return (predicate or "related to").split(":")[-1].replace("_", " ")


def _category_type(category, fallback):
    return category.split(":")[-1] if category else fallback


def mechanism_id(name):
    return f"dismech:mechanism:{name}"


def edge_id(kind, subject, predicate, obj):
    dataset = "monarch" if kind == "association" else "dismech"
    return f"{dataset}:{subject}|{predicate}|{obj}"


def parse_edge_id(value):
    match = re.fullmatch(r"(monarch|dismech):([^|]+)\|([^|]+)\|([^|]+)", value or "")
    if not match:
        raise ToolInputError(
            "edge_id must look like 'monarch:<subject>|<predicate>|<object>' or "
            "'dismech:<mechanism>|leads_to|<mechanism>', as returned in graph.edges[].id"
        )
    return match.groups()


# ------------------------------------------------------- shared edge rows

# Every edge query returns the same columns so one function turns rows into envelope parts.
ASSOCIATION_COLUMNS = """
    'association' AS kind, d.id AS subject_id, d.name AS subject_label, 'Disease' AS subject_type,
    null AS subject_description, r.predicate AS predicate,
    o.id AS object_id, o.label AS object_label, o.category AS object_type, null AS object_description,
    null AS pmid, null AS snippet, null AS explanation,
    r.publications AS publications, r.primary_knowledge_source AS primary_knowledge_source,
    r.source AS source, r.source_version AS source_version, r.edge_origin AS edge_origin
"""

LEADS_TO_COLUMNS = """
    'leads_to' AS kind, a.name AS subject_id, a.name AS subject_label, 'Mechanism' AS subject_type,
    a.description AS subject_description, 'leads_to' AS predicate,
    b.name AS object_id, b.name AS object_label, 'Mechanism' AS object_type, b.description AS object_description,
    r.evidence_pmid AS pmid, r.evidence_snippet AS snippet, r.dismech_explanation AS explanation,
    null AS publications, null AS primary_knowledge_source,
    r.source AS source, r.source_version AS source_version, r.edge_origin AS edge_origin
"""

HAS_MECHANISM_COLUMNS = """
    'has_mechanism' AS kind, d.id AS subject_id, d.name AS subject_label, 'Disease' AS subject_type,
    null AS subject_description, 'has_mechanism' AS predicate,
    a.name AS object_id, a.name AS object_label, 'Mechanism' AS object_type, a.description AS object_description,
    h.evidence_pmid AS pmid, h.evidence_snippet AS snippet, h.dismech_explanation AS explanation,
    null AS publications, null AS primary_knowledge_source,
    h.source AS source, h.source_version AS source_version, h.edge_origin AS edge_origin
"""


def add_edge_row(env, row):
    """Turn one edge row into nodes, an edge, evidence, observations and interpretation."""
    kind = row["kind"]
    dataset = row["source"] or ("monarch" if kind == "association" else "dismech")
    version = env.version(dataset, row["source_version"])

    def node_ref(prefix):
        raw_id, node_type = row[f"{prefix}_id"], row[f"{prefix}_type"]
        if node_type == "Mechanism":
            node_id = mechanism_id(raw_id)
            env.add_node(node_id, row[f"{prefix}_label"], "Mechanism", "dismech", row["source_version"],
                         description=row[f"{prefix}_description"])
        else:
            node_id = raw_id
            env.add_node(node_id, row[f"{prefix}_label"], _category_type(node_type, "Entity"), dataset,
                         row["source_version"])
        return node_id

    subject, obj = node_ref("subject"), node_ref("object")
    predicate = row["predicate"]
    eid = edge_id(kind, row["subject_id"], predicate, row["object_id"])
    if eid in env.edges:
        return eid

    evidence_ids = []
    if kind in ("leads_to", "has_mechanism") and row["pmid"]:
        evidence_ids.append(env.add_evidence(f"{eid}#{row['pmid']}", row["pmid"], dataset, version, row["snippet"]))
    elif kind == "association":
        references = _as_list(row["publications"]) or _as_list(row["primary_knowledge_source"])
        for reference in references:
            evidence_ids.append(env.add_evidence(f"{eid}#{reference}", reference, dataset, version))

    env.edges[eid] = {
        "id": eid,
        "source": subject,
        "target": obj,
        "predicate": predicate,
        "edge_origin": row["edge_origin"] or "recorded",
        "source_dataset": dataset,
        "source_version": version,
        "evidence_ids": evidence_ids,
    }

    if kind == "has_mechanism":
        env.observations.append({
            "text": f"DisMech lists {row['object_label']} as a pathophysiology mechanism of {row['subject_label']}.",
            "refs": evidence_ids,
        })
        if row["object_description"]:
            env.observations.append({
                "text": f"DisMech describes {row['object_label']}: {row['object_description']}",
                "refs": evidence_ids,
            })
        if row["explanation"]:
            env.interpretation.append({
                "text": row["explanation"],
                "basis": "source_curator_commentary",
                "refs": evidence_ids,
            })
    elif kind == "leads_to":
        env.observations.append({
            "text": f"DisMech records that {row['subject_label']} leads to {row['object_label']}.",
            "refs": evidence_ids,
        })
        if row["explanation"]:
            env.interpretation.append({
                "text": row["explanation"],
                "basis": "source_curator_commentary",
                "refs": evidence_ids,
            })
    else:
        env.observations.append({
            "text": f"{dataset.capitalize()} records: {row['subject_label']} {_humanize(predicate)} "
                    f"{row['object_label']} ({row['object_id']}).",
            "refs": evidence_ids,
        })

    if not evidence_ids:
        env._edges_without_evidence.append(eid)
    return eid


def _read(session, text, parameters):
    return session.execute_read(lambda tx: [record.data() for record in tx.run(text, parameters)])


def _limit_arg(args, default=DEFAULT_LIMIT):
    value = args.get("limit", default)
    try:
        value = int(value)
    except (TypeError, ValueError):
        raise ToolInputError("limit must be an integer")
    if not 1 <= value <= MAX_LIMIT:
        raise ToolInputError(f"limit must be between 1 and {MAX_LIMIT}")
    return value


def _required(args, name):
    value = args.get(name)
    if not isinstance(value, str) or not value.strip():
        raise ToolInputError(f"{name} is required")
    return value.strip()


def _disease_label(session, disease_id):
    rows = _read(session, "MATCH (d:Disease {id: $id}) RETURN d.name AS name", {"id": disease_id})
    return rows[0]["name"] if rows else None


# ------------------------------------------------------------------ tools


def search_entities(session, args, env):
    text = _required(args, "text")
    limit = _limit_arg(args, default=20)
    # Neptune only accepts a literal LIMIT, so the validated integer is inlined.
    query = f"""
        MATCH (n)
        WITH n, coalesce(n.name, n.label, '') AS label
        WHERE toLower(label) CONTAINS toLower($text) OR toLower(coalesce(n.id, '')) = toLower($text)
        RETURN labels(n) AS labels, n.id AS id, label, n.category AS category,
               n.description AS description, n.source AS source, n.source_version AS source_version
        ORDER BY label
        LIMIT {limit + 1}
    """
    parameters = {"text": text}
    env.set_query(query, parameters)
    rows = _read(session, query, parameters)
    truncated = len(rows) > limit

    duplicates = []
    for row in rows[:limit]:
        known = [name for name in row["labels"] if name in ("Disease", "Entity", "Mechanism")]
        label = (known or row["labels"] or ["Entity"])[0]
        dataset = row["source"] or LABEL_DATASET.get(label, UNRECORDED)
        if label == "Mechanism":
            node_id, node_type = mechanism_id(row["label"]), "Mechanism"
        else:
            node_id, node_type = row["id"], _category_type(row["category"], label)
        if node_id in env.nodes:
            duplicates.append(node_id)
        env.add_node(node_id, row["label"], node_type, dataset, row["source_version"], description=row["description"])

    if any(n["source_dataset"] == UNRECORDED for n in env.nodes.values()):
        env.limit("Disease nodes are merged from Monarch and DisMech and carry no single source_dataset.")
    if duplicates:
        env.limit("The loaded graph stores " + ", ".join(sorted(set(duplicates))) + " as more than one node "
                  "(for example as both a Disease and an Entity); they are shown once here.")
    if truncated:
        env.limit(f"More than {limit} entities match '{text}'; only the first {limit} are returned.")
    if not rows:
        env.limit(f"No entity whose name or id matches '{text}' is recorded in the loaded graph.")
    count = len(env.nodes)
    noun = "entity matches" if count == 1 else "entities match"
    return env.build("graph", f"{count} {noun} '{text}' in the loaded graph.")


def get_phenotypes(session, args, env):
    disease_id = _required(args, "disease_id")
    limit = _limit_arg(args)
    query = f"""
        MATCH (d:Disease {{id: $disease_id}})-[r:ASSOCIATION]->(o:Entity)
        WHERE r.predicate = 'biolink:has_phenotype'
        RETURN {ASSOCIATION_COLUMNS}
        ORDER BY object_label
        LIMIT {limit + 1}
    """
    parameters = {"disease_id": disease_id}
    env.set_query(query, parameters)
    rows = _read(session, query, parameters)

    for row in rows[:limit]:
        add_edge_row(env, row)

    env.limit("Phenotypes come from Monarch only; DisMech phenotype records are not loaded into the graph.")
    if len(rows) > limit:
        env.limit(f"More than {limit} phenotype associations are recorded; only the first {limit} are returned.")
    if not rows:
        name = _disease_label(session, disease_id)
        env.limit(
            f"No Disease node with id {disease_id} is in the loaded graph." if name is None else
            f"No phenotype association is recorded for {name} ({disease_id}) in the loaded data."
        )
        return env.build("graph", f"No phenotypes recorded for {disease_id}.")

    count = min(len(rows), limit)
    return env.build("graph", f"{count} phenotype associations recorded in Monarch for "
                              f"{rows[0]['subject_label']} ({disease_id}).")


def get_mechanism_paths(session, args, env):
    disease_id = _required(args, "disease_id")
    query = f"""
        MATCH (d:Disease {{id: $disease_id}})-[h:HAS_MECHANISM]->(a:Mechanism)
        RETURN {HAS_MECHANISM_COLUMNS}
        UNION ALL
        MATCH (d:Disease {{id: $disease_id}})-[:HAS_MECHANISM]->(a:Mechanism)-[r:LEADS_TO]->(b:Mechanism)
        RETURN {LEADS_TO_COLUMNS}
    """
    parameters = {"disease_id": disease_id}
    env.set_query(query, parameters)
    rows = _read(session, query, parameters)
    # Mechanism steps first, then causal edges, so observations read in a stable order.
    rows.sort(key=lambda r: (r["kind"] != "has_mechanism", r["subject_id"] or "", r["object_id"] or ""))

    for row in rows:
        add_edge_row(env, row)

    if not rows:
        name = _disease_label(session, disease_id)
        env.limit(
            f"No Disease node with id {disease_id} is in the loaded graph." if name is None else
            f"No DisMech mechanism is recorded for {name} ({disease_id}) in the loaded data."
        )
        return env.build("graph", f"No mechanism paths recorded for {disease_id}.")

    steps = {r["object_id"] for r in rows if r["kind"] == "has_mechanism"}
    linked = {n for r in rows if r["kind"] == "leads_to" for n in (r["subject_id"], r["object_id"])}
    unlinked = sorted(steps - linked)
    if unlinked:
        env.limit("No causal (leads_to) edge is recorded into or out of: " + ", ".join(unlinked) + ".")
    env.limit("Only the first evidence item of each DisMech causal edge is loaded into the graph.")

    edge_count = sum(1 for r in rows if r["kind"] == "leads_to")
    disease = next(r["subject_label"] for r in rows if r["kind"] == "has_mechanism")
    return env.build("graph", f"DisMech records {len(steps)} mechanisms and {edge_count} causal edges "
                              f"for {disease} ({disease_id}).")


def get_evidence(session, args, env):
    edge = args.get("edge_id")
    reference = args.get("reference")
    if bool(edge) == bool(reference):
        raise ToolInputError("Pass exactly one of edge_id or reference")

    if edge:
        dataset, subject, predicate, obj = parse_edge_id(edge)
        if dataset == "dismech":
            query = f"""
                MATCH (a:Mechanism {{name: $subject}})-[r:LEADS_TO]->(b:Mechanism {{name: $object}})
                RETURN {LEADS_TO_COLUMNS}
            """
            parameters = {"subject": subject, "object": obj}
        else:
            query = f"""
                MATCH (d:Disease {{id: $subject}})-[r:ASSOCIATION {{predicate: $predicate}}]->(o {{id: $object}})
                RETURN {ASSOCIATION_COLUMNS}
            """
            parameters = {"subject": subject, "predicate": predicate, "object": obj}
        env.set_query(query, parameters)
        rows = _read(session, query, parameters)
        for row in rows:
            add_edge_row(env, row)
        if not rows:
            env.limit(f"No edge {edge} is recorded in the loaded graph.")
            return env.build("graph", f"No edge {edge} found.")
        return env.build("graph", f"{len(env.evidence)} evidence item(s) recorded for {edge}.")

    reference = reference.strip()
    # Publications cannot be filtered in openCypher portably (a list in Neo4j, a string in
    # Neptune), so association candidates are narrowed in the query and matched here.
    query = f"""
        MATCH (a:Mechanism)-[r:LEADS_TO]->(b:Mechanism)
        WHERE r.evidence_pmid = $reference
        RETURN {LEADS_TO_COLUMNS}
        UNION ALL
        MATCH (d:Disease)-[r:ASSOCIATION]->(o)
        WHERE r.primary_knowledge_source = $reference OR r.publications IS NOT NULL
        RETURN {ASSOCIATION_COLUMNS}
    """
    parameters = {"reference": reference}
    env.set_query(query, parameters)
    rows = [
        row for row in _read(session, query, parameters)
        if row["kind"] == "leads_to"
        or row["primary_knowledge_source"] == reference
        or reference in _as_list(row["publications"])
    ]
    for row in rows:
        add_edge_row(env, row)
    if not rows:
        env.limit(f"No edge citing {reference} is recorded in the loaded graph.")
        return env.build("graph", f"No edges cite {reference}.")
    return env.build("graph", f"{len(env.edges)} edge(s) in the loaded graph cite {reference}.")


# --------------------------------------------------- guarded read-only query

_STRINGS_AND_COMMENTS = re.compile(r"'(?:\\.|[^'\\])*'|\"(?:\\.|[^\"\\])*\"|`[^`]*`|//[^\n]*|/\*.*?\*/", re.S)
_WRITE_OR_PROCEDURE = re.compile(
    r"\b(CREATE|MERGE|DELETE|DETACH|SET|REMOVE|DROP|FOREACH|CALL|LOAD\s+CSV|USING\s+PERIODIC)\b", re.I)
_RELATIONSHIP_PATTERN = re.compile(r"\[[^\[\]]*\]")
_HOPS = re.compile(r"\*\s*(\d+)?\s*(\.\.)?\s*(\d+)?")


def validate_readonly_query(text):
    """Reject anything that is not a single bounded read. This is the application-layer
    guard; AD-004 also expects IAM to deny writes once Neptune IAM auth is enabled."""
    if not isinstance(text, str) or not text.strip():
        raise ToolInputError("query is required")
    if len(text) > 4000:
        raise ToolInputError("query is longer than 4000 characters")
    code = _STRINGS_AND_COMMENTS.sub("''", text)
    if ";" in code:
        raise ToolInputError("Only a single statement is allowed (no ';')")
    found = _WRITE_OR_PROCEDURE.search(code)
    if found:
        raise ToolInputError(f"Read-only queries only: '{found.group(1).upper()}' is not allowed")
    if not re.search(r"\bRETURN\b", code, re.I):
        raise ToolInputError("The query must RETURN results")
    for pattern in _RELATIONSHIP_PATTERN.findall(code):
        hops = _HOPS.search(pattern)
        if not hops:
            continue
        low, dots, high = hops.groups()
        upper = int(high) if high else (int(low) if low and not dots else None)
        if upper is None:
            raise ToolInputError(f"Variable-length patterns need an upper bound of at most {QUERY_MAX_HOPS} hops")
        if upper > QUERY_MAX_HOPS:
            raise ToolInputError(f"Variable-length patterns may span at most {QUERY_MAX_HOPS} hops")


def _to_json(value):
    if isinstance(value, Node):
        return {"labels": sorted(value.labels), "properties": _to_json(dict(value))}
    if isinstance(value, Relationship):
        return {"type": value.type, "properties": _to_json(dict(value))}
    if isinstance(value, Path):
        return {"nodes": [_to_json(n) for n in value.nodes], "relationships": [_to_json(r) for r in value.relationships]}
    if isinstance(value, dict):
        return {k: _to_json(v) for k, v in value.items()}
    if isinstance(value, (list, tuple)):
        return [_to_json(v) for v in value]
    if value is None or isinstance(value, (str, int, float, bool)):
        return value
    return str(value)


def run_readonly_query(session, args, env):
    text = args.get("query")
    parameters = args.get("parameters") or {}
    if not isinstance(parameters, dict):
        raise ToolInputError("parameters must be an object")
    validate_readonly_query(text)
    env.set_query(text, parameters)

    def work(tx):
        result = tx.run(text, parameters)
        columns = list(result.keys())
        rows = []
        for record in result:
            if len(rows) == QUERY_MAX_ROWS:
                return columns, rows, True
            rows.append([_to_json(record[c]) for c in columns])
        return columns, rows, False

    columns, rows, truncated = session.execute_read(work)
    env.limit("Rows are returned as the query produced them. They carry provenance only if the query "
              "returns source, source_version and evidence properties.")
    if truncated:
        env.limit(f"Results are cut off at {QUERY_MAX_ROWS} rows.")
    if not rows:
        env.limit("The query matched nothing in the loaded graph.")
    return env.build("table", f"Read-only query returned {len(rows)} row(s){' (truncated)' if truncated else ''}.",
                     table={"columns": columns, "rows": rows})


TOOLS = {
    "kg_search_entities": search_entities,
    "kg_get_phenotypes": get_phenotypes,
    "kg_get_mechanism_paths": get_mechanism_paths,
    "kg_get_evidence": get_evidence,
    "kg_run_readonly_query": run_readonly_query,
}


# ---------------------------------------------------------------- handler


def _gateway_metadata(context):
    custom = getattr(getattr(context, "client_context", None), "custom", None)
    return custom if isinstance(custom, dict) else {}


def _tool_name(event, metadata):
    name = metadata.get("bedrockAgentCoreToolName") or event.get("tool")
    if name and "___" in name:
        name = name.split("___", 1)[1]
    return name


def _log_call(tool, args, metadata, context, result, status, error, started):
    """One JSON line per tool call. Gateway does not pass the caller's identity to the
    Lambda, so these ids are what tie a query back to Gateway's own records of the call."""
    inputs = json.dumps(args, default=str)
    print(json.dumps({
        "event": "kg_tool_call",
        "tool": tool,
        "status": status,
        "duration_ms": round((time.time() - started) * 1000),
        "gateway_request_id": metadata.get("bedrockAgentCoreAwsRequestId"),
        "mcp_message_id": metadata.get("bedrockAgentCoreMcpMessageId"),
        "gateway_id": metadata.get("bedrockAgentCoreGatewayId"),
        "target_id": metadata.get("bedrockAgentCoreTargetId"),
        "lambda_request_id": getattr(context, "aws_request_id", None),
        "inputs": inputs[:LOG_INPUTS_MAX_CHARS],
        "inputs_truncated": len(inputs) > LOG_INPUTS_MAX_CHARS,
        "result_type": result["result_type"],
        "nodes": len(result["graph"]["nodes"]),
        "edges": len(result["graph"]["edges"]),
        "rows": len(result["table"]["rows"]) if "table" in result else None,
        "limitations": len(result["limitations"]),
        "error": error,
    }))


def handler(event, context):
    started = time.time()
    event = dict(event or {})
    metadata = _gateway_metadata(context)
    tool = _tool_name(event, metadata)
    args = {k: v for k, v in event.items() if k != "tool"}
    request_id = metadata.get("bedrockAgentCoreAwsRequestId") or getattr(context, "aws_request_id", None)
    env = Envelope(tool, args, request_id)
    status, error_text = "ok", None

    if tool not in TOOLS:
        status = "rejected"
        env.limit(f"Unknown tool '{tool}'. Available tools: {', '.join(TOOLS)}.")
        result = env.build("text", "Tool not found.")
    else:
        try:
            with get_driver().session() as session:
                result = TOOLS[tool](session, args, env)
        except ToolInputError as error:
            status = "rejected"
            env.limit(str(error))
            result = env.build("text", "The tool input was rejected.")
        except Exception as error:  # report, never invent: the agent sees the failure as a limitation
            status = "error"
            error_text = f"{type(error).__name__}: {error}"
            env.limit(f"The graph query failed: {error_text}")
            result = env.build("text", "The graph query failed.")

    _log_call(tool, args, metadata, context, result, status, error_text, started)
    return result
