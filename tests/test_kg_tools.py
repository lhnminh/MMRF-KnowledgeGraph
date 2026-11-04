"""The kg-graph tools: what goes in observations, interpretation and limitations."""

import importlib.util
import os

from mmrf_knowledgegraph.etl import ROOT

os.environ.setdefault("NEPTUNE_URI", "bolt://unused:7687")  # read at import; these tests never connect
spec = importlib.util.spec_from_file_location("kg_handler", ROOT / "terraform/lambda_build/handler.py")
handler = importlib.util.module_from_spec(spec)
spec.loader.exec_module(handler)

MYELOMA = "MONDO:0009693"
ANEMIA_NOTE = "Not a recorded edge: Bone Marrow Failure -> Anemia, Neutropenia, Thrombocytopenia."


class FakeRecord(dict):
    def data(self):
        return self


class FakeSession:
    def __init__(self, rows):
        self.rows = rows

    def execute_read(self, work):
        return work(self)

    def run(self, _query, _parameters):
        return [FakeRecord(row) for row in self.rows]


def row(kind, subject, obj, **values):
    return {
        "kind": kind, "subject_id": subject, "subject_label": subject, "subject_type": "Mechanism",
        "subject_description": None, "predicate": kind, "object_id": obj, "object_label": obj,
        "object_type": "Mechanism", "object_description": None, "pmid": "PMID:1", "snippet": "quoted",
        "explanation": None, "publications": None, "primary_knowledge_source": None, "frequency": None,
        "source": "dismech", "source_version": "b1ca0b2", "edge_origin": "recorded", **values,
    }


def mechanism_paths(rows, disease_id=MYELOMA):
    env = handler.Envelope("kg_get_mechanism_paths", {}, None)
    return handler.get_mechanism_paths(FakeSession(rows), {"disease_id": disease_id}, env)


def has_note(envelope, text):
    return any(text in limitation for limitation in envelope["limitations"])


def test_described_step_without_an_edge_is_reported():
    rows = [row("has_mechanism", MYELOMA, "Bone Marrow Failure", subject_type="Disease")]
    envelope = mechanism_paths(rows)
    assert has_note(envelope, ANEMIA_NOTE)
    assert has_note(envelope, "identified by hand")
    assert not has_note(envelope, "description of Hypercalcemia")  # that mechanism is not in these results


def test_described_step_is_dropped_once_an_edge_records_it():
    rows = [
        row("has_mechanism", MYELOMA, "Bone Marrow Failure", subject_type="Disease"),
        row("leads_to", "Bone Marrow Failure", "Anemia"),
    ]
    assert has_note(mechanism_paths(rows), "Bone Marrow Failure -> Neutropenia, Thrombocytopenia.")

    rows += [row("leads_to", "Bone Marrow Failure", name) for name in ("Neutropenia", "Thrombocytopenia")]
    envelope = mechanism_paths(rows)
    assert not has_note(envelope, "Not a recorded edge: Bone Marrow Failure")
    assert not has_note(envelope, "identified by hand")


def test_mechanism_description_is_interpretation_not_observation():
    rows = [row("has_mechanism", MYELOMA, "Bone Marrow Failure", subject_type="Disease",
                object_description="leading to anemia")]
    envelope = mechanism_paths(rows)
    assert "leading to anemia" not in " ".join(item["text"] for item in envelope["observations"])
    assert any("leading to anemia" in item["text"] and item["basis"] == "source_curator_commentary"
               and item["refs"] for item in envelope["interpretation"])


def test_described_steps_are_only_reported_for_their_disease():
    rows = [row("has_mechanism", "MONDO:1", "Bone Marrow Failure", subject_type="Disease")]
    assert not has_note(mechanism_paths(rows, "MONDO:1"), ANEMIA_NOTE)


def test_phenotype_description_is_interpretation_not_observation():
    phenotype = row(
        "association", MYELOMA, "HP:0001903", subject_type="Disease", object_type="biolink:PhenotypicFeature",
        object_label="Anemia", predicate="biolink:has_phenotype", frequency="VERY_FREQUENT",
        object_description="anemia due to marrow infiltration", explanation="curator commentary",
    )
    env = handler.Envelope("kg_get_phenotypes", {}, None)
    envelope = handler.get_phenotypes(FakeSession([phenotype]), {"disease_id": MYELOMA}, env)

    observations = " ".join(item["text"] for item in envelope["observations"])
    assert "has phenotype Anemia" in observations and "very frequent" in observations
    assert "due to marrow infiltration" not in observations

    interpretation = envelope["interpretation"]
    assert any("due to marrow infiltration" in item["text"] for item in interpretation)
    assert any(item["text"] == "curator commentary" for item in interpretation)
    assert all(item["basis"] == "source_curator_commentary" and item["refs"] for item in interpretation)
