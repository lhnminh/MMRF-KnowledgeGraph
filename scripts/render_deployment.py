"""Render a concrete EKS manifest from Terraform outputs, without applying it."""

import argparse
import json
import subprocess
from pathlib import Path

import yaml

ROOT = Path(__file__).resolve().parents[1]


if __name__ == "__main__":
    parser = argparse.ArgumentParser()
    parser.add_argument("--image", required=True, help="Multi-architecture or EKS-compatible image URI")
    parser.add_argument("--region", default="us-east-1")
    parser.add_argument("--output", type=Path, default=Path("deploy/orion.rendered.yaml"))
    args = parser.parse_args()
    output = json.loads(
        subprocess.check_output(
            ["terraform", "-chdir=" + str(ROOT / "terraform"), "output", "-json"], text=True
        )
    )

    def value(key):
        return output[key]["value"]

    if not value("runtime_arn"):
        raise SystemExit("Create the AgentCore runtime before rendering the bridge")
    text = (
        (ROOT / "deploy/orion.yaml")
        .read_text()
        .replace("IMAGE_URI", args.image)
        .replace("RUNTIME_ARN", value("runtime_arn"))
        .replace("VPC_CIDR", value("vpc_cidr"))
    )
    docs = list(yaml.safe_load_all(text))
    for item in docs:
        if item["kind"] == "Deployment":
            for env in item["spec"]["template"]["spec"]["containers"][0]["env"]:
                if env["name"] == "AWS_REGION":
                    env["value"] = args.region
    docs.insert(
        0,
        {
            "apiVersion": "v1",
            "kind": "ServiceAccount",
            "metadata": {
                "name": "orion-bridge",
                "namespace": "mmrf-synthetic",
                "annotations": {"eks.amazonaws.com/role-arn": value("bridge_role_arn")},
            },
        },
    )
    args.output.write_text(yaml.safe_dump_all(docs, sort_keys=False))
    print(f"Rendered {args.output}; review and kubectl apply it")
