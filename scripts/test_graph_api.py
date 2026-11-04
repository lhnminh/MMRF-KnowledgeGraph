"""Read-only live API checks. Retrieve the sandbox token from SSM without printing it."""

import argparse
import json
from urllib.parse import urlsplit

import boto3
import requests


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--url", required=True)
    parser.add_argument("--profile", default="mmrf-hackathon")
    parser.add_argument("--region", default="us-west-2")
    parser.add_argument("--token-parameter", default="/mmrf-kg-graph-api/access-token")
    args = parser.parse_args()
    url = args.url.rstrip("/")
    parsed = urlsplit(url)
    if parsed.scheme != "https" or not parsed.hostname or not parsed.hostname.endswith(
        f".execute-api.{args.region}.amazonaws.com"
    ) or parsed.username or parsed.password or parsed.query or parsed.fragment or parsed.path:
        parser.error("Use the HTTPS API Gateway base URL from the Terraform graph_url output.")
    session = boto3.Session(profile_name=args.profile, region_name=args.region)
    if session.client("sts").get_caller_identity()["Account"] != "548574235556":
        parser.error("Expected the hackathon sandbox account.")
    token = session.client("ssm").get_parameter(Name=args.token_parameter, WithDecryption=True)["Parameter"]["Value"]
    api = url + "/api/graph"
    unauthorized = requests.get(api, timeout=(3, 30), allow_redirects=False)
    assert unauthorized.status_code == 401, "Unauthenticated graph request was not rejected"
    headers = {"Authorization": "Bearer " + token}
    summaries = []
    for params in [{"view": "all"}, {"source": "monarch"}, {"view": "genetics", "q": "TP53"},
                   {"q": "no-recorded-node-8ef9c"}, {"limit": "1"}]:
        result = requests.get(api, headers=headers, params=params, timeout=(3, 30), allow_redirects=False)
        assert result.status_code == 200, f"Graph request failed: HTTP {result.status_code}"
        data = result.json()
        graph = data["graph"]
        node_ids = {node["id"] for node in graph["nodes"]}
        assert all(edge[end] in node_ids for edge in graph["edges"] for end in ["source", "target"])
        assert all("source_version" in edge and "source_dataset" in edge for edge in graph["edges"])
        if params.get("source"):
            assert all(edge["source_dataset"] == params["source"] for edge in graph["edges"])
        if params.get("q", "").startswith("no-recorded"):
            assert not graph["edges"] and data["limitations"]
        if params.get("limit"):
            assert len(graph["edges"]) <= int(params["limit"])
        summaries.append({"filters": params, "nodes": len(graph["nodes"]), "edges": len(graph["edges"])})
    rejected = requests.get(api, headers=headers, params={"query": "MATCH (n) RETURN n"},
                            timeout=(3, 30), allow_redirects=False)
    assert rejected.status_code == 400, "Caller-supplied query was not rejected"
    print(json.dumps({"status": "passed", "unauthenticated_status": 401, "query_input_status": 400,
                      "checks": summaries}, indent=2))


if __name__ == "__main__":
    main()
