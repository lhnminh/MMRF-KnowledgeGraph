import pytest

from mmrf_knowledgegraph.etl import ROOT, Builder
from mmrf_knowledgegraph.graph import GraphTools, Local
from mmrf_knowledgegraph.virtual_lab import VirtualLab


@pytest.fixture
def graph_path(tmp_path):
    b = Builder()
    b.curated(ROOT / "data/curated/mm_evidence.json")
    b.write(tmp_path)
    return tmp_path / "graph.json"


@pytest.fixture
def graph(graph_path):
    return GraphTools(Local(graph_path))


class Reply:
    status_code = 200

    def __init__(self, data):
        self.data = data

    def json(self):
        return self.data


class SyntheticGateway:
    """Strict fake of the inspected developer API, with disjoint female/male cases."""

    def __init__(self):
        self.headers = {}
        self.calls = []

    def mount(self, *_):
        pass

    def post(self, url, json, timeout, allow_redirects):
        assert timeout == (3, 15) and not allow_redirects
        self.calls.append((url, json))
        selection = json.get("variables", {}).get("filter", json.get("filter0", {}))
        female = selection == {"in": {"demographic.gender": ["female"]}}
        empty = selection == {"in": {"case_id": []}}
        if url.endswith("/guppy/graphql"):
            assert "$filter" in json["query"]
            ids = [] if empty else ["SYNTH-CASE-0001"] if female else ["SYNTH-CASE-0001", "SYNTH-CASE-0002"]
            return Reply({"data": {"CaseCentric_case_centric": [{"case_id": i, "samples": []} for i in ids]}})
        assert url.endswith("/protein-paint/termdb/topMutatedGenes")
        assert json["genome"] == "hg38" and json["dslabel"] == "MMRF"
        return Reply(
            {
                "genes": []
                if empty
                else [
                    {"gene": "TP53", "mutationStat": [{"count": 1}]},
                    {"gene": "KRAS", "mutationStat": [{"count": 0 if female else 1}]},
                ]
            }
        )


@pytest.fixture
def gateway():
    return SyntheticGateway()


@pytest.fixture
def lab(gateway):
    return VirtualLab("http://synthetic.invalid", session=gateway)
