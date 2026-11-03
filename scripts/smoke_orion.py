"""Run the target question and contextual follow-up against a running agent server."""

import argparse
import json

import requests


def ask(url, payload, token=None):
    with requests.post(
        url, json=payload, headers={"X-Orion-Token": token or ""}, timeout=(5, 240), stream=True
    ) as response:
        response.raise_for_status()
        result = None
        tools = []
        for line in response.iter_lines(decode_unicode=True):
            if not line or not line.startswith("data: "):
                continue
            event = json.loads(line[6:])
            if event["event"] == "error":
                raise RuntimeError(event["error"])
            if event["event"] == "tool_end":
                assert event["status"] == "success", event
                tools.append(event["name"])
            if event["event"] == "result":
                result = event
        assert result, "Missing final answer"
        return result, tools


if __name__ == "__main__":
    import os

    parser = argparse.ArgumentParser()
    parser.add_argument("--url", default="http://localhost:8080/invocations")
    parser.add_argument("--require-aws", action="store_true")
    args = parser.parse_args()
    prompt = "How is TP53 connected to multiple myeloma, what evidence supports the relationship, and what does the synthetic Virtual Lab cohort show?"
    selection = {"in": {"demographic.gender": ["female"]}}
    result, tools = ask(
        args.url, {"prompt": prompt, "cohort_filter": selection}, os.getenv("ORION_INTERNAL_TOKEN")
    )
    assert {"find_gene_disease_connections", "get_supporting_evidence", "summarize_synthetic_cohort"} <= set(
        tools
    ), tools
    assert result["cohort"]["data_kind"] == "synthetic" and result["cohort"]["total_cases"] == 500
    assert result["graph"]["relationships"] and result["graph"]["paths"]
    if args.require_aws:
        assert result["mode"] == "bedrock" and result["graph_backend"] == "neptune"
    followup, tools = ask(
        args.url,
        {
            "prompt": "Show related genes with similar phenotypes.",
            "cohort_filter": selection,
            "history": [
                {"role": "user", "content": prompt},
                {"role": "assistant", "content": result["answer"]},
            ],
        },
        os.getenv("ORION_INTERNAL_TOKEN"),
    )
    assert followup["graph"]["relationships"]
    assert any(
        name in tools for name in ["compare_entities", "get_graph_neighborhood", "find_related_entities"]
    )
    print(
        json.dumps(
            {
                "mode": result["mode"],
                "graph_backend": result["graph_backend"],
                "cohort_size": result["cohort"]["total_cases"],
                "followup_tools": tools,
                "passed": True,
            },
            indent=2,
        )
    )
