import hashlib
import importlib.util
import json
from pathlib import Path
from types import SimpleNamespace

import pytest

spec = importlib.util.spec_from_file_location(
    "graph_api_handler", Path(__file__).resolve().parents[1] / "graph_api" / "handler.py"
)
api = importlib.util.module_from_spec(spec)
spec.loader.exec_module(api)


@pytest.fixture
def token(monkeypatch):
    value = "test-access-token-that-is-long-enough"
    monkeypatch.setenv("ACCESS_TOKEN_SHA256", hashlib.sha256(value.encode()).hexdigest())
    return value


def event(token=None, params=None, path="/api/graph", method="GET"):
    return {
        "requestContext": {"http": {"method": method}}, "rawPath": path,
        "headers": {"Authorization": f"Bearer {token}"} if token else {}, "queryStringParameters": params,
    }


def table_result():
    disease = {"labels": ["Disease"], "properties": {
        "id": api.DISEASE, "name": "Multiple Myeloma", "label": "plasma cell myeloma",
        "source": "monarch", "source_version": "2026-10-07",
    }}

    def row(target_id, label, category, source, predicate, **metadata):
        return [disease, {"type": "ASSOCIATION", "properties": {
            "source": source, "source_version": "a" * 40 if source == "dismech" else "2026-10-07",
            "predicate": predicate, "evidence_pmid": "PMID:123", "evidence_snippet": "PRIVATE ARTICLE TEXT",
            "description": "PRIVATE CURATOR TEXT", **metadata,
        }}, {"labels": ["Entity"], "properties": {"id": target_id, "label": label, "category": category}}]

    return {
        "result_type": "table", "table": {"columns": ["a", "r", "b"], "rows": [
            row("HP:0001903", "Anemia", "biolink:PhenotypicFeature", "monarch", "biolink:has_phenotype"),
            row("HP:0001903", "Anemia", "biolink:PhenotypicFeature", "dismech", "biolink:has_phenotype"),
            row("HGNC:11998", "TP53", "biolink:Gene", "dismech", "dismech:has_genetic_factor",
                review_status="reviewed", confidence=0.8, retrieved_at="2026-11-04", synthetic=False),
        ]}, "limitations": [], "trace": {"request_id": "upstream-request"},
    }


@pytest.fixture
def tool(monkeypatch):
    calls = []

    def invoke(name, **arguments):
        calls.append((name, arguments))
        return table_result()

    monkeypatch.setattr(api, "invoke_tool", invoke)
    return calls


def test_authentication_is_required_before_any_tool_call(token, tool):
    for supplied in [None, "wrong-but-long-enough-token", "ü" * 20]:
        assert api.handler(event(supplied), None)["statusCode"] == 401
    assert not tool


def test_missing_secret_fails_closed(monkeypatch, tool):
    monkeypatch.delenv("ACCESS_TOKEN_SHA256", raising=False)
    assert api.handler(event("x" * 32), None)["statusCode"] == 401
    assert not tool


@pytest.mark.parametrize("params", [
    {"query": "MATCH (n) RETURN n"}, {"tool": "kg_get_evidence"}, {"limit": "0"}, {"limit": "101"},
    {"limit": "1.5"}, {"view": "bogus"}, {"disease_id": "MONDO:other"}, {"q": "x" * 201},
    {"depth": "99"},
])
def test_rejects_invalid_inputs_without_querying(token, tool, params):
    assert api.handler(event(token, params), None)["statusCode"] == 400
    assert not tool


def test_graph_preserves_distinct_sources_and_citations(token, tool):
    result = api.handler(event(token), None)
    assert result["statusCode"] == 200
    data = json.loads(result["body"])
    assert len(data["graph"]["nodes"]) == 3
    assert len(data["graph"]["edges"]) == 3
    disease = next(n for n in data["graph"]["nodes"] if n["id"] == api.DISEASE)
    assert {p["source_dataset"] for p in disease["sources"]} == {"monarch", "dismech"}
    assert disease["label"] == "Multiple Myeloma" and disease["aliases"] == ["plasma cell myeloma"]
    assert all(p["source_version"] == "2026-10-07" for p in disease["sources"]
               if p["source_dataset"] == "monarch")
    assert all(e["url"] == "https://pubmed.ncbi.nlm.nih.gov/123/" for e in data["evidence"])
    gene_edge = next(e for e in data["graph"]["edges"] if e["target"] == "HGNC:11998")
    assert gene_edge["confidence"] == 0.8 and gene_edge["review_status"] == "reviewed"
    assert gene_edge["synthetic"] is False
    assert gene_edge["original_id"] is None
    assert gene_edge["tool_edge_id"] == f"dismech:{api.DISEASE}|dismech:has_genetic_factor|HGNC:11998"
    assert gene_edge["identifier_origin"] == "derived from recorded endpoints"
    assert "PRIVATE ARTICLE TEXT" not in result["body"] and "PRIVATE CURATOR TEXT" not in result["body"]
    assert tool == [("kg_run_readonly_query", {"query": api.QUERY, "parameters": {"disease_id": api.DISEASE}})]


@pytest.mark.parametrize("params, expected", [
    ({"source": "monarch"}, 1), ({"entity_type": "Gene"}, 1), ({"q": "tp53"}, 1),
    ({"view": "phenotypes"}, 2), ({"view": "genetics"}, 1), ({"view": "treatments"}, 0),
    ({"predicate": "biolink:has_phenotype", "source": "dismech"}, 1), ({"node_id": "HGNC:11998"}, 1),
    ({"q": "nothing recorded"}, 0), ({"view": "clinical"}, 2),
])
def test_filters_keep_endpoints_and_only_related_evidence(token, tool, params, expected):
    data = json.loads(api.handler(event(token, params), None)["body"])
    edges, nodes = data["graph"]["edges"], data["graph"]["nodes"]
    assert len(edges) == expected
    assert {n["id"] for n in nodes} == {e[key] for e in edges for key in ("source", "target")}
    assert {e["id"] for e in data["evidence"]} == {rid for e in edges for rid in e["evidence_ids"]}
    assert data["facets"]["sources"] == ["dismech", "monarch"]


def test_limit_and_upstream_truncation_are_explicit(token, tool, monkeypatch):
    data = api.get_graph({"limit": "1"})
    assert len(data["graph"]["edges"]) == 1 and data["meta"]["truncated"] is True
    result = table_result()
    result["limitations"] = ["Results are cut off at 100 rows."]
    monkeypatch.setattr(api, "invoke_tool", lambda *a, **k: result)
    assert api.get_graph({})["meta"]["truncated"] is True


def test_upstream_failure_is_normalized_and_does_not_leak(token, monkeypatch):
    def fail(*args, **kwargs):
        raise RuntimeError("sensitive infrastructure details")

    monkeypatch.setattr(api, "invoke_tool", fail)
    response = api.handler(event(token), SimpleNamespace(aws_request_id="request-1"))
    assert response["statusCode"] == 502
    assert "sensitive" not in response["body"]
    assert json.loads(response["body"])["request_id"] == "request-1"


def test_only_allowed_paths_and_methods_are_available(token, tool):
    assert api.handler(event(token, path="/api/query"), None)["statusCode"] == 404
    assert api.handler(event(token, method="POST"), None)["statusCode"] == 405
    assert api.handler(event(path="/"), None)["statusCode"] == 200
    assert api.handler(event(path="/assets/../../handler.py"), None)["statusCode"] == 404
    assert not tool
