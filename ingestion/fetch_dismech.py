"""Fetch one DisMech YAML record at an explicit commit for local evaluation.

DisMech's bulk-data license is unsettled (see
https://dismech.monarchinitiative.org/downloads/index.html) so we do not
bulk-import the dataset. A rendered page's CC-BY license does not establish
this YAML's redistribution terms. Confirm permission before sharing it or derivatives.
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
    "/{revision}/kb/disorders/Multiple_Myeloma.yaml"
)
OUT_PATH = Path(__file__).resolve().parent.parent / "data" / "raw" / "dismech_mm.yaml"


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--revision", required=True, help="Exact 40-character source Git commit SHA")
    args = parser.parse_args()
    if not re.fullmatch(r"[a-fA-F0-9]{40}", args.revision):
        parser.error("Provide an immutable 40-character commit SHA")
    url = SOURCE_URL.format(revision=args.revision)
    resp = requests.get(url, timeout=30)
    resp.raise_for_status()

    OUT_PATH.parent.mkdir(parents=True, exist_ok=True)
    OUT_PATH.write_text(resp.text)
    OUT_PATH.with_suffix(".yaml.provenance.json").write_text(
        json.dumps(
            {
                "retrieved_at": datetime.now(timezone.utc).isoformat(),
                "source_url": url,
                "source_revision": args.revision,
                "sha256": hashlib.sha256(OUT_PATH.read_bytes()).hexdigest(),
                "redistribution_terms": "unconfirmed",
            },
            indent=2,
        )
    )
    print(f"Wrote DisMech Multiple Myeloma record -> {OUT_PATH}")
    print(f"Source: {url}. Redistribution terms remain unconfirmed.")


if __name__ == "__main__":
    main()
