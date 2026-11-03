import csv
import json
import sys

import pytest
import yaml

from mmrf_knowledgegraph.etl import ROOT, Builder

sys.path.insert(0, str(ROOT / "scripts"))
from validate import validate_files


def test_neptune_csv_roundtrip_stable_ids_and_unknown_retrieval(tmp_path):
    b = Builder()
    b.curated(ROOT / "data/curated/mm_evidence.json")
    b.write(tmp_path)
    counts = validate_files(tmp_path)
    assert counts["nodes"] >= 7 and counts["relationships"] >= 9
    before = (tmp_path / "relationships.csv").read_bytes()
    b.write(tmp_path)
    assert (tmp_path / "relationships.csv").read_bytes() == before
    with (tmp_path / "nodes.csv").open(newline="") as f:
        header = next(csv.reader(f))
    assert ":ID" in header and ":LABEL" in header


def test_monarch_preserves_original_ids_predicates_and_all_provenance(tmp_path):
    original = {
        "id": "upstream-edge-id",
        "subject": "MONDO:0009693",
        "subject_label": "myeloma",
        "subject_category": "biolink:Disease",
        "object": "HGNC:11998",
        "object_label": "TP53",
        "object_category": "biolink:Gene",
        "predicate": "biolink:associated_with",
        "primary_knowledge_source": "infores:test",
        "publications": ["PMID:30373884", "PMID:31991614"],
        "qualifiers": {"custom": "retained"},
    }
    path = tmp_path / "monarch.json"
    path.write_text(
        json.dumps({"entity": {}, "associations": [original, {**original, "id": "negated", "negated": True}]})
    )
    b = Builder()
    b.monarch(path)
    assert "negated" not in b.edges
    edge = b.edges["upstream-edge-id"]
    assert edge["original"] == original
    assert edge["predicate"] == original["predicate"]
    assert len(edge["evidence"]) == 2
    assert edge["retrieved_at"] == "unknown"
    assert edge["retrieval_status"] == "not_recorded_in_original_download"


def test_dismech_preserves_all_downstream_evidence_and_original_ids(tmp_path):
    evidence = [
        {"reference": "PMID:1", "snippet": "synthetic test snippet", "explanation": "test"},
        {"reference": "PMID:2", "supports": "SUPPORT"},
    ]
    raw = {
        "name": "Multiple Myeloma",
        "disease_term": {"term": {"id": "MONDO:0009693"}},
        "pathophysiology": [
            {"name": "Test mechanism", "downstream": [{"target": "Second mechanism", "evidence": evidence}]}
        ],
        "genetic": [{"name": "TP53", "gene_term": {"term": {"id": "hgnc:11998", "label": "TP53"}}}],
    }
    path = tmp_path / "dismech.yaml"
    path.write_text(yaml.safe_dump(raw))
    b = Builder()
    b.dismech(path)
    assert "hgnc:11998" in b.nodes["HGNC:11998"]["aliases"]
    edge = next(e for e in b.edges.values() if e["predicate"] == "dismech:downstream")
    assert edge["evidence"] == evidence
    assert edge["original"]["target"] == "Second mechanism"
    b.write(tmp_path / "out")
    validate_files(tmp_path / "out")


def test_dangling_edges_are_rejected(tmp_path):
    b = Builder()
    b.curated(ROOT / "data/curated/mm_evidence.json")
    b.write(tmp_path)
    path = tmp_path / "nodes.csv"
    rows = list(csv.reader(path.open()))
    with path.open("w", newline="") as f:
        csv.writer(f).writerows(rows[:2])
    with pytest.raises(AssertionError, match="Dangling"):
        validate_files(tmp_path)
