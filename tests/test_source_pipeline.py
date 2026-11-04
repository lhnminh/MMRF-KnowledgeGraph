import importlib
import importlib.util
import json
import sys
from io import BytesIO
from pathlib import Path
from types import ModuleType
from urllib.parse import parse_qs, urlsplit

import pytest
from botocore.exceptions import ClientError

ROOT = Path(__file__).resolve().parents[1]
spec = importlib.util.spec_from_file_location(
    "source_pipeline", ROOT / "source_pipeline/__init__.py",
    submodule_search_locations=[str(ROOT / "source_pipeline")],
)
package = importlib.util.module_from_spec(spec)
sys.modules["source_pipeline"] = package
spec.loader.exec_module(package)
common = importlib.import_module("source_pipeline.common")
fetch = importlib.import_module("source_pipeline.fetch")
ingest = importlib.import_module("source_pipeline.ingest")
spec = importlib.util.spec_from_file_location("pipeline_legacy_loader", ROOT / "ingestion/load_neo4j.py")
loader = importlib.util.module_from_spec(spec)
spec.loader.exec_module(loader)
spec = importlib.util.spec_from_file_location("pipeline_cli", ROOT / "scripts/run_source_pipeline.py")
cli = importlib.util.module_from_spec(spec)
spec.loader.exec_module(cli)

RUN_ID = "ad5ac2f0-98ae-4dca-aaff-a28a259e6f18"
REVISION = "b1ca0b232b815a21b4ba95a62828c9979e9a49e7"
BUCKET = "sandbox-ingest"
YAML = b"""
name: Multiple Myeloma
disease_term: {term: {id: 'MONDO:0009693'}}
pathophysiology:
  - name: A
    evidence: [{reference: 'PMID:1', snippet: first, explanation: commentary}]
    downstream: [{target: B, evidence: [{reference: 'PMID:2'}, {reference: 'PMID:3'}]}]
  - name: B
phenotypes:
  - name: Anemia
    phenotype_term: {term: {id: 'HP:0001903', label: Anemia}}
genetic:
  - name: TP53 Deletion
    gene_term: {term: {id: 'hgnc:11998', label: TP53}}
treatments:
  - name: Recorded therapy
    treatment_term:
      term: {id: 'NCIT:1'}
      therapeutic_agent: [{preferred_term: example, term: {id: 'CHEBI:1'}}]
"""


class FakeS3:
    def __init__(self):
        self.objects = {}
        self.puts = []

    def list_objects_v2(self, Bucket, Prefix, MaxKeys):
        assert Bucket == BUCKET and Prefix.startswith(common.PREFIX) and MaxKeys == 1
        return {"Contents": [{"Key": key} for key in sorted(self.objects) if key.startswith(Prefix)][:MaxKeys]}

    def get_object(self, Bucket, Key):
        if Key not in self.objects:
            raise ClientError({"Error": {"Code": "NoSuchKey"}}, "GetObject")
        return {"Body": BytesIO(self.objects[Key])}

    def put_object(self, **kwargs):
        key = kwargs["Key"]
        assert kwargs["Bucket"] == BUCKET and kwargs["ServerSideEncryption"] == "AES256"
        if kwargs.get("IfNoneMatch") == "*" and key in self.objects:
            raise ClientError({"Error": {"Code": "PreconditionFailed"}}, "PutObject")
        self.objects[key] = kwargs["Body"]
        self.puts.append(key)
        return {}


def getter(*, changed_version=False, total=1, negated=False, category="biolink:Disease"):
    def get(url):
        parsed = urlsplit(url)
        if parsed.netloc == "raw.githubusercontent.com":
            assert REVISION in parsed.path and parsed.path.endswith("Multiple_Myeloma.yaml")
            return YAML
        assert parsed.netloc == "api.monarchinitiative.org"
        if parsed.path.endswith("/version"):
            get.versions += 1
            version = "changed" if changed_version and get.versions > 1 else "2026-10-07"
            return common.encode({"monarch_kg_version": version})
        if "/entity/" in parsed.path:
            return common.encode({"id": common.DISEASE, "name": "Multiple Myeloma"})
        params = parse_qs(parsed.query)
        assert params["subject"] == [common.DISEASE]
        return common.encode({"total": total, "items": [{
            "id": "source:association-1", "subject": common.DISEASE, "object": "HP:0001903",
            "subject_category": category, "predicate": "biolink:has_phenotype", "object_label": "Anemia",
            "object_category": "biolink:PhenotypicFeature", "publications": ["PMID:1", "PMID:2"],
            "negated": negated, "original_predicate": "original:has_phenotype",
        }]})

    get.versions = 0
    return get


@pytest.fixture
def snapshot():
    s3 = FakeS3()
    receipt = fetch.fetch_run(s3, BUCKET, RUN_ID, REVISION, getter())
    manifest, files = ingest.read_snapshot(s3, BUCKET, receipt)
    return s3, receipt, manifest, files


def test_fetch_commits_manifest_last_and_preserves_original_records(snapshot):
    s3, receipt, manifest, files = snapshot
    assert s3.puts[-1] == receipt["manifest_key"]
    assert manifest["sources"]["monarch"]["source_version"] == "2026-10-07"
    assert manifest["sources"]["dismech"]["source_version"] == REVISION
    assert files["dismech_mm.yaml"].encode() == YAML
    monarch = json.loads(files["monarch_mm.json"])
    assert monarch["associations"][0]["original_predicate"] == "original:has_phenotype"
    assert monarch["associations"][0]["publications"] == ["PMID:1", "PMID:2"]
    assert "monarch-associations-000.json" in manifest["files"]
    assert all(value["retrieved_at"] and value["source_url"] for value in manifest["sources"].values())


def test_replaying_committed_fetch_does_not_refetch_or_overwrite(snapshot):
    s3, receipt, _, _ = snapshot
    count = len(s3.puts)

    def fail(_):
        raise AssertionError("Committed snapshots must not be fetched again")

    assert fetch.fetch_run(s3, BUCKET, RUN_ID, REVISION, fail) == receipt
    assert len(s3.puts) == count


@pytest.mark.parametrize("options", [
    {"changed_version": True}, {"total": 101}, {"total": 0}, {"negated": True},
    {"category": "biolink:Protein"}, {"total": 2},
])
def test_bad_or_changing_sources_never_commit_a_snapshot(options):
    s3 = FakeS3()
    with pytest.raises(ValueError):
        fetch.fetch_run(s3, BUCKET, RUN_ID, REVISION, getter(**options))
    assert not s3.objects


def test_interrupted_upload_is_retried_under_a_new_artifact_path():
    s3 = FakeS3()
    original_put = s3.put_object

    def interrupt(**kwargs):
        if len(s3.puts) == 2:
            raise RuntimeError("Simulated interrupted upload")
        return original_put(**kwargs)

    s3.put_object = interrupt
    with pytest.raises(RuntimeError):
        fetch.fetch_run(s3, BUCKET, RUN_ID, REVISION, getter())
    partial = dict(s3.objects)
    assert not any(key.endswith("manifest.json") for key in partial)
    s3.put_object = original_put
    receipt = fetch.fetch_run(s3, BUCKET, RUN_ID, REVISION, getter())
    manifest, _ = ingest.read_snapshot(s3, BUCKET, receipt)
    assert all(s3.objects[key] == value for key, value in partial.items())
    assert not set(partial).intersection(item["key"] for item in manifest["files"].values())


@pytest.mark.parametrize("change", ["artifact", "receipt", "manifest", "other_bucket"])
def test_ingest_refuses_corruption_and_wrong_receipts_before_database_access(snapshot, change, monkeypatch):
    s3, receipt, manifest, _ = snapshot
    if change == "artifact":
        s3.objects[manifest["files"]["monarch-entity.json"]["key"]] += b"tampered"
    elif change == "receipt":
        receipt["manifest_sha256"] = "0" * 64
    elif change == "manifest":
        s3.objects[receipt["manifest_key"]] += b"tampered"
    else:
        receipt["bucket"] = "another-bucket"
    monkeypatch.setenv("INGEST_BUCKET", BUCKET)
    import boto3
    monkeypatch.setattr(boto3, "client", lambda name: s3)
    backend = ModuleType("handler")

    def fail():
        raise AssertionError("Database driver must not be accessed for unverified source files")

    backend.get_driver = fail
    monkeypatch.setitem(sys.modules, "handler", backend)
    monkeypatch.setitem(sys.modules, "load_neo4j", loader)
    with pytest.raises(ValueError):
        ingest.handler(receipt, None)


def test_manifest_rejects_keys_outside_its_run(snapshot):
    _, _, manifest, _ = snapshot
    manifest["files"]["monarch_mm.json"]["key"] = "raw/other-run/monarch_mm.json"
    with pytest.raises(ValueError, match="belong to this run"):
        common.validate_manifest(manifest, RUN_ID)


@pytest.mark.parametrize("extra_encoding", [False, True])
def test_cli_verifies_saved_result_as_an_object(snapshot, extra_encoding):
    s3, receipt, _, _ = snapshot
    output = {"status": "SUCCEEDED", "manifest_uri": f"s3://{BUCKET}/{receipt['manifest_key']}",
              "manifest_sha256": receipt["manifest_sha256"]}
    content = common.encode(output)
    if extra_encoding:
        content = common.encode(content.decode())
    s3.objects[receipt["manifest_key"].removesuffix("manifest.json") + "result.json"] = content
    if extra_encoding:
        with pytest.raises(ValueError, match="Stored S3 result"):
            cli.verify_saved_result(s3, output)
    else:
        cli.verify_saved_result(s3, output)


def test_monarch_descendants_require_explicit_source_closure():
    record = {"subject": "MONDO:0004819", "object": "MONDO:0005235"}
    assert not common.in_disease_subset(record)
    assert common.in_disease_subset({**record, "subject_closure": [common.DISEASE]})
    assert not common.in_disease_subset({**record, "subject_closure": "prefix:" + common.DISEASE})


class FakeResult(list):
    def single(self):
        return {"c": 8}


class FakeDriver:
    def __init__(self, corrupt_counts=False):
        self.calls = []
        self.committed = False
        self.corrupt_counts = corrupt_counts
        self.write_transactions = 0

    def session(self):
        return self

    def __enter__(self):
        return self

    def __exit__(self, *_):
        return False

    def execute_write(self, callback):
        self.write_transactions += 1
        callback(self)
        self.committed = True

    def execute_read(self, callback):
        return callback(self)

    def run(self, query, **parameters):
        self.calls.append((query, parameters))
        if query == ingest.VALIDATE:
            return FakeResult([
                {"source": "monarch", "version": "2026-10-07", "relationships": 0 if self.corrupt_counts else 1},
                {"source": "dismech", "version": REVISION, "relationships": 7},
            ])
        return FakeResult()


def test_sources_and_provenance_are_written_in_one_transaction_then_read_back(snapshot):
    _, receipt, manifest, files = snapshot
    driver = FakeDriver()
    result = ingest.ingest_snapshot(driver, manifest, files, BUCKET, receipt["manifest_sha256"], loader)
    assert driver.write_transactions == 1 and driver.committed
    assert result["status"] == "SUCCEEDED" and result["expected_relationships"] == 8
    stamps = [params for query, params in driver.calls if "SET r.ingestion_run_id" in query]
    assert len(stamps) == 8 and all(params["run_id"] == RUN_ID for params in stamps)
    assert all(params["raw_s3_uri"].startswith("s3://" + BUCKET) for params in stamps)
    assert any("PMID:3" in params["evidence_json"] for params in stamps)
    assert len([query for query, _ in driver.calls if query == ingest.VALIDATE]) == 2


def test_wrong_counts_abort_before_transaction_commit(snapshot):
    _, receipt, manifest, files = snapshot
    driver = FakeDriver(corrupt_counts=True)
    with pytest.raises(ValueError, match="validation"):
        ingest.ingest_snapshot(driver, manifest, files, BUCKET, receipt["manifest_sha256"], loader)
    assert not driver.committed


def test_parsed_versions_must_match_manifest_before_writing(snapshot):
    _, receipt, manifest, files = snapshot
    manifest["sources"]["monarch"]["source_version"] = "different"
    driver = FakeDriver()
    with pytest.raises(ValueError, match="versions"):
        ingest.ingest_snapshot(driver, manifest, files, BUCKET, receipt["manifest_sha256"], loader)
    assert not driver.calls


@pytest.mark.parametrize("identifier", ["../outside", "raw/anything", "", "not-a-uuid"])
def test_run_ids_cannot_select_arbitrary_s3_prefixes(identifier):
    with pytest.raises(ValueError):
        common.run_prefix(identifier)


def completed(snapshot):
    return {
        "status": "SUCCEEDED", "run_id": snapshot["run_id"],
        "manifest_sha256": snapshot["manifest_sha256"],
        "manifest_uri": f"s3://{BUCKET}/{snapshot['manifest_key']}",
        "validation": [{"source": "monarch", "relationships": 1}],
    }


def test_direct_pipeline_invokes_only_after_snapshot_commit_and_saves_result():
    s3 = FakeS3()
    invoked = []

    def invoke(snapshot):
        manifest, files = ingest.read_snapshot(s3, BUCKET, snapshot)
        assert manifest["run_id"] == RUN_ID and files["dismech_mm.yaml"]
        assert s3.puts[-1] == snapshot["manifest_key"]
        invoked.append(snapshot)
        return completed(snapshot)

    output = fetch.run_pipeline(s3, BUCKET, RUN_ID, REVISION, invoke, getter())
    assert len(invoked) == 1
    assert s3.puts[-1] == common.run_prefix(RUN_ID) + "result.json"
    cli.verify_saved_result(s3, output)

    def fail(_):
        raise AssertionError("A completed run must not refetch or reinvoke ingestion")

    puts = list(s3.puts)
    assert fetch.run_pipeline(s3, BUCKET, RUN_ID, REVISION, fail, fail) == output
    assert s3.puts == puts


def test_scheduler_retries_reuse_a_run_and_each_occurrence_gets_a_fresh_one(monkeypatch):
    import boto3

    monkeypatch.setattr(boto3, "client", lambda *args, **kwargs: object())
    monkeypatch.setenv("INGEST_BUCKET", BUCKET)
    monkeypatch.setenv("DISMECH_REVISION", REVISION)
    monkeypatch.setattr(fetch, "run_pipeline", lambda s3, bucket, run_id, revision, invoke: run_id)
    schedule = "arn:aws:scheduler:us-west-2:548574235556:schedule/default/mmrf-kg-source-pipeline-daily"
    event = {"id": schedule + "#2026-11-05T02:00:00Z", "source": "aws.scheduler"}
    first = fetch.handler(event, None)
    retry = fetch.handler({**event, "attempt_number": 2}, None)
    next_day = fetch.handler({**event, "id": schedule + "#2026-11-06T02:00:00Z"}, None)
    other_schedule = fetch.handler({**event, "id": schedule + "-other#2026-11-05T02:00:00Z"}, None)
    assert str(fetch.uuid.UUID(first)) == first
    assert first == retry and len({first, next_day, other_schedule}) == 3
    assert fetch.handler({**event, "run_id": RUN_ID}, None) == RUN_ID


def test_failed_direct_invocation_can_retry_the_same_snapshot(snapshot):
    s3, receipt, _, _ = snapshot

    def timeout(_):
        raise TimeoutError("Simulated uncertain invocation outcome")

    with pytest.raises(TimeoutError):
        fetch.run_pipeline(s3, BUCKET, RUN_ID, REVISION, timeout)
    failed = json.loads(s3.objects[common.run_prefix(RUN_ID) + "result.json"])
    assert failed["status"] == "FAILED" and failed["error_type"] == "TimeoutError"

    def no_download(_):
        raise AssertionError("Retry must reuse the committed snapshot")

    output = fetch.run_pipeline(s3, BUCKET, RUN_ID, REVISION, completed, no_download)
    assert output["manifest_sha256"] == receipt["manifest_sha256"]
    cli.verify_saved_result(s3, output)


@pytest.mark.parametrize("kind", ["function_error", "failed_status", "wrong_run", "wrong_hash"])
def test_direct_lambda_errors_or_mismatched_receipts_are_rejected(snapshot, kind):
    _, receipt, _, _ = snapshot
    output = completed(receipt)
    response = {}
    if kind == "function_error":
        response["FunctionError"] = "Unhandled"
    elif kind == "failed_status":
        output["status"] = "FAILED"
    elif kind == "wrong_run":
        output["run_id"] = "other"
    else:
        output["manifest_sha256"] = "0" * 64

    class Client:
        def invoke(self, **kwargs):
            assert kwargs["InvocationType"] == "RequestResponse"
            assert kwargs["FunctionName"] == "private-ingest"
            assert json.loads(kwargs["Payload"]) == receipt
            return {**response, "Payload": BytesIO(common.encode(output))}

    with pytest.raises((RuntimeError, ValueError)):
        fetch.invoke_ingestion(Client(), "private-ingest", receipt)


def test_new_upstream_relationship_shapes_keep_loader_identities(snapshot):
    _, _, _, files = snapshot
    monarch, dismech, _, _ = loader.parse_sources(
        files["monarch_mm.json"], files["dismech_mm.yaml"], files["dismech_mm.yaml.provenance.json"]
    )
    monarch["associations"] = [{
        "subject": "HGNC:11998", "object": common.DISEASE, "subject_category": "biolink:Gene",
        "predicate": "biolink:gene_associated_with_condition",
    }]
    dismech["has_subtypes"] = [{"name": "Example subtype"}]
    dismech["genetic"][0]["subtype"] = "Example subtype"
    dismech["treatments"][0]["target_mechanisms"] = [{"target": "A"}]
    records = ingest.expected_records(monarch, dismech)
    subtype = loader.subtype_id(common.DISEASE, "Example subtype")
    assert ("association", "HGNC:11998", "biolink:gene_associated_with_condition", common.DISEASE,
            "monarch") in records
    assert ("association", common.DISEASE, "dismech:has_subtype", subtype, "dismech") in records
    assert ("driver", subtype, "HAS_DRIVER_GENE", "HGNC:11998", "dismech") in records
    assert ("target", "dismech:treatment:Recorded therapy", "TARGETS_MECHANISM", "A", "dismech") in records
    driver = FakeDriver()
    loader.load_monarch(driver, monarch, "test")
    loader.load_dismech(driver, dismech, "test")
    assert any(params.get("subtype") == subtype for _, params in driver.calls)
    assert any(params.get("mechanism") == "A" and "TARGETS_MECHANISM" in query
               for query, params in driver.calls)
