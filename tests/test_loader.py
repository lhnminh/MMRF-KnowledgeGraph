import importlib.util

import pytest

from mmrf_knowledgegraph.etl import ROOT

spec = importlib.util.spec_from_file_location("orion_utility", ROOT / "terraform/utility.py")
utility = importlib.util.module_from_spec(spec)
spec.loader.exec_module(utility)


def test_loader_uses_fixed_bucket_role_region_and_opencypher(monkeypatch):
    class Client:
        def start_loader_job(self, **kwargs):
            self.arguments = kwargs
            return {"payload": {"loadId": "test-load"}}

    client = Client()
    monkeypatch.setenv("NEPTUNE_ENDPOINT", "https://neptune.invalid:8182")
    monkeypatch.setenv("GRAPH_BUCKET", "synthetic-test-bucket")
    monkeypatch.setenv("LOADER_ROLE_ARN", "arn:aws:iam::123456789012:role/test-loader")
    monkeypatch.setenv("AWS_REGION", "us-east-1")
    monkeypatch.setattr(utility.boto3, "client", lambda *args, **kwargs: client)
    result = utility.handler({"action": "load", "version": "a" * 24, "bucket": "outside"}, None)
    assert result["payload"]["loadId"] == "test-load"
    assert client.arguments["source"] == "s3://synthetic-test-bucket/graph/" + "a" * 24 + "/"
    assert client.arguments["format"] == "opencypher"
    assert client.arguments["s3BucketRegion"] == "us-east-1"
    assert client.arguments["failOnError"] is True
    assert client.arguments["updateSingleCardinalityProperties"] is True
    with pytest.raises(ValueError, match="content hash"):
        utility.handler({"action": "load", "version": "../arbitrary"}, None)


def test_neptune_sdk_request_matches_aws_model():
    import boto3
    from botocore.stub import Stubber

    from mmrf_knowledgegraph.graph import Neptune

    client = boto3.client(
        "neptunedata",
        endpoint_url="https://neptune.invalid:8182",
        region_name="us-east-1",
        aws_access_key_id="synthetic-test-only",
        aws_secret_access_key="synthetic-test-only",
    )
    graph = Neptune("https://neptune.invalid:8182", client)
    with Stubber(client) as stub:
        stub.add_response(
            "execute_open_cypher_query",
            {
                "results": [
                    {
                        "id": "HGNC:11998",
                        "name": "TP53",
                        "entity_type": "Gene",
                        "aliases_json": '["NCBIGene:7157"]',
                        "provenance_json": "[]",
                    }
                ]
            },
        )
        nodes = graph.search("TP53", ["Gene"])
        assert nodes[0]["aliases"] == ["NCBIGene:7157"]
        stub.assert_no_pending_responses()
