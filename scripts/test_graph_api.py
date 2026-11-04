"""Read-only live checks of the public sandbox graph API; no AWS credentials are required."""

import argparse
import json
from urllib.parse import urlsplit

import requests


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--url", required=True)
    parser.add_argument("--region", default="us-west-2")
    args = parser.parse_args()
    url = args.url.rstrip("/")
    parsed = urlsplit(url)
    if parsed.scheme != "https" or not parsed.hostname or not parsed.hostname.endswith(
        f".execute-api.{args.region}.amazonaws.com"
    ) or parsed.username or parsed.password or parsed.query or parsed.fragment or parsed.path:
        parser.error("Use the HTTPS API Gateway base URL from the Terraform graph_url output.")
    api = url + "/api/graph"
    summaries = []
    for params in [{"view": "all"}, {"source": "monarch"}, {"source": "opentargets"},
                   {"view": "treatments", "source": "opentargets", "q": "bortezomib"},
                   {"view": "genetics", "q": "TP53"},
                   {"q": "no-recorded-node-8ef9c"}, {"limit": "1"}]:
        result = requests.get(api, params=params, timeout=(3, 30), allow_redirects=False)
        assert result.status_code == 200, f"Graph request failed: HTTP {result.status_code}"
        data = result.json()
        graph = data["graph"]
        node_ids = {node["id"] for node in graph["nodes"]}
        assert all(edge[end] in node_ids for edge in graph["edges"] for end in ["source", "target"])
        assert all("source_version" in edge and "source_dataset" in edge for edge in graph["edges"])
        assert "opentargets" in data["facets"]["sources"], "Open Targets is missing from the source selector"
        if params.get("source"):
            assert all(edge["source_dataset"] == params["source"] for edge in graph["edges"])
        if params.get("source") == "opentargets":
            assert graph["edges"], "No recorded Open Targets drug targets were returned"
            assert all(edge["relationship_type"] == "TARGETS_GENE" for edge in graph["edges"])
            assert data["evidence"] and all(edge["source_url"] for edge in graph["edges"])
        if params.get("q", "").startswith("no-recorded"):
            assert not graph["edges"] and data["limitations"]
        if params.get("limit"):
            assert len(graph["edges"]) <= int(params["limit"])
        summaries.append({"filters": params, "nodes": len(graph["nodes"]), "edges": len(graph["edges"])})
    rejected = requests.get(api, params={"query": "MATCH (n) RETURN n"},
                            timeout=(3, 30), allow_redirects=False)
    assert rejected.status_code == 400, "Caller-supplied query was not rejected"
    print(json.dumps({"status": "passed", "public_access": True, "query_input_status": 400,
                      "checks": summaries}, indent=2))


if __name__ == "__main__":
    main()
