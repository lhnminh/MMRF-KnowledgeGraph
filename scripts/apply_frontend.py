"""Overlay only ORION-owned files onto the exact sandbox-pinned frontend revision."""

import argparse
import json
import shutil
import subprocess
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]


def apply(target, verify=True):
    pins = json.loads((ROOT / "sources.json").read_text())
    if verify:
        actual = subprocess.check_output(["git", "-C", str(target), "rev-parse", "HEAD"], text=True).strip()
        if actual != pins["frontend"]["commit"]:
            raise SystemExit("Frontend HEAD does not match the sandbox frontend pin in sources.json")
    for source in (ROOT / "frontend/overlay").rglob("*"):
        if source.is_file():
            dest = target / source.relative_to(ROOT / "frontend/overlay")
            if dest.exists() and dest.read_bytes() != source.read_bytes():
                raise SystemExit(f"Refusing to replace a modified ORION file: {dest}")
            dest.parent.mkdir(parents=True, exist_ok=True)
            shutil.copyfile(source, dest)
    print(f"Applied ORION /orion page and /api/orion/chat to {target}")


if __name__ == "__main__":
    parser = argparse.ArgumentParser()
    parser.add_argument("frontend", type=Path)
    args = parser.parse_args()
    apply(args.frontend.resolve())
