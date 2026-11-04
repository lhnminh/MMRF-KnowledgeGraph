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
genetic:
  - name: TP53 Deletion
    association: Somatic
    gene_term: {term: {id: "hgnc:11998", label: TP53}}
    notes: deletion of 17p13
  - name: Hyperdiploidy
    association: Somatic
treatments:
  - name: Proteasome Inhibitor Therapy
    description: targets the proteasome
    evidence: [{reference: "PMID:5", snippet: quoted trial}]
    treatment_term:
      term: {id: "NCIT:C15986", label: Pharmacotherapy}
      therapeutic_agent:
        - {preferred_term: bortezomib, term: {id: "CHEBI:52717", label: bortezomib}}
  - name: Autologous Stem Cell Transplantation
    therapeutic_modality: SURGERY
    treatment_term: {term: {id: "NCIT:C15289", label: Organ Transplantation}}
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
    edge_writes = [(q, p) for q, p in tx.calls if "]->(" in q and "version" in p]  # not the id migration
    # Two associations, two mechanisms, one causal edge, one phenotype, two genetic, two treatments, one agent.
    assert len(edge_writes) == 11
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
    assert "{predicate: $predicate, source: 'dismech'}" in dismech_query
    assert parameters["predicate"] == "biolink:has_phenotype"
    assert "MERGE (o:Entity {id: $object})" in dismech_query
    # A phenotype already loaded from Monarch keeps its stored provenance.
    assert "o.source = coalesce(o.source, 'dismech')" in dismech_query
    assert parameters["pmid"] == "PMID:4" and parameters["snippet"] == "quoted phenotype"
    assert parameters["frequency"] == "VERY_FREQUENT"


def test_genetic_records_use_hgnc_ids_or_a_namespaced_id_without_a_gene():
    tx = FakeTx()
    loader.load_dismech(tx, loader.yaml.safe_load(DISMECH_YAML), "b1ca0b2")
    factors = {p["record_name"]: p for _, p in tx.calls if p.get("predicate") == "dismech:has_genetic_factor"}
    assert factors["TP53 Deletion"]["object"] == "HGNC:11998"
    assert factors["TP53 Deletion"]["object_label"] == "TP53"
    assert factors["TP53 Deletion"]["description"] == "deletion of 17p13"
    assert factors["Hyperdiploidy"]["object"] == "dismech:genetic:Hyperdiploidy"
    assert factors["Hyperdiploidy"]["pmid"] is None  # no evidence is recorded, and none is invented


def test_each_treatment_is_its_own_node_linked_to_its_agents():
    tx = FakeTx()
    loader.load_dismech(tx, loader.yaml.safe_load(DISMECH_YAML), "b1ca0b2")
    treatments = {p["record_name"]: p for _, p in tx.calls if p.get("predicate") == "dismech:has_recorded_treatment"}
    assert treatments["Proteasome Inhibitor Therapy"]["object"] == "dismech:treatment:Proteasome Inhibitor Therapy"
    assert treatments["Proteasome Inhibitor Therapy"]["ontology_term"] == "NCIT:C15986"
    assert treatments["Autologous Stem Cell Transplantation"]["therapeutic_modality"] == "SURGERY"
    agents = [p for q, p in tx.calls if "HAS_AGENT" in q]
    assert [(p["treatment"], p["agent"]) for p in agents] == [
        ("dismech:treatment:Proteasome Inhibitor Therapy", "CHEBI:52717")
    ]


def test_parameters_are_neptune_safe():
    tx = FakeTx()
    loader.load_monarch(tx, MONARCH, "2026-10-07")
    assert association_call(tx, "biolink:has_phenotype")[1]["publications"] == "PMID:1;PMID:2"
    assert association_call(tx, "biolink:subclass_of")[1]["publications"] == ""
    loader.load_dismech(tx, loader.yaml.safe_load(DISMECH_YAML), "b1ca0b2")
    for _, parameters in tx.calls:
        assert not any(isinstance(value, (list, dict)) for value in parameters.values())


def test_all_treatment_citations_and_original_metadata_are_preserved():
    dismech = loader.yaml.safe_load(DISMECH_YAML)
    evidence = [
        {"reference": "PMID:5", "reference_title": "Synthetic trial citation",
         "evidence_source": "HUMAN_CLINICAL", "supports": "SUPPORT",
         "snippet": "source quotation", "explanation": "curator interpretation"},
        {"reference": "PMID:6", "reference_title": "Synthetic guideline citation",
         "evidence_source": "OTHER", "supports": "PARTIAL", "review_status": "example"},
        {"reference": "PMID:5", "supports": "REFUTE", "explanation": "another source excerpt"},
        {"explanation": "Evidence item without a reference must also survive"},
    ]
    dismech["treatments"][0]["evidence"] = evidence
    tx = FakeTx()
    loader.load_dismech(tx, dismech, "b1ca0b2")
    query, parameters = association_call(tx, "dismech:has_recorded_treatment")
    assert json.loads(parameters["evidence_json"]) == evidence
    assert parameters["evidence_count"] == 4
    assert parameters["publications"] == "PMID:5;PMID:6"
    assert parameters["pmid"] == "PMID:5"  # existing tools remain compatible
    for name in ("evidence_json", "evidence_count", "publications"):
        assert f"r.{name} = ${name}" in query
    # Evidence belongs to the disease/treatment relationship, not a shared treatment node.
    assert "MERGE (d)-[r:ASSOCIATION {predicate: $predicate, source: 'dismech'}]->(o)" in query


def test_missing_evidence_writes_empty_values_instead_of_leaving_old_citations():
    tx = FakeTx()
    loader.load_dismech(tx, loader.yaml.safe_load(DISMECH_YAML), "b1ca0b2")
    _, parameters = next((q, p) for q, p in tx.calls
                         if p.get("record_name") == "Autologous Stem Cell Transplantation")
    assert parameters["evidence_json"] == "[]"
    assert parameters["evidence_count"] == 0
    assert parameters["publications"] == ""
    assert parameters["pmid"] is None


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


JMML_MONARCH = {
    "source_version": "2026-10-07",
    "entity": {"id": "MONDO:0011908", "name": "juvenile myelomonocytic leukemia"},
    "associations": [
        {
            "subject": "HGNC:6407",
            "subject_label": "KRAS",
            "subject_category": "biolink:Gene",
            "object": "MONDO:0011908",
            "object_label": "juvenile myelomonocytic leukemia",
            "object_category": "biolink:Disease",
            "predicate": "biolink:gene_associated_with_condition",
            "primary_knowledge_source": "infores:orphanet",
            "publications": None,
        }
    ],
}
JMML_YAML = """
name: Juvenile Myelomonocytic Leukemia
disease_term: {term: {id: "MONDO:0011908"}}
has_subtypes:
  - name: KRAS-mutated
    display_name: KRAS-mutated JMML
    classification: molecular_driver
    description: somatic KRAS gain of function
    evidence: [{reference: "PMID:30670449", snippet: subtype quote, explanation: subtype commentary}]
pathophysiology:
  - name: RAS/MAPK Pathway Hyperactivation
genetic:
  - name: KRAS
    association: Somatic activating mutation
    subtype: KRAS-mutated
    gene_term: {term: {id: "hgnc:6407", label: KRAS}}
  - name: SETBP1
    association: Cooperating somatic mutation
    gene_term: {term: {id: "hgnc:15573", label: SETBP1}}
treatments:
  - name: Trametinib
    evidence: [{reference: "PMID:38867349", snippet: "Ten infants and children were enrolled", explanation: trial}]
    treatment_term:
      term: {id: "NCIT:C93352", label: Targeted Therapy}
      therapeutic_agent:
        - {preferred_term: trametinib, term: {id: "CHEBI:75998", label: trametinib}}
    target_mechanisms:
      - target: RAS/MAPK Pathway Hyperactivation
        treatment_effect: INHIBITS
        description: inhibits MEK1/2
        evidence: [{reference: "PMID:38867349", snippet: "phase II trial", explanation: aligns}]
"""


def test_gene_subject_association_keeps_the_gene_an_entity_at_gene_level():
    tx = FakeTx()
    loader.load_monarch(tx, JMML_MONARCH, "2026-10-07")
    query, parameters = association_call(tx, "biolink:gene_associated_with_condition")
    assert "MERGE (s:Entity {id: $subject})" in query and "MERGE (o:Disease {id: $object})" in query
    assert "MERGE (d:Disease {id: $subject})" not in query
    assert parameters["subject"] == "HGNC:6407" and parameters["object"] == "MONDO:0011908"
    assert parameters["primary_knowledge_source"] == "infores:orphanet"
    assert "{predicate: $predicate, source: 'monarch'}" in query
    assert "source_version = $version" in query and "edge_origin = 'recorded'" in query


def test_subtype_is_loaded_with_its_driver_gene_and_only_where_dismech_records_one():
    tx = FakeTx()
    loader.load_dismech(tx, loader.yaml.safe_load(JMML_YAML), "b1ca0b2")
    subtype = next(p for _, p in tx.calls if p.get("predicate") == "dismech:has_subtype")
    assert subtype["object"] == "dismech:subtype:MONDO:0011908:KRAS-mutated"
    assert subtype["association_type"] == "molecular_driver" and subtype["pmid"] == "PMID:30670449"
    drivers = [p for q, p in tx.calls if "HAS_DRIVER_GENE" in q]
    assert [(p["subtype"], p["gene"]) for p in drivers] == [("dismech:subtype:MONDO:0011908:KRAS-mutated", "HGNC:6407")]


def test_treatment_is_linked_to_the_mechanism_it_targets_with_that_edges_evidence():
    tx = FakeTx()
    loader.load_dismech(tx, loader.yaml.safe_load(JMML_YAML), "b1ca0b2")
    query, parameters = next((q, p) for q, p in tx.calls if "TARGETS_MECHANISM" in q)
    assert parameters["treatment"] == "dismech:treatment:Trametinib"
    assert parameters["mechanism"] == "dismech:mechanism:MONDO:0011908:RAS/MAPK Pathway Hyperactivation"
    assert parameters["effect"] == "INHIBITS" and parameters["pmid"] == "PMID:38867349"
    assert "source_version = $version" in query and "edge_origin = 'recorded'" in query
    for _, params in tx.calls:
        assert not any(isinstance(value, (list, dict)) for value in params.values())


def test_load_graphs_loads_every_set_and_reports_each_version():
    result = loader.load_graphs(
        FakeDriver(),
        [
            (json.dumps(MONARCH), DISMECH_YAML, PROVENANCE),
            (json.dumps(JMML_MONARCH), JMML_YAML, PROVENANCE),
        ],
    )
    assert result["monarch_version"] == "2026-10-07" and result["dismech_version"] == "b1ca0b2"


def test_lambda_loads_the_named_sources_and_rejects_names_that_could_leave_the_prefix(monkeypatch):
    objects = {
        "raw/monarch_jmml.json": json.dumps(JMML_MONARCH),
        "raw/dismech_jmml.yaml": JMML_YAML,
        "raw/dismech_jmml.yaml.provenance.json": PROVENANCE,
    }
    requested = []

    class FakeS3:
        def get_object(self, Bucket, Key):
            requested.append(Key)
            return {"Body": BytesIO(objects[Key].encode())}

    boto3 = ModuleType("boto3")
    boto3.client = lambda name: FakeS3()
    handler = ModuleType("handler")
    handler.get_driver = FakeDriver
    monkeypatch.setitem(sys.modules, "boto3", boto3)
    monkeypatch.setitem(sys.modules, "handler", handler)
    monkeypatch.setenv("INGEST_BUCKET", "fixed-bucket")

    loader.lambda_handler({"sources": ["jmml"]}, None)
    assert set(requested) == set(objects)

    for bad in (["../secrets"], ["JMML"], ["a/b"], "jmml", [1]):
        with pytest.raises(ValueError, match="sources must be"):
            loader.lambda_handler({"sources": bad}, None)


DRUG_TARGETS = {
    "source_version": "26.09",
    "agents": [
        {
            "agent_id": "CHEBI:75998", "agent_label": "trametinib", "chembl_id": "CHEMBL2103875", "status": "matched",
            "mechanisms": [
                {"mechanism_of_action": "MAP2K2 inhibitor", "action_type": "INHIBITOR",
                 "references": ["FDA:a.pdf", "FDA:a.pdf"],
                 "targets": [{"ensembl_id": "E2", "symbol": "MAP2K2", "hgnc_id": "HGNC:6842"}]},
                {"mechanism_of_action": "MEK1/2 inhibitor", "action_type": "INHIBITOR",
                 "references": ["FDA:b.pdf"],
                 "targets": [{"ensembl_id": "E2", "symbol": "MAP2K2", "hgnc_id": "HGNC:6842"},
                             {"ensembl_id": "E1", "symbol": "MAP2K1", "hgnc_id": "HGNC:6840"},
                             {"ensembl_id": "E9", "symbol": "NOHGNC", "hgnc_id": None}]},
            ],
        },
        {"agent_id": "CHEBI:1", "agent_label": "unknown", "chembl_id": None, "status": "not_found", "mechanisms": []},
    ],
}


def test_drug_targets_load_one_edge_per_drug_and_gene_with_joined_strings():
    tx = FakeTx()
    loader.load_drug_targets(tx, DRUG_TARGETS, "26.09")
    assert sorted(p["gene"] for _, p in tx.calls) == ["HGNC:6840", "HGNC:6842"]  # no HGNC id, no edge; unmatched agent skipped
    query, edge = next((q, p) for q, p in tx.calls if p["gene"] == "HGNC:6842")
    assert "MATCH (a:Entity {id: $agent})" in query and "MERGE (a)-[r:TARGETS_GENE]->(g)" in query
    assert "source_version = $version" in query and "edge_origin = 'recorded'" in query
    assert edge["agent"] == "CHEBI:75998" and edge["chembl_id"] == "CHEMBL2103875" and edge["version"] == "26.09"
    assert edge["action_type"] == "INHIBITOR"
    assert edge["mechanism_of_action"] == "MAP2K2 inhibitor | MEK1/2 inhibitor"
    assert edge["references"] == "FDA:a.pdf;FDA:b.pdf"  # repeated references are kept once
    for _, parameters in tx.calls:
        assert not any(isinstance(value, (list, dict)) for value in parameters.values())


def test_unversioned_drug_targets_are_refused():
    with pytest.raises(ValueError, match="fetch_drug_targets"):
        loader.parse_drug_targets(json.dumps({**DRUG_TARGETS, "source_version": None}))


def test_drug_targets_load_last_and_only_when_asked(monkeypatch):
    objects = {
        "raw/monarch_mm.json": json.dumps(MONARCH), "raw/dismech_mm.yaml": DISMECH_YAML,
        "raw/dismech_mm.yaml.provenance.json": PROVENANCE, "raw/drug_targets.json": json.dumps(DRUG_TARGETS),
    }
    requested = []

    class FakeS3:
        def get_object(self, Bucket, Key):
            requested.append(Key)
            return {"Body": BytesIO(objects[Key].encode())}

    boto3 = ModuleType("boto3")
    boto3.client = lambda name: FakeS3()
    handler = ModuleType("handler")
    handler.get_driver = FakeDriver
    monkeypatch.setitem(sys.modules, "boto3", boto3)
    monkeypatch.setitem(sys.modules, "handler", handler)
    monkeypatch.setenv("INGEST_BUCKET", "fixed-bucket")

    assert loader.lambda_handler({}, None)["opentargets_version"] is None
    assert "raw/drug_targets.json" not in requested
    assert loader.lambda_handler({"drug_targets": True}, None)["opentargets_version"] == "26.09"
    assert "raw/drug_targets.json" in requested
    assert loader.lambda_handler({"drug_targets": "yes"}, None)["opentargets_version"] is None  # only true loads it


OUTCOME_YAML = """
name: Example Disease
disease_term: {term: {id: "MONDO:0000001"}}
pathophysiology:
  - name: Step A
    downstream:
      - {target: Step B, evidence: [{reference: "PMID:7"}]}
      - {target: Anemia, evidence: [{reference: "PMID:8", snippet: quoted}]}
      - {target: Free text outcome}
  - name: Step B
phenotypes:
  - name: Anemia
    phenotype_term: {term: {id: "HP:0001903", label: Anemia}}
treatments:
  - name: Drug therapy
    treatment_term:
      term: {id: "NCIT:C1", label: Therapy}
    target_mechanisms: [{target: Step B, treatment_effect: INHIBITS}]
"""


def load_example():
    tx = FakeTx()
    loader.load_dismech(tx, loader.yaml.safe_load(OUTCOME_YAML), "v1")
    return tx


def test_mechanism_ids_include_the_disease_so_equal_names_in_two_diseases_stay_apart():
    assert loader.mechanism_id("MONDO:1", "Immune Dysregulation") == "dismech:mechanism:MONDO:1:Immune Dysregulation"
    assert loader.mechanism_id("MONDO:1", "X") != loader.mechanism_id("MONDO:2", "X")
    tx = load_example()
    steps = [p for q, p in tx.calls if "HAS_MECHANISM" in q and "MERGE (m:Mechanism {id: $mechanism})" in q]
    assert [p["mechanism"] for p in steps] == [
        "dismech:mechanism:MONDO:0000001:Step A", "dismech:mechanism:MONDO:0000001:Step B"
    ]
    assert all(p["disease_id"] == "MONDO:0000001" for p in steps)
    assert not any("Mechanism {name:" in q for q, _ in tx.calls)  # nothing is keyed by name any more


def test_a_downstream_target_that_is_not_a_step_never_becomes_a_mechanism():
    tx = load_example()
    leads_to = [p for q, p in tx.calls if "-[r:LEADS_TO]->" in q]
    assert [(p["from_id"], p["to_id"]) for p in leads_to] == [
        ("dismech:mechanism:MONDO:0000001:Step A", "dismech:mechanism:MONDO:0000001:Step B")
    ]
    outcomes = {p["label"]: p for q, p in tx.calls if "LEADS_TO_PHENOTYPE" in q}
    assert outcomes["Anemia"]["outcome"] == "HP:0001903"  # the record's own phenotype node
    assert outcomes["Anemia"]["pmid"] == "PMID:8" and outcomes["Anemia"]["snippet"] == "quoted"
    assert outcomes["Free text outcome"]["outcome"] == "dismech:outcome:MONDO:0000001:Free text outcome"
    for query, _ in tx.calls:
        if "LEADS_TO_PHENOTYPE" in query:
            assert "MATCH (m:Mechanism {id: $mechanism})" in query and "MERGE (m:Mechanism" not in query
            assert "source_version = $version" in query and "edge_origin = 'recorded'" in query


def test_outcomes_are_written_after_the_phenotypes_they_may_point_at():
    tx = load_example()
    phenotype = next(i for i, (q, p) in enumerate(tx.calls) if p.get("predicate") == "biolink:has_phenotype")
    outcome = next(i for i, (q, p) in enumerate(tx.calls) if "LEADS_TO_PHENOTYPE" in q)
    assert phenotype < outcome


def test_treatment_links_to_an_existing_mechanism_and_never_creates_one():
    tx = load_example()
    query, parameters = next((q, p) for q, p in tx.calls if "TARGETS_MECHANISM" in q)
    assert "MATCH (m:Mechanism {id: $mechanism})" in query and "MERGE (m:Mechanism" not in query
    assert parameters["mechanism"] == "dismech:mechanism:MONDO:0000001:Step B"


def test_legacy_mechanisms_are_migrated_in_place_before_anything_is_merged():
    tx = load_example()
    first, second = tx.calls[0][0], tx.calls[1][0]
    assert "WHERE m.id IS NULL" in first and "size(diseases) = 1" in first
    assert "SET m.id = 'dismech:mechanism:' + head(diseases) + ':' + m.name" in first
    assert "SET m.retired = true" in second and "parents = 0" in second
    assert not any(word in first + second for word in ("DELETE", "REMOVE", "DETACH"))  # the loader cannot delete
    assert all(p == {} for _, p in tx.calls[:2])
