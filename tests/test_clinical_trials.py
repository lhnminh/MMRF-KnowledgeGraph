"""Clinical trial joins require source identifiers; narrative text is never a join key."""

import copy
import hashlib
import importlib.util
import json
from pathlib import Path

import pytest
from test_ingest import DISMECH_YAML, MONARCH, PROVENANCE, FakeDriver, FakeTx, loader

spec = importlib.util.spec_from_file_location("fetch_clinical_trials", Path(__file__).resolve().parents[1] / "ingestion/fetch_clinical_trials.py")
fetch = importlib.util.module_from_spec(spec)
spec.loader.exec_module(fetch)

XML = b"""<PubmedArticleSet>
<PubmedArticle><MedlineCitation><PMID>5</PMID><Article><DataBankList><DataBank>
<DataBankName>ClinicalTrials.gov</DataBankName><AccessionNumberList>
<AccessionNumber>NCT00000001</AccessionNumber><AccessionNumber>NCT00000002</AccessionNumber>
</AccessionNumberList></DataBank></DataBankList></Article></MedlineCitation></PubmedArticle>
<PubmedArticle><MedlineCitation><PMID>6</PMID><Article>
<Abstract><AbstractText>NCT99999999 appears only in prose.</AbstractText></Abstract>
</Article></MedlineCitation></PubmedArticle>
</PubmedArticleSet>"""


def study(identifier):
    return {"protocolSection": {
        "identificationModule": {"nctId": identifier, "briefTitle": "Synthetic registry test", "acronym": "DEMO"},
        "statusModule": {"overallStatus": "COMPLETED", "lastUpdatePostDateStruct": {"date": "2026-11-04"}},
        "designModule": {"phases": ["PHASE1", "PHASE2"], "studyType": "INTERVENTIONAL",
                         "enrollmentInfo": {"count": 12, "type": "ACTUAL"}},
    }, "hasResults": False}


def sources():
    record = loader.yaml.safe_load(DISMECH_YAML)
    record["treatments"][0]["evidence"] = [
        {"reference": "PMID:5", "evidence_source": "HUMAN_CLINICAL"},
        {"reference": "PMID:7", "evidence_source": "OTHER"},
    ]
    record["treatments"][1]["evidence"] = [{"reference": "PMID:6", "evidence_source": "HUMAN_CLINICAL"}]
    return [{"record": record, "disease_id": "MONDO:0009693", "source_version": "b1ca0b2"}]


def get(url):
    if url.endswith("/version"):
        return json.dumps({"dataTimestamp": "2026-11-04T10:00:04", "apiVersion": "2.0.5"}).encode()
    if url.startswith(fetch.PUBMED):
        return XML
    return json.dumps(study(url.split("/")[-1].split("?")[0])).encode()


def fixture():
    data, _ = fetch.collect(sources(), get=get)
    text = json.dumps(data)
    provenance = json.dumps({"sha256": hashlib.sha256(text.encode()).hexdigest(),
                             "source_version": data["source_version"],
                             "snapshot_s3_uri": "s3://example/raw/snapshot.json",
                             "pubmed_s3_uri": "s3://example/raw/pubmed.xml"})
    return data, text, provenance


def test_fetch_accepts_only_structured_cross_references_and_retains_all_studies():
    data, xml = fetch.collect(sources(), get=get)
    assert len(data["studies"]) == 2 and len(data["links"]) == 2
    assert {r["nct_id"] for r in data["links"]} == {"NCT00000001", "NCT00000002"}
    assert data["unmatched"][0]["reference"] == "PMID:6"  # prose does not resolve an ID
    assert data["excluded"][0]["reference"] == "PMID:7"  # guideline isn't converted into a trial
    assert data["pubmed"]["sha256"] == hashlib.sha256(xml).hexdigest()


def test_pubmed_missing_citations_and_malformed_accessions_fail_closed():
    with pytest.raises(ValueError, match="omitted"):
        fetch.pubmed_accessions(XML, {"5", "6", "7"})
    with pytest.raises(ValueError, match="invalid registry"):
        fetch.pubmed_accessions(XML.replace(b"NCT00000001", b"not-a-trial"), {"5", "6"})


def test_wrong_registry_identity_and_changing_version_fail_before_publishing():
    def wrong_id(url):
        return json.dumps(study("NCT99999999")).encode() if "/studies/" in url else get(url)

    with pytest.raises(ValueError, match="different trial"):
        fetch.collect(sources(), get=wrong_id)
    versions = iter([b'{"dataTimestamp":"first"}', b'{"dataTimestamp":"second"}'])
    with pytest.raises(ValueError, match="version changed"):
        fetch.collect(sources(), get=lambda url: next(versions) if url.endswith("/version") else get(url))


def test_trial_metadata_and_both_paths_keep_source_provenance_and_scalar_values():
    _, text, provenance = fixture()
    data, prov = loader.parse_clinical_trials(text, provenance, [(sources()[0]["record"], "b1ca0b2")])
    tx = FakeTx()
    loader.load_clinical_trials(tx, data, prov)
    assert len(tx.calls) == 6  # two nodes, two disease paths, two treatment paths
    for query, params in tx.calls:
        assert not any(isinstance(value, (list, dict)) for value in params.values())
        assert params["version"] == data["source_version"]
        assert params["raw_s3_uri"] == prov["snapshot_s3_uri"]
        if "MERGE (t:ClinicalTrial" in query:
            assert params["phase"] == "PHASE1;PHASE2"
            assert params["enrollment_count"] == 12 and params["enrollment_type"] == "ACTUAL"
        else:
            assert params["references"] == "PMID:5" and params["dismech_version"] == "b1ca0b2"
    assert any("disease_id: $disease" in q for q, _ in tx.calls)  # shared treatment nodes remain disease-scoped


@pytest.mark.parametrize("mutation", ["checksum", "pubmed_join", "source_version", "duplicate_link", "unknown_treatment"])
def test_invalid_snapshot_is_rejected_before_any_graph_writes(mutation):
    data, text, provenance = fixture()
    data = copy.deepcopy(data)
    if mutation == "pubmed_join":
        data["pubmed"]["cross_references"]["5"] = []
    elif mutation == "source_version":
        data["links"][0]["dismech_version"] = "wrong-version"
    elif mutation == "duplicate_link":
        data["links"].append(data["links"][0])
    elif mutation == "unknown_treatment":
        data["links"][0]["treatment_id"] = "unknown"
    text = json.dumps(data)
    prov = json.loads(provenance)
    prov["sha256"] = "bad" if mutation == "checksum" else hashlib.sha256(text.encode()).hexdigest()
    driver = FakeDriver()
    with pytest.raises(ValueError):
        loader.load_graphs(driver, [(json.dumps(MONARCH), loader.yaml.safe_dump(sources()[0]["record"]), PROVENANCE)],
                           clinical_trials_text=text, clinical_trials_provenance_text=json.dumps(prov))
    assert driver.tx.calls == []
