"""Smoke test: SigV4-sign MCP JSON-RPC calls to the kg-graph Gateway and print the results.

Confirms the Gateway itself (not just the Lambda behind it) is reachable, as another team's agent would call it.
"""
import argparse
import json

import boto3
import requests
from botocore.auth import SigV4Auth
from botocore.awsrequest import AWSRequest

REGION = "us-west-2"


def signed_post(gateway_url: str, body: dict) -> requests.Response:
    data = json.dumps(body)
    request = AWSRequest(method="POST", url=gateway_url, data=data, headers={
        "Content-Type": "application/json",
        "Accept": "application/json, text/event-stream",
    })
    credentials = boto3.Session().get_credentials().get_frozen_credentials()
    SigV4Auth(credentials, "bedrock-agentcore", REGION).add_auth(request)
    return requests.post(gateway_url, data=data, headers=dict(request.headers))


def run_smoke_test(gateway_url: str) -> None:
    init_resp = signed_post(gateway_url, {
        "jsonrpc": "2.0", "id": 1, "method": "initialize",
        "params": {
            "protocolVersion": "2025-06-18",
            "capabilities": {},
            "clientInfo": {"name": "test-client", "version": "0.1"},
        },
    })
    print("initialize:", init_resp.status_code, init_resp.text[:2000])

    list_resp = signed_post(gateway_url, {"jsonrpc": "2.0", "id": 2, "method": "tools/list", "params": {}})
    print("\ntools/list:", list_resp.status_code, list_resp.text[:4000])

    call_resp = signed_post(gateway_url, {
        "jsonrpc": "2.0", "id": 3, "method": "tools/call",
        "params": {"name": "kg-graph___kg_search_entities", "arguments": {"text": "myeloma"}},
    })
    print("\ntools/call:", call_resp.status_code, call_resp.text[:4000])


if __name__ == "__main__":
    p = argparse.ArgumentParser(description=__doc__)
    p.add_argument("--gateway-url", help="Defaults to `terraform output -raw gateway_url`")
    args = p.parse_args()

    gateway_url = args.gateway_url
    if not gateway_url:
        import subprocess
        gateway_url = subprocess.run(
            ["terraform", "-chdir=terraform", "output", "-raw", "gateway_url"],
            capture_output=True, text=True, check=True,
        ).stdout.strip()

    run_smoke_test(gateway_url)
