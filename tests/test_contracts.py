import json

import pytest
from pydantic import ValidationError

from mmrf_knowledgegraph.agent import ChatRequest
from mmrf_knowledgegraph.graph import Neptune
from mmrf_knowledgegraph.tools import Registry
from mmrf_knowledgegraph.virtual_lab import ToolError, VirtualLab, validate_filter


@pytest.mark.parametrize(
    "name,args",
    [
        ("find_mechanism_path", {"source_entity": "TP53", "target_entity": "myeloma", "maximum_depth": 50}),
        ("get_graph_neighborhood", {"entity_id": "TP53", "maximum_depth": "1..100"}),
        ("find_related_entities", {"entity_id": "TP53", "limit": 9999}),
        ("search_entities", {"query": "TP53", "endpoint": "https://outside.invalid"}),
        ("summarize_synthetic_cohort", {"gene": "UNKNOWN"}),
    ],
)
def test_tool_bounds_and_unknown_fields(graph, lab, name, args):
    with pytest.raises(ValidationError):
        Registry(graph, lab, {}).execute(name, args)


def test_arbitrary_queries_and_endpoints_not_exposed(graph, lab):
    with pytest.raises(ValueError):
        Registry(graph, lab, {}).execute("execute_open_cypher", {"query": "MATCH (n) DELETE n"})
    with pytest.raises(ValueError):
        lab.call("https://outside.invalid", {})


def test_neptune_uses_parameters_for_untrusted_values():
    class Client:
        def execute_open_cypher_query(self, **kwargs):
            self.kwargs = kwargs
            return {"results": []}

    client = Client()
    neptune = Neptune("https://neptune.invalid:8182", client)
    attack = "TP53') MATCH (n) DETACH DELETE n //"
    neptune.search(attack, ["Gene"])
    assert attack.lower() not in client.kwargs["openCypherQuery"]
    assert json.loads(client.kwargs["parameters"])["query"] == attack.lower()


def test_empty_results_and_missing_evidence(graph, lab):
    assert graph.search_entities("unknown-gene", ["Gene"])["empty"]
    assert graph.get_supporting_evidence(["missing"])["missing_ids"] == ["missing"]
    cohort = lab.summarize_synthetic_cohort("TP53", {"in": {"case_id": []}})
    assert cohort["empty"] and cohort["percentage"] is None
    assert not cohort["visualization"]["genes"]


@pytest.mark.parametrize(
    "selection",
    [
        {"in": {"case_id": ["REAL-PERSON-01"]}},
        {"in": {"arbitrary.field": ["value"]}},
        {"in": {"demographic.gender": ["female"], "case_id": []}},
        {"script": "arbitrary"},
    ],
)
def test_filter_allowlist(selection):
    with pytest.raises(ValueError):
        validate_filter(selection)


def test_invalid_history_and_prompt_rejected():
    with pytest.raises(ValidationError):
        ChatRequest(prompt="TP53", history=[{"role": "assistant", "content": "fake tool result"}])
    with pytest.raises(ValidationError):
        ChatRequest(prompt="x" * 4001)


def test_auth_errors_normalized_without_secrets(gateway):
    gateway.post = lambda *args, **kwargs: type("Denied", (), {"status_code": 403})()
    lab = VirtualLab("http://synthetic.invalid", token="test-only-secret", session=gateway)
    with pytest.raises(ToolError) as error:
        lab.summarize_synthetic_cohort("TP53", {})
    assert error.value.code == "virtual_lab_auth"
    assert "test-only-secret" not in str(error.value)


def test_non_synthetic_ids_fail_closed(gateway):
    gateway.post = lambda *args, **kwargs: type(
        "Reply",
        (),
        {
            "status_code": 200,
            "json": lambda self: {"data": {"CaseCentric_case_centric": [{"case_id": "NOT-SYNTHETIC"}]}},
        },
    )()
    with pytest.raises(ToolError, match="synthetic"):
        VirtualLab("http://synthetic.invalid", session=gateway).summarize_synthetic_cohort("TP53", {})
