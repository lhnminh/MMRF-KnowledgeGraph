import argparse
from pathlib import Path

from mmrf_knowledgegraph.etl import transform

if __name__ == "__main__":
    p = argparse.ArgumentParser(
        description="Write Neptune openCypher CSV and local graph; raw inputs stay ignored."
    )
    p.add_argument("--output", type=Path, default=Path("data/transformed"))
    p.add_argument(
        "--include-dismech", action="store_true", help="Private use only after checking YAML data terms"
    )
    p.add_argument("--curated-only", action="store_true", help="Portable demo without local raw downloads")
    a = p.parse_args()
    graph = transform(a.output, a.include_dismech, not a.curated_only, include_external=not a.curated_only)
    print(f"Wrote {len(graph['nodes'])} nodes and {len(graph['relationships'])} relationships to {a.output}")
