"""The kg-ingest loader: provenance, one node per disease, and Neptune-safe values."""

import importlib.util
import json
import sys
from io import BytesIO
from types import ModuleType

import pytest

from mmrf_knowledgegraph.etl import ROOT

spec = importlib.util.spec_from_file_location("load_neo4j", ROOT / "ingestion/load_neo4j.py")
loader = importlib.util.module_from_spec(spec)
spec.loader.exec_module(loader)

MONARCH = {
    "source_version": "2026-10-07",
    "entity": {"id": "MONDO:0009693", "name": "plasma cell myeloma"},
    "associations": [
        {
            "subject": "MONDO:0009693",
            "object": "HP:0000938",
            "object_label": "Osteopenia",
            "object_category": "biolink:PhenotypicFeature",
            "predicate": "biolink:has_phenotype",
            "publications": ["PMID:1", "PMID:2"],
        },
        {
            "subject": "MONDO:0005235",
            "subject_label": "smoldering plasma cell myeloma",
            "object": "MONDO:0009693",
            "object_label": "plasma cell myeloma",
            "object_category": "biolink:Disease",
            "predicate": "biolink:subclass_of",
            "publications": None,
        },
    ],
}
DISMECH_YAML = """
name: Multiple Myeloma
disease_term: {term: {id: "MONDO:0009693"}}
pathophysiology:
  - name: A
    description: first step
    downstream:
      - target: B
        evidence: [{reference: "PMID:3", snippet: quoted, explanation: commentary}]
  - name: B
phenotypes:
  - name: Anemia
    frequency: VERY_FREQUENT
    description: low red cells
    evidence: [{reference: "PMID:4", snippet: quoted phenotype, explanation: phenotype commentary}]
    phenotype_term: {term: {id: "HP:0001903", label: Anemia}}
"""
PROVENANCE = json.dumps({"source_revision": "b1ca0b2"})


class FakeResult:
    def consume(self):
        return None

    def single(self):
        return {"c": 0}


class FakeTx:
    def __init__(self):
        self.calls = []

    def run(self, query, **parameters):
        self.calls.append((query, parameters))
        return FakeResult()


class FakeSession:
    def __init__(self, tx):
        self.tx = tx

    def __enter__(self):
        return self

    def __exit__(self, *_):
        return False

    def execute_write(self, work, *args):
        return work(self.tx, *args)

    def execute_read(self, work):
        return work(self.tx)


class FakeDriver:
    def __init__(self):
        self.tx = FakeTx()

    def session(self):
        return FakeSession(self.tx)


def association_call(tx, predicate):
    return next((q, p) for q, p in tx.calls if p.get("predicate") == predicate)


def test_disease_objects_stay_diseases_and_other_objects_stay_entities():
    tx = FakeTx()
    loader.load_monarch(tx, MONARCH, "2026-10-07")
    assert "MERGE (o:Entity {id: $object})" in association_call(tx, "biolink:has_phenotype")[0]
    assert "MERGE (o:Disease {id: $object})" in association_call(tx, "biolink:subclass_of")[0]


def test_every_edge_records_its_source_version_and_origin():
    tx = FakeTx()
    loader.load_monarch(tx, MONARCH, "2026-10-07")
    loader.load_dismech(tx, loader.yaml.safe_load(DISMECH_YAML), "b1ca0b2")
    edge_writes = [(q, p) for q, p in tx.calls if "]->(" in q]
    assert len(edge_writes) == 6  # two associations, two mechanisms, one causal edge, one phenotype
    for query, parameters in edge_writes:
        assert "source_version = $version" in query and "edge_origin = 'recorded'" in query
        assert parameters["version"] in {"2026-10-07", "b1ca0b2"}


def test_monarch_and_dismech_keep_separate_edges_to_one_phenotype_node():
    tx = FakeTx()
    loader.load_monarch(tx, MONARCH, "2026-10-07")
    loader.load_dismech(tx, loader.yaml.safe_load(DISMECH_YAML), "b1ca0b2")
    monarch_query = association_call(tx, "biolink:has_phenotype")[0]
    dismech_query, parameters = next((q, p) for q, p in tx.calls if p.get("object") == "HP:0001903")
    assert "{predicate: $predicate, source: 'monarch'}" in monarch_query
    assert "{predicate: 'biolink:has_phenotype', source: 'dismech'}" in dismech_query
    assert "MERGE (o:Entity {id: $object})" in dismech_query
    # A phenotype already loaded from Monarch keeps its stored provenance.
    assert "o.source = coalesce(o.source, 'dismech')" in dismech_query
    assert parameters["pmid"] == "PMID:4" and parameters["snippet"] == "quoted phenotype"
    assert parameters["frequency"] == "VERY_FREQUENT"


def test_parameters_are_neptune_safe():
    tx = FakeTx()
    loader.load_monarch(tx, MONARCH, "2026-10-07")
    assert association_call(tx, "biolink:has_phenotype")[1]["publications"] == "PMID:1;PMID:2"
    assert association_call(tx, "biolink:subclass_of")[1]["publications"] == ""
    for _, parameters in tx.calls:
        assert not any(isinstance(value, (list, dict)) for value in parameters.values())


def test_commentary_stays_separate_from_the_evidence_fields():
    tx = FakeTx()
    loader.load_dismech(tx, loader.yaml.safe_load(DISMECH_YAML), "b1ca0b2")
    _, parameters = next((q, p) for q, p in tx.calls if "LEADS_TO" in q)
    assert parameters["pmid"] == "PMID:3" and parameters["snippet"] == "quoted"
    assert parameters["explanation"] == "commentary"


@pytest.mark.parametrize(
    "monarch, provenance",
    [({**MONARCH, "source_version": None}, PROVENANCE), (MONARCH, "{}")],
)
def test_unversioned_sources_are_refused(monarch, provenance):
    with pytest.raises(ValueError, match="re-run"):
        loader.load_graph(FakeDriver(), json.dumps(monarch), DISMECH_YAML, provenance)


def test_lambda_reads_only_the_fixed_bucket_and_prefix(monkeypatch):
    objects = {
        "raw/monarch_mm.json": json.dumps(MONARCH),
        "raw/dismech_mm.yaml": DISMECH_YAML,
        "raw/dismech_mm.yaml.provenance.json": PROVENANCE,
    }
    requested = []

    class FakeS3:
        def get_object(self, Bucket, Key):
            requested.append((Bucket, Key))
            return {"Body": BytesIO(objects[Key].encode())}

    boto3 = ModuleType("boto3")
    boto3.client = lambda name: FakeS3()
    handler = ModuleType("handler")
    handler.get_driver = FakeDriver
    monkeypatch.setitem(sys.modules, "boto3", boto3)
    monkeypatch.setitem(sys.modules, "handler", handler)
    monkeypatch.setenv("INGEST_BUCKET", "fixed-bucket")

    result = loader.lambda_handler({"bucket": "outside", "key": "elsewhere"}, None)

    assert {bucket for bucket, _ in requested} == {"fixed-bucket"}
    assert {key for _, key in requested} == set(objects)
    assert result["monarch_version"] == "2026-10-07" and result["dismech_version"] == "b1ca0b2"
