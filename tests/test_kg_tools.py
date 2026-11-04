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
        "record_name": None, "association_type": None, "source": "dismech", "source_version": "b1ca0b2", "edge_origin": "recorded", **values,
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


def test_genetic_edge_states_its_record_and_an_absent_citation_is_reported():
    factor = row(
        "association", MYELOMA, "HGNC:11998", subject_type="Disease", object_type="biolink:Gene", object_label="TP53",
        predicate="dismech:has_genetic_factor", record_name="TP53 Deletion", association_type="Somatic", pmid=None,
    )
    env = handler.Envelope("kg_get_evidence", {}, None)
    handler.add_edge_row(env, factor)
    envelope = env.build("graph", "")
    assert "Record: TP53 Deletion. Association: somatic." in envelope["observations"][0]["text"]
    assert has_note(envelope, "No evidence reference is recorded for: dismech:MONDO:0009693|dismech:has_genetic_factor")


def test_recorded_treatment_is_never_presented_as_a_recommendation():
    treatment = row(
        "association", MYELOMA, "dismech:treatment:X", subject_type="Disease", object_type="biolink:Treatment",
        object_label="X", predicate="dismech:has_recorded_treatment", record_name="X",
    )
    env = handler.Envelope("kg_get_evidence", {}, None)
    handler.add_edge_row(env, treatment)
    envelope = env.build("graph", "")
    assert has_note(envelope, "not a treatment recommendation")
    assert "Record:" not in envelope["observations"][0]["text"]  # the name is already the label


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


# ---------------------------------------------- gene, gene-disease and treatment tools

JMML = "MONDO:0011908"


class RoutedSession(FakeSession):
    """Answers each query by what it asks, and records the queries it was sent."""

    def __init__(self, responder):
        self.responder = responder
        self.queries = []

    def run(self, query, parameters):
        self.queries.append(query)
        return [FakeRecord(r) for r in self.responder(query, parameters)]


def respond(main_rows=(), gene=("HGNC:6407", "KRAS"), disease=("Juvenile Myelomonocytic Leukemia",), variants=()):
    def responder(query, _parameters):
        if "g.label AS label" in query:
            return [{"id": gene[0], "label": gene[1]}] if gene else []
        if "d.name AS name" in query:
            return [{"name": disease[0]}] if disease else []
        if "CONTAINS toLower($variant)" in query:
            return list(variants)
        if "UNION ALL" in query:
            return list(main_rows)
        return []  # per-mechanism context lookups

    return responder


def run_tool(tool, args, responder):
    session = RoutedSession(responder)
    env = handler.Envelope(tool.__name__, args, None)
    return tool(session, args, env), session


MONARCH_GENE_EDGE = row(
    "association", "HGNC:6407", JMML, subject_type="biolink:Gene", subject_label="KRAS",
    object_type="biolink:Disease", object_label="juvenile myelomonocytic leukemia",
    predicate="biolink:gene_associated_with_condition", pmid=None, snippet=None, source="monarch",
    source_version="2026-10-07", primary_knowledge_source="infores:orphanet",
)


def test_unknown_gene_is_reported_not_guessed():
    for tool, args in ((handler.get_gene_context, {"gene": "NOPE"}),
                       (handler.get_gene_disease_association, {"gene": "NOPE", "disease_id": JMML})):
        envelope, _ = run_tool(tool, args, respond(gene=None))
        assert envelope["graph"]["edges"] == []
        assert has_note(envelope, "No human gene node matching 'NOPE'")


def test_gene_symbol_resolves_only_to_an_hgnc_id():
    _, session = run_tool(handler.get_gene_context, {"gene": "Kras"}, respond())
    assert "STARTS WITH 'HGNC:'" in session.queries[0]  # a mouse Kras is never returned


def test_gene_context_states_none_recorded_per_loaded_source_and_names_the_unloaded_ones():
    envelope, _ = run_tool(handler.get_gene_context, {"gene": "KRAS"}, respond([MONARCH_GENE_EDGE]))
    assert envelope["graph"]["edges"][0]["id"] == f"monarch:HGNC:6407|biolink:gene_associated_with_condition|{JMML}"
    assert has_note(envelope, "DisMech: no record of KRAS (HGNC:6407)")
    assert not has_note(envelope, "Monarch: no record")
    assert has_note(envelope, "CIViC is not loaded. Open Targets is loaded only for the targets of drugs")


def test_gene_level_association_reports_the_change_in_specificity_for_a_variant():
    envelope, _ = run_tool(handler.get_gene_disease_association,
                           {"gene": "KRAS", "disease_id": JMML, "variant": "KRAS G12D"}, respond([MONARCH_GENE_EDGE]))
    assert has_note(envelope, "Requested specificity: variant 'KRAS G12D'")
    assert has_note(envelope, "reported at gene level: KRAS (HGNC:6407)")
    assert has_note(envelope, "DisMech: no genetic factor linking KRAS")
    observation = envelope["observations"][0]["text"]
    assert "KRAS gene associated with condition juvenile myelomonocytic leukemia (MONDO:0011908)" in observation
    assert "infores:orphanet" in {e["reference"] for e in envelope["evidence"]}


def test_a_missing_gene_disease_link_is_named_not_invented():
    envelope, _ = run_tool(handler.get_gene_disease_association, {"gene": "KRAS", "disease_id": JMML}, respond())
    assert envelope["graph"]["edges"] == []
    assert has_note(envelope, "Monarch: no association between KRAS (HGNC:6407)")
    assert has_note(envelope, "DisMech: no subtype")


def test_an_empty_variant_is_rejected():
    session = RoutedSession(respond())
    env = handler.Envelope("kg_get_gene_disease_association", {}, None)
    try:
        handler.get_gene_disease_association(session, {"gene": "KRAS", "disease_id": JMML, "variant": " "}, env)
    except handler.ToolInputError as error:
        assert "variant" in str(error)
    else:
        raise AssertionError("expected ToolInputError")


TRAMETINIB_ROWS = [
    row("association", JMML, "dismech:treatment:Trametinib", subject_type="Disease", subject_label="JMML",
        object_type="biolink:Treatment", object_label="Trametinib", predicate="dismech:has_recorded_treatment",
        record_name="Trametinib", pmid="PMID:38867349", snippet="Ten infants and children were enrolled"),
    row("has_agent", "dismech:treatment:Trametinib", "CHEBI:75998", subject_type="biolink:Treatment",
        subject_label="Trametinib", object_type="biolink:Drug", object_label="trametinib", predicate="has_agent",
        pmid=None, snippet=None),
    row("targets_mechanism", "dismech:treatment:Trametinib", "RAS/MAPK Pathway Hyperactivation",
        subject_type="biolink:Treatment", subject_label="Trametinib", predicate="targets_mechanism",
        pmid="PMID:38867349", snippet="phase II trial", effect="INHIBITS",
        edge_description="inhibits MEK1/2 downstream of Ras", explanation="supports the link"),
]


def test_treatment_links_to_its_mechanism_with_commentary_kept_out_of_observations():
    envelope, _ = run_tool(handler.get_treatment_evidence, {"disease_id": JMML, "treatment": "trametinib"},
                           respond(TRAMETINIB_ROWS))
    observations = " ".join(item["text"] for item in envelope["observations"])
    assert "Trametinib inhibits RAS/MAPK Pathway Hyperactivation" in observations
    assert "MEK1/2" not in observations
    texts = [item["text"] for item in envelope["interpretation"]]
    assert "inhibits MEK1/2 downstream of Ras" in texts and "supports the link" in texts
    assert "Ten infants and children were enrolled" in {e["snippet"] for e in envelope["evidence"]}
    assert not has_note(envelope, "No evidence reference is recorded")  # agent links carry none, by design
    assert has_note(envelope, "No molecular target (for example a gene or protein) is recorded for: trametinib")
    assert has_note(envelope, "not a treatment recommendation")
    assert has_note(envelope, "no structured fields")


def test_treatment_filters_narrow_the_result_and_report_untargeted_treatments():
    other = row("association", JMML, "dismech:treatment:Azacitidine", subject_type="Disease", subject_label="JMML",
                object_type="biolink:Treatment", object_label="Azacitidine",
                predicate="dismech:has_recorded_treatment", record_name="Azacitidine")
    rows = TRAMETINIB_ROWS + [other]
    envelope, _ = run_tool(handler.get_treatment_evidence, {"disease_id": JMML, "treatment": "azacitidine"},
                           respond(rows))
    assert [edge["target"] for edge in envelope["graph"]["edges"]] == ["dismech:treatment:Azacitidine"]
    assert has_note(envelope, "No treatment-to-mechanism edge is recorded for: Azacitidine.")

    envelope, _ = run_tool(handler.get_treatment_evidence, {"disease_id": JMML, "mechanism": "ras/mapk"},
                           respond(rows))
    assert {n["label"] for n in envelope["graph"]["nodes"] if n["type"] == "Treatment"} == {"Trametinib"}


def test_no_treatment_is_reported_as_none_recorded():
    envelope, _ = run_tool(handler.get_treatment_evidence, {"disease_id": JMML}, respond())
    assert has_note(envelope, "(none recorded)")
    envelope, _ = run_tool(handler.get_treatment_evidence, {"disease_id": JMML}, respond(disease=None))
    assert has_note(envelope, f"No Disease node with id {JMML}")


def test_evidence_lookup_accepts_a_gene_subject_and_the_new_edge_kinds():
    for edge, expect in (
        (f"monarch:HGNC:6407|biolink:gene_associated_with_condition|{JMML}", "MATCH (d {id: $subject})"),
        ("dismech:dismech:treatment:Trametinib|targets_mechanism|RAS/MAPK Pathway Hyperactivation",
         "TARGETS_MECHANISM"),
        ("dismech:dismech:subtype:MONDO:0011908:KRAS-mutated|has_driver_gene|HGNC:6407", "HAS_DRIVER_GENE"),
        ("dismech:dismech:treatment:Trametinib|has_agent|CHEBI:75998", "HAS_AGENT"),
    ):
        _, session = run_tool(handler.get_evidence, {"edge_id": edge}, lambda *_: [])
        assert expect in session.queries[0], edge
        assert "(d:Disease {id: $subject})-[r:ASSOCIATION" not in session.queries[0]


def test_every_union_part_returns_the_same_columns():
    def columns(sql):
        return [part.strip().split(" AS ")[-1] for part in sql.replace("\n", " ").split(",") if " AS " in part]

    sets = [handler.ASSOCIATION_COLUMNS, handler.LEADS_TO_COLUMNS, handler.HAS_MECHANISM_COLUMNS,
            handler.DRIVER_GENE_COLUMNS, handler.AGENT_COLUMNS, handler.TARGETS_COLUMNS,
            handler.TARGETS_GENE_COLUMNS, handler.OUTCOME_COLUMNS]
    assert all(columns(s) == columns(sets[0]) for s in sets)


def target_row(agent, symbol, number, action="INHIBITOR", moa="Dual specificity kinase inhibitor"):
    return row(
        "targets_gene", f"CHEBI:{agent}", f"HGNC:{number}", subject_type="biolink:Drug", subject_label="trametinib",
        object_type="biolink:Gene", object_label=symbol, predicate="targets_gene", pmid=None, snippet=None,
        publications="FDA:label/a.pdf;FDA:label/b.pdf", record_name=moa, association_type=action,
        source="opentargets", source_version="26.09",
    )


def test_a_drug_target_is_open_targets_record_with_its_label_references_and_version():
    gene_rows = [target_row("75998", "MAP2K1", 6840), target_row("75998", "MAP2K2", 6842)]
    envelope, _ = run_tool(handler.get_treatment_evidence, {"disease_id": JMML, "treatment": "trametinib"},
                           respond(TRAMETINIB_ROWS + gene_rows))
    observations = [o["text"] for o in envelope["observations"] if o["text"].startswith("Open Targets")]
    assert observations[0].startswith("Open Targets records trametinib as inhibitor of MAP2K1 (HGNC:6840)")
    edge = next(e for e in envelope["graph"]["edges"] if e["predicate"] == "targets_gene")
    assert edge["source_dataset"] == "opentargets" and edge["source_version"] == "26.09"
    assert edge["id"] == "opentargets:CHEBI:75998|targets_gene|HGNC:6840"
    assert {"FDA:label/a.pdf", "FDA:label/b.pdf"} <= {e["reference"] for e in envelope["evidence"]}
    assert not has_note(envelope, "No molecular target")
    assert has_note(envelope, "not part of the DisMech disease record")


def test_agents_without_a_drug_target_are_still_reported():
    other = row("has_agent", "dismech:treatment:Trametinib", "CHEBI:2038", subject_type="biolink:Treatment",
                subject_label="Trametinib", object_type="biolink:Drug", object_label="azacitidine",
                predicate="has_agent", pmid=None, snippet=None)
    envelope, _ = run_tool(handler.get_treatment_evidence, {"disease_id": JMML},
                           respond(TRAMETINIB_ROWS + [other, target_row("75998", "MAP2K1", 6840)]))
    assert has_note(envelope, "No molecular target (for example a gene or protein) is recorded for: azacitidine")


def test_a_very_long_target_list_is_capped_and_the_cut_is_stated():
    many = [target_row("52717", f"PSM{i:02d}", 9000 + i) for i in range(25)]
    agent = row("has_agent", "dismech:treatment:Trametinib", "CHEBI:52717", subject_type="biolink:Treatment",
                subject_label="Trametinib", object_type="biolink:Drug", object_label="trametinib",
                predicate="has_agent", pmid=None, snippet=None)
    many_rows = [r for r in many]
    envelope, _ = run_tool(handler.get_treatment_evidence, {"disease_id": JMML},
                           respond(TRAMETINIB_ROWS[:1] + [agent] + many_rows))
    shown = [e for e in envelope["graph"]["edges"] if e["predicate"] == "targets_gene"]
    assert len(shown) == handler.MAX_TARGETS_PER_AGENT
    assert has_note(envelope, f"trametinib has 25 recorded targets; only the first {handler.MAX_TARGETS_PER_AGENT}")


def test_gene_context_counts_open_targets_without_reporting_it_as_missing():
    envelope, _ = run_tool(handler.get_gene_context, {"gene": "MAP2K1"},
                           respond([target_row("75998", "MAP2K1", 6840)], gene=("HGNC:6840", "MAP2K1")))
    assert "1 from Open Targets" in envelope["summary"]
    assert has_note(envelope, "Monarch: no record of MAP2K1") and has_note(envelope, "DisMech: no record of MAP2K1")
    assert not has_note(envelope, "Open Targets: no record")


def test_evidence_lookup_accepts_a_drug_target_edge_id():
    _, session = run_tool(handler.get_evidence, {"edge_id": "opentargets:CHEBI:75998|targets_gene|HGNC:6840"},
                          lambda *_: [])
    assert "TARGETS_GENE" in session.queries[0]


STEP = "dismech:mechanism:MONDO:0011908:Myelomonocytic Progenitor Expansion"


def outcome_row(label, object_id):
    return row("leads_to_phenotype", STEP, object_id, subject_label="Myelomonocytic Progenitor Expansion",
               object_type="biolink:PhenotypicFeature", object_label=label, predicate="leads_to_phenotype")


def test_a_mechanism_to_phenotype_edge_is_reported_and_counted_as_a_link():
    rows = [
        row("has_mechanism", JMML, STEP, subject_type="Disease", subject_label="JMML",
            object_label="Myelomonocytic Progenitor Expansion"),
        outcome_row("Anemia", "HP:0001903"),
    ]
    envelope, _ = run_tool(handler.get_mechanism_paths, {"disease_id": JMML}, respond(rows))
    edge = next(e for e in envelope["graph"]["edges"] if e["predicate"] == "leads_to_phenotype")
    assert edge["source"] == STEP and edge["target"] == "HP:0001903"  # node ids are the stored ids
    assert "1 edges to a downstream phenotype" in envelope["summary"]
    assert any("leads to Anemia" in o["text"] for o in envelope["observations"])
    assert not has_note(envelope, "No causal (leads_to) edge is recorded into or out of")  # it has an outgoing edge


def test_retired_stubs_without_an_id_are_never_returned_by_search():
    def responder(query, _parameters):
        return [
            {"labels": ["Mechanism"], "id": "dismech:mechanism:MONDO:1:Anemia", "label": "Anemia", "category": None,
             "description": None, "source": "dismech", "source_version": "v1"},
            {"labels": ["Mechanism"], "id": None, "label": "Anemia", "category": None,
             "description": None, "source": None, "source_version": None},
        ]

    envelope, _ = run_tool(handler.search_entities, {"text": "anemia"}, responder)
    assert [n["id"] for n in envelope["graph"]["nodes"]] == ["dismech:mechanism:MONDO:1:Anemia"]


def test_queries_ignore_stubs_and_other_diseases_mechanisms():
    _, paths = run_tool(handler.get_mechanism_paths, {"disease_id": JMML}, lambda *_: [])
    assert "WHERE b.id IS NOT NULL" in paths.queries[0]
    _, treatment = run_tool(handler.get_treatment_evidence, {"disease_id": JMML}, lambda *_: [])
    assert "WHERE m.disease_id = $disease_id" in treatment.queries[0]
    _, evidence = run_tool(handler.get_evidence, {"reference": "PMID:1"}, lambda *_: [])
    assert "a.id IS NOT NULL AND b.id IS NOT NULL" in evidence.queries[0]


def test_evidence_lookup_resolves_mechanism_edges_by_id():
    for edge, expect in (
        (f"dismech:{STEP}|leads_to|dismech:mechanism:MONDO:0011908:Other", "Mechanism {id: $subject}"),
        (f"dismech:{STEP}|leads_to_phenotype|HP:0001903", "LEADS_TO_PHENOTYPE"),
        (f"dismech:{JMML}|has_mechanism|{STEP}", "Mechanism {id: $object}"),
    ):
        _, session = run_tool(handler.get_evidence, {"edge_id": edge}, lambda *_: [])
        assert expect in session.queries[0], edge
        assert "{name:" not in session.queries[0]
