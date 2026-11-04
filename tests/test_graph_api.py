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


def event(params=None, path="/api/graph", method="GET", headers=None):
    return {
        "requestContext": {"http": {"method": method}}, "rawPath": path,
        "headers": headers or {}, "queryStringParameters": params,
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
        result = table_result()
        if arguments["query"] != api.QUERY:
            result["table"]["rows"] = []
        return result

    monkeypatch.setattr(api, "invoke_tool", invoke)
    return calls


@pytest.mark.parametrize("headers", [{}, {"Authorization": "Bearer obsolete-token"}])
def test_graph_is_public_and_does_not_require_credentials(tool, headers):
    result = api.handler(event(headers=headers), None)
    assert result["statusCode"] == 200
    assert json.loads(result["body"])["graph"]["edges"]
    assert tool


@pytest.mark.parametrize("params", [
    {"query": "MATCH (n) RETURN n"}, {"tool": "kg_get_evidence"}, {"limit": "0"}, {"limit": "101"},
    {"limit": "1.5"}, {"view": "bogus"}, {"disease_id": "MONDO:other"}, {"q": "x" * 201},
    {"depth": "99"},
])
def test_rejects_invalid_inputs_without_querying(tool, params):
    assert api.handler(event(params), None)["statusCode"] == 400
    assert not tool


def test_graph_preserves_distinct_sources_and_citations(tool):
    result = api.handler(event(), None)
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
    assert tool == [
        ("kg_run_readonly_query", {"query": query, "parameters": {"disease_id": api.DISEASE}})
        for query in [api.QUERY, api.TARGET_QUERY.replace("{offset}", "0")]
    ]


@pytest.mark.parametrize("params, expected", [
    ({"source": "monarch"}, 1), ({"entity_type": "Gene"}, 1), ({"q": "tp53"}, 1),
    ({"view": "phenotypes"}, 2), ({"view": "genetics"}, 1), ({"view": "treatments"}, 0),
    ({"predicate": "biolink:has_phenotype", "source": "dismech"}, 1), ({"node_id": "HGNC:11998"}, 1),
    ({"q": "nothing recorded"}, 0), ({"view": "clinical"}, 2),
])
def test_filters_keep_endpoints_and_only_related_evidence(tool, params, expected):
    data = json.loads(api.handler(event(params), None)["body"])
    edges, nodes = data["graph"]["edges"], data["graph"]["nodes"]
    assert len(edges) == expected
    assert {n["id"] for n in nodes} == {e[key] for e in edges for key in ("source", "target")}
    assert {e["id"] for e in data["evidence"]} == {rid for e in edges for rid in e["evidence_ids"]}
    assert data["facets"]["sources"] == ["dismech", "monarch"]


def test_limit_and_upstream_truncation_are_explicit(tool, monkeypatch):
    data = api.get_graph({"limit": "1"})
    assert len(data["graph"]["edges"]) == 1 and data["meta"]["truncated"] is True
    result = table_result()
    result["limitations"] = ["Results are cut off at 100 rows."]
    def invoke(*args, **kwargs):
        if kwargs["query"] == api.QUERY:
            return result
        return {"table": {"columns": ["a", "r", "b"], "rows": []}}

    monkeypatch.setattr(api, "invoke_tool", invoke)
    assert api.get_graph({})["meta"]["truncated"] is True


def test_upstream_failure_is_normalized_and_does_not_leak(monkeypatch):
    def fail(*args, **kwargs):
        raise RuntimeError("sensitive infrastructure details")

    monkeypatch.setattr(api, "invoke_tool", fail)
    response = api.handler(event(), SimpleNamespace(aws_request_id="request-1"))
    assert response["statusCode"] == 502
    assert "sensitive" not in response["body"]
    assert json.loads(response["body"])["request_id"] == "request-1"


def test_only_allowed_paths_and_methods_are_available(tool):
    assert api.handler(event(path="/api/query"), None)["statusCode"] == 404
    assert api.handler(event(method="POST"), None)["statusCode"] == 405
    assert api.handler(event(path="/"), None)["statusCode"] == 200
    assert api.handler(event(path="/assets/../../handler.py"), None)["statusCode"] == 404
    assert not tool


def target_rows(count):
    return [[
        {"labels": ["Entity"], "properties": {
            "id": "CHEBI:synthetic-drug", "label": "Synthetic drug", "category": "biolink:Drug",
        }},
        {"type": "TARGETS_GENE", "properties": {
            "source": "opentargets", "source_version": "26.09", "chembl_id": "CHEMBL123",
            "action_type": "INHIBITOR", "mechanism_of_action": "Synthetic inhibition",
            "references": "FDA:https://example.org/label;DailyMed:https://example.org/drug",
        }},
        {"labels": ["Entity"], "properties": {
            "id": f"HGNC:{900000 + index}", "label": f"TEST{index}", "category": "biolink:Gene",
        }},
    ] for index in range(count)]


def paged_tool(monkeypatch, count):
    calls = []
    rows = target_rows(count)

    def invoke(name, **arguments):
        calls.append(arguments)
        if arguments["query"] == api.QUERY:
            return table_result()
        offset = next(page * 100 for page in range(api.MAX_TARGET_PAGES)
                      if arguments["query"] == api.TARGET_QUERY.replace("{offset}", str(page * 100)))
        return {"table": {"columns": ["a", "r", "b"], "rows": rows[offset:offset + 100]},
                "limitations": ["Results are cut off at 100 rows."] if count > offset + 100 else [],
                "trace": {"request_id": f"page-{offset}", "tool_calls": [{"tool": name}]}}

    monkeypatch.setattr(api, "invoke_tool", invoke)
    return calls


def test_open_targets_is_a_source_and_treatment_view_includes_its_targets(monkeypatch):
    paged_tool(monkeypatch, 1)
    graph = api.get_graph({"view": "treatments", "source": "opentargets"})
    assert graph["facets"]["sources"] == ["dismech", "monarch", "opentargets"]
    assert graph["meta"]["maximum_depth"] == 3
    assert len(graph["graph"]["edges"]) == 1
    edge = graph["graph"]["edges"][0]
    assert edge["source_dataset"] == "opentargets" and edge["source_version"] == "26.09"
    assert edge["relationship_type"] == "TARGETS_GENE" and edge["action_type"] == "INHIBITOR"
    assert edge["source_url"] == "https://platform.opentargets.org/drug/CHEMBL123"
    assert {ref["url"] for ref in graph["evidence"]} == {"https://example.org/label", "https://example.org/drug"}
    assert all(source["url"] for node in graph["graph"]["nodes"] for source in node["sources"])


def test_target_pagination_keeps_later_genes_searchable_and_preserves_traces(monkeypatch):
    calls = paged_tool(monkeypatch, 144)
    graph = api.get_graph({"source": "opentargets", "q": "TEST143"})
    assert len(calls) == 3
    assert all(call["parameters"] == {"disease_id": api.DISEASE} for call in calls)
    assert graph["meta"]["total_edges"] == 147
    assert graph["meta"]["truncated"] is False
    assert graph["graph"]["edges"][0]["target"] == "HGNC:900143"
    assert graph["trace"]["upstream_request_ids"] == ["upstream-request", "page-0", "page-100"]
    assert len(graph["trace"]["tool_calls"]) == 2


def test_target_fetch_and_display_limits_remain_bounded(monkeypatch):
    calls = paged_tool(monkeypatch, 250)
    graph = api.get_graph({"source": "opentargets"})
    assert len(calls) == 3
    assert graph["meta"]["total_edges"] == 203
    assert len(graph["graph"]["edges"]) == 100 and graph["meta"]["truncated"] is True
    assert any("Drug-target results are cut off at 200" in text for text in graph["limitations"])


def test_wrong_target_response_fails_without_returning_partial_data(monkeypatch):
    monkeypatch.setattr(api, "invoke_tool", lambda *args, **kwargs: table_result())
    result = api.handler(event(), None)
    assert result["statusCode"] == 502 and "graph" not in json.loads(result["body"])
