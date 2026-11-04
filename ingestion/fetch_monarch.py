"""Pull a disease entity + its associations from the Monarch API.

This legacy fetcher is retained for compatibility. Prefer scripts/extract.py's
bounded calls and retrieval manifest. Check individual upstream data terms;
Monarch's preferred-license guidance is not a blanket CC0 grant.

The default is multiple myeloma -> data/raw/monarch_mm.json. For another disease pass
--mondo-id, --name and any --gene HGNC ids whose gene-to-disease association you need, for example
  --mondo-id MONDO:0011908 --name jmml --gene HGNC:6407  ->  data/raw/monarch_jmml.json
Gene associations are fetched with the gene as subject, as Monarch stores them, and are kept
at the specificity Monarch records (gene level; this fetcher never invents a variant).
"""
import argparse
import json
import re
from pathlib import Path

import requests

API = "https://api.monarchinitiative.org/v3/api"
DEFAULT_MONDO_ID = "MONDO:0009693"  # plasma cell myeloma / multiple myeloma
RAW_DIR = Path(__file__).resolve().parent.parent / "data" / "raw"


def fetch_all_associations(subject: str, limit: int = 100, obj: "str | None" = None) -> list[dict]:
    associations: list[dict] = []
    offset = 0
    while True:
        params = {"subject": subject, "limit": limit, "offset": offset}
        if obj:
            params["object"] = obj
        resp = requests.get(f"{API}/association", params=params, timeout=30)
        resp.raise_for_status()
        page = resp.json()
        associations.extend(page["items"])
        offset += limit
        if offset >= page["total"]:
            break
    return associations


def fetch_source_version() -> str:
    resp = requests.get(f"{API}/version", timeout=30)
    resp.raise_for_status()
    return resp.json()["monarch_kg_version"]


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--mondo-id", default=DEFAULT_MONDO_ID)
    parser.add_argument("--name", default="mm", help="Short lowercase name used in the output file name")
    parser.add_argument("--gene", action="append", default=[], help="HGNC id; repeatable")
    args = parser.parse_args()
    if not re.fullmatch(r"MONDO:\d+", args.mondo_id):
        parser.error("--mondo-id must look like MONDO:0011908")
    if not re.fullmatch(r"[a-z0-9_]+", args.name):
        parser.error("--name may only contain lowercase letters, digits and '_'")
    for gene in args.gene:
        if not re.fullmatch(r"HGNC:\d+", gene):
            parser.error("--gene must look like HGNC:6407")

    out_path = RAW_DIR / f"monarch_{args.name}.json"
    entity = requests.get(f"{API}/entity/{args.mondo_id}", timeout=30).json()
    by_id = {a["id"]: a for a in fetch_all_associations(args.mondo_id)}
    for gene in args.gene:
        for association in fetch_all_associations(gene, obj=args.mondo_id):
            by_id[association["id"]] = association
    source_version = fetch_source_version()

    out_path.parent.mkdir(parents=True, exist_ok=True)
    out_path.write_text(
        json.dumps(
            {"source_version": source_version, "entity": entity, "associations": list(by_id.values())}, indent=2
        ),
        encoding="utf-8",
    )
    print(f"Wrote {len(by_id)} associations for {args.mondo_id} (Monarch KG {source_version}) -> {out_path}")


if __name__ == "__main__":
    main()
