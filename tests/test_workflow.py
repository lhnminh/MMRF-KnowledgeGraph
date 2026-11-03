import json

from fastapi.testclient import TestClient

from mmrf_knowledgegraph.agent import Agent, ChatRequest
from mmrf_knowledgegraph.server import app
from mmrf_knowledgegraph.tools import Registry


class ScriptedBedrock:
    """Fixtures reproduce the Converse tool-use protocol, including tool-result messages."""

    def __init__(self):
        self.calls = []

    def converse(self, **kwargs):
        self.calls.append(kwargs)
        previous = [c["toolResult"] for m in kwargs["messages"] for c in m["content"] if "toolResult" in c]
        names = {t["toolSpec"]["name"] for t in kwargs["toolConfig"]["tools"]}
        assert "execute_query" not in names
        if len(previous) == 0:
            name, args = "search_entities", {"query": "TP53", "entity_types": ["Gene"]}
        elif len(previous) == 1:
            name, args = "find_gene_disease_connections", {"gene": "TP53", "disease": "Multiple Myeloma"}
        elif len(previous) == 2:
            edges = previous[-1]["content"][0]["json"]["relationships"]
            name, args = "get_supporting_evidence", {"relationship_ids": [r["id"] for r in edges]}
        elif len(previous) == 3:
            name, args = "summarize_synthetic_cohort", {"gene": "TP53"}
        else:
            return {
                "stopReason": "end_turn",
                "output": {
                    "message": {
                        "role": "assistant",
                        "content": [
                            {
                                "text": "Public evidence: TP53 has a myeloma association (PMID:30373884). Synthetic cohort: 1 of 1 fabricated cases; not a clinical estimate."
                            }
                        ],
                    }
                },
            }
        return {
            "stopReason": "tool_use",
            "output": {
                "message": {
                    "role": "assistant",
                    "content": [{"toolUse": {"toolUseId": str(len(previous)), "name": name, "input": args}}],
                }
            },
        }


def test_compound_question_bedrock_protocol_and_current_cohort(graph, lab, gateway):
    selection = {"in": {"demographic.gender": ["female"]}}
    model = ScriptedBedrock()
    agent = Agent(Registry(graph, lab, selection), model=model, mode="bedrock", backend="local")
    events = list(agent.run(ChatRequest(prompt="TP53 evidence and cohort?", cohort_filter=selection)))
    tools = [e["name"] for e in events if e["event"] == "tool_start"]
    assert tools == [
        "search_entities",
        "find_gene_disease_connections",
        "get_supporting_evidence",
        "summarize_synthetic_cohort",
    ]
    result = events[-1]
    assert result["cohort"]["total_cases"] == 1
    assert result["cohort"]["mutated_cases"] == 1
    assert result["graph"]["paths"]
    assert any(
        e["reference"] == "PMID:30373884" for r in result["graph"]["relationships"] for e in r["evidence"]
    )
    assert gateway.calls[0][1]["variables"]["filter"] == gateway.calls[1][1]["filter0"] == selection


def test_agentcore_http_contract_and_context_followup(graph, lab, monkeypatch):
    agent = Agent(Registry(graph, lab, {}), mode="rehearsal", backend="local")
    monkeypatch.setattr("mmrf_knowledgegraph.server.create_agent", lambda request: agent)
    client = TestClient(app)
    assert client.get("/ping").json() == {"status": "Healthy"}
    first = client.post("/invocations", json={"prompt": "TP53 evidence and synthetic cohort"})
    assert first.headers["content-type"].startswith("text/event-stream")

    def parse(response):
        return [json.loads(line[6:]) for line in response.text.splitlines() if line.startswith("data: ")]

    result = parse(first)[-1]
    second = client.post(
        "/invocations",
        json={
            "prompt": "Show related genes with similar phenotypes.",
            "history": [
                {"role": "user", "content": "TP53 evidence and synthetic cohort"},
                {"role": "assistant", "content": result["answer"]},
            ],
        },
    )
    events = parse(second)
    assert any(e.get("name") == "compare_entities" for e in events)
    assert "does not establish matching gene-specific phenotypes" in events[-1]["answer"]
    assert events[-1]["mode"] == "rehearsal"
    assert events[-1]["cohort"]["visualization"]["component"] == "VirtualLabBarChart"


def test_no_silent_fallback_on_aws_failure(graph, lab):
    class UnavailableModel:
        def converse(self, **kwargs):
            raise RuntimeError("Model access denied")

    agent = Agent(Registry(graph, lab, {}), model=UnavailableModel(), mode="bedrock")
    import pytest

    with pytest.raises(RuntimeError, match="Model access denied"):
        list(agent.run(ChatRequest(prompt="TP53")))


def test_tool_failure_stays_visible_without_cohort_claim(graph):
    agent = Agent(Registry(graph, None, {}), mode="rehearsal", backend="local")
    events = list(agent.run(ChatRequest(prompt="TP53 evidence and cohort")))
    assert any(e.get("status") == "error" and e.get("name") == "summarize_synthetic_cohort" for e in events)
    assert events[-1]["cohort"] is None
    assert "no cohort result is reported" in events[-1]["answer"]
