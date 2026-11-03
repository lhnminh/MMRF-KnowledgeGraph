"""Lossless source records plus a small, queryable property-graph projection."""

import csv
import hashlib
import json
from datetime import datetime, timezone
from pathlib import Path

import yaml

ROOT = Path(__file__).resolve().parents[2]
DISEASE = "MONDO:0009693"
GENES = {"TP53", "KRAS", "NRAS", "BRAF"}
NODE_ATTRIBUTES = [
    (key, "String")
    for key in [
        "source_url",
        "review_status",
        "evidence_type",
        "evidence_level",
        "evidence_direction",
        "significance",
    ]
]
EDGE_ATTRIBUTES = [
    ("association_score", "Double"),
    *[
        (key, "String")
        for key in [
            "clinical_stage",
            "action_type",
            "evidence_type",
            "evidence_level",
            "evidence_direction",
            "significance",
        ]
    ],
]


def stable_id(prefix, value):
    return (
        prefix
        + ":"
        + hashlib.sha256(json.dumps(value, sort_keys=True, default=str).encode()).hexdigest()[:24]
    )


def file_provenance(path, source_url, retrieved_at=None):
    sidecar = path.with_suffix(path.suffix + ".provenance.json")
    saved = json.loads(sidecar.read_text()) if sidecar.exists() else {}
    return {
        "source_url": saved.get("source_url", source_url),
        "sha256": hashlib.sha256(path.read_bytes()).hexdigest(),
        # Never infer retrieval from transformation time or filesystem mtime.
        "retrieved_at": saved.get("retrieved_at", retrieved_at or "unknown"),
        "source_revision": saved.get("source_revision", "unknown"),
        "retrieval_status": "recorded"
        if saved.get("retrieved_at", retrieved_at)
        else "not_recorded_in_original_download",
    }


class Builder:
    def __init__(self):
        self.nodes = {}
        self.edges = {}
        self.sources = []

    def node(self, id, name, entity_type, source, aliases=None, original=None):
        current = self.nodes.setdefault(
            id, {"id": id, "name": name, "entity_type": entity_type, "aliases": [], "provenance": []}
        )
        current["aliases"] = sorted(set(current["aliases"] + (aliases or []) + [name]))
        source_metadata = next((s for s in self.sources if s["name"] == source), {})
        item = {"source": source, **source_metadata, "original": original or {}}
        if item not in current["provenance"]:
            current["provenance"].append(item)
        return id

    def edge(self, subject, object, predicate, source, provenance, evidence=None, original=None, id=None):
        edge_id = id or stable_id("orion-edge", [subject, object, predicate, source, original])
        self.edges[edge_id] = {
            "id": edge_id,
            "subject": subject,
            "object": object,
            "predicate": predicate,
            "source": source,
            **provenance,
            "review_status": "source_assertion_not_independently_reviewed",
            "confidence": "not_reported",
            "synthetic": False,
            "evidence": evidence or [],
            "original": original or {},
        }
        return self.edges[edge_id]

    def opentargets(self, path):
        from .public_sources import load_opentargets

        load_opentargets(self, path)

    def civic(self, path):
        from .public_sources import load_civic

        load_civic(self, path)

    def curated(self, path):
        data = json.loads(path.read_text())
        prov = file_provenance(path, "data/curated/mm_evidence.json", "2026-11-03")
        self.sources.append(
            {
                "name": "orion_public_curation",
                **prov,
                "terms": "Original factual paraphrases with links; underlying publications retain their terms.",
            }
        )
        for n in data["nodes"]:
            self.node(n["id"], n["name"], n["entity_type"], "orion_public_curation", n.get("aliases"), n)
        for a in data["assertions"]:
            p = {**prov, "source_url": a["url"]}
            self.edge(
                a["subject"],
                a["object"],
                a["predicate"],
                "orion_public_curation",
                p,
                [
                    {
                        "reference": a["reference"],
                        "source_url": a["url"],
                        "summary": a["summary"],
                        "interpretation": "manual_publication_curation",
                    }
                ],
                a,
            )

    def monarch(self, path):
        data = json.loads(path.read_text())
        prov = file_provenance(path, "https://api.monarchinitiative.org/v3/api/association")
        self.sources.append(
            {
                "name": "monarch",
                **prov,
                "terms": "Check upstream source terms as well as Monarch terms; raw input stays local.",
            }
        )
        for a in data["associations"]:
            if a.get("negated"):
                continue  # retained in raw source, never project a negated edge as a positive assertion
            pair = {a["subject"], a["object"]}
            gene = bool({a.get("subject_label"), a.get("object_label")} & GENES)
            phenotype = "PhenotypicFeature" in str(a.get("object_category"))
            if DISEASE not in pair or not (gene or phenotype):
                continue
            for end in ("subject", "object"):
                self.node(
                    a[end],
                    a.get(end + "_label") or a[end],
                    (a.get(end + "_category") or "Entity").removeprefix("biolink:"),
                    "monarch",
                    original={"id": a[end]},
                )
            pubs = a.get("publications") or []
            evidence = [{"reference": p, "source_url": reference_url(p)} for p in pubs]
            self.edge(a["subject"], a["object"], a["predicate"], "monarch", prov, evidence, a, a.get("id"))

    def dismech(self, path):
        data = yaml.safe_load(path.read_text())
        prov = file_provenance(
            path, "https://github.com/monarch-initiative/dismech/blob/main/kb/disorders/Multiple_Myeloma.yaml"
        )
        self.sources.append(
            {
                "name": "dismech",
                **prov,
                "terms": "Private transformation only: YAML redistribution terms unconfirmed.",
            }
        )
        disease = data["disease_term"]["term"]["id"]
        self.node(disease, data["name"], "Disease", "dismech", original=data["disease_term"])

        def mechanism(name):
            id = stable_id("dismech-mechanism", [disease, name])
            self.node(id, name, "Mechanism", "dismech")
            return id

        for step in data.get("pathophysiology", []):
            mid = mechanism(step["name"])
            self.edge(disease, mid, "dismech:pathophysiology", "dismech", prov, step.get("evidence"), step)
            for link in step.get("downstream", []):
                self.edge(
                    mid,
                    mechanism(link["target"]),
                    "dismech:downstream",
                    "dismech",
                    prov,
                    link.get("evidence"),
                    link,
                )
            for term in step.get("biological_processes", []):
                if term.get("term", {}).get("id"):
                    t = term["term"]
                    self.node(t["id"], t["label"], "BiologicalProcess", "dismech", original=term)
                    self.edge(
                        mid,
                        t["id"],
                        "dismech:biological_processes",
                        "dismech",
                        prov,
                        step.get("evidence"),
                        term,
                    )
        for key, termkey, category in [
            ("genetic", "gene_term", "Gene"),
            ("phenotypes", "phenotype_term", "PhenotypicFeature"),
            ("treatments", "treatment_term", "TherapeuticApproach"),
        ]:
            for item in data.get(key, []):
                term = item.get(termkey, {}).get("term", {})
                id = term.get("id") or stable_id("dismech-entity", [key, item["name"]])
                original_id = id
                # Canonical HGNC casing joins the curated gene; retain the exact source ID as an alias
                # and in the complete original record, rather than creating two ambiguous TP53 nodes.
                if id.startswith("hgnc:"):
                    id = "HGNC:" + id.split(":", 1)[1]
                node_type = (
                    "Drug" if key == "treatments" and id.startswith(("CHEBI:", "DrugBank:")) else category
                )
                self.node(
                    id,
                    term.get("label", item["name"]),
                    node_type,
                    "dismech",
                    aliases=[original_id],
                    original=item,
                )
                self.edge(disease, id, "dismech:" + key, "dismech", prov, item.get("evidence"), item)

    def write(self, output):
        output.mkdir(parents=True, exist_ok=True)
        graph = {
            "schema_version": 1,
            "built_at": datetime.now(timezone.utc).isoformat(),
            "sources": self.sources,
            "nodes": sorted(self.nodes.values(), key=lambda n: n["id"]),
            "relationships": sorted(self.edges.values(), key=lambda r: r["id"]),
        }
        (output / "graph.json").write_text(json.dumps(graph, indent=2, default=str), encoding="utf-8")
        node_fields = [
            ":ID",
            ":LABEL",
            "id:String",
            "name:String",
            "entity_type:String",
            "aliases_json:String",
            "provenance_json:String",
            *[f"{key}:{kind}" for key, kind in NODE_ATTRIBUTES],
        ]
        edge_fields = [
            ":ID",
            ":START_ID",
            ":END_ID",
            ":TYPE",
            "id:String",
            "predicate:String",
            "source:String",
            "source_url:String",
            "retrieved_at:String",
            "review_status:String",
            "confidence:String",
            "synthetic:Bool",
            "evidence_json:String",
            "original_json:String",
            "provenance_json:String",
            *[f"{key}:{kind}" for key, kind in EDGE_ATTRIBUTES],
        ]
        with (output / "nodes.csv").open("w", newline="", encoding="utf-8") as f:
            w = csv.writer(f)
            w.writerow(node_fields)
            for n in graph["nodes"]:
                w.writerow(
                    [
                        n["id"],
                        "Entity;" + n["entity_type"],
                        n["id"],
                        n["name"],
                        n["entity_type"],
                        json.dumps(n["aliases"]),
                        json.dumps(n["provenance"], default=str),
                        *[n.get(key, "") for key, _ in NODE_ATTRIBUTES],
                    ]
                )
        with (output / "relationships.csv").open("w", newline="", encoding="utf-8") as f:
            w = csv.writer(f)
            w.writerow(edge_fields)
            for r in graph["relationships"]:
                # One physical type; original predicate is preserved verbatim as a property.
                w.writerow(
                    [
                        r["id"],
                        r["subject"],
                        r["object"],
                        "RELATIONSHIP",
                        *[
                            r[k]
                            for k in [
                                "id",
                                "predicate",
                                "source",
                                "source_url",
                                "retrieved_at",
                                "review_status",
                                "confidence",
                            ]
                        ],
                        "false",
                        json.dumps(r["evidence"], default=str),
                        json.dumps(r["original"], default=str),
                        json.dumps(
                            {
                                k: r[k]
                                for k in [
                                    "sha256",
                                    "source_revision",
                                    "retrieval_status",
                                    "license",
                                    "raw_response_files",
                                    "raw_response_sha256",
                                    "snapshot_metadata",
                                ]
                                if k in r
                            }
                        ),
                        *[r.get(key, "") for key, _ in EDGE_ATTRIBUTES],
                    ]
                )
        return graph


def reference_url(ref):
    if ref.startswith("PMID:"):
        return "https://pubmed.ncbi.nlm.nih.gov/" + ref.split(":", 1)[1] + "/"
    return "https://monarchinitiative.org/"


def transform(
    output=ROOT / "data/transformed", include_dismech=False, include_monarch=True, include_external=True
):
    b = Builder()
    b.curated(ROOT / "data/curated/mm_evidence.json")
    if include_monarch and (ROOT / "data/raw/monarch_mm.json").exists():
        b.monarch(ROOT / "data/raw/monarch_mm.json")
    if include_dismech:
        b.dismech(ROOT / "data/raw/dismech_mm.yaml")
    if include_external:
        for source in ["opentargets", "civic"]:
            path = ROOT / f"data/raw/{source}_mm.json"
            if path.exists():
                getattr(b, source)(path)
    return b.write(output)
