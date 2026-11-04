"""The Gateway smoke test must fail on the problems it exists to catch."""

import importlib.util
from types import SimpleNamespace

import pytest

from mmrf_knowledgegraph.etl import ROOT

spec = importlib.util.spec_from_file_location("test_gateway_script", ROOT / "scripts/test_gateway.py")
smoke = importlib.util.module_from_spec(spec)
spec.loader.exec_module(smoke)


def envelope(result_type="graph", nodes=(), edges=(), evidence=(), table=None, summary="ok"):
    result = {
        "contract_version": smoke.CONTRACT_VERSION, "result_type": result_type, "summary": summary,
        "graph": {"nodes": list(nodes), "edges": list(edges)}, "evidence": list(evidence), "limitations": [],
    }
    if table is not None:
        result["table"] = table
    return result


class HealthyGateway:
    """Answers every call the way the deployed graph should."""

    nodes = smoke.NODES
    relationships = smoke.RELATIONSHIPS

    def list_tools(self):
        return set(smoke.TOOLS)

    def call_tool(self, name, **arguments):
        if name == "kg_run_readonly_query":
            query = arguments["query"]
            if query == smoke.WRITE_ATTEMPT:
                return envelope("text", summary="The tool input was rejected.")
            count = self.relationships if "[r]" in query else self.nodes
            return envelope("table", table={"columns": ["c"], "rows": [[count]]})
        if name == "kg_search_entities":
            return envelope(nodes=[{"id": smoke.DISEASE_ID}])
        if name == "kg_get_phenotypes":
            return envelope(edges=[{}] * smoke.PHENOTYPE_EDGES)
        if name == "kg_get_mechanism_paths":
            return envelope(edges=[{"id": "m", "predicate": "has_mechanism"}] * smoke.MECHANISMS
                            + [{"id": "c", "predicate": "leads_to"}] * smoke.CAUSAL_EDGES)
        if name == "kg_get_evidence":
            return envelope(evidence=[{"reference": "PMID:1"}])
        if name == "kg_get_gene_disease_association":
            return envelope(edges=[{"source": smoke.KRAS_ID, "target": smoke.JMML_ID, "source_dataset": "monarch"}])
        if name == "kg_get_treatment_evidence":
            return envelope(edges=[{"predicate": "targets_mechanism"}], evidence=[{"reference": smoke.TRAMETINIB_PMID}])
        if name == "kg_get_drug_context":
            return envelope(edges=[{"predicate": "targets_gene"}])
        raise AssertionError(name)


def test_a_healthy_gateway_passes():
    assert smoke.run_checks(HealthyGateway()) == []


def test_an_empty_graph_fails_even_though_each_call_succeeds():
    gateway = HealthyGateway()
    gateway.nodes = gateway.relationships = 0
    assert smoke.run_checks(gateway) == ["graph has the expected size"]


def test_a_failed_query_fails_despite_a_normal_reply():
    class QueriesFail(HealthyGateway):
        def call_tool(self, name, **arguments):
            return envelope("text", summary="The graph query failed.")

    failures = smoke.run_checks(QueriesFail())
    assert "graph has the expected size" in failures and "search finds the disease" in failures


def test_a_missing_tool_fails():
    gateway = HealthyGateway()
    gateway.list_tools = lambda: smoke.TOOLS - {"kg_get_evidence"}
    assert smoke.run_checks(gateway) == ["tools are listed"]


def test_a_graph_without_the_jmml_links_fails():
    class NoJmml(HealthyGateway):
        def call_tool(self, name, **arguments):
            if name in {"kg_get_gene_disease_association", "kg_get_treatment_evidence"}:
                return envelope()
            return super().call_tool(name, **arguments)

    assert smoke.run_checks(NoJmml()) == ["JMML gene, mechanism and treatment links are returned"]


def test_a_graph_without_drug_targets_fails():
    class NoDrug(HealthyGateway):
        def call_tool(self, name, **arguments):
            if name == "kg_get_drug_context":
                return envelope()
            return super().call_tool(name, **arguments)

    assert smoke.run_checks(NoDrug()) == ["drug targets are returned"]


def test_an_accepted_write_query_fails():
    class GuardBroken(HealthyGateway):
        def call_tool(self, name, **arguments):
            if arguments.get("query") == smoke.WRITE_ATTEMPT:
                return envelope("table", table={"columns": ["1"], "rows": []})
            return super().call_tool(name, **arguments)

    assert smoke.run_checks(GuardBroken()) == ["write queries are rejected"]


def test_an_unexpected_contract_version_fails():
    class NewContract(HealthyGateway):
        def call_tool(self, name, **arguments):
            return {**super().call_tool(name, **arguments), "contract_version": "9.9"}

    assert "search finds the disease" in smoke.run_checks(NewContract())


@pytest.mark.parametrize(
    "status_code, text",
    [
        (400, '{"error": {"message": "Unsupported protocol version"}}'),
        (200, '{"jsonrpc": "2.0", "id": 1, "error": {"code": -32600, "message": "bad"}}'),
    ],
)
def test_http_and_rpc_errors_are_failures(status_code, text):
    with pytest.raises(smoke.CallFailed):
        smoke.parse_reply(SimpleNamespace(status_code=status_code, text=text))


def test_event_stream_replies_are_parsed():
    response = SimpleNamespace(status_code=200, text='event: message\ndata: {"result": {"tools": []}}\n\n')
    assert smoke.parse_reply(response) == {"tools": []}


def test_a_gateway_that_cannot_initialize_exits_non_zero(monkeypatch):
    response = SimpleNamespace(status_code=403, text="Forbidden")
    monkeypatch.setattr(smoke, "signed_post", lambda *args: response)
    assert smoke.run_smoke_test("https://example.invalid/mcp") == 1
