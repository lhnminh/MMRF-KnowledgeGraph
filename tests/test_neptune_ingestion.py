import csv
import sys

import pytest

from mmrf_knowledgegraph.etl import ROOT, Builder

sys.path.insert(0, str(ROOT / "scripts"))
from bulk_load import prepare_export
from validate import validate_files


@pytest.fixture
def export(tmp_path):
    directory = tmp_path / "original"
    builder = Builder()
    builder.curated(ROOT / "data/curated/mm_evidence.json")
    builder.write(directory)
    staging = tmp_path / "staging"
    staging.mkdir()
    return directory, staging


def test_bulk_load_reuses_existing_neptune_ids_without_changing_local_neo4j_export(export):
    directory, staging = export
    before = {name: (directory / name).read_bytes() for name in ["nodes.csv", "relationships.csv"]}
    matched = prepare_export(
        directory, staging, [{"logical_id": "HGNC:11998", "neptune_id": "old-native-uuid"}]
    )
    assert matched == 1
    nodes = list(csv.DictReader((staging / "nodes.csv").open()))
    tp53 = next(row for row in nodes if row["id:String"] == "HGNC:11998")
    assert tp53[":ID"] == "old-native-uuid"
    edges = list(csv.DictReader((staging / "relationships.csv").open()))
    assert any("old-native-uuid" in [row[":START_ID"], row[":END_ID"]] for row in edges)
    assert not any("HGNC:11998" in [row[":START_ID"], row[":END_ID"]] for row in edges)
    assert validate_files(staging) == validate_files(directory)
    for name, contents in before.items():
        assert (directory / name).read_bytes() == contents


def test_bulk_load_fails_on_existing_duplicate_logical_ids(export):
    directory, staging = export
    with pytest.raises(ValueError, match="duplicate logical node ID"):
        prepare_export(
            directory,
            staging,
            [
                {"logical_id": "HGNC:11998", "neptune_id": "uuid-a"},
                {"logical_id": "HGNC:11998", "neptune_id": "uuid-b"},
            ],
        )


def test_bulk_load_reuses_unique_entity_when_a_legacy_nonentity_has_same_source_id(export):
    directory, staging = export
    assert (
        prepare_export(
            directory,
            staging,
            [
                {"logical_id": "MONDO:0009693", "neptune_id": "legacy-disease", "labels": ["Disease"]},
                {"logical_id": "MONDO:0009693", "neptune_id": "canonical-entity", "labels": ["Entity"]},
            ],
        )
        == 1
    )
    nodes = list(csv.DictReader((staging / "nodes.csv").open()))
    disease = next(row for row in nodes if row["id:String"] == "MONDO:0009693")
    assert disease[":ID"] == "canonical-entity"


def test_bulk_load_fails_if_a_new_id_would_overwrite_an_unrelated_existing_node(export):
    directory, staging = export
    with pytest.raises(ValueError, match="Native Neptune ID collision"):
        prepare_export(directory, staging, [{"logical_id": "other-entity", "neptune_id": "HGNC:11998"}])


def test_empty_neptune_preserves_csv_ids(export):
    directory, staging = export
    assert prepare_export(directory, staging, []) == 0
    for name in ["nodes.csv", "relationships.csv"]:
        assert (directory / name).read_bytes() == (staging / name).read_bytes()
