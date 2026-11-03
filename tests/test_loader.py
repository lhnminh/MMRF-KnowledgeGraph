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
