"""Pull the top associated targets + drugs/clinical candidates for multiple myeloma from Open Targets.

Open Targets Platform data is CC0 (https://platform-docs.opentargets.org/licence).
Only the multiple myeloma slice is queried via the public GraphQL API; no bulk downloads.
Open Targets uses MONDO_0009693, the same disease as our graph's MONDO:0009693 (underscore vs colon).
Every gene carries `hgnc_symbol` (Open Targets' approvedSymbol) so it can be joined with CIViC on symbol.
The API returns tied scores in arbitrary order, so we fetch extra rows, keep the top 50 plus any genes
tied with #50, and sort by (score desc, Ensembl ID) so the target list is the same on every run.
"""
import hashlib
import json
from datetime import datetime, timezone
from pathlib import Path

import requests

API = "https://api.platform.opentargets.org/api/v4/graphql"
DISEASE_ID = "MONDO_0009693"  # plasma cell myeloma / multiple myeloma
TOP_N_TARGETS = 50
FETCH_SIZE = 100  # extra rows so genes tied with #50 are not cut off arbitrarily
OUT_PATH = Path(__file__).resolve().parent.parent / "data" / "raw" / "opentargets_mm.json"
RAW_PATH = OUT_PATH.with_name("opentargets_mm.raw.json")  # exact API reply; hashes to raw_response_sha256

QUERY = """
query($id: String!, $size: Int!) {
  meta {
    dataVersion { year month iteration }
    apiVersion { x y z }
  }
  disease(efoId: $id) {
    id
    name
    associatedTargets(page: {index: 0, size: $size}) {
      count
      rows {
        score
        target { id hgnc_symbol: approvedSymbol approvedName biotype }
      }
    }
    drugAndClinicalCandidates {
      count
      rows {
        id
        maxClinicalStage
        drug {
          id
          name
          drugType
          maximumClinicalStage
          mechanismsOfAction {
            rows {
              mechanismOfAction
              actionType
              targets { id hgnc_symbol: approvedSymbol }
            }
          }
        }
        clinicalReports { id source clinicalStage url }
      }
    }
  }
}
"""


def main() -> None:
    fetched_at = datetime.now(timezone.utc)
    resp = requests.post(
        API,
        json={"query": QUERY, "variables": {"id": DISEASE_ID, "size": FETCH_SIZE}},
        timeout=120,
    )
    resp.raise_for_status()
    OUT_PATH.parent.mkdir(parents=True, exist_ok=True)
    RAW_PATH.write_bytes(resp.content)
    body = resp.json()
    if "errors" in body:
        raise RuntimeError(body["errors"])
    data = body["data"]

    version = data["meta"]["dataVersion"]
    release = f"{version['year']}.{version['month']}"
    if version.get("iteration"):
        release += f".{version['iteration']}"

    disease = data["disease"]
    rows = sorted(disease["associatedTargets"]["rows"], key=lambda r: (-r["score"], r["target"]["id"]))
    cutoff_score = rows[TOP_N_TARGETS - 1]["score"]
    if rows[-1]["score"] == cutoff_score:
        raise RuntimeError("Ties at the cutoff extend past FETCH_SIZE; increase FETCH_SIZE")
    targets = [r for r in rows if r["score"] >= cutoff_score]
    drugs = disease["drugAndClinicalCandidates"]["rows"]

    OUT_PATH.parent.mkdir(parents=True, exist_ok=True)
    OUT_PATH.write_text(
        json.dumps(
            {
                "source": "Open Targets Platform",
                "source_url": API,
                "data_version": release,
                "api_version": data["meta"]["apiVersion"],
                "fetch_date": fetched_at.date().isoformat(),
                "fetch_timestamp_utc": fetched_at.isoformat(timespec="seconds"),
                "raw_response_file": RAW_PATH.name,
                "raw_response_sha256": hashlib.sha256(resp.content).hexdigest(),
                "license": "CC0 1.0",
                "disease": {
                    "id": disease["id"],
                    "name": disease["name"],
                    "graph_disease_id": "MONDO:0009693",
                },
                "total_associated_targets": disease["associatedTargets"]["count"],
                "target_cutoff": {
                    "top_n": TOP_N_TARGETS,
                    "cutoff_score": cutoff_score,
                    "tied_with_cutoff_included": len(targets) - TOP_N_TARGETS,
                },
                "associated_targets": targets,
                "drug_and_clinical_candidates": drugs,
            },
            indent=2,
        )
    )
    print(
        f"Wrote {len(targets)} targets and {len(drugs)} drugs/clinical candidates "
        f"for {DISEASE_ID} (Open Targets {release}) -> {OUT_PATH}"
    )


if __name__ == "__main__":
    main()
