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
MAX_PATHS = 20

# Default dataset by node label, used when the node itself has no `source` property.
# Disease nodes are merged from both Monarch and DisMech, so they get no default.
LABEL_DATASET = {"Entity": "monarch", "Mechanism": "dismech"}
DATASET_NAMES = {"monarch": "Monarch", "dismech": "DisMech", "opentargets": "Open Targets"}
# The sources whose absence for a gene or disease is reported. Open Targets is loaded only for drug targets.
COVERAGE_DATASETS = ("monarch", "dismech")

# Steps DisMech's mechanism descriptions state but its record has no edge for (AD-004). DisMech has no
# field for these, so they are written by hand from all eight descriptions at commit b1ca0b2. Each is
# (mechanism whose description says it, upstream, downstream names, what the description says).
# A downstream name is dropped once a leads_to edge records that step.
DESCRIBED_WITHOUT_EDGE = {
    "MONDO:0009693": [
        ("Clonal Plasma Cell Proliferation", "Genetic alterations", ["Clonal Plasma Cell Proliferation"],
         "IGH translocations and hyperdiploidy drive the clonal expansion. DisMech records them as genetic "
         "factors of the disease, with no edge to any mechanism."),
        ("Monoclonal Immunoglobulin Production", "Monoclonal Immunoglobulin Production", ["Cast nephropathy"],
         "these proteins deposit in the kidneys and cause cast nephropathy. The graph has only the direct edge "
         "to Renal Impairment."),
        ("Osteolytic Bone Disease", "Myeloma cells", ["Osteoclast activation", "Osteoblast suppression"],
         "myeloma cells do this by secreting RANKL, MIP-1alpha and DKK1. The graph has only the direct edge "
         "from Clonal Plasma Cell Proliferation to Osteolytic Bone Disease."),
        ("Osteolytic Bone Disease", "Osteolytic Bone Disease", ["Lytic bone lesions", "Pathologic fractures", "Bone pain"],
         "uncoupled bone remodeling leads to these."),
        ("Bone Marrow Failure", "Bone Marrow Failure", ["Anemia", "Neutropenia", "Thrombocytopenia"],
         "displacing normal blood-forming cells leads to these."),
        ("Obesity-Driven Metabolic Dysregulation", "Obesity-Driven Metabolic Dysregulation", ["Increased mortality"],
         "severe obesity at diagnosis is associated with it."),
        ("Immune Dysregulation", "Multiple Myeloma", ["Immune Dysregulation"],
         "myeloma causes this immunodeficiency. The only causal edge into Immune Dysregulation is from "
         "Obesity-Driven Metabolic Dysregulation."),
        ("Immune Dysregulation", "Immune Dysregulation", ["Recurrent infections"],
         "it leads to increased susceptibility to infections."),
        ("Renal Impairment", "Hypercalcemia", ["Renal Impairment"],
         "hypercalcemia contributes to renal dysfunction, including through nephrocalcinosis."),
        ("Renal Impairment", "Light chain amyloidosis and light chain deposition disease", ["Renal Impairment"],
         "these are additional causes of renal dysfunction."),
        ("Hypercalcemia", "Renal Impairment", ["Hypercalcemia"],
         "impaired renal calcium excretion makes hypercalcemia worse."),
        ("Hypercalcemia", "Hypercalcemia", ["Confusion", "Constipation", "Cardiac arrhythmias"],
         "it contributes to these."),
    ],
}

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
        # Ordered chains of returned edges: [{"node_ids": [...], "edge_ids": [...]}].
        self.paths = []
        # Node ids where chains start. A tool opts in to graph.paths by setting this; None lists no paths.
        self.path_roots = None
        # True when the tool recorded the paths itself, so none are derived from the edges.
        self.explicit_paths = False
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

    def add_path(self, node_ids, edge_ids):
        """Record one ordered chain: node_ids[i] and node_ids[i + 1] are joined by edge_ids[i]."""
        path = {"node_ids": list(node_ids), "edge_ids": list(edge_ids)}
        if path not in self.paths:
            self.paths.append(path)

    def derive_paths(self):
        """Every longest chain of two or more returned edges that follows edge direction from a root
        without visiting a node twice. These are chains in the returned graph, not the order in which
        Neptune evaluated the query. Single edges are not paths: graph.edges already lists them."""
        outgoing = {}
        for eid in sorted(self.edges):
            edge = self.edges[eid]
            outgoing.setdefault(edge["source"], []).append((eid, edge["target"]))
        roots = [node_id for node_id in self.path_roots or [] if node_id in self.nodes]

        found, truncated = [], False

        def walk(node_id, node_ids, edge_ids):
            nonlocal truncated
            if truncated:
                return
            extended = False
            for eid, target in outgoing.get(node_id, []):
                if target in node_ids:
                    continue
                extended = True
                walk(target, node_ids + [target], edge_ids + [eid])
            if not extended and len(edge_ids) >= 2:
                if len(found) >= MAX_PATHS:
                    truncated = True
                    return
                found.append({"node_ids": node_ids, "edge_ids": edge_ids})

        for root in roots:
            walk(root, [root], [])
        return found, truncated

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
        paths = self.paths
        if not self.explicit_paths and not paths and self.path_roots:
            paths, truncated = self.derive_paths()
            if truncated:
                self.limit(f"More than {MAX_PATHS} paths follow the returned edges; only the first {MAX_PATHS} "
                           "are listed in graph.paths. graph.edges is complete.")
        envelope = {
            "contract_version": CONTRACT_VERSION,
            "result_type": result_type,
            "summary": summary,
            "observations": self.observations,
            "interpretation": self.interpretation,
            "graph": {"nodes": list(self.nodes.values()), "edges": list(self.edges.values()), "paths": paths},
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


def edge_id(dataset, subject, predicate, obj):
    return f"{dataset}:{subject}|{predicate}|{obj}"


def parse_edge_id(value):
    match = re.fullmatch(r"(monarch|dismech|opentargets):([^|]+)\|([^|]+)\|([^|]+)", value or "")
    if not match:
        raise ToolInputError(
            "edge_id must look like '<monarch, dismech or opentargets>:<subject>|<predicate>|<object>', "
            "as returned in graph.edges[].id"
        )
    return match.groups()


# ------------------------------------------------------- shared edge rows

# Every edge query returns the same columns so one function turns rows into envelope parts.
ASSOCIATION_COLUMNS = """
    'association' AS kind, d.id AS subject_id, coalesce(d.name, d.label) AS subject_label,
    coalesce(d.category, 'Disease') AS subject_type,
    null AS subject_description, r.predicate AS predicate,
    o.id AS object_id, coalesce(o.label, o.name) AS object_label, o.category AS object_type,
    r.description AS object_description,
    r.evidence_pmid AS pmid, r.evidence_snippet AS snippet, r.dismech_explanation AS explanation,
    r.publications AS publications, r.primary_knowledge_source AS primary_knowledge_source,
    r.frequency AS frequency, r.record_name AS record_name, r.association_type AS association_type,
    r.source AS source, r.source_version AS source_version, r.edge_origin AS edge_origin,
    null AS effect, null AS edge_description
"""

LEADS_TO_COLUMNS = """
    'leads_to' AS kind, a.id AS subject_id, a.name AS subject_label, 'Mechanism' AS subject_type,
    a.description AS subject_description, 'leads_to' AS predicate,
    b.id AS object_id, b.name AS object_label, 'Mechanism' AS object_type, b.description AS object_description,
    r.evidence_pmid AS pmid, r.evidence_snippet AS snippet, r.dismech_explanation AS explanation,
    null AS publications, null AS primary_knowledge_source,
    null AS frequency, null AS record_name, null AS association_type,
    r.source AS source, r.source_version AS source_version, r.edge_origin AS edge_origin,
    null AS effect, null AS edge_description
"""

HAS_MECHANISM_COLUMNS = """
    'has_mechanism' AS kind, d.id AS subject_id, d.name AS subject_label, 'Disease' AS subject_type,
    null AS subject_description, 'has_mechanism' AS predicate,
    a.id AS object_id, a.name AS object_label, 'Mechanism' AS object_type, a.description AS object_description,
    h.evidence_pmid AS pmid, h.evidence_snippet AS snippet, h.dismech_explanation AS explanation,
    null AS publications, null AS primary_knowledge_source,
    null AS frequency, null AS record_name, null AS association_type,
    h.source AS source, h.source_version AS source_version, h.edge_origin AS edge_origin,
    null AS effect, null AS edge_description
"""


# A subtype defined by a driver gene (DisMech genetic factor with a subtype). Structural: no evidence of its own.
DRIVER_GENE_COLUMNS = """
    'driver_gene' AS kind, s.id AS subject_id, s.label AS subject_label, s.category AS subject_type,
    null AS subject_description, 'has_driver_gene' AS predicate,
    g.id AS object_id, g.label AS object_label, g.category AS object_type, null AS object_description,
    null AS pmid, null AS snippet, null AS explanation,
    null AS publications, null AS primary_knowledge_source,
    null AS frequency, null AS record_name, r.association_type AS association_type,
    r.source AS source, r.source_version AS source_version, r.edge_origin AS edge_origin,
    null AS effect, null AS edge_description
"""

# A treatment linked to the agent it uses. Structural: no evidence of its own.
AGENT_COLUMNS = """
    'has_agent' AS kind, t.id AS subject_id, t.label AS subject_label, t.category AS subject_type,
    null AS subject_description, 'has_agent' AS predicate,
    a.id AS object_id, a.label AS object_label, a.category AS object_type, null AS object_description,
    null AS pmid, null AS snippet, null AS explanation,
    null AS publications, null AS primary_knowledge_source,
    null AS frequency, null AS record_name, null AS association_type,
    r.source AS source, r.source_version AS source_version, r.edge_origin AS edge_origin,
    null AS effect, null AS edge_description
"""

# A treatment and the mechanism it acts on, with that edge's own evidence and commentary.
TARGETS_COLUMNS = """
    'targets_mechanism' AS kind, t.id AS subject_id, t.label AS subject_label, t.category AS subject_type,
    null AS subject_description, 'targets_mechanism' AS predicate,
    m.id AS object_id, m.name AS object_label, 'Mechanism' AS object_type, m.description AS object_description,
    r.evidence_pmid AS pmid, r.evidence_snippet AS snippet, r.dismech_explanation AS explanation,
    null AS publications, null AS primary_knowledge_source,
    null AS frequency, null AS record_name, null AS association_type,
    r.source AS source, r.source_version AS source_version, r.edge_origin AS edge_origin,
    r.treatment_effect AS effect, r.description AS edge_description
"""

# A mechanism and a downstream outcome that is not itself a mechanism (usually a phenotype), with the edge's evidence.
OUTCOME_COLUMNS = """
    'leads_to_phenotype' AS kind, a.id AS subject_id, a.name AS subject_label, 'Mechanism' AS subject_type,
    a.description AS subject_description, 'leads_to_phenotype' AS predicate,
    o.id AS object_id, o.label AS object_label, o.category AS object_type, null AS object_description,
    r.evidence_pmid AS pmid, r.evidence_snippet AS snippet, r.dismech_explanation AS explanation,
    null AS publications, null AS primary_knowledge_source,
    null AS frequency, null AS record_name, null AS association_type,
    r.source AS source, r.source_version AS source_version, r.edge_origin AS edge_origin,
    null AS effect, null AS edge_description
"""

# A drug and a gene it acts on, from Open Targets' mechanism-of-action record for the drug. The supporting
# labels and documents are in `references`, so they are read as the edge's publications.
TARGETS_GENE_COLUMNS = """
    'targets_gene' AS kind, a.id AS subject_id, a.label AS subject_label, a.category AS subject_type,
    null AS subject_description, 'targets_gene' AS predicate,
    g.id AS object_id, g.label AS object_label, g.category AS object_type, null AS object_description,
    null AS pmid, null AS snippet, null AS explanation,
    r.references AS publications, null AS primary_knowledge_source,
    null AS frequency, r.mechanism_of_action AS record_name, r.action_type AS association_type,
    r.source AS source, r.source_version AS source_version, r.edge_origin AS edge_origin,
    null AS effect, null AS edge_description
"""

# Structural links that carry no evidence of their own; their absence is not reported as a gap.
STRUCTURAL_KINDS = ("driver_gene", "has_agent")


def add_edge_row(env, row):
    """Turn one edge row into nodes, an edge, evidence, observations and interpretation."""
    kind = row["kind"]
    dataset = row["source"] or ("monarch" if kind == "association" else "dismech")
    version = env.version(dataset, row["source_version"])

    def node_ref(prefix):
        raw_id, node_type = row[f"{prefix}_id"], row[f"{prefix}_type"]
        if node_type == "Mechanism":
            node_id = raw_id  # the stored id already includes the disease: names repeat across diseases
            env.add_node(node_id, row[f"{prefix}_label"], "Mechanism", "dismech", row["source_version"],
                         description=row[f"{prefix}_description"])
        else:
            node_id = raw_id
            env.add_node(node_id, row[f"{prefix}_label"], _category_type(node_type, "Entity"), dataset,
                         row["source_version"])
        return node_id

    subject, obj = node_ref("subject"), node_ref("object")
    predicate = row["predicate"]
    eid = edge_id(dataset, row["subject_id"], predicate, row["object_id"])
    if eid in env.edges:
        return eid

    evidence_ids = []
    if row["pmid"]:
        evidence_ids.append(env.add_evidence(f"{eid}#{row['pmid']}", row["pmid"], dataset, version, row["snippet"]))
    elif kind in ("association", "targets_gene"):
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
        # Descriptions explain causes that no edge records, so they stay out of observations.
        if row["object_description"]:
            env.interpretation.append({
                "text": f"DisMech describes {row['object_label']}: {row['object_description']}",
                "basis": "source_curator_commentary",
                "refs": evidence_ids,
            })
        if row["explanation"]:
            env.interpretation.append({
                "text": row["explanation"],
                "basis": "source_curator_commentary",
                "refs": evidence_ids,
            })
    elif kind in ("leads_to", "leads_to_phenotype"):
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
    elif kind == "driver_gene":
        how = f" ({row['association_type']})" if row["association_type"] else ""
        env.observations.append({
            "text": f"DisMech records {row['subject_label']} as defined by a driver alteration in "
                    f"{row['object_label']}{how}.",
            "refs": evidence_ids,
        })
    elif kind == "has_agent":
        env.observations.append({
            "text": f"DisMech records {row['object_label']} as the agent of {row['subject_label']}.",
            "refs": evidence_ids,
        })
    elif kind == "targets_gene":
        action = (row["association_type"] or "acts on").lower().replace(";", " and ").replace("_", " ")
        env.observations.append({
            "text": f"Open Targets records {row['subject_label']} as {action} of {row['object_label']} "
                    f"({row['object_id']}). Mechanism of action: {row['record_name']}.",
            "refs": evidence_ids,
        })
    elif kind == "targets_mechanism":
        effect = (row.get("effect") or "targets").lower().replace("_", " ")
        env.observations.append({
            "text": f"DisMech records that {row['subject_label']} {effect} {row['object_label']}.",
            "refs": evidence_ids,
        })
        # Both are curator wording about why the link holds, so neither is an observation.
        for text in (row.get("edge_description"), row["explanation"]):
            if text:
                env.interpretation.append({"text": text, "basis": "source_curator_commentary", "refs": evidence_ids})
    else:
        source_name = DATASET_NAMES.get(dataset, dataset)
        details = ""
        if row["record_name"] and row["record_name"] != row["object_label"]:
            details += f" Record: {row['record_name']}."
        if row["association_type"]:
            details += f" Association: {row['association_type'].lower()}."
        if row["frequency"]:
            details += f" Frequency: {row['frequency'].replace('_', ' ').lower()}."
        env.observations.append({
            "text": f"{source_name} records: {row['subject_label']} {_humanize(predicate)} "
                    f"{row['object_label']} ({row['object_id']}).{details}",
            "refs": evidence_ids,
        })
        if predicate == "dismech:has_recorded_treatment":
            env.limit("A recorded treatment is published knowledge in the source, not a treatment recommendation.")
        # A phenotype description often explains the cause ("anemia due to ..."), which is not
        # what the association edge records, so it stays out of observations.
        if row["object_description"]:
            env.interpretation.append({
                "text": f"{source_name} describes {row['object_label']} in {row['subject_label']}: "
                        f"{row['object_description']}",
                "basis": "source_curator_commentary",
                "refs": evidence_ids,
            })
        if row["explanation"]:
            env.interpretation.append({
                "text": row["explanation"],
                "basis": "source_curator_commentary",
                "refs": evidence_ids,
            })

    if not evidence_ids and kind not in STRUCTURAL_KINDS:
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
            if not row["id"]:
                continue  # a retired stub left by an older loader: not a mechanism, never returned
            node_id, node_type = row["id"], "Mechanism"
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
        ORDER BY object_label, source DESC
        LIMIT {limit + 1}
    """
    parameters = {"disease_id": disease_id}
    env.set_query(query, parameters)
    rows = _read(session, query, parameters)
    shown = rows[:limit]

    for row in shown:
        add_edge_row(env, row)

    env.limit("A phenotype recorded by both Monarch and DisMech is one node with one edge per source.")
    env.limit("A recorded phenotype does not say how the disease causes it: no edge in the graph links a "
              "phenotype to a mechanism.")
    if len(rows) > limit:
        env.limit(f"More than {limit} phenotype associations are recorded; only the first {limit} are returned.")
    if not rows:
        name = _disease_label(session, disease_id)
        env.limit(
            f"No Disease node with id {disease_id} is in the loaded graph." if name is None else
            f"No phenotype association is recorded for {name} ({disease_id}) in the loaded data."
        )
        return env.build("graph", f"No phenotypes recorded for {disease_id}.")

    dismech_count = sum(1 for row in shown if row["source"] == "dismech")
    return env.build("graph", f"{len(shown)} phenotype associations recorded for {rows[0]['subject_label']} "
                              f"({disease_id}): {len(shown) - dismech_count} from Monarch, "
                              f"{dismech_count} from DisMech.")


def get_mechanism_paths(session, args, env):
    disease_id = _required(args, "disease_id")
    query = f"""
        MATCH (d:Disease {{id: $disease_id}})-[h:HAS_MECHANISM]->(a:Mechanism)
        RETURN {HAS_MECHANISM_COLUMNS}
        UNION ALL
        MATCH (d:Disease {{id: $disease_id}})-[:HAS_MECHANISM]->(a:Mechanism)-[r:LEADS_TO]->(b:Mechanism)
        WHERE b.id IS NOT NULL
        RETURN {LEADS_TO_COLUMNS}
        UNION ALL
        MATCH (d:Disease {{id: $disease_id}})-[:HAS_MECHANISM]->(a:Mechanism)-[r:LEADS_TO_PHENOTYPE]->(o:Entity)
        RETURN {OUTCOME_COLUMNS}
    """
    parameters = {"disease_id": disease_id}
    env.set_query(query, parameters)
    rows = _read(session, query, parameters)
    # Mechanism steps first, then causal edges, so observations read in a stable order.
    rows.sort(key=lambda r: (r["kind"] != "has_mechanism", r["subject_label"] or "", r["object_label"] or ""))

    for row in rows:
        add_edge_row(env, row)
    # Causal chains start at a mechanism that no leads_to edge points into. Starting at the disease would
    # also list every direct disease-to-mechanism edge as a shortcut into the chain.
    caused = {r["object_id"] for r in rows if r["kind"] == "leads_to"}
    env.path_roots = sorted({r["object_id"] for r in rows if r["kind"] == "has_mechanism"} - caused)

    if not rows:
        name = _disease_label(session, disease_id)
        env.limit(
            f"No Disease node with id {disease_id} is in the loaded graph." if name is None else
            f"No DisMech mechanism is recorded for {name} ({disease_id}) in the loaded data."
        )
        return env.build("graph", f"No mechanism paths recorded for {disease_id}.")

    # Compared by name: the ids carry the disease, the descriptions and the hand-written gaps do not.
    steps = {r["object_label"] for r in rows if r["kind"] == "has_mechanism"}
    linked = {r["subject_label"] for r in rows if r["kind"] in ("leads_to", "leads_to_phenotype")}
    linked |= {r["object_label"] for r in rows if r["kind"] == "leads_to"}
    unlinked = sorted(steps - linked)
    if unlinked:
        env.limit("No causal (leads_to) edge is recorded into or out of: " + ", ".join(unlinked) + ".")
    env.limit("Only the first evidence item of each DisMech causal edge is loaded into the graph.")
    recorded = {(r["subject_label"], r["object_label"]) for r in rows if r["kind"] in ("leads_to", "leads_to_phenotype")}
    gap_found = False
    for described_in, upstream, downstream, text in DESCRIBED_WITHOUT_EDGE.get(disease_id, []):
        missing = [name for name in downstream if (upstream, name) not in recorded]
        if described_in in steps and missing:
            gap_found = True
            env.limit(f"Not a recorded edge: {upstream} -> {', '.join(missing)}. "
                      f"DisMech's description of {described_in} says {text}")
    if gap_found:
        env.limit("Those 'not a recorded edge' steps were identified by hand from the mechanism descriptions. "
                  "No edge links a mechanism to a phenotype, even where they share a name, as Hypercalcemia does.")

    edge_count = sum(1 for r in rows if r["kind"] == "leads_to")
    outcome_count = sum(1 for r in rows if r["kind"] == "leads_to_phenotype")
    disease = next(r["subject_label"] for r in rows if r["kind"] == "has_mechanism")
    outcomes = f" and {outcome_count} edges to a downstream phenotype" if outcome_count else ""
    return env.build("graph", f"DisMech records {len(steps)} mechanisms and {edge_count} causal edges{outcomes} "
                              f"for {disease} ({disease_id}).")


def get_evidence(session, args, env):
    edge = args.get("edge_id")
    reference = args.get("reference")
    if bool(edge) == bool(reference):
        raise ToolInputError("Pass exactly one of edge_id or reference")

    if edge:
        dataset, subject, predicate, obj = parse_edge_id(edge)
        if predicate == "leads_to":
            query = f"""
                MATCH (a:Mechanism {{id: $subject}})-[r:LEADS_TO]->(b:Mechanism {{id: $object}})
                RETURN {LEADS_TO_COLUMNS}
            """
            parameters = {"subject": subject, "object": obj}
        elif predicate == "leads_to_phenotype":
            query = f"""
                MATCH (a:Mechanism {{id: $subject}})-[r:LEADS_TO_PHENOTYPE]->(o:Entity {{id: $object}})
                RETURN {OUTCOME_COLUMNS}
            """
            parameters = {"subject": subject, "object": obj}
        elif predicate == "has_mechanism":
            query = f"""
                MATCH (d:Disease {{id: $subject}})-[h:HAS_MECHANISM]->(a:Mechanism {{id: $object}})
                RETURN {HAS_MECHANISM_COLUMNS}
            """
            parameters = {"subject": subject, "object": obj}
        elif predicate == "has_driver_gene":
            query = f"""
                MATCH (s:Entity {{id: $subject}})-[r:HAS_DRIVER_GENE]->(g:Entity {{id: $object}})
                RETURN {DRIVER_GENE_COLUMNS}
            """
            parameters = {"subject": subject, "object": obj}
        elif predicate == "has_agent":
            query = f"""
                MATCH (t:Entity {{id: $subject}})-[r:HAS_AGENT]->(a:Entity {{id: $object}})
                RETURN {AGENT_COLUMNS}
            """
            parameters = {"subject": subject, "object": obj}
        elif predicate == "targets_gene":
            query = f"""
                MATCH (a:Entity {{id: $subject}})-[r:TARGETS_GENE]->(g:Entity {{id: $object}})
                RETURN {TARGETS_GENE_COLUMNS}
            """
            parameters = {"subject": subject, "object": obj}
        elif predicate == "targets_mechanism":
            query = f"""
                MATCH (t:Entity {{id: $subject}})-[r:TARGETS_MECHANISM]->(m:Mechanism {{id: $object}})
                RETURN {TARGETS_COLUMNS}
            """
            parameters = {"subject": subject, "object": obj}
        else:
            # The subject is a disease for most edges, but a gene for Monarch gene-to-disease associations.
            query = f"""
                MATCH (d {{id: $subject}})-[r:ASSOCIATION {{predicate: $predicate, source: $dataset}}]->(o {{id: $object}})
                RETURN {ASSOCIATION_COLUMNS}
            """
            parameters = {"subject": subject, "predicate": predicate, "object": obj, "dataset": dataset}
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
        WHERE r.evidence_pmid = $reference AND a.id IS NOT NULL AND b.id IS NOT NULL
        RETURN {LEADS_TO_COLUMNS}
        UNION ALL
        MATCH (a:Mechanism)-[r:LEADS_TO_PHENOTYPE]->(o:Entity)
        WHERE r.evidence_pmid = $reference
        RETURN {OUTCOME_COLUMNS}
        UNION ALL
        MATCH (d:Disease)-[h:HAS_MECHANISM]->(a:Mechanism)
        WHERE h.evidence_pmid = $reference
        RETURN {HAS_MECHANISM_COLUMNS}
        UNION ALL
        MATCH (d)-[r:ASSOCIATION]->(o)
        WHERE r.evidence_pmid = $reference OR r.primary_knowledge_source = $reference OR r.publications IS NOT NULL
        RETURN {ASSOCIATION_COLUMNS}
        UNION ALL
        MATCH (t:Entity)-[r:TARGETS_MECHANISM]->(m:Mechanism)
        WHERE r.evidence_pmid = $reference
        RETURN {TARGETS_COLUMNS}
        UNION ALL
        MATCH (a:Entity)-[r:TARGETS_GENE]->(g:Entity)
        WHERE r.references IS NOT NULL
        RETURN {TARGETS_GENE_COLUMNS}
    """
    parameters = {"reference": reference}
    env.set_query(query, parameters)
    rows = [
        row for row in _read(session, query, parameters)
        if row["pmid"] == reference
        or row["primary_knowledge_source"] == reference
        or reference in _as_list(row["publications"])
    ]
    for row in rows:
        add_edge_row(env, row)
    if not rows:
        env.limit(f"No edge citing {reference} is recorded in the loaded graph.")
        return env.build("graph", f"No edges cite {reference}.")
    return env.build("graph", f"{len(env.edges)} edge(s) in the loaded graph cite {reference}.")


# ------------------------------------- gene, gene-disease and treatment tools

# Drugs such as proteasome inhibitors list every subunit of the complex, which would swamp a response.
MAX_TARGETS_PER_AGENT = 10

NOT_LOADED = ("CIViC is not loaded. Open Targets is loaded only for the targets of drugs in DisMech treatment "
              "records, not for its gene-disease associations or other data, so neither is covered for genes or diseases.")


def _resolve_gene(session, value):
    """(id, label) of a human gene node from an HGNC id or an approved symbol, or None.

    Symbols only resolve to HGNC ids, so a mouse or other-species gene with the same name is never returned."""
    if re.fullmatch(r"HGNC:\d+", value, re.I):
        rows = _read(session, "MATCH (g:Entity {id: $id}) RETURN g.id AS id, g.label AS label", {"id": value.upper()})
    else:
        rows = _read(
            session,
            "MATCH (g:Entity) WHERE g.id STARTS WITH 'HGNC:' AND toLower(g.label) = toLower($symbol) "
            "RETURN g.id AS id, g.label AS label ORDER BY id",
            {"symbol": value},
        )
    return (rows[0]["id"], rows[0]["label"]) if rows else None


def _gene_not_found(env, gene):
    env.limit(f"No human gene node matching '{gene}' (an HGNC id such as HGNC:6407, or an approved symbol) is "
              "recorded in the loaded graph. A gene is present only if a loaded Monarch or DisMech record names it.")
    return env.build("graph", f"No gene recorded for {gene}.")


def get_gene_context(session, args, env):
    gene = _required(args, "gene")
    limit = _limit_arg(args)
    resolved = _resolve_gene(session, gene)
    if resolved is None:
        return _gene_not_found(env, gene)
    gene_id, gene_label = resolved

    query = f"""
        MATCH (d)-[r:ASSOCIATION]->(o)
        WHERE d.id = $gene_id
        RETURN {ASSOCIATION_COLUMNS}
        UNION ALL
        MATCH (d)-[r:ASSOCIATION]->(o)
        WHERE o.id = $gene_id
        RETURN {ASSOCIATION_COLUMNS}
        UNION ALL
        MATCH (s:Entity)-[r:HAS_DRIVER_GENE]->(g:Entity)
        WHERE g.id = $gene_id
        RETURN {DRIVER_GENE_COLUMNS}
        UNION ALL
        MATCH (a:Entity)-[r:TARGETS_GENE]->(g:Entity)
        WHERE g.id = $gene_id
        RETURN {TARGETS_GENE_COLUMNS}
    """
    parameters = {"gene_id": gene_id}
    env.set_query(query, parameters)
    rows = _read(session, query, parameters)
    rows.sort(key=lambda r: (r["source"] or "", r["predicate"] or "", r["object_label"] or ""))
    shown = rows[:limit]
    for row in shown:
        add_edge_row(env, row)

    counts = {dataset: sum(1 for r in shown if r["source"] == dataset) for dataset in DATASET_NAMES}
    for dataset in COVERAGE_DATASETS:
        if counts[dataset] == 0:
            env.limit(f"{DATASET_NAMES[dataset]}: no record of {gene_label} ({gene_id}) is recorded in the loaded "
                      "graph (none recorded).")
    env.limit(NOT_LOADED)
    env.limit("Edges exist only for the diseases and genes that were loaded from each source, so 'none recorded' "
              "does not mean the source holds no record for this gene.")
    if len(rows) > limit:
        env.limit(f"More than {limit} edges are recorded for {gene_label}; only the first {limit} are returned.")
    return env.build("graph", f"{gene_label} ({gene_id}): {len(shown)} recorded edge(s), {counts['monarch']} from "
                              f"Monarch, {counts['dismech']} from DisMech and {counts['opentargets']} from Open "
                              "Targets (drug targets).")


def get_gene_disease_association(session, args, env):
    gene = _required(args, "gene")
    disease_id = _required(args, "disease_id")
    variant = args.get("variant")
    if variant is not None and (not isinstance(variant, str) or not variant.strip()):
        raise ToolInputError("variant must be a non-empty string")
    variant = variant.strip() if variant else None

    resolved = _resolve_gene(session, gene)
    if resolved is None:
        return _gene_not_found(env, gene)
    gene_id, gene_label = resolved

    # Monarch stores the gene as subject; DisMech stores the disease as subject. Both are read, and the
    # subtype that the gene defines (with its driver-gene link) shows where within the disease it sits.
    query = f"""
        MATCH (d)-[r:ASSOCIATION]->(o)
        WHERE d.id = $gene_id AND o.id = $disease_id
        RETURN {ASSOCIATION_COLUMNS}
        UNION ALL
        MATCH (d)-[r:ASSOCIATION]->(o)
        WHERE d.id = $disease_id AND o.id = $gene_id
        RETURN {ASSOCIATION_COLUMNS}
        UNION ALL
        MATCH (d {{id: $disease_id}})-[r:ASSOCIATION {{predicate: 'dismech:has_subtype'}}]->(o)-[:HAS_DRIVER_GENE]->(g:Entity {{id: $gene_id}})
        RETURN {ASSOCIATION_COLUMNS}
        UNION ALL
        MATCH (d {{id: $disease_id}})-[:ASSOCIATION {{predicate: 'dismech:has_subtype'}}]->(s)-[r:HAS_DRIVER_GENE]->(g:Entity {{id: $gene_id}})
        RETURN {DRIVER_GENE_COLUMNS}
    """
    parameters = {"gene_id": gene_id, "disease_id": disease_id}
    env.set_query(query, parameters)
    rows = _read(session, query, parameters)
    rows.sort(key=lambda r: (r["source"] or "", r["predicate"] or "", r["object_label"] or ""))
    for row in rows:
        add_edge_row(env, row)
    # The gene and the disease point at each other (Monarch and DisMech disagree on direction), so start
    # at the disease: it reaches the gene directly and through the subtype that the gene defines.
    env.path_roots = [disease_id]

    disease_name = _disease_label(session, disease_id)
    disease = f"{disease_name} ({disease_id})" if disease_name else disease_id
    if disease_name is None:
        env.limit(f"No Disease node with id {disease_id} is in the loaded graph.")

    monarch = [r for r in rows if r["source"] == "monarch"]
    factors = [r for r in rows if r["predicate"] == "dismech:has_genetic_factor"]
    subtypes = [r for r in rows if r["predicate"] == "dismech:has_subtype"]
    if not monarch:
        env.limit(f"Monarch: no association between {gene_label} ({gene_id}) and {disease} is recorded in the "
                  "loaded graph (none recorded).")
    else:
        env.limit(f"The Monarch association links the gene {gene_label} ({gene_id}) to the disease. It is recorded "
                  "at gene level and does not say which variants are involved.")
    if not factors:
        env.limit(f"DisMech: no genetic factor linking {gene_label} ({gene_id}) to {disease} is recorded in the "
                  "loaded graph (none recorded).")
    if not subtypes:
        env.limit(f"DisMech: no subtype of {disease} is recorded as defined by {gene_label} (none recorded).")
    env.limit(NOT_LOADED)

    if variant:
        found = _read(
            session,
            "MATCH (n) WHERE toLower(coalesce(n.label, n.name, '')) CONTAINS toLower($variant) AND n.id <> $gene_id "
            "RETURN n.id AS id, coalesce(n.label, n.name) AS label LIMIT 5",
            {"variant": variant, "gene_id": gene_id},
        )
        if found:
            env.limit(f"Entities whose name contains '{variant}': "
                      + ", ".join(f"{r['label']} ({r['id']})" for r in found)
                      + ". This tool does not follow variant-level links, so none is reported as an association.")
        else:
            env.limit(f"Requested specificity: variant '{variant}'. No variant-level record matching it is in the "
                      f"loaded graph, so the association is reported at gene level: {gene_label} ({gene_id}). "
                      f"It links the gene to the disease and is not evidence about '{variant}' itself.")

    return env.build("graph", f"{gene_label} ({gene_id}) and {disease}: {len(monarch)} Monarch association(s), "
                              f"{len(factors)} DisMech genetic factor(s), {len(subtypes)} DisMech subtype(s).")


def _resolve_drug(session, value):
    """(id, label) of a drug node from a ChEBI/ChEMBL/NCIT id or a name, or None.

    A drug is an Entity that is the object of a HAS_AGENT edge. Matching a name is exact
    (case-insensitive) so a partial word does not silently pick one of several drugs."""
    if re.fullmatch(r"(CHEBI|CHEMBL|NCIT):\w+", value, re.I):
        rows = _read(
            session,
            "MATCH (:Entity)-[:HAS_AGENT]->(a:Entity {id: $id}) RETURN a.id AS id, a.label AS label LIMIT 1",
            {"id": value.upper() if value.upper().startswith(("CHEBI", "NCIT")) else value},
        )
    else:
        rows = _read(
            session,
            "MATCH (:Entity)-[:HAS_AGENT]->(a:Entity) WHERE toLower(a.label) = toLower($name) "
            "RETURN a.id AS id, a.label AS label ORDER BY id LIMIT 1",
            {"name": value},
        )
    return (rows[0]["id"], rows[0]["label"]) if rows else None


def get_drug_context(session, args, env):
    """Everything the graph records about one drug: its gene targets (Open Targets), the DisMech
    treatments it is an agent of, and the diseases those treatments are recorded for."""
    drug = _required(args, "drug")
    limit = _limit_arg(args)
    resolved = _resolve_drug(session, drug)
    if resolved is None:
        env.limit(f"No drug matching '{drug}' (a name such as trametinib, or a CHEBI/CHEMBL/NCIT id) is an agent "
                  "of any recorded treatment in the loaded graph. Drugs are limited to the agents DisMech names "
                  "in its treatment records.")
        return env.build("graph", f"No drug recorded for {drug}.")
    drug_id, drug_label = resolved

    query = f"""
        MATCH (a:Entity {{id: $drug_id}})-[r:TARGETS_GENE]->(g:Entity)
        RETURN {TARGETS_GENE_COLUMNS}
        UNION ALL
        MATCH (t:Entity)-[r:HAS_AGENT]->(a:Entity {{id: $drug_id}})
        RETURN {AGENT_COLUMNS}
        UNION ALL
        MATCH (d:Disease)-[r:ASSOCIATION {{predicate: 'dismech:has_recorded_treatment'}}]->(o)-[:HAS_AGENT]->(a:Entity {{id: $drug_id}})
        RETURN {ASSOCIATION_COLUMNS}
    """
    parameters = {"drug_id": drug_id}
    env.set_query(query, parameters)
    rows = _read(session, query, parameters)
    targets = [r for r in rows if r["kind"] == "targets_gene"]
    treatments = [r for r in rows if r["kind"] == "association"][:limit]
    shown_targets = sorted(targets, key=lambda r: r["object_label"] or "")[:MAX_TARGETS_PER_AGENT]
    for row in [r for r in rows if r["kind"] == "has_agent"] + treatments + shown_targets:
        add_edge_row(env, row)

    if not targets:
        env.limit(f"No molecular target is recorded for {drug_label} ({drug_id}) in the loaded graph "
                  "(none recorded). Targets come from Open Targets' mechanism-of-action record.")
    if len(targets) > len(shown_targets):
        env.limit(f"{drug_label} has {len(targets)} recorded targets; only the first {len(shown_targets)} "
                  f"(alphabetical) are returned. Use kg_get_gene_context for any one gene.")
    if not treatments:
        env.limit(f"{drug_label} is in the graph as a drug agent but no disease treatment currently references it.")
    env.limit("Open Targets drug targets are the drug's overall mechanism of action, not disease-specific, and "
              "are not a treatment recommendation.")
    diseases = sorted({r["subject_label"] for r in treatments})
    disease_text = (" for " + ", ".join(diseases)) if diseases else ""
    return env.build("graph", f"{drug_label} ({drug_id}): {len(targets)} recorded gene target(s) and "
                              f"{len(treatments)} recorded treatment use(s){disease_text}.")


def get_treatment_evidence(session, args, env):
    disease_id = _required(args, "disease_id")
    treatment, mechanism = args.get("treatment"), args.get("mechanism")
    for name, value in (("treatment", treatment), ("mechanism", mechanism)):
        if value is not None and (not isinstance(value, str) or not value.strip()):
            raise ToolInputError(f"{name} must be a non-empty string")
    treatment = treatment.strip().lower() if treatment else None
    mechanism = mechanism.strip().lower() if mechanism else None

    recorded = "{predicate: 'dismech:has_recorded_treatment'}"
    query = f"""
        MATCH (d {{id: $disease_id}})-[r:ASSOCIATION {recorded}]->(o)
        RETURN {ASSOCIATION_COLUMNS}
        UNION ALL
        MATCH (d {{id: $disease_id}})-[:ASSOCIATION {recorded}]->(t)-[r:HAS_AGENT]->(a)
        RETURN {AGENT_COLUMNS}
        UNION ALL
        MATCH (d {{id: $disease_id}})-[:ASSOCIATION {recorded}]->(t)-[r:TARGETS_MECHANISM]->(m:Mechanism)
        WHERE m.disease_id = $disease_id
        RETURN {TARGETS_COLUMNS}
        UNION ALL
        MATCH (d {{id: $disease_id}})-[:ASSOCIATION {recorded}]->(t)-[:HAS_AGENT]->(a)-[r:TARGETS_GENE]->(g:Entity)
        RETURN {TARGETS_GENE_COLUMNS}
    """
    parameters = {"disease_id": disease_id}
    env.set_query(query, parameters)
    rows = _read(session, query, parameters)
    treatments = [r for r in rows if r["kind"] == "association"]
    agents = [r for r in rows if r["kind"] == "has_agent"]
    targets = [r for r in rows if r["kind"] == "targets_mechanism"]
    genes = [r for r in rows if r["kind"] == "targets_gene"]

    kept = {r["object_id"] for r in treatments if not treatment or treatment in (r["object_label"] or "").lower()}
    if treatment:
        kept |= {r["subject_id"] for r in agents if treatment in (r["object_label"] or "").lower()}
    if mechanism:
        targets = [r for r in targets if mechanism in (r["object_label"] or "").lower()]
        kept &= {r["subject_id"] for r in targets}
    treatments = [r for r in treatments if r["object_id"] in kept]
    agents = [r for r in agents if r["subject_id"] in kept]
    targets = [r for r in targets if r["subject_id"] in kept]
    agent_ids = {r["object_id"] for r in agents}
    genes = sorted((r for r in genes if r["subject_id"] in agent_ids), key=lambda r: (r["subject_label"], r["object_label"]))
    with_target = {r["subject_label"] for r in genes}
    shown_genes, cut = [], {}
    for gene in genes:
        if sum(1 for g in shown_genes if g["subject_id"] == gene["subject_id"]) < MAX_TARGETS_PER_AGENT:
            shown_genes.append(gene)
        else:
            cut[gene["subject_label"]] = cut.get(gene["subject_label"], 0) + 1

    if not treatments:
        name = _disease_label(session, disease_id)
        env.limit(
            f"No Disease node with id {disease_id} is in the loaded graph." if name is None else
            f"No recorded treatment{' matching the filters' if treatment or mechanism else ''} is recorded for "
            f"{name} ({disease_id}) in the loaded data (none recorded)."
        )
        return env.build("graph", f"No recorded treatments for {disease_id}.")

    for row in treatments + agents + targets + shown_genes:
        add_edge_row(env, row)
    # Chains run disease -> treatment -> agent -> target gene, and disease -> treatment -> mechanism.
    env.path_roots = [disease_id]

    # Show where the targeted mechanism sits in the disease record, so its origin is visible.
    for mechanism_node in sorted({r["object_id"] for r in targets}):
        context = _read(
            session,
            f"MATCH (d:Disease {{id: $disease_id}})-[h:HAS_MECHANISM]->(a:Mechanism {{id: $mechanism}}) "
            f"RETURN {HAS_MECHANISM_COLUMNS}",
            {"disease_id": disease_id, "mechanism": mechanism_node},
        )
        for row in context:
            add_edge_row(env, row)

    untargeted = sorted({r["object_label"] for r in treatments} - {r["subject_label"] for r in targets})
    if untargeted:
        env.limit("No treatment-to-mechanism edge is recorded for: " + ", ".join(untargeted) + ".")
    no_target = sorted({r["object_label"] for r in agents} - with_target)
    if no_target:
        env.limit("No molecular target (for example a gene or protein) is recorded for: " + ", ".join(no_target)
                  + " (none recorded). A target named only in a description or evidence snippet is text, not a "
                  "recorded edge.")
    if genes:
        env.limit("Drug targets are Open Targets' own record of each drug's mechanism of action, not part of the "
                  "DisMech disease record; the cited DisMech trial evidence does not refer to them.")
    for label, extra in sorted(cut.items()):
        env.limit(f"{label} has {extra + MAX_TARGETS_PER_AGENT} recorded targets; only the first "
                  f"{MAX_TARGETS_PER_AGENT} (alphabetical) are returned. Use kg_get_gene_context for any one gene.")
    if any(item["snippet"] for item in env.evidence.values()):
        env.limit("Trial population and result appear only as the quoted evidence snippet; the graph has no "
                  "structured fields for them, and any count or rate must be read from the quoted text.")
    return env.build("graph", f"{len(treatments)} recorded treatment(s) for {treatments[0]['subject_label']} "
                              f"({disease_id}), {len(targets)} linked to a targeted mechanism and "
                              f"{len(with_target)} with a recorded drug target.")


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


def _walk_graph_values(value, visit):
    """Call visit on every node, relationship and path inside a result value, however deeply nested."""
    if isinstance(value, (Node, Relationship, Path)):
        visit(value)
    elif isinstance(value, dict):
        for item in value.values():
            _walk_graph_values(item, visit)
    elif isinstance(value, (list, tuple)):
        for item in value:
            _walk_graph_values(item, visit)


def _graph_node(env, node):
    """Add a returned node to the graph and give back its id, or None if it has no stable id."""
    props, labels = dict(node), set(node.labels)
    node_id = props.get("id")
    if not node_id:
        return None  # a node returned without its id property cannot be referred to by an edge
    kind = next((name for name in sorted(labels) if name != "Entity"), "Entity")
    dataset = props.get("source") or next((LABEL_DATASET[n] for n in sorted(labels) if n in LABEL_DATASET), UNRECORDED)
    label = props.get("name") if "Disease" in labels else props.get("label")
    label = label or props.get("label") or props.get("name") or node_id
    env.add_node(node_id, label, _category_type(props.get("category"), kind), dataset, props.get("source_version"))
    return node_id


def _graph_edge(env, relationship, known):
    """Add a returned relationship to the graph and give back its id, or None if an end is not in the result."""
    start, end = relationship.nodes
    if start is None or end is None:
        return None
    subject = known.get(start.element_id) or dict(start).get("id")
    obj = known.get(end.element_id) or dict(end).get("id")
    if subject not in env.nodes or obj not in env.nodes:
        return None
    props = dict(relationship)
    dataset = props.get("source") or UNRECORDED
    predicate = props.get("predicate") or relationship.type.lower()
    eid = edge_id(dataset, subject, predicate, obj)
    if eid in env.edges:
        return eid
    version = env.version(dataset, props.get("source_version"))
    references = ([props["evidence_pmid"]] if props.get("evidence_pmid") else
                  _as_list(props.get("publications")) or _as_list(props.get("primary_knowledge_source"))
                  or _as_list(props.get("references")))
    env.edges[eid] = {
        "id": eid,
        "source": subject,
        "target": obj,
        "predicate": predicate,
        "edge_origin": props.get("edge_origin") or "recorded",
        "source_dataset": dataset,
        "source_version": version,
        "evidence_ids": [
            env.add_evidence(f"{eid}#{reference}", reference, dataset, version, props.get("evidence_snippet"))
            for reference in references
        ],
    }
    return eid


def _collect_graph(env, raw_rows):
    """Fill graph.nodes, graph.edges and graph.paths from the nodes, relationships and paths a query
    returned, using the same ids as the named tools. Scalars stay in the table only."""
    nodes, relationships, paths = [], [], []

    def visit(value):
        if isinstance(value, Node):
            nodes.append(value)
        elif isinstance(value, Relationship):
            relationships.append(value)
        else:
            nodes.extend(value.nodes)
            relationships.extend(value.relationships)
            paths.append(value)

    for row in raw_rows:
        _walk_graph_values(row, visit)
    if not (nodes or relationships):
        return

    # A relationship names its ends by element id and may carry no properties, so ends are matched to the
    # full nodes returned alongside it.
    known = {}
    for node in nodes:
        node_id = _graph_node(env, node)
        if node_id:
            known[node.element_id] = node_id
    skipped = sum(1 for relationship in relationships if _graph_edge(env, relationship, known) is None)

    env.explicit_paths = True  # the query's own paths are listed; none are guessed from the edges
    for path in paths:
        node_ids = [known.get(node.element_id) for node in path.nodes]
        edge_ids = [_graph_edge(env, relationship, known) for relationship in path.relationships]
        if None in node_ids or None in edge_ids:
            skipped += 1
        else:
            env.add_path(node_ids, edge_ids)

    env.limit("graph holds the nodes, relationships and paths that the query returned, and only those that "
              "carry an id property. Values returned as plain columns appear in table only.")
    if any(not dict(node).get("id") for node in nodes):
        env.limit("Some returned nodes have no id property, so they are in table but not in graph.")
    if skipped:
        env.limit(f"{skipped} returned relationship(s) or path(s) are in table but not in graph, because the "
                  "nodes at their ends were not returned with an id property.")


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
        rows, raw = [], []
        for record in result:
            if len(rows) == QUERY_MAX_ROWS:
                return columns, rows, raw, True
            raw.append([record[c] for c in columns])
            rows.append([_to_json(value) for value in raw[-1]])
        return columns, rows, raw, False

    columns, rows, raw, truncated = session.execute_read(work)
    _collect_graph(env, raw)
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
    "kg_get_gene_context": get_gene_context,
    "kg_get_gene_disease_association": get_gene_disease_association,
    "kg_get_treatment_evidence": get_treatment_evidence,
    "kg_get_drug_context": get_drug_context,
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
        "paths": len(result["graph"]["paths"]),
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
