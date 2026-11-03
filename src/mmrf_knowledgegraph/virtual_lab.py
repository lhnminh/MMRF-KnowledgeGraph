"""Allowlisted, server-side adapter for the pinned synthetic Virtual Lab gateway."""

import os
import re
from urllib.parse import urlparse

import requests
from requests.adapters import HTTPAdapter
from urllib3.util.retry import Retry

PANEL = {"TP53", "KRAS", "NRAS", "BRAF", "DIS3", "MYC"}
ENDPOINTS = {"cases": "/guppy/graphql", "mutations": "/protein-paint/termdb/topMutatedGenes"}
CASE_QUERY = (
    "query($filter:JSON){CaseCentric_case_centric(first:1000,filter:$filter){case_id samples{sample_id}}}"
)


class ToolError(Exception):
    def __init__(self, code, message, retryable=False):
        self.code, self.message, self.retryable = code, message, retryable
        super().__init__(message)


def validate_filter(value, depth=0):
    """Narrow hackathon subset. Unsupported current-cohort filters fail explicitly."""
    if depth > 5 or not isinstance(value, dict):
        raise ValueError("Unsupported cohort filter")
    if not value:
        return {}
    if len(value) != 1:
        raise ValueError("Cohort filter must contain one operator")
    op, content = next(iter(value.items()))
    if op in {"and", "or"}:
        if not isinstance(content, list) or not 1 <= len(content) <= 10:
            raise ValueError("Invalid cohort filter group")
        return {op: [validate_filter(c, depth + 1) for c in content]}
    if op != "in" or not isinstance(content, dict) or len(content) != 1:
        raise ValueError("ORION supports gender and explicit synthetic case selections only")
    field, vals = next(iter(content.items()))
    if not isinstance(vals, list) or len(vals) > 1000 or any(not isinstance(v, str) for v in vals):
        raise ValueError("Invalid cohort values")
    if field == "demographic.gender" and all(v in {"female", "male"} for v in vals):
        return value
    if field in {"case_id", "cases.case_id"} and all(re.fullmatch(r"SYNTH-CASE-\d{4}", v) for v in vals):
        return {"in": {"case_id": vals}}
    raise ValueError("Unsupported cohort field or non-synthetic case identifier")


class VirtualLab:
    def __init__(self, base_url=None, token=None, session=None):
        self.base_url = (base_url or os.environ["VIRTUAL_LAB_URL"]).rstrip("/")
        parsed = urlparse(self.base_url)
        if (
            parsed.scheme not in {"http", "https"}
            or not parsed.hostname
            or parsed.username
            or parsed.query
            or parsed.fragment
        ):
            raise ValueError("Invalid server-configured Virtual Lab URL")
        self.session = session or requests.Session()
        self.session.headers.update({"Content-Type": "application/json"})
        token = token or os.getenv("VIRTUAL_LAB_TOKEN")
        if token:
            self.session.headers["Authorization"] = "Bearer " + token
        retry = Retry(
            total=2,
            connect=2,
            read=1,
            status=2,
            backoff_factor=0.3,
            status_forcelist=[429, 502, 503, 504],
            allowed_methods=["POST"],
            respect_retry_after_header=False,
        )
        # All allowlisted POSTs are read operations.
        self.session.mount("http://", HTTPAdapter(max_retries=retry))
        self.session.mount("https://", HTTPAdapter(max_retries=retry))

    def call(self, operation, body):
        if operation not in ENDPOINTS:
            raise ValueError("Endpoint is not allowed")
        try:
            r = self.session.post(
                self.base_url + ENDPOINTS[operation], json=body, timeout=(3, 15), allow_redirects=False
            )
            if r.status_code in {401, 403}:
                raise ToolError("virtual_lab_auth", "Virtual Lab authentication failed")
            if not 200 <= r.status_code < 300:
                raise ToolError(
                    "virtual_lab_unavailable",
                    "Virtual Lab request failed",
                    r.status_code >= 500 or r.status_code == 429,
                )
            result = r.json()
            if not isinstance(result, dict):
                raise ToolError("virtual_lab_response", "Virtual Lab returned an unexpected response shape")
            if result.get("errors") or result.get("error"):
                raise ToolError("virtual_lab_response", "Virtual Lab returned an error")
            return result
        except requests.RequestException as exc:
            raise ToolError(
                "virtual_lab_unavailable", "Virtual Lab timed out or is unreachable", True
            ) from exc
        except (ValueError, TypeError) as exc:
            raise ToolError("virtual_lab_response", "Virtual Lab returned invalid JSON") from exc

    def summarize_synthetic_cohort(self, gene, cohort_filter):
        if gene not in PANEL:
            raise ValueError("Gene is outside the synthetic six-gene panel")
        selection = validate_filter(cohort_filter)
        cases = self.call("cases", {"query": CASE_QUERY, "variables": {"filter": selection}})
        try:
            ids = [c["case_id"] for c in cases["data"]["CaseCentric_case_centric"]]
            if len(ids) > 1000 or len(set(ids)) != len(ids):
                raise ValueError("Cohort response exceeds the demo cap or contains duplicate cases")
            if any(not re.fullmatch(r"SYNTH-CASE-\d{4}", i) for i in ids):
                raise ToolError("unexpected_data", "Expected synthetic case identifiers")
            counts = self.call("mutations", {"genome": "hg38", "dslabel": "MMRF", "filter0": selection})
            genes = [{"symbol": g["gene"], "count": g["mutationStat"][0]["count"]} for g in counts["genes"]]
            if len({g["symbol"] for g in genes}) != len(genes):
                raise ValueError("Duplicate gene counts")
            if any(
                g["symbol"] not in PANEL or type(g["count"]) is not int or not 0 <= g["count"] <= len(ids)
                for g in genes
            ):
                raise ValueError("Invalid mutation counts")
        except (KeyError, IndexError, TypeError, ValueError) as exc:
            raise ToolError(
                "virtual_lab_response", "Virtual Lab response did not match the pinned synthetic API"
            ) from exc
        n = next((g["count"] for g in genes if g["symbol"] == gene), 0)
        return {
            "data_kind": "synthetic",
            "gene": gene,
            "total_cases": len(ids),
            "mutated_cases": n,
            "percentage": round(100 * n / len(ids), 2) if ids else None,
            "empty": not ids,
            "cohort_filter": selection,
            "case_ids": ids,
            "visualization": {"component": "VirtualLabBarChart", "genes": genes, "total_cases": len(ids)},
            "provenance": {
                "source": "MMRF synthetic sandbox",
                "endpoints": list(ENDPOINTS.values()),
                "reference_key": "hg38 is a fabricated reference compatibility key",
            },
            "caution": "Fabricated cohort observations; not clinical evidence or population estimates.",
        }
