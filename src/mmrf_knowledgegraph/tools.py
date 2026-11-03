"""Typed public tool contracts shared with Bedrock Converse."""

from typing import Literal

from pydantic import BaseModel, ConfigDict, Field

from .virtual_lab import ToolError


class Arguments(BaseModel):
    model_config = ConfigDict(extra="forbid", strict=True)


Text = str


class Search(Arguments):
    query: str = Field(min_length=1, max_length=120)
    entity_types: list[
        Literal[
            "Disease",
            "Gene",
            "Protein",
            "PhenotypicFeature",
            "Pathway",
            "Mechanism",
            "BiologicalProcess",
            "Drug",
            "TherapeuticApproach",
        ]
    ] = Field(default_factory=list, max_length=8)


class Connections(Arguments):
    gene: str = Field(min_length=1, max_length=120)
    disease: str = Field(min_length=1, max_length=120)


class Path(Arguments):
    source_entity: str = Field(min_length=1, max_length=120)
    target_entity: str = Field(min_length=1, max_length=120)
    maximum_depth: int = Field(default=3, ge=1, le=3)


class Evidence(Arguments):
    relationship_ids: list[str] = Field(min_length=1, max_length=50)


class Related(Arguments):
    entity_id: str = Field(min_length=1, max_length=120)
    relationship_types: list[str] = Field(default_factory=list, max_length=10)
    limit: int = Field(default=20, ge=1, le=50)


class Compare(Arguments):
    entity_ids: list[str] = Field(min_length=2, max_length=4)
    disease: str = Field(min_length=1, max_length=120)


class Neighborhood(Arguments):
    entity_id: str = Field(min_length=1, max_length=120)
    maximum_depth: int = Field(default=2, ge=1, le=3)


class Cohort(Arguments):
    gene: Literal["TP53", "KRAS", "NRAS", "BRAF", "DIS3", "MYC"]


class Discover(Arguments):
    pass


CONTRACTS = {
    "search_entities": (
        Search,
        "Resolve a name or original identifier to graph entities. Returns source provenance.",
    ),
    "find_gene_disease_connections": (
        Connections,
        "Find up to 20 bounded paths between a gene and disease. Returns predicates and provenance; connections do not imply causation.",
    ),
    "find_mechanism_path": (
        Path,
        "Find paths of at most three edges, preserving source direction, mechanisms and evidence.",
    ),
    "get_supporting_evidence": (
        Evidence,
        "Retrieve publication references, source URLs, review status and original provenance for relationship IDs.",
    ),
    "find_related_entities": (
        Related,
        "Find adjacent entities, optionally filtering by exact original relationship predicates.",
    ),
    "compare_entities": (
        Compare,
        "Compare 2–4 genes' connections to a disease. Shared disease is not shared gene-level phenotype evidence.",
    ),
    "get_graph_neighborhood": (
        Neighborhood,
        "Retrieve at most 50 edges within three hops. Includes original predicates and provenance.",
    ),
    "summarize_synthetic_cohort": (
        Cohort,
        "Query the current synthetic cohort via Guppy and ProteinPaint. Returns counts and existing Virtual Lab chart configuration. Uses the exact browser cohort selection.",
    ),
    "discover_platform_tools": (Discover, "List the available allowlisted tools and their limits."),
}


class Registry:
    def __init__(self, graph, virtual_lab, cohort_filter):
        self.graph, self.virtual_lab, self.cohort_filter = graph, virtual_lab, cohort_filter

    def schemas(self):
        return [
            {
                "toolSpec": {
                    "name": name,
                    "description": desc,
                    "inputSchema": {"json": schema.model_json_schema()},
                }
            }
            for name, (schema, desc) in CONTRACTS.items()
        ]

    def execute(self, name, arguments):
        if name not in CONTRACTS:
            raise ValueError("Unknown tool; use discover_platform_tools")
        validated = CONTRACTS[name][0].model_validate(arguments).model_dump()
        if name == "discover_platform_tools":
            return {"tools": [{"name": n, "description": d} for n, (_, d) in CONTRACTS.items()]}
        if name == "summarize_synthetic_cohort":
            if self.virtual_lab is None:
                raise ToolError(
                    "virtual_lab_unconfigured", "Set VIRTUAL_LAB_URL to the synthetic sandbox gateway"
                )
            return self.virtual_lab.summarize_synthetic_cohort(**validated, cohort_filter=self.cohort_filter)
        return getattr(self.graph, name)(**validated)
