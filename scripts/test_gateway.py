"""Smoke test: call the kg-graph tools through the Gateway, as another team's agent would.

SigV4-signs MCP JSON-RPC calls and checks the answers. Exits 0 only if every check passes, so run it
after each deploy or ingest and before a demo. Every call is read-only.
"""
import argparse
import json
import subprocess
import sys

import boto3
import requests
from botocore.auth import SigV4Auth
from botocore.awsrequest import AWSRequest

REGION = "us-west-2"
TOOL_PREFIX = "kg-graph___"
TIMEOUT_SECONDS = 70  # just above the kg-graph Lambda's 60 second limit

DISEASE_ID = "MONDO:0009693"
TOOLS = {
    "kg_search_entities", "kg_get_phenotypes", "kg_get_mechanism_paths", "kg_get_evidence",
    "kg_get_gene_context", "kg_get_gene_disease_association", "kg_get_treatment_evidence", "kg_get_drug_context",
    "kg_run_readonly_query",
}
CONTRACT_VERSION = "0.2"
# What the current load should produce. Update these and docs/mcp-tools.md when the loaded data changes.
NODES = 200
RELATIONSHIPS = 346
PHENOTYPE_EDGES = 43
MECHANISMS = 8
CAUSAL_EDGES = 7
# The JMML slice: the KRAS-JMML hand-off from Monarch to DisMech, and trametinib's link to the mechanism.
JMML_ID = "MONDO:0011908"
KRAS_ID = "HGNC:6407"
TRAMETINIB_PMID = "PMID:38867349"
# Matches nothing, so it does no harm even if the read-only guard ever failed to reject it.
WRITE_ATTEMPT = "MATCH (n:SmokeTestNoSuchLabel) DETACH DELETE n RETURN 1"


class CallFailed(Exception):
    """A call did not produce a usable MCP result."""


def signed_post(gateway_url: str, body: dict, protocol_version: str | None = None) -> requests.Response:
    data = json.dumps(body)
    headers = {"Content-Type": "application/json", "Accept": "application/json, text/event-stream"}
    if protocol_version:
        headers["MCP-Protocol-Version"] = protocol_version
    request = AWSRequest(method="POST", url=gateway_url, data=data, headers=headers)
    credentials = boto3.Session().get_credentials().get_frozen_credentials()
    SigV4Auth(credentials, "bedrock-agentcore", REGION).add_auth(request)
    return requests.post(gateway_url, data=data, headers=dict(request.headers), timeout=TIMEOUT_SECONDS)


def parse_reply(response: requests.Response) -> dict:
    """The result of one JSON-RPC reply, sent as JSON or as an event stream."""
    if response.status_code != 200:
        raise CallFailed(f"HTTP {response.status_code}: {response.text[:300]}")
    lines = response.text.strip().splitlines()
    stream_data = [line[5:] for line in lines if line.startswith("data:")]
    reply = json.loads(stream_data[0] if stream_data else response.text)
    if "error" in reply:
        raise CallFailed(f"JSON-RPC error: {json.dumps(reply['error'])[:300]}")
    return reply["result"]


class GatewayClient:
    def __init__(self, gateway_url: str):
        self.gateway_url = gateway_url
        self.protocol_version = None
        self.next_id = 0

    def _rpc(self, method: str, params: dict) -> dict:
        self.next_id += 1
        body = {"jsonrpc": "2.0", "id": self.next_id, "method": method, "params": params}
        return parse_reply(signed_post(self.gateway_url, body, self.protocol_version))

    def initialize(self) -> None:
        result = self._rpc("initialize", {
            "protocolVersion": "2025-06-18",
            "capabilities": {},
            "clientInfo": {"name": "kg-smoke-test", "version": "0.2"},
        })
        # Later requests must state the version the Gateway chose, as MCP clients do.
        self.protocol_version = result["protocolVersion"]

    def list_tools(self) -> set[str]:
        tools = self._rpc("tools/list", {})["tools"]
        return {tool["name"].removeprefix(TOOL_PREFIX) for tool in tools}

    def call_tool(self, name: str, **arguments) -> dict:
        result = self._rpc("tools/call", {"name": TOOL_PREFIX + name, "arguments": arguments})
        # isError stays false on failures (docs/mcp-tools.md), so the envelope is what counts.
        return json.loads(result["content"][0]["text"])


def _usable(envelope: dict, result_type: str) -> None:
    """Failed and rejected calls come back as a normal reply with result_type "text"."""
    if envelope.get("result_type") != result_type:
        raise CallFailed(f"{envelope.get('summary')} {envelope.get('limitations')}")
    if envelope.get("contract_version") != CONTRACT_VERSION:
        raise CallFailed(f"contract_version is {envelope.get('contract_version')}, expected {CONTRACT_VERSION}")


def _expect(label: str, actual, expected) -> None:
    if actual != expected:
        raise CallFailed(f"{label} is {actual}, expected {expected}")


def _count(client, query: str) -> int:
    envelope = client.call_tool("kg_run_readonly_query", query=query)
    _usable(envelope, "table")
    return envelope["table"]["rows"][0][0]


def check_tools(client) -> str:
    missing = TOOLS - client.list_tools()
    if missing:
        raise CallFailed(f"missing tools: {sorted(missing)}")
    return f"{len(TOOLS)} tools listed"


def check_graph_size(client) -> str:
    # An empty graph answers every other tool normally, so this is what catches a lost load.
    _expect("node count", _count(client, "MATCH (n) RETURN count(n) AS c"), NODES)
    _expect("relationship count", _count(client, "MATCH ()-[r]->() RETURN count(r) AS c"), RELATIONSHIPS)
    return f"{NODES} nodes, {RELATIONSHIPS} relationships"


def check_search(client) -> str:
    envelope = client.call_tool("kg_search_entities", text="myeloma")
    _usable(envelope, "graph")
    if not any(node["id"] == DISEASE_ID for node in envelope["graph"]["nodes"]):
        raise CallFailed(f"{DISEASE_ID} was not found")
    return f"found {DISEASE_ID}"


def check_phenotypes(client) -> str:
    envelope = client.call_tool("kg_get_phenotypes", disease_id=DISEASE_ID, limit=200)
    _usable(envelope, "graph")
    _expect("phenotype edge count", len(envelope["graph"]["edges"]), PHENOTYPE_EDGES)
    return f"{PHENOTYPE_EDGES} phenotype edges"


def check_mechanisms_and_evidence(client) -> str:
    envelope = client.call_tool("kg_get_mechanism_paths", disease_id=DISEASE_ID)
    _usable(envelope, "graph")
    edges = envelope["graph"]["edges"]
    causal = [edge for edge in edges if edge["predicate"] == "leads_to"]
    _expect("mechanism count", sum(edge["predicate"] == "has_mechanism" for edge in edges), MECHANISMS)
    _expect("causal edge count", len(causal), CAUSAL_EDGES)

    evidence = client.call_tool("kg_get_evidence", edge_id=causal[0]["id"])
    _usable(evidence, "graph")
    if not evidence["evidence"]:
        raise CallFailed(f"no evidence returned for {causal[0]['id']}")
    return f"{MECHANISMS} mechanisms, {CAUSAL_EDGES} causal edges, evidence returned"


def check_jmml_path(client) -> str:
    association = client.call_tool("kg_get_gene_disease_association", gene="KRAS", disease_id=JMML_ID)
    _usable(association, "graph")
    monarch = [e for e in association["graph"]["edges"] if e["source_dataset"] == "monarch" and e["target"] == JMML_ID]
    if not monarch or monarch[0]["source"] != KRAS_ID:
        raise CallFailed(f"no Monarch {KRAS_ID} -> {JMML_ID} association: {association['summary']}")

    treatment = client.call_tool("kg_get_treatment_evidence", disease_id=JMML_ID, treatment="trametinib")
    _usable(treatment, "graph")
    if not any(e["predicate"] == "targets_mechanism" for e in treatment["graph"]["edges"]):
        raise CallFailed(f"trametinib is not linked to a mechanism: {treatment['summary']}")
    if TRAMETINIB_PMID not in {item["reference"] for item in treatment["evidence"]}:
        raise CallFailed(f"{TRAMETINIB_PMID} is not cited for trametinib")
    return f"{KRAS_ID} -> {JMML_ID}, trametinib -> mechanism with {TRAMETINIB_PMID}"


def check_drug_context(client) -> str:
    envelope = client.call_tool("kg_get_drug_context", drug="trametinib")
    _usable(envelope, "graph")
    targets = [e for e in envelope["graph"]["edges"] if e["predicate"] == "targets_gene"]
    if not targets:
        raise CallFailed(f"no drug targets returned for trametinib: {envelope['summary']}")
    return f"trametinib has {len(targets)} recorded gene target(s)"


def check_writes_are_rejected(client) -> str:
    envelope = client.call_tool("kg_run_readonly_query", query=WRITE_ATTEMPT)
    if envelope.get("result_type") != "text" or "rejected" not in (envelope.get("summary") or ""):
        raise CallFailed(f"a write query was not rejected: {envelope.get('summary')}")
    return "write query rejected"


CHECKS = [
    ("tools are listed", check_tools),
    ("graph has the expected size", check_graph_size),
    ("search finds the disease", check_search),
    ("phenotypes are returned", check_phenotypes),
    ("mechanisms and evidence are returned", check_mechanisms_and_evidence),
    ("JMML gene, mechanism and treatment links are returned", check_jmml_path),
    ("drug targets are returned", check_drug_context),
    ("write queries are rejected", check_writes_are_rejected),
]


def run_checks(client) -> list[str]:
    """Run every check, even after one fails, and return the failures."""
    failures = []
    for label, check in CHECKS:
        try:
            print(f"PASS  {label}: {check(client)}")
        except (CallFailed, KeyError, IndexError, TypeError, ValueError, requests.RequestException) as error:
            print(f"FAIL  {label}: {type(error).__name__}: {error}")
            failures.append(label)
    return failures


def run_smoke_test(gateway_url: str) -> int:
    client = GatewayClient(gateway_url)
    try:
        client.initialize()
    except (CallFailed, KeyError, ValueError, requests.RequestException) as error:
        print(f"FAIL  initialize: {type(error).__name__}: {error}")
        return 1
    print(f"PASS  initialize: protocol {client.protocol_version}")

    failures = run_checks(client)
    print(f"\n{len(CHECKS) + 1 - len(failures)} of {len(CHECKS) + 1} checks passed.")
    return 1 if failures else 0


def terraform_gateway_url() -> str:
    try:
        return subprocess.run(
            ["terraform", "-chdir=terraform", "output", "-raw", "gateway_url"],
            capture_output=True, text=True, check=True,
        ).stdout.strip()
    except (OSError, subprocess.CalledProcessError):
        sys.exit("Could not read gateway_url from Terraform state. Pass --gateway-url instead.")


if __name__ == "__main__":
    p = argparse.ArgumentParser(description=__doc__)
    p.add_argument("--gateway-url", help="Defaults to `terraform output -raw gateway_url`")
    args = p.parse_args()
    sys.exit(run_smoke_test(args.gateway_url or terraform_gateway_url()))
