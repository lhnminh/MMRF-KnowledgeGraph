"""Fetch one DisMech YAML record at an explicit commit for local evaluation.

DisMech's bulk-data license is unsettled (see
https://dismech.monarchinitiative.org/downloads/index.html) so we do not
bulk-import the dataset. A rendered page's CC-BY license does not establish
this YAML's redistribution terms. Confirm permission before sharing it or derivatives.

One record per run. The default is Multiple Myeloma -> data/raw/dismech_mm.yaml. Pass
--disorder <File_Name_Without_.yaml> and --name <short name> for another record, for example
  --disorder Juvenile_Myelomonocytic_Leukemia --name jmml  ->  data/raw/dismech_jmml.yaml
"""

import argparse
import hashlib
import json
import re
from datetime import datetime, timezone
from pathlib import Path

import requests

SOURCE_URL = (
    "https://raw.githubusercontent.com/monarch-initiative/dismech"
    "/{revision}/kb/disorders/{disorder}.yaml"
)
RAW_DIR = Path(__file__).resolve().parent.parent / "data" / "raw"
DEFAULT_DISORDER = "Multiple_Myeloma"
DEFAULT_NAME = "mm"


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--revision", required=True, help="Exact 40-character source Git commit SHA")
    parser.add_argument("--disorder", default=DEFAULT_DISORDER, help="DisMech kb/disorders file name, no .yaml")
    parser.add_argument("--name", default=DEFAULT_NAME, help="Short lowercase name used in the output file name")
    args = parser.parse_args()
    if not re.fullmatch(r"[a-fA-F0-9]{40}", args.revision):
        parser.error("Provide an immutable 40-character commit SHA")
    if not re.fullmatch(r"[A-Za-z0-9_.\-]+", args.disorder):
        parser.error("--disorder may only contain letters, digits, '_', '.' and '-'")
    if not re.fullmatch(r"[a-z0-9_]+", args.name):
        parser.error("--name may only contain lowercase letters, digits and '_'")
    url = SOURCE_URL.format(revision=args.revision, disorder=args.disorder)
    resp = requests.get(url, timeout=30)
    resp.raise_for_status()

    out_path = RAW_DIR / f"dismech_{args.name}.yaml"
    out_path.parent.mkdir(parents=True, exist_ok=True)
    out_path.write_text(resp.text, encoding="utf-8")
    out_path.with_suffix(".yaml.provenance.json").write_text(
        json.dumps(
            {
                "retrieved_at": datetime.now(timezone.utc).isoformat(),
                "source_url": url,
                "source_revision": args.revision,
                "sha256": hashlib.sha256(out_path.read_bytes()).hexdigest(),
                "redistribution_terms": "unconfirmed",
            },
            indent=2,
        )
    )
    print(f"Wrote DisMech {args.disorder} record -> {out_path}")
    print(f"Source: {url}. Redistribution terms remain unconfirmed.")


if __name__ == "__main__":
    main()
