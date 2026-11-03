"""Fetch pinned disposable sources and apply the ORION frontend overlay."""

import argparse
import json
import subprocess
from pathlib import Path

from apply_frontend import apply

ROOT = Path(__file__).resolve().parents[1]


def run(*args, cwd=None):
    subprocess.run(args, cwd=cwd, check=True)


if __name__ == "__main__":
    parser = argparse.ArgumentParser()
    parser.add_argument(
        "--without-overlay",
        action="store_true",
        help="Fetch pristine sources before the first upstream AWS deployment",
    )
    args = parser.parse_args()
    pins = json.loads((ROOT / "sources.json").read_text())
    sandbox = ROOT / "vendor/sandbox"
    sandbox.parent.mkdir(exist_ok=True)
    if not sandbox.exists():
        run("git", "clone", "--no-checkout", pins["sandbox"]["url"], str(sandbox))
        run("git", "fetch", "--depth=1", "origin", pins["sandbox"]["commit"], cwd=sandbox)
        run("git", "checkout", "--detach", pins["sandbox"]["commit"], cwd=sandbox)
    else:
        actual = subprocess.check_output(["git", "-C", str(sandbox), "rev-parse", "HEAD"], text=True).strip()
        if actual != pins["sandbox"]["commit"]:
            raise SystemExit(
                "Existing sandbox does not match sources.json; move it aside or select the correct revision"
            )
    source_pins = json.loads((sandbox / "sources.json").read_text())
    if source_pins["frontend"] != pins["frontend"]:
        raise SystemExit("Frontend pin disagrees with the pinned sandbox sources.json")
    frontend = sandbox / ".sources/frontend"
    if not (frontend / "src/pages/orion.tsx").exists():
        run("python3", "scripts/fetch_sources.py", cwd=sandbox)
    if not args.without_overlay:
        apply(frontend)
        print(
            "Run the Compose commands in README.md; upstream start.sh would reject the intentional frontend edits."
        )
