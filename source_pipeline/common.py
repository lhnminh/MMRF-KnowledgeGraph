import hashlib
import json
import re

DISEASE = "MONDO:0009693"
PREFIX = "raw/source-pipeline/runs/"
MAX_OBJECT_BYTES = 4 * 1024 * 1024
MAX_ASSOCIATIONS = 100
MAX_EDGES = 300
DATA_FILES = {"monarch_mm.json", "dismech_mm.yaml", "dismech_mm.yaml.provenance.json"}


def in_disease_subset(record):
    if DISEASE in (record.get("subject"), record.get("object")):
        return True
    # Monarch's subject filter includes ontology descendants. Retain only records
    # whose source-supplied closure explicitly contains the requested disease.
    return any(isinstance(record.get(key), list) and DISEASE in record[key]
               for key in ("subject_closure", "object_closure"))


def run_prefix(run_id):
    if not isinstance(run_id, str) or not re.fullmatch(
        r"[0-9a-f]{8}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{12}", run_id
    ):
        raise ValueError("run_id must be a lowercase UUID")
    return PREFIX + run_id + "/"


def encode(data):
    return json.dumps(data, ensure_ascii=False, sort_keys=True, separators=(",", ":")).encode()


def digest(content):
    return hashlib.sha256(content).hexdigest()


def read_object(s3, bucket, key):
    response = s3.get_object(Bucket=bucket, Key=key)
    with response["Body"] as body:
        content = body.read(MAX_OBJECT_BYTES + 1)
    if len(content) > MAX_OBJECT_BYTES:
        raise ValueError("Source object exceeds the small-refresh size limit")
    return content


def validate_manifest(manifest, run_id):
    prefix = run_prefix(run_id)
    if manifest.get("schema_version") != "1" or manifest.get("run_id") != run_id:
        raise ValueError("Manifest schema or run identity is invalid")
    if manifest.get("disease_id") != DISEASE or set(manifest.get("sources", {})) != {"monarch", "dismech"}:
        raise ValueError("Only the configured Multiple Myeloma sources are supported")
    files = manifest.get("files", {})
    if not DATA_FILES.issubset(files) or not 3 <= len(files) <= 20:
        raise ValueError("Manifest source files are missing or out of bounds")
    keys = set()
    for descriptor in files.values():
        key = descriptor.get("key", "")
        if not key.startswith(prefix + "artifacts/") or key in keys:
            raise ValueError("Manifest object keys must be unique and belong to this run")
        keys.add(key)
        if not re.fullmatch(r"[0-9a-f]{64}", descriptor.get("sha256", "")):
            raise ValueError("Manifest checksum is invalid")
        size = descriptor.get("bytes")
        if type(size) is not int or not 0 < size <= MAX_OBJECT_BYTES:
            raise ValueError("Manifest object size is invalid")
    for source in manifest["sources"].values():
        if not source.get("source_version") or not source.get("retrieved_at") or not source.get("source_url"):
            raise ValueError("Source version, retrieval date and URL are required")
