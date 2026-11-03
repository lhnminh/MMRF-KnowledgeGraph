"""Pull the single DisMech multiple myeloma disorder page.

DisMech's bulk-data license is unsettled (see
https://dismech.monarchinitiative.org/downloads/index.html) so we do not
bulk-import the dataset. The rendered site pages are CC-BY 4.0; we pull only
the one Multiple Myeloma disorder record and cite the source commit.
"""
from pathlib import Path

import requests

SOURCE_URL = (
    "https://raw.githubusercontent.com/monarch-initiative/dismech"
    "/main/kb/disorders/Multiple_Myeloma.yaml"
)
OUT_PATH = Path(__file__).resolve().parent.parent / "data" / "raw" / "dismech_mm.yaml"


def main() -> None:
    resp = requests.get(SOURCE_URL, timeout=30)
    resp.raise_for_status()

    OUT_PATH.parent.mkdir(parents=True, exist_ok=True)
    OUT_PATH.write_text(resp.text)
    print(f"Wrote DisMech Multiple Myeloma record -> {OUT_PATH}")
    print(f"Source: {SOURCE_URL} (pin a commit SHA here before redistributing)")


if __name__ == "__main__":
    main()
