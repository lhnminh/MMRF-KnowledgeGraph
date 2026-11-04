"""Fetch the molecular targets of the drugs named in the DisMech records, from Open Targets.

DisMech names a drug's target only in free text (for example "MEK1/2 inhibitor"), so no edge links a
drug to a gene. Open Targets records each drug's mechanism of action with its target gene and the
label or document that supports it. This script takes every therapeutic agent in data/raw/dismech_*.yaml,
finds the Open Targets drug of the same name, and saves its mechanisms of action to
data/raw/drug_targets.json.

Open Targets Platform data is CC0 (https://platform-docs.opentargets.org/licence). Only the drugs
already in the graph are queried through the public GraphQL API; no bulk downloads.

Matching is by exact drug name, never by fuzzy search, and an agent with no match is saved as
"not_found" rather than guessed. Targets are joined to the graph on HGNC id, taken from the target's
own HGNC cross-reference; a target without one is saved with hgnc_id null and is not loaded.
"""

import argparse
import glob
import json
from datetime import datetime, timezone
from pathlib import Path

import requests
import yaml

API = "https://api.platform.opentargets.org/api/v4/graphql"
RAW_DIR = Path(__file__).resolve().parent.parent / "data" / "raw"
OUT_PATH = RAW_DIR / "drug_targets.json"

SEARCH = """
query Search($name: String!) {
  search(queryString: $name, entityNames: ["drug"], page: {index: 0, size: 5}) { hits { id name } }
}
"""
DRUG = """
query Drug($id: String!) {
  drug(chemblId: $id) {
    id
    name
    mechanismsOfAction {
      rows {
        mechanismOfAction
        actionType
        targets { id approvedSymbol }
        references { source ids }
      }
    }
  }
}
"""
TARGET = """
query Target($id: String!) { target(ensemblId: $id) { id approvedSymbol dbXrefs { id source } } }
"""
VERSION = "query { meta { dataVersion { year month } } }"


def post(session, query, variables=None):
    response = session.post(API, json={"query": query, "variables": variables or {}}, timeout=(5, 40))
    response.raise_for_status()
    body = response.json()
    if body.get("errors"):
        raise RuntimeError(f"Open Targets returned errors: {body['errors'][:1]}")
    return body["data"]


def agents_in(paths):
    """{agent id: agent name} for every therapeutic agent in the given DisMech files."""
    agents = {}
    for path in paths:
        record = yaml.safe_load(Path(path).read_text(encoding="utf-8"))
        for treatment in record.get("treatments", []):
            for agent in treatment.get("treatment_term", {}).get("therapeutic_agent", []):
                agents[agent["term"]["id"]] = agent["preferred_term"]
    return agents


def hgnc_id(session, ensembl_id, cache):
    """'HGNC:<n>' from the target's own cross-reference, or None. Cached because targets repeat."""
    if ensembl_id not in cache:
        target = post(session, TARGET, {"id": ensembl_id})["target"] or {}
        numbers = [x["id"] for x in target.get("dbXrefs", []) if x["source"] == "HGNC"]
        cache[ensembl_id] = f"HGNC:{numbers[0]}" if numbers else None
    return cache[ensembl_id]


def fetch_agent(session, agent_id, name, cache):
    record = {"agent_id": agent_id, "agent_label": name, "chembl_id": None, "status": "not_found", "mechanisms": []}
    hits = post(session, SEARCH, {"name": name})["search"]["hits"]
    exact = [h for h in hits if h["name"].lower() == name.lower()]
    if not exact:
        return record
    drug = post(session, DRUG, {"id": exact[0]["id"]})["drug"]
    record["chembl_id"] = drug["id"]
    record["status"] = "matched"
    for row in (drug.get("mechanismsOfAction") or {}).get("rows", []):
        record["mechanisms"].append(
            {
                "mechanism_of_action": row["mechanismOfAction"],
                "action_type": row["actionType"],
                "references": [f"{r['source']}:{i}" for r in row.get("references") or [] for i in r["ids"]],
                "targets": [
                    {"ensembl_id": t["id"], "symbol": t["approvedSymbol"], "hgnc_id": hgnc_id(session, t["id"], cache)}
                    for t in row["targets"]
                ],
            }
        )
    return record


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--dismech", nargs="+", default=sorted(glob.glob(str(RAW_DIR / "dismech_*.yaml"))))
    args = parser.parse_args()
    agents = agents_in(args.dismech)
    if not agents:
        parser.error("No therapeutic agents found in the DisMech files")

    session = requests.Session()
    version = post(session, VERSION)["meta"]["dataVersion"]
    cache = {}
    records = [fetch_agent(session, agent_id, name, cache) for agent_id, name in sorted(agents.items())]

    OUT_PATH.write_text(
        json.dumps(
            {
                "source_version": f"{version['year']}.{version['month']}",
                "retrieved_at": datetime.now(timezone.utc).isoformat(),
                "source_url": API,
                "agents": records,
            },
            indent=2,
        ),
        encoding="utf-8",
    )
    matched = [r for r in records if r["status"] == "matched"]
    print(f"Wrote {len(records)} agents ({len(matched)} matched, {len(records) - len(matched)} not found) -> {OUT_PATH}")
    for r in records:
        targets = sorted({t["symbol"] for m in r["mechanisms"] for t in m["targets"]})
        print(f"  {r['agent_label']:14} {r['status']:10} {', '.join(targets) or '-'}")


if __name__ == "__main__":
    main()
