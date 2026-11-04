"""Build separate source-fetch and incremental-ingest Lambda packages without modifying the core package."""

import shutil
import tempfile
import zipfile
from pathlib import Path

from build_lambda import ZIP_TIMESTAMP, install

ROOT = Path(__file__).resolve().parent.parent
OUTPUT = ROOT / "terraform" / "source-pipeline"


def archive(source, output):
    with zipfile.ZipFile(output, "w", zipfile.ZIP_DEFLATED) as package:
        for path in sorted(source.rglob("*")):
            if not path.is_file() or "__pycache__" in path.parts:
                continue
            info = zipfile.ZipInfo(path.relative_to(source).as_posix(), ZIP_TIMESTAMP)
            info.compress_type = zipfile.ZIP_DEFLATED
            info.external_attr = 0o644 << 16
            package.writestr(info, path.read_bytes())


def main():
    OUTPUT.mkdir(parents=True, exist_ok=True)
    with tempfile.TemporaryDirectory() as temporary:
        build = Path(temporary)
        for name in ("fetch", "ingest"):
            target = build / name
            package = target / "source_pipeline"
            package.mkdir(parents=True)
            for filename in ("__init__.py", "common.py", name + ".py"):
                shutil.copy(ROOT / "source_pipeline" / filename, package / filename)
            if name == "ingest":
                install(target)
                shutil.copy(ROOT / "ingestion/load_neo4j.py", target / "load_neo4j.py")
                shutil.copy(ROOT / "terraform/lambda_build/handler.py", target / "handler.py")
            output = OUTPUT / (name + ".zip")
            archive(target, output)
            print(f"Built {output.relative_to(ROOT)} ({output.stat().st_size:,} bytes)")


if __name__ == "__main__":
    main()
