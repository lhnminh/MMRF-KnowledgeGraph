"""Load Monarch, DisMech, Open Targets and CIViC into Neo4j or Amazon Neptune.

`dismech_explanation` holds DisMech's curator commentary, kept separate from
graph-encoded edges (predicate/evidence_pmid) so the agent never presents it as fact.

Connection (environment variables):
  NEO4J_URI      bolt URI (default bolt://localhost:7687). For Neptune use
                 bolt://<cluster-endpoint>:8182.
  NEPTUNE_MODE   "1"/"true" to target Neptune (or pass --neptune): encrypted Bolt,
                 placeholder credentials (IAM DB auth is off on the cluster).
  NEO4J_USER / NEO4J_PASSWORD   local Neo4j only (default neo4j / password).

Usage:
  python ingestion/load_neo4j.py              # load into the configured database
  python ingestion/load_neo4j.py --dry-run    # read files, print counts, no DB needed
  python ingestion/load_neo4j.py --verify     # read-only: count nodes/edges in the DB
  (add --neptune, or set NEPTUNE_MODE=1, for Neptune)

Neptune compatibility (openCypher over Bolt):
  - Neptune cannot store list-valued properties. Every list is stored as a
    "|"-joined string (read back with split(x, '|')). New Open Targets/CIViC
    properties are written this way on both backends; Monarch `publications`
    stays a list on Neo4j (unchanged) and is "|"-joined in Neptune mode only.
  - No CREATE CONSTRAINT / INDEX and no CALL procedures (APOC); idempotency
    comes from MERGE on each node's key property.
  - Only functions on Neptune's supported list are used (coalesce); no
    join()/reduce(), so the same Cypher runs on Neo4j too.
"""
import argparse
import json
import os
import sys
from collections import Counter
from pathlib import Path

import yaml

DATA_DIR = Path(__file__).resolve().parent.parent / "data" / "raw"
NEO4J_URI = os.environ.get("NEO4J_URI", "bolt://localhost:7687")
NEO4J_AUTH = (os.environ.get("NEO4J_USER", "neo4j"), os.environ.get("NEO4J_PASSWORD", "password"))
# Neptune ignores Bolt credentials when IAM DB auth is disabled, but the driver needs a value.
NEPTUNE_PLACEHOLDER_AUTH = ("username", "password")

LIST_SEP = "|"
BATCH_SIZE = 500
OT = "opentargets"
CIVIC = "civic"
RECORDED = "recorded"


def join_list(values) -> str | None:
    """Neptune can't store list properties; store them as a '|'-joined string."""
    values = [str(v) for v in (values or []) if v is not None and v != ""]
    return LIST_SEP.join(values) if values else None


# ---------------------------------------------------------------------------
# Monarch + DisMech (Cypher unchanged; Neptune mode only flattens the list)
# ---------------------------------------------------------------------------

def load_monarch(tx, monarch: dict, neptune: bool = False) -> None:
    entity = monarch["entity"]
    tx.run(
        "MERGE (d:Disease {id: $id}) SET d.name = $name",
        id=entity["id"],
        name=entity["name"],
    )
    for assoc in monarch["associations"]:
        publications = assoc.get("publications") or []
        tx.run(
            """
            MERGE (d:Disease {id: $subject})
            MERGE (o:Entity {id: $object}) SET o.label = $object_label, o.category = $object_category
            MERGE (d)-[r:ASSOCIATION {predicate: $predicate}]->(o)
            SET r.source = 'monarch',
                r.primary_knowledge_source = $primary_knowledge_source,
                r.publications = $publications
            """,
            subject=assoc["subject"],
            object=assoc["object"],
            object_label=assoc.get("object_label"),
            object_category=assoc.get("object_category"),
            predicate=assoc["predicate"],
            primary_knowledge_source=assoc.get("primary_knowledge_source"),
            publications=join_list(publications) if neptune else publications,
        )


def load_dismech(tx, dismech: dict) -> None:
    disease_id = dismech["disease_term"]["term"]["id"]
    tx.run("MERGE (d:Disease {id: $id}) SET d.name = $name", id=disease_id, name=dismech["name"])

    for step in dismech["pathophysiology"]:
        tx.run(
            """
            MERGE (m:Mechanism {name: $name})
            SET m.description = $description, m.source = 'dismech'
            MERGE (d:Disease {id: $disease_id})
            MERGE (d)-[:HAS_MECHANISM {source: 'dismech'}]->(m)
            """,
            name=step["name"],
            description=step.get("description"),
            disease_id=disease_id,
        )
        for downstream in step.get("downstream", []):
            evidence = (downstream.get("evidence") or [{}])[0]
            tx.run(
                """
                MERGE (a:Mechanism {name: $from_name})
                MERGE (b:Mechanism {name: $to_name})
                MERGE (a)-[r:LEADS_TO]->(b)
                SET r.source = 'dismech',
                    r.evidence_pmid = $pmid,
                    r.evidence_snippet = $snippet,
                    r.dismech_explanation = $explanation
                """,
                from_name=step["name"],
                to_name=downstream["target"],
                pmid=evidence.get("reference"),
                snippet=evidence.get("snippet"),
                explanation=evidence.get("explanation"),
            )


def count_monarch(monarch: dict) -> tuple[Counter, Counter]:
    nodes, edges = Counter(), Counter()
    assocs = monarch["associations"]
    nodes["Disease"] = len({monarch["entity"]["id"]} | {a["subject"] for a in assocs})
    nodes["Entity"] = len({a["object"] for a in assocs})
    edges["ASSOCIATION"] = len({(a["subject"], a["predicate"], a["object"]) for a in assocs})
    return nodes, edges


def count_dismech(dismech: dict) -> tuple[Counter, Counter]:
    nodes, edges = Counter(), Counter()
    steps = dismech["pathophysiology"]
    names = {s["name"] for s in steps}
    leads_to = {(s["name"], d["target"]) for s in steps for d in s.get("downstream", [])}
    names |= {t for _, t in leads_to}
    nodes["Disease"] = 1
    nodes["Mechanism"] = len(names)
    edges["HAS_MECHANISM"] = len({s["name"] for s in steps})
    edges["LEADS_TO"] = len(leads_to)
    return nodes, edges


# ---------------------------------------------------------------------------
# Open Targets + CIViC: build de-duplicated rows (shared by dry run and load)
# ---------------------------------------------------------------------------

def _prov(source: str, version: str) -> dict:
    return {"source_dataset": source, "source_version": version, "edge_origin": RECORDED}


def build_opentargets(ot: dict) -> dict:
    version = ot["data_version"]
    disease_id = ot["disease"]["graph_disease_id"]
    prov = _prov(OT, version)

    genes: dict[str, dict] = {}  # symbol -> gene info

    def add_gene(target: dict, name=None, biotype=None):
        g = genes.setdefault(target["hgnc_symbol"], {"ensembl_ids": set(), "name": None, "biotype": None})
        g["ensembl_ids"].add(target["id"])
        g["name"] = g["name"] or name
        g["biotype"] = g["biotype"] or biotype
        g.setdefault("primary_ensembl", None)
        return g

    assoc_rows = []
    for a in ot["associated_targets"]:
        t = a["target"]
        g = add_gene(t, t.get("approvedName"), t.get("biotype"))
        g["primary_ensembl"] = g["primary_ensembl"] or t["id"]  # association target ID wins
        assoc_rows.append({"symbol": t["hgnc_symbol"], "score": a["score"]})

    drug_rows, candidate_rows, targets = [], [], {}
    for c in ot["drug_and_clinical_candidates"]:
        drug = c["drug"]
        reports = c.get("clinicalReports") or []
        drug_rows.append({
            "chembl_id": drug["id"],
            "name": drug["name"],
            "drug_type": drug.get("drugType"),
            "max_stage": drug.get("maximumClinicalStage"),  # drug's overall stage, any disease
        })
        candidate_rows.append({
            "chembl_id": drug["id"],
            "clinical_stage": c.get("maxClinicalStage"),  # stage for myeloma specifically
            "candidate_id": c.get("id"),
            "clinical_report_ids": join_list(r.get("id") for r in reports),
            "clinical_report_urls": join_list(r.get("url") for r in reports),
        })
        for moa in (drug.get("mechanismsOfAction") or {}).get("rows") or []:
            for t in moa.get("targets") or []:
                if not t.get("hgnc_symbol"):
                    continue
                add_gene(t)
                key = (drug["id"], t["hgnc_symbol"], moa.get("mechanismOfAction"))
                targets.setdefault(key, {
                    "chembl_id": drug["id"],
                    "symbol": t["hgnc_symbol"],
                    "mechanism": moa.get("mechanismOfAction"),
                    "action_type": moa.get("actionType"),
                })

    # One Ensembl ID per gene: the association target's ID, else the lowest
    # (deterministic). Any others are kept in alt_ensembl_ids (e.g. IL3RA).
    for g in genes.values():
        ids = sorted(g.pop("ensembl_ids"))
        g["ensembl_id"] = g.pop("primary_ensembl") or ids[0]
        g["alt_ensembl_ids"] = join_list(i for i in ids if i != g["ensembl_id"])

    return {
        "source": OT, "version": version, "prov": prov,
        "disease": {"id": disease_id, "name": ot["disease"]["name"]},
        "genes": genes,
        "associated_targets": assoc_rows,
        "drugs": list({d["chembl_id"]: d for d in drug_rows}.values()),
        "candidate_for": list({d["chembl_id"]: d for d in candidate_rows}.values()),
        "targets": list(targets.values()),
    }


def _feature_genes(variant: dict) -> list[tuple[dict, str | None]]:
    """(gene, fusion_partner) pairs for a CIViC variant; fusions return both partners."""
    fi = variant["feature"]["featureInstance"]
    if fi.get("__typename") == "Gene":
        return [(fi, None)]
    if fi.get("__typename") == "Fusion":
        return [(g, side) for g, side in ((fi.get("fivePrimeGene"), "5prime"), (fi.get("threePrimeGene"), "3prime")) if g]
    return []


def build_civic(civic: dict) -> dict:
    version = civic["data_version"]
    disease_id = civic["disease"]["graph_disease_id"]
    civic_doid = civic["disease"]["doid"].removeprefix("DOID:")
    prov = _prov(CIVIC, version)

    genes, variants, variant_of = {}, {}, {}
    evidence, about, in_disease, therapies, involves = [], set(), set(), {}, set()

    for e in civic["evidence_items"]:
        mp = e["molecularProfile"]
        src = e.get("source") or {}
        evidence.append({
            "civic_eid": e["id"],
            "name": e.get("name"),
            "evidence_type": e.get("evidenceType"),
            "level": e.get("evidenceLevel"),
            "direction": e.get("evidenceDirection"),
            "significance": e.get("significance"),
            "status": e.get("status"),
            "rating": e.get("evidenceRating"),
            "variant_origin": e.get("variantOrigin"),
            "molecular_profile": mp.get("name"),
            "pmid": f"PMID:{src['citationId']}" if src.get("sourceType") == "PUBMED" and src.get("citationId") else None,
            "citation": src.get("citation"),
            "description": e.get("description"),
        })
        for v in mp.get("variants") or []:
            variants[v["id"]] = {
                "civic_id": v["id"],
                "name": v["name"],
                "feature_name": v["feature"]["name"],
                "full_name": f"{v['feature']['name']} {v['name']}",
            }
            about.add((e["id"], v["id"]))
            for g, partner in _feature_genes(v):
                genes.setdefault(g["hgnc_symbol"], {"entrez_id": g.get("entrezId")})
                variant_of[(v["id"], g["hgnc_symbol"])] = {
                    "civic_id": v["id"], "symbol": g["hgnc_symbol"], "fusion_partner": partner,
                }
        if str((e.get("disease") or {}).get("doid")) == civic_doid:
            in_disease.add(e["id"])
        else:
            print(f"  warning: CIViC EID{e['id']} disease {e.get('disease')} is not {civic_doid}; no IN_DISEASE edge")
        for t in e.get("therapies") or []:
            therapies.setdefault(t["name"], {"name": t["name"], "ncit_id": t.get("ncitId"), "civic_id": t["id"]})
            involves.add((e["id"], t["name"]))

    return {
        "source": CIVIC, "version": version, "prov": prov,
        "disease": {"id": disease_id, "name": civic["disease"]["name"]},
        "genes": genes,
        "variants": list(variants.values()),
        "variant_of": list(variant_of.values()),
        "evidence": evidence,
        "about_variant": [{"civic_eid": a, "civic_id": b} for a, b in sorted(about)],
        "in_disease": [{"civic_eid": a} for a in sorted(in_disease)],
        "therapies": list(therapies.values()),
        "involves_therapy": [{"civic_eid": a, "therapy": b} for a, b in sorted(involves)],
    }


def build_genes(ot_rows: dict, civic_rows: dict) -> list[dict]:
    """Merge gene info from both sources into one row per HGNC symbol.

    Neptune has no list properties, so a gene found in both sources gets
    source_dataset "opentargets|civic" and source_version aligned position-by-position.
    Computing this here (not in Cypher) keeps it exact and idempotent on reload.
    """
    rows = []
    for symbol in sorted(set(ot_rows["genes"]) | set(civic_rows["genes"])):
        o, c = ot_rows["genes"].get(symbol, {}), civic_rows["genes"].get(symbol, {})
        sources = [s for s, info in ((ot_rows, o), (civic_rows, c)) if info]
        rows.append({
            "symbol": symbol,
            "ensembl_id": o.get("ensembl_id"),
            "alt_ensembl_ids": o.get("alt_ensembl_ids"),
            "entrez_id": c.get("entrez_id"),
            "name": o.get("name"),
            "biotype": o.get("biotype"),
            "source_dataset": LIST_SEP.join(s["source"] for s in sources),
            "source_version": LIST_SEP.join(s["version"] for s in sources),
        })
    return rows


def count_supplements(ot_rows: dict, civic_rows: dict, gene_rows: list[dict]) -> tuple[Counter, Counter]:
    nodes, edges = Counter(), Counter()
    nodes["Disease"] = len({ot_rows["disease"]["id"], civic_rows["disease"]["id"]})
    nodes["Gene"] = len(gene_rows)
    nodes["Drug"] = len(ot_rows["drugs"])
    nodes["Variant"] = len(civic_rows["variants"])
    nodes["Evidence"] = len(civic_rows["evidence"])
    nodes["Therapy"] = len(civic_rows["therapies"])
    edges["ASSOCIATED_TARGET"] = len(ot_rows["associated_targets"])
    edges["CANDIDATE_FOR"] = len(ot_rows["candidate_for"])
    edges["TARGETS"] = len(ot_rows["targets"])
    edges["VARIANT_OF"] = len(civic_rows["variant_of"])
    edges["ABOUT_VARIANT"] = len(civic_rows["about_variant"])
    edges["IN_DISEASE"] = len(civic_rows["in_disease"])
    edges["INVOLVES_THERAPY"] = len(civic_rows["involves_therapy"])
    return nodes, edges


# ---------------------------------------------------------------------------
# Open Targets + CIViC: batched UNWIND writes
# ---------------------------------------------------------------------------

# Provenance SET fragment for nodes/edges owned by a single source.
PROV = "{v}.source_dataset = $prov.source_dataset, {v}.source_version = $prov.source_version, {v}.edge_origin = $prov.edge_origin"


def run_batched(tx, query: str, rows: list[dict], **params) -> None:
    for i in range(0, len(rows), BATCH_SIZE):
        tx.run(query, rows=rows[i:i + BATCH_SIZE], **params)


def load_genes(tx, gene_rows: list[dict]) -> None:
    run_batched(tx, """
        UNWIND $rows AS row
        MERGE (g:Gene {symbol: row.symbol})
        SET g.ensembl_id = row.ensembl_id, g.alt_ensembl_ids = row.alt_ensembl_ids,
            g.entrez_id = row.entrez_id, g.name = row.name, g.biotype = row.biotype,
            g.source_dataset = row.source_dataset, g.source_version = row.source_version,
            g.edge_origin = $edge_origin
        """, gene_rows, edge_origin=RECORDED)


def _merge_disease(tx, disease: dict) -> None:
    # Disease is shared with Monarch/DisMech; only name it if this load created it.
    tx.run("MERGE (d:Disease {id: $id}) ON CREATE SET d.name = $name", id=disease["id"], name=disease["name"])


def load_opentargets(tx, rows: dict) -> None:
    p = {"prov": rows["prov"], "disease_id": rows["disease"]["id"]}
    _merge_disease(tx, rows["disease"])
    run_batched(tx, f"""
        UNWIND $rows AS row
        MATCH (d:Disease {{id: $disease_id}})
        MATCH (g:Gene {{symbol: row.symbol}})
        MERGE (d)-[r:ASSOCIATED_TARGET]->(g)
        SET r.score = row.score, {PROV.format(v='r')}
        """, rows["associated_targets"], **p)
    run_batched(tx, f"""
        UNWIND $rows AS row
        MERGE (dr:Drug {{chembl_id: row.chembl_id}})
        SET dr.name = row.name, dr.drug_type = row.drug_type, dr.max_stage = row.max_stage,
            {PROV.format(v='dr')}
        """, rows["drugs"], **p)
    run_batched(tx, f"""
        UNWIND $rows AS row
        MATCH (dr:Drug {{chembl_id: row.chembl_id}})
        MATCH (d:Disease {{id: $disease_id}})
        MERGE (dr)-[r:CANDIDATE_FOR]->(d)
        SET r.clinical_stage = row.clinical_stage, r.candidate_id = row.candidate_id,
            r.clinical_report_ids = row.clinical_report_ids,
            r.clinical_report_urls = row.clinical_report_urls, {PROV.format(v='r')}
        """, rows["candidate_for"], **p)
    # Keyed on mechanism: a drug can hit one gene via several mechanisms (e.g. imatinib/ABL1).
    run_batched(tx, f"""
        UNWIND $rows AS row
        MATCH (dr:Drug {{chembl_id: row.chembl_id}})
        MATCH (g:Gene {{symbol: row.symbol}})
        MERGE (dr)-[r:TARGETS {{mechanism: row.mechanism}}]->(g)
        SET r.action_type = row.action_type, {PROV.format(v='r')}
        """, rows["targets"], **p)


def load_civic(tx, rows: dict) -> None:
    p = {"prov": rows["prov"], "disease_id": rows["disease"]["id"]}
    _merge_disease(tx, rows["disease"])
    run_batched(tx, f"""
        UNWIND $rows AS row
        MERGE (v:Variant {{civic_id: row.civic_id}})
        SET v.name = row.name, v.feature_name = row.feature_name, v.full_name = row.full_name,
            {PROV.format(v='v')}
        """, rows["variants"], **p)
    run_batched(tx, f"""
        UNWIND $rows AS row
        MATCH (v:Variant {{civic_id: row.civic_id}})
        MATCH (g:Gene {{symbol: row.symbol}})
        MERGE (v)-[r:VARIANT_OF]->(g)
        SET r.fusion_partner = row.fusion_partner, {PROV.format(v='r')}
        """, rows["variant_of"], **p)
    run_batched(tx, f"""
        UNWIND $rows AS row
        MERGE (e:Evidence {{civic_eid: row.civic_eid}})
        SET e.name = row.name, e.evidence_type = row.evidence_type, e.level = row.level,
            e.direction = row.direction, e.significance = row.significance, e.status = row.status,
            e.rating = row.rating, e.variant_origin = row.variant_origin,
            e.molecular_profile = row.molecular_profile, e.pmid = row.pmid,
            e.citation = row.citation, e.description = row.description, {PROV.format(v='e')}
        """, rows["evidence"], **p)
    run_batched(tx, f"""
        UNWIND $rows AS row
        MATCH (e:Evidence {{civic_eid: row.civic_eid}})
        MATCH (v:Variant {{civic_id: row.civic_id}})
        MERGE (e)-[r:ABOUT_VARIANT]->(v)
        SET {PROV.format(v='r')}
        """, rows["about_variant"], **p)
    run_batched(tx, f"""
        UNWIND $rows AS row
        MATCH (e:Evidence {{civic_eid: row.civic_eid}})
        MATCH (d:Disease {{id: $disease_id}})
        MERGE (e)-[r:IN_DISEASE]->(d)
        SET {PROV.format(v='r')}
        """, rows["in_disease"], **p)
    run_batched(tx, f"""
        UNWIND $rows AS row
        MERGE (t:Therapy {{name: row.name}})
        SET t.ncit_id = row.ncit_id, t.civic_id = row.civic_id, {PROV.format(v='t')}
        """, rows["therapies"], **p)
    run_batched(tx, f"""
        UNWIND $rows AS row
        MATCH (e:Evidence {{civic_eid: row.civic_eid}})
        MATCH (t:Therapy {{name: row.therapy}})
        MERGE (e)-[r:INVOLVES_THERAPY]->(t)
        SET {PROV.format(v='r')}
        """, rows["involves_therapy"], **p)


# ---------------------------------------------------------------------------
# Entry point
# ---------------------------------------------------------------------------

def read_sources(require_all: bool) -> dict:
    files = {
        "monarch": ("monarch_mm.json", json.loads),
        "dismech": ("dismech_mm.yaml", yaml.safe_load),
        OT: ("opentargets_mm.json", json.loads),
        CIVIC: ("civic_mm.json", json.loads),
    }
    data = {}
    for key, (name, parse) in files.items():
        path = DATA_DIR / name
        if not path.exists():
            if require_all:
                sys.exit(f"Missing {path}; run the fetch scripts first (see README).")
            print(f"  {name}: not found, skipped")
            continue
        data[key] = parse(path.read_text(encoding="utf-8"))
    return data


def count_sections(data: dict) -> list[tuple[str, Counter, Counter]]:
    sections = []
    if "monarch" in data:
        sections.append(("Monarch", *count_monarch(data["monarch"])))
    if "dismech" in data:
        sections.append(("DisMech", *count_dismech(data["dismech"])))
    if OT in data and CIVIC in data:
        ot_rows, civic_rows = build_opentargets(data[OT]), build_civic(data[CIVIC])
        sections.append(("Open Targets + CIViC", *count_supplements(ot_rows, civic_rows, build_genes(ot_rows, civic_rows))))
    return sections


def expected_totals(data: dict) -> tuple[Counter, Counter]:
    """Whole-graph counts after a load into an empty database (Disease is shared across sources)."""
    nodes, edges = Counter(), Counter()
    for _, n, e in count_sections(data):
        nodes.update(n)
        edges.update(e)
    disease_ids = set()
    if "monarch" in data:
        disease_ids |= {data["monarch"]["entity"]["id"]} | {a["subject"] for a in data["monarch"]["associations"]}
    if "dismech" in data:
        disease_ids.add(data["dismech"]["disease_term"]["term"]["id"])
    for key in (OT, CIVIC):
        if key in data:
            disease_ids.add(data[key]["disease"]["graph_disease_id"])
    if disease_ids:
        nodes["Disease"] = len(disease_ids)
    return nodes, edges


def dry_run() -> None:
    print(f"Dry run: reading {DATA_DIR} (no database connection)")
    data = read_sources(require_all=False)
    sections = count_sections(data)
    if OT in data and CIVIC in data:
        ot_genes = build_opentargets(data[OT])["genes"]
        civic_genes = build_civic(data[CIVIC])["genes"]
        shared = sorted(set(ot_genes) & set(civic_genes))
        print(f"  genes in both Open Targets and CIViC ({len(shared)}): {', '.join(shared)}")
    for title, nodes, edges in sections:
        print(f"\n{title}")
        for kind, counter in (("nodes", nodes), ("edges", edges)):
            for label, n in counter.items():
                print(f"  {kind[:-1]:4} {label:18} {n:5}")
    print("\n(Disease nodes are shared across sources: all sources use MONDO:0009693.)")
    if len(sections) > 1:
        nodes, edges = expected_totals(data)
        print(f"Whole graph: {sum(nodes.values())} nodes, {sum(edges.values())} edges (compare with --verify)")


# Read-only; labels(), type(), count() and UNWIND are all on Neptune's supported list.
VERIFY_NODES = "MATCH (n) UNWIND labels(n) AS label RETURN label, count(*) AS n"
VERIFY_EDGES = "MATCH ()-[r]->() RETURN type(r) AS type, count(*) AS n"


def read_counts(tx) -> tuple[dict, dict]:
    nodes = {rec["label"]: rec["n"] for rec in tx.run(VERIFY_NODES)}
    edges = {rec["type"]: rec["n"] for rec in tx.run(VERIFY_EDGES)}
    return nodes, edges


def verify(neptune: bool) -> None:
    driver = connect(neptune)
    with driver.session() as session:
        nodes, edges = session.execute_read(read_counts)
    driver.close()

    print(f"Verify: counts in {'Neptune' if neptune else 'Neo4j'} at {NEO4J_URI} (read-only)")
    data = read_sources(require_all=False)
    exp_nodes, exp_edges = expected_totals(data) if data else (Counter(), Counter())
    have_expected = bool(exp_nodes or exp_edges)
    if have_expected:
        print("Expected counts come from the local data files (same as --dry-run).")
    mismatches = 0
    for kind, actual, expected in (("node", nodes, exp_nodes), ("edge", edges, exp_edges)):
        for name in sorted(set(actual) | set(expected)):
            got = actual.get(name, 0)
            line = f"  {kind} {name:18} {got:6}"
            if have_expected:
                want = expected.get(name, 0)
                ok = got == want
                mismatches += not ok
                line += f"   expected {want:6}   {'ok' if ok else 'DIFFERENT'}"
            print(line)
    print(f"Total: {sum(nodes.values())} nodes, {sum(edges.values())} edges")
    if have_expected:
        print("All counts match." if not mismatches else f"{mismatches} count(s) differ from the local files.")


def connect(neptune: bool):
    from neo4j import GraphDatabase  # imported here so --dry-run works without the driver

    if neptune:
        if "NEO4J_URI" not in os.environ:
            sys.exit("NEPTUNE_MODE needs NEO4J_URI=bolt://<neptune-cluster-endpoint>:8182")
        return GraphDatabase.driver(NEO4J_URI, auth=NEPTUNE_PLACEHOLDER_AUTH, encrypted=True)
    return GraphDatabase.driver(NEO4J_URI, auth=NEO4J_AUTH)


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__.split("\n")[0])
    parser.add_argument("--dry-run", action="store_true", help="read files and print counts; no DB connection")
    parser.add_argument("--verify", action="store_true", help="read-only: print node/edge counts in the database")
    parser.add_argument("--neptune", action="store_true", help="target Amazon Neptune (same as NEPTUNE_MODE=1)")
    args = parser.parse_args()

    if args.dry_run:
        dry_run()
        return

    neptune = args.neptune or os.environ.get("NEPTUNE_MODE", "").lower() in ("1", "true", "yes")
    if args.verify:
        verify(neptune)
        return

    data = read_sources(require_all=True)
    ot_rows, civic_rows = build_opentargets(data[OT]), build_civic(data[CIVIC])
    gene_rows = build_genes(ot_rows, civic_rows)

    driver = connect(neptune)
    with driver.session() as session:
        session.execute_write(load_monarch, data["monarch"], neptune)
        session.execute_write(load_dismech, data["dismech"])
        session.execute_write(load_genes, gene_rows)
        session.execute_write(load_opentargets, ot_rows)
        session.execute_write(load_civic, civic_rows)
    driver.close()
    target = "Neptune" if neptune else "Neo4j"
    print(f"Loaded Monarch, DisMech, Open Targets and CIViC into {target} at {NEO4J_URI}.")


if __name__ == "__main__":
    main()
