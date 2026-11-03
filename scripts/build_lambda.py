"""Build terraform/lambda_loader.zip, the one package behind both Lambdas.

Run this before every `terraform apply` that should deploy code changes: Terraform deploys whatever
zip it finds, so a stale zip silently deploys old code. It holds kg-graph (handler.py), kg-ingest
(load_neo4j.py) and their Linux dependencies. boto3 and botocore come with the Lambda runtime.
"""

import shutil
import subprocess
import sys
import tempfile
import zipfile
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
OUTPUT = ROOT / "terraform" / "lambda_loader.zip"
SOURCES = [ROOT / "terraform" / "lambda_build" / "handler.py", ROOT / "ingestion" / "load_neo4j.py"]
DEPENDENCIES = ["neo4j==5.28.8", "pyyaml==6.0.3"]
# Fixed so that rebuilding unchanged code gives an identical zip and Terraform sees no change.
ZIP_TIMESTAMP = (2026, 1, 1, 0, 0, 0)


def install(target: Path) -> None:
    if shutil.which("uv"):
        command = ["uv", "pip", "install", "--python-platform", "x86_64-manylinux2014", "--python-version", "3.12"]
    else:
        command = [sys.executable, "-m", "pip", "install", "--platform", "manylinux2014_x86_64"]
        command += ["--python-version", "3.12", "--only-binary=:all:", "--no-compile"]
    subprocess.run([*command, "--target", str(target), *DEPENDENCIES], check=True)


def main() -> None:
    with tempfile.TemporaryDirectory() as temporary:
        build = Path(temporary)
        install(build)
        for source in SOURCES:
            shutil.copy(source, build / source.name)
        files = sorted(p for p in build.rglob("*") if p.is_file() and "__pycache__" not in p.parts)
        with zipfile.ZipFile(OUTPUT, "w", zipfile.ZIP_DEFLATED) as archive:
            for path in files:
                info = zipfile.ZipInfo(path.relative_to(build).as_posix(), ZIP_TIMESTAMP)
                info.compress_type = zipfile.ZIP_DEFLATED
                info.external_attr = 0o644 << 16
                archive.writestr(info, path.read_bytes())
    print(f"Wrote {OUTPUT} ({OUTPUT.stat().st_size / 1_000_000:.1f} MB, {len(files)} files)")


if __name__ == "__main__":
    main()
