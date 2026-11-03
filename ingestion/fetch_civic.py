"""Pull all CIViC evidence items for multiple myeloma (DOID:9538).

CIViC data is CC0 (https://civic.readthedocs.io/en/latest/about/faq.html).
Only the multiple myeloma slice is queried via the public GraphQL API; no bulk downloads.
CIViC's default evidence query returns accepted + submitted items (rejected are excluded);
each item keeps its `status` so the loader can tell curated from pending evidence.

CIViC uses Disease Ontology DOID:9538; our graph uses MONDO:0009693. CIViC's own cross-reference
(myDiseaseInfo.mondoId) is saved as `disease.mondo_id` so the two can be linked.
Every gene carries `hgnc_symbol` (CIViC's gene name) and `entrezId`; fusions list both partner
genes, so evidence can be joined with Open Targets on symbol.
"""
import hashlib
import json
from datetime import datetime, timezone
from pathlib import Path

import requests

API = "https://civicdb.org/api/graphql"
DOID = "9538"  # multiple myeloma
GRAPH_DISEASE_ID = "MONDO:0009693"  # our graph's ID; CIViC's own xref must match it
OUT_PATH = Path(__file__).resolve().parent.parent / "data" / "raw" / "civic_mm.json"
RAW_GLOB = "civic_mm.raw.*.json"  # exact API replies, one per request; hash to raw_response_sha256

DISEASE_QUERY = """
query($doid: String!) {
  diseases(doid: $doid) { nodes { id name doid myDiseaseInfo { mondoId } } }
  dataReleases { name }
}
"""

EVIDENCE_QUERY = """
query($diseaseId: Int!, $after: String) {
  evidenceItems(diseaseId: $diseaseId, first: 100, after: $after) {
    totalCount
    pageInfo { hasNextPage endCursor }
    nodes {
      id
      name
      status
      evidenceType
      evidenceLevel
      evidenceDirection
      evidenceRating
      significance
      variantOrigin
      description
      disease { id name doid }
      molecularProfile {
        id
        name
        variants {
          id
          name
          feature {
            id
            name
            featureInstance {
              __typename
              ... on Gene { hgnc_symbol: name entrezId }
              ... on Fusion {
                fivePrimeGene { hgnc_symbol: name entrezId }
                threePrimeGene { hgnc_symbol: name entrezId }
              }
            }
          }
        }
      }
      therapies { id name ncitId }
      source { citationId sourceType citation }
    }
  }
}
"""


raw_responses: list[bytes] = []  # each raw API response body, in request order


def graphql(query: str, variables: dict) -> dict:
    resp = requests.post(API, json={"query": query, "variables": variables}, timeout=60)
    resp.raise_for_status()
    raw_responses.append(resp.content)
    body = resp.json()
    if "errors" in body:
        raise RuntimeError(body["errors"])
    return body["data"]


def fetch_all_evidence(disease_id: int) -> list[dict]:
    items: list[dict] = []
    after = None
    while True:
        page = graphql(EVIDENCE_QUERY, {"diseaseId": disease_id, "after": after})["evidenceItems"]
        items.extend(page["nodes"])
        if not page["pageInfo"]["hasNextPage"]:
            break
        after = page["pageInfo"]["endCursor"]
    if len(items) != page["totalCount"]:
        raise RuntimeError(
            f"CIViC returned {len(items)} evidence items but reported totalCount={page['totalCount']}"
        )
    return items


def normalize_mondo(mondo_id: str | None) -> str:
    """'MONDO_0009693', 'mondo:0009693' or '0009693' -> 'MONDO:0009693'."""
    value = (mondo_id or "").strip().upper().replace("_", ":")
    if value.isdigit():
        value = f"MONDO:{value.zfill(7)}"
    return value


def latest_monthly_release(releases: list[dict]) -> str:
    """Pick the newest dated release (names like '01-Nov-2026'); skips 'nightly'."""
    dated = []
    for release in releases:
        try:
            dated.append((datetime.strptime(release["name"], "%d-%b-%Y"), release["name"]))
        except ValueError:
            continue
    if not dated:
        raise RuntimeError("CIViC returned no dated monthly releases")
    return max(dated)[1]


def main() -> None:
    fetched_at = datetime.now(timezone.utc)
    meta = graphql(DISEASE_QUERY, {"doid": DOID})
    disease = meta["diseases"]["nodes"][0]
    mondo_id = normalize_mondo((disease.get("myDiseaseInfo") or {}).get("mondoId"))
    if mondo_id != GRAPH_DISEASE_ID:
        raise RuntimeError(
            f"CIViC maps DOID:{DOID} to {mondo_id or 'no MONDO ID'}, expected {GRAPH_DISEASE_ID}"
        )
    release = latest_monthly_release(meta["dataReleases"])  # the API itself serves live data
    evidence = fetch_all_evidence(disease["id"])

    OUT_PATH.parent.mkdir(parents=True, exist_ok=True)
    for old in OUT_PATH.parent.glob(RAW_GLOB):
        old.unlink()
    raw_files = []
    for i, raw in enumerate(raw_responses, start=1):
        raw_path = OUT_PATH.with_name(f"civic_mm.raw.{i}.json")
        raw_path.write_bytes(raw)
        raw_files.append(raw_path.name)

    OUT_PATH.write_text(
        json.dumps(
            {
                "source": "CIViC",
                "source_url": API,
                "data_version": f"live API (latest monthly release: {release})",
                "fetch_date": fetched_at.date().isoformat(),
                "fetch_timestamp_utc": fetched_at.isoformat(timespec="seconds"),
                "raw_response_files": raw_files,
                "raw_response_sha256": [hashlib.sha256(raw).hexdigest() for raw in raw_responses],
                "license": "CC0 1.0",
                "disease": {
                    "id": disease["id"],
                    "name": disease["name"],
                    "doid": f"DOID:{disease['doid']}",
                    "mondo_id": mondo_id,
                    "graph_disease_id": GRAPH_DISEASE_ID,
                },
                "evidence_items": evidence,
            },
            indent=2,
        )
    )
    print(f"Wrote {len(evidence)} evidence items for DOID:{DOID} -> {OUT_PATH}")


if __name__ == "__main__":
    main()
