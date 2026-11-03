import csv
import hashlib
import json
import sys

import pytest

from mmrf_knowledgegraph.etl import ROOT, Builder, transform

sys.path.insert(0, str(ROOT / "scripts"))
from load_neo4j import properties, read_export
from validate import validate_files


@pytest.fixture
def snapshots(tmp_path):
    common = {
        "source_url": "https://example.org/synthetic-fixture",
        "fetch_timestamp_utc": "2026-11-03T12:00:00+00:00",
        "data_version": "synthetic-fixture-v1",
        "license": "CC0 1.0",
    }
    ot = {
        **common,
        "disease": {"id": "MONDO_0009693", "name": "Multiple Myeloma", "graph_disease_id": "MONDO:0009693"},
        "associated_targets": [{"score": 0.7, "target": {"id": "ENSG_TEST_TP53", "hgnc_symbol": "TP53"}}],
        "drug_and_clinical_candidates": [
            {
                "id": "synthetic-candidate-1",
                "maxClinicalStage": "PHASE_1",
                "drug": {
                    "id": "CHEMBL_TEST",
                    "name": "Synthetic drug",
                    "mechanismsOfAction": {
                        "rows": [
                            {
                                "mechanismOfAction": "Synthetic inhibition",
                                "actionType": "INHIBITOR",
                                "targets": [{"id": "ENSG_TEST_TP53", "hgnc_symbol": "TP53"}],
                            }
                        ]
                    },
                },
                "clinicalReports": [
                    {"id": "synthetic-trial", "url": "https://example.org/trial", "clinicalStage": "PHASE_1"}
                ],
            }
        ],
    }
    item = {
        "id": 1,
        "name": "Synthetic evidence 1",
        "status": "ACCEPTED",
        "evidenceType": "PREDICTIVE",
        "evidenceLevel": "B",
        "evidenceDirection": "DOES_NOT_SUPPORT",
        "significance": "RESISTANCE",
        "evidenceRating": 3,
        "description": "Synthetic test evidence only.",
        "disease": {"id": 41},
        "source": {
            "citationId": "synthetic-reference",
            "sourceType": "PUBMED",
            "citation": "Synthetic citation",
        },
        "molecularProfile": {
            "id": 10,
            "name": "Synthetic complex profile",
            "variants": [
                {
                    "id": 20,
                    "name": "Synthetic fusion",
                    "feature": {
                        "id": 30,
                        "name": "TP53::TEST_PARTNER",
                        "featureInstance": {
                            "__typename": "Fusion",
                            "fivePrimeGene": {"hgnc_symbol": "TP53", "entrezId": 7157},
                            "threePrimeGene": {"hgnc_symbol": "TEST_PARTNER", "entrezId": 999999},
                        },
                    },
                }
            ],
        },
        "therapies": [{"id": 50, "name": "Synthetic combination", "ncitId": "TEST"}],
    }
    civic = {
        **common,
        "disease": {
            "id": 41,
            "name": "Multiple Myeloma",
            "doid": "DOID:9538",
            "mondo_id": "MONDO:0009693",
            "graph_disease_id": "MONDO:0009693",
        },
        "evidence_items": [
            item,
            {**item, "id": 2, "status": "SUBMITTED"},
            {**item, "id": 3, "status": "REJECTED"},
        ],
    }
    paths = []
    for name, data in [("opentargets", ot), ("civic", civic)]:
        path = tmp_path / f"{name}.json"
        path.write_text(json.dumps(data))
        paths.append(path)
    return paths


def build(snapshots):
    builder = Builder()
    builder.curated(ROOT / "data/curated/mm_evidence.json")
    builder.opentargets(snapshots[0])
    builder.civic(snapshots[1])
    return builder


def test_public_sources_join_genes_and_keep_native_identifiers_and_qualifiers(snapshots, tmp_path):
    builder = build(snapshots)
    tp53 = [n for n in builder.nodes.values() if n["entity_type"] == "Gene" and n["name"] == "TP53"]
    assert len(tp53) == 1 and tp53[0]["id"] == "HGNC:11998"
    assert {"ENSG_TEST_TP53", "NCBIGene:7157"} <= set(tp53[0]["aliases"])
    assert "NCBIGene:999999" in builder.nodes  # both fusion partners, no invented fusion-gene node
    association = next(r for r in builder.edges.values() if r["predicate"] == "opentargets:associated_target")
    assert association["association_score"] == 0.7
    assert association["confidence"] == "not_reported"  # a source score is not a probability/confidence
    candidate = next(
        r for r in builder.edges.values() if r["predicate"] == "opentargets:drug_or_clinical_candidate"
    )
    assert candidate["clinical_stage"] == "PHASE_1"
    assert candidate["evidence"][0]["source_url"] == "https://example.org/trial"
    evidence = builder.nodes["CIViC:evidence:1"]
    assert evidence["evidence_direction"] == "DOES_NOT_SUPPORT"
    assert evidence["significance"] == "RESISTANCE"
    assert evidence["review_status"] == "ACCEPTED"
    assert builder.nodes["CIViC:evidence:2"]["review_status"] == "SUBMITTED"
    assert "CIViC:evidence:3" not in builder.nodes
    contextual = next(
        r
        for r in builder.edges.values()
        if r["subject"] == "CIViC:evidence:1" and r["predicate"] == "civic:about_molecular_profile"
    )
    assert contextual["object"] == "CIViC:molecular_profile:10"
    assert contextual["original"]["evidenceRating"] == 3
    assert contextual["evidence"][0]["reference"] == "PMID:synthetic-reference"
    assert not any(
        r["source"] == "civic" and r["subject"] == "HGNC:11998" and r["object"] == "MONDO:0009693"
        for r in builder.edges.values()
    )
    builder.write(tmp_path / "export")
    validate_files(tmp_path / "export")
    read_export(tmp_path / "export")
    rows = list(csv.DictReader((tmp_path / "export/relationships.csv").open()))
    assoc = next(r for r in rows if r["predicate:String"] == "opentargets:associated_target")
    assert properties(assoc)["association_score"] == 0.7
    provenance = json.loads(assoc["provenance_json:String"])
    assert provenance["source_revision"] == "synthetic-fixture-v1"
    assert provenance["license"] == "CC0 1.0"
    assert association["retrieved_at"] == "2026-11-03T12:00:00+00:00"


def test_rebuilding_public_snapshots_has_stable_ids_and_csvs(snapshots, tmp_path):
    first, second = tmp_path / "first", tmp_path / "second"
    build(snapshots).write(first)
    build(snapshots).write(second)
    for filename in ["nodes.csv", "relationships.csv"]:
        assert (first / filename).read_bytes() == (second / filename).read_bytes()


def test_public_snapshot_checks_raw_hash_and_preserves_manifest(snapshots):
    path = snapshots[0]
    raw = path.with_name("response.json")
    raw.write_text('{"synthetic": true}')
    data = json.loads(path.read_text())
    data.update(raw_response_file=raw.name, raw_response_sha256=hashlib.sha256(raw.read_bytes()).hexdigest())
    path.write_text(json.dumps(data))
    builder = Builder()
    builder.opentargets(path)
    assert builder.sources[0]["raw_response_sha256"] == [data["raw_response_sha256"]]
    raw.write_text("tampered")
    with pytest.raises(ValueError, match="checksum mismatch"):
        Builder().opentargets(path)


@pytest.mark.parametrize("source_index", [0, 1])
def test_public_sources_reject_wrong_disease(snapshots, source_index):
    path = snapshots[source_index]
    data = json.loads(path.read_text())
    data["disease"]["graph_disease_id"] = "MONDO:OTHER"
    path.write_text(json.dumps(data))
    with pytest.raises(ValueError, match="snapshot is not for"):
        getattr(Builder(), ["opentargets", "civic"][source_index])(path)


def test_curated_only_excludes_local_public_snapshots(tmp_path):
    graph = transform(tmp_path, include_monarch=False, include_external=False)
    assert {r["source"] for r in graph["relationships"]} == {"orion_public_curation"}


def test_large_source_records_survive_validation_and_neo4j_parsing(snapshots, tmp_path):
    path = snapshots[0]
    data = json.loads(path.read_text())
    large_note = "synthetic fixture " * 10000
    data["drug_and_clinical_candidates"][0]["source_note"] = large_note
    path.write_text(json.dumps(data))
    output = tmp_path / "large-export"
    build(snapshots).write(output)
    validate_files(output)
    _, edge_groups = read_export(output)
    candidate = next(
        r
        for r in edge_groups["RELATIONSHIP"]
        if r["properties"]["predicate"] == "opentargets:drug_or_clinical_candidate"
    )
    assert json.loads(candidate["properties"]["original_json"])["source_note"] == large_note


@pytest.mark.parametrize("value", ["nan", "inf", "-inf"])
def test_loader_rejects_nonfinite_source_scores(value):
    with pytest.raises(ValueError, match="Non-finite"):
        properties({"association_score:Double": value})
