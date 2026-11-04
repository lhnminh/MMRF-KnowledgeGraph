"""Internet-connected Lambda: fixed public endpoints -> immutable S3 snapshot + commit manifest."""

import json
import logging
import os
import re
import time
import uuid
from datetime import datetime, timezone
from urllib.error import HTTPError, URLError
from urllib.parse import urlencode
from urllib.request import Request, urlopen

from source_pipeline.common import (
    DISEASE,
    MAX_ASSOCIATIONS,
    MAX_OBJECT_BYTES,
    digest,
    encode,
    in_disease_subset,
    read_object,
    run_prefix,
    validate_manifest,
)

API = "https://api.monarchinitiative.org/v3/api"
DISMECH = "https://raw.githubusercontent.com/monarch-initiative/dismech/{revision}/kb/disorders/Multiple_Myeloma.yaml"
LOGGER = logging.getLogger(__name__)


class SourceFetchError(RuntimeError):
    pass


def download(url):
    for attempt in range(3):
        try:
            request = Request(url, headers={"User-Agent": "ORION-hackathon-source-pipeline/1.0"})
            with urlopen(request, timeout=25) as response:
                content = response.read(MAX_OBJECT_BYTES + 1)
            if len(content) > MAX_OBJECT_BYTES:
                raise ValueError("Public source response exceeds the small-refresh limit")
            return content
        except HTTPError as error:
            if error.code not in {429, 500, 502, 503, 504}:
                raise SourceFetchError(f"Public source returned HTTP {error.code}") from None
        except (URLError, TimeoutError):
            pass
        if attempt < 2:
            time.sleep(2 ** attempt)
    raise SourceFetchError("Public source did not respond successfully after three attempts")


def collect(revision, get=download):
    if not re.fullmatch(r"[0-9a-f]{40}", revision):
        raise ValueError("DisMech must use an immutable 40-character commit SHA")
    files, calls = {}, []

    def fetch_json(name, url):
        content = get(url)
        files[name] = content
        calls.append({"url": url, "artifact": name})
        return json.loads(content)

    before = fetch_json("monarch-version-before.json", API + "/version")
    version = before.get("monarch_kg_version")
    if not isinstance(version, str) or not version:
        raise ValueError("Monarch did not report a source version")
    entity = fetch_json("monarch-entity.json", API + "/entity/" + DISEASE)
    if entity.get("id") != DISEASE or not entity.get("name"):
        raise ValueError("Monarch returned an unexpected disease entity")
    associations, seen, total = [], set(), None
    offset = 0
    for page_number in range(4):
        url = API + "/association?" + urlencode({"subject": DISEASE, "limit": 100, "offset": offset})
        page = fetch_json(f"monarch-associations-{page_number:03}.json", url)
        if type(page.get("total")) is not int or not 0 < page["total"] <= MAX_ASSOCIATIONS:
            raise ValueError("Monarch subset is empty or exceeds the incremental-refresh bound; review before loading")
        if total is not None and total != page["total"]:
            raise ValueError("Monarch association count changed during retrieval")
        total = page["total"]
        items = page.get("items")
        if not isinstance(items, list) or not items:
            raise ValueError("Monarch pagination ended before the reported total")
        for record in items:
            identifier = record.get("id")
            if not identifier or identifier in seen:
                raise ValueError("Monarch returned missing or repeated association identifiers")
            seen.add(identifier)
            if not in_disease_subset(record):
                raise ValueError("Association is outside the Multiple Myeloma subset")
            if record.get("subject_category") not in {"biolink:Disease", "biolink:Gene"} or record.get("negated"):
                raise ValueError("Association semantics are unsupported by the current graph loader")
            associations.append(record)
        offset += len(items)
        if offset == total:
            break
        if offset > total:
            raise ValueError("Monarch returned more associations than its reported total")
    if len(associations) != total:
        raise ValueError("Monarch pagination exceeded the bounded request count")
    after = fetch_json("monarch-version-after.json", API + "/version")
    if after.get("monarch_kg_version") != version:
        raise ValueError("Monarch KG version changed during retrieval; no snapshot was committed")
    retrieved_at = datetime.now(timezone.utc).isoformat()
    files["monarch_mm.json"] = encode({"source_version": version, "entity": entity, "associations": associations})
    url = DISMECH.format(revision=revision)
    files["dismech_mm.yaml"] = get(url)
    if not files["dismech_mm.yaml"]:
        raise ValueError("DisMech returned an empty record")
    dismech_retrieved_at = datetime.now(timezone.utc).isoformat()
    files["dismech_mm.yaml.provenance.json"] = encode({
        "source_revision": revision, "source_url": url, "retrieved_at": dismech_retrieved_at,
        "sha256": digest(files["dismech_mm.yaml"]), "redistribution_terms": "unconfirmed; sandbox-only evaluation",
    })
    sources = {
        "monarch": {
            "source_version": version, "source_url": API + "/association", "retrieved_at": retrieved_at,
            "association_count": len(associations), "calls": calls,
            "redistribution_terms": "Upstream dataset terms apply; no blanket KG license assumed",
        },
        "dismech": {
            "source_version": revision, "source_url": url, "retrieved_at": dismech_retrieved_at,
            "redistribution_terms": "unconfirmed; one pinned record for private sandbox evaluation only",
        },
    }
    return files, sources


def receipt(manifest, content, bucket):
    return {
        "run_id": manifest["run_id"], "bucket": bucket,
        "manifest_key": run_prefix(manifest["run_id"]) + "manifest.json", "manifest_sha256": digest(content),
        "source_versions": {key: value["source_version"] for key, value in manifest["sources"].items()},
    }


def fetch_run(s3, bucket, run_id, revision, get=download):
    prefix = run_prefix(run_id)
    manifest_key = prefix + "manifest.json"
    # Without unrestricted ListBucket, GetObject on a missing key is deliberately
    # indistinguishable from AccessDenied. List only this exact manifest prefix first.
    listing = s3.list_objects_v2(Bucket=bucket, Prefix=manifest_key, MaxKeys=1)
    if any(item["Key"] == manifest_key for item in listing.get("Contents", [])):
        existing = read_object(s3, bucket, manifest_key)
        manifest = json.loads(existing)
        validate_manifest(manifest, run_id)
        if manifest["sources"]["dismech"]["source_version"] != revision:
            raise ValueError("Committed run uses a different DisMech revision")
        return receipt(manifest, existing, bucket)

    files, sources = collect(revision, get)
    # A retry uses a new artifact path, leaving an interrupted attempt unchanged.
    artifact_prefix = prefix + "artifacts/" + str(uuid.uuid4()) + "/"
    descriptors = {}
    for name, content in files.items():
        if not content or len(content) > MAX_OBJECT_BYTES:
            raise ValueError("Source artifact is empty or too large")
        key = artifact_prefix + name
        s3.put_object(
            Bucket=bucket, Key=key, Body=content, ServerSideEncryption="AES256", IfNoneMatch="*",
            ContentType="application/json" if name.endswith(".json") else "application/yaml",
        )
        descriptors[name] = {"key": key, "sha256": digest(content), "bytes": len(content)}
    manifest = {
        "schema_version": "1", "run_id": run_id, "disease_id": DISEASE,
        "created_at": datetime.now(timezone.utc).isoformat(), "files": descriptors, "sources": sources,
    }
    validate_manifest(manifest, run_id)
    content = encode(manifest)
    # This is the commit point: ingestion never reads an incomplete download attempt.
    s3.put_object(Bucket=bucket, Key=manifest_key, Body=content, ServerSideEncryption="AES256",
                  ContentType="application/json", IfNoneMatch="*")
    return receipt(manifest, content, bucket)


def invoke_ingestion(client, function_name, snapshot):
    response = client.invoke(FunctionName=function_name, InvocationType="RequestResponse", Payload=encode(snapshot))
    with response["Payload"] as payload:
        result = json.loads(payload.read())
    if response.get("FunctionError") or result.get("status") != "SUCCEEDED":
        raise RuntimeError("Ingestion failed; inspect the ingestion Lambda logs for details")
    if result.get("run_id") != snapshot["run_id"] or result.get("manifest_sha256") != snapshot["manifest_sha256"]:
        raise ValueError("Ingestion result does not match the committed snapshot")
    return result


def run_pipeline(s3, bucket, run_id, revision, invoke, get=download):
    prefix = run_prefix(run_id)
    result_key = prefix + "result.json"
    existing = None
    listing = s3.list_objects_v2(Bucket=bucket, Prefix=result_key, MaxKeys=1)
    if any(item["Key"] == result_key for item in listing.get("Contents", [])):
        existing = json.loads(read_object(s3, bucket, result_key))

    def save(result):
        s3.put_object(Bucket=bucket, Key=result_key, Body=encode(result),
                      ContentType="application/json", ServerSideEncryption="AES256")

    try:
        snapshot = fetch_run(s3, bucket, run_id, revision, get)
        if isinstance(existing, dict) and existing.get("status") == "SUCCEEDED":
            if existing.get("run_id") != run_id or existing.get("manifest_sha256") != snapshot["manifest_sha256"]:
                raise ValueError("Saved result does not match the committed snapshot")
            return existing
        result = invoke(snapshot)
        if result.get("status") != "SUCCEEDED" or result.get("run_id") != run_id or result.get("manifest_sha256") != snapshot["manifest_sha256"]:
            raise ValueError("Ingestion result does not match the committed snapshot")
        save(result)
        return result
    except Exception as error:
        # A timeout may happen after a graph commit. Preserve that uncertainty and make
        # retrying the same run safe, rather than claiming a failed invocation rolled back.
        if not isinstance(existing, dict) or existing.get("status") != "SUCCEEDED":
            save({
                "status": "FAILED", "run_id": run_id,
                "completed_at": datetime.now(timezone.utc).isoformat(), "error_type": type(error).__name__,
                "message": "See Lambda logs. A failure may occur after commit; retry this run ID safely to confirm.",
            })
        LOGGER.exception("Source ingestion failed run_id=%s", run_id)
        raise


def handler(event, context):
    import boto3
    from botocore.config import Config

    event = event or {}
    # A future EventBridge event ID gives retries the same run identity; manual calls
    # can provide an explicit run_id. URLs, bucket and disease never come from the event.
    run_id = event.get("run_id")
    if not run_id:
        run_id = str(uuid.uuid5(uuid.NAMESPACE_URL, "orion-source:" + event["id"])) if event.get("id") else str(uuid.uuid4())
    client = boto3.client("lambda", config=Config(connect_timeout=5, read_timeout=310,
                                                 retries={"total_max_attempts": 1}))
    return run_pipeline(
        boto3.client("s3"), os.environ["INGEST_BUCKET"], run_id, os.environ["DISMECH_REVISION"],
        lambda snapshot: invoke_ingestion(client, os.environ["INGEST_FUNCTION_NAME"], snapshot),
    )
