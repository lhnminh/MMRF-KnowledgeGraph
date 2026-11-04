"""Project bounded public snapshots without discarding their source assertion context."""

import hashlib
import json
import math

from .etl import DISEASE, file_provenance, stable_id


def snapshot(builder, path, source, record_keys):
    data = json.loads(path.read_text())
    if data["disease"].get("graph_disease_id") != DISEASE:
        raise ValueError(f"{source}: snapshot is not for {DISEASE}")
    metadata = {k: v for k, v in data.items() if k not in record_keys}
    files = data.get("raw_response_files", [])
    hashes = data.get("raw_response_sha256", [])
    if data.get("raw_response_file"):
        files, hashes = [data["raw_response_file"]], [data["raw_response_sha256"]]
    if len(files) != len(hashes):
        raise ValueError(f"{source}: incomplete raw response manifest")
    for filename, expected in zip(files, hashes, strict=True):
        raw_path = path.parent / filename
        if raw_path.parent.resolve() != path.parent.resolve():
            raise ValueError("Raw response must be beside the snapshot")
        if hashlib.sha256(raw_path.read_bytes()).hexdigest() != expected:
            raise ValueError(f"{source}: raw response checksum mismatch: {filename}")
    prov = {
        **file_provenance(
            path, data["source_url"], data.get("fetch_timestamp_utc") or data.get("fetch_date")
        ),
        "source_revision": data.get("data_version", "unknown"),
        "license": data.get("license", "not_reported"),
        "raw_response_files": files,
        "raw_response_sha256": hashes,
        "snapshot_metadata": metadata,
    }
    builder.sources.append({"name": source, **prov})
    builder.node(
        DISEASE,
        data["disease"]["name"],
        "Disease",
        source,
        aliases=[str(data["disease"]["id"])],
        original=data["disease"],
    )
    return data, prov


def gene(builder, source, symbol, native_id, record):
    # Approved symbols join the two public sources and the four existing HGNC nodes.
    # Do not guess from substrings or variant labels. Every native ID remains an alias.
    matches = [
        n for n in builder.nodes.values() if n["entity_type"] == "Gene" and symbol and n["name"] == symbol
    ]
    if len(matches) > 1:
        raise ValueError(f"Ambiguous approved gene symbol: {symbol}")
    entity_id = matches[0]["id"] if matches else native_id
    if not entity_id:
        raise ValueError("Gene has no source identifier")
    return builder.node(entity_id, symbol or native_id, "Gene", source, aliases=[native_id], original=record)


def edge(builder, source, prov, subject, predicate, object_id, key, original, evidence=None, **attributes):
    result = builder.edge(
        subject,
        object_id,
        predicate,
        source,
        prov,
        evidence,
        original,
        id=stable_id(f"{source}-edge", [subject, predicate, object_id, key]),
    )
    result.update(attributes)
    return result


def load_opentargets(builder, path):
    source = "opentargets"
    data, prov = snapshot(builder, path, source, {"associated_targets", "drug_and_clinical_candidates"})
    if data["disease"]["id"] != "MONDO_0009693":
        raise ValueError("Unexpected Open Targets native disease identifier")
    disease_url = "https://platform.opentargets.org/disease/MONDO_0009693"
    for row in data["associated_targets"]:
        target = row["target"]
        entity_id = gene(builder, source, target.get("hgnc_symbol"), target["id"], target)
        score = float(row["score"])
        if not math.isfinite(score) or not 0 <= score <= 1:
            raise ValueError("Invalid Open Targets association score")
        edge(
            builder,
            source,
            {**prov, "source_url": disease_url + "/associations"},
            entity_id,
            "opentargets:associated_target",
            DISEASE,
            target["id"],
            row,
            association_score=score,
        )
    for row in data["drug_and_clinical_candidates"]:
        drug = row["drug"]
        drug_id = builder.node(drug["id"], drug["name"], "Drug", source, original=drug)
        drug_url = f"https://platform.opentargets.org/drug/{drug['id']}"
        builder.nodes[drug_id]["source_url"] = drug_url
        reports = [
            {"reference": report["id"], "source_url": report["url"], "original": report}
            for report in row.get("clinicalReports", [])
            if report.get("url")
        ]
        edge(
            builder,
            source,
            {**prov, "source_url": disease_url + "/knownDrugs"},
            drug_id,
            "opentargets:drug_or_clinical_candidate",
            DISEASE,
            row["id"],
            row,
            reports,
            clinical_stage=row.get("maxClinicalStage") or "not_reported",
        )
        for mechanism in (drug.get("mechanismsOfAction") or {}).get("rows", []):
            mid = stable_id("opentargets-mechanism", [drug_id, mechanism])
            builder.node(mid, mechanism["mechanismOfAction"], "Mechanism", source, original=mechanism)
            mp = {**prov, "source_url": drug_url}
            edge(
                builder,
                source,
                mp,
                drug_id,
                "opentargets:has_mechanism_of_action",
                mid,
                mid,
                mechanism,
                action_type=mechanism.get("actionType") or "not_reported",
            )
            for target in mechanism.get("targets", []):
                target_id = gene(builder, source, target.get("hgnc_symbol"), target["id"], target)
                edge(builder, source, mp, mid, "opentargets:has_target", target_id, mid, mechanism)


def load_civic(builder, path):
    source = "civic"
    data, prov = snapshot(builder, path, source, {"evidence_items"})
    if data["disease"].get("mondo_id") != DISEASE or data["disease"].get("doid") != "DOID:9538":
        raise ValueError("Unexpected CIViC disease cross-reference")
    for item in data["evidence_items"]:
        if item["status"] == "REJECTED":
            continue
        if item["status"] not in {"ACCEPTED", "SUBMITTED"}:
            raise ValueError(f"Unknown CIViC review status: {item['status']}")
        if str(item["disease"]["id"]) != str(data["disease"]["id"]):
            raise ValueError("CIViC evidence belongs to another disease")
        eid = f"CIViC:evidence:{item['id']}"
        url = f"https://civicdb.org/evidence/{item['id']}"
        attrs = {
            "review_status": item["status"],
            "evidence_type": item["evidenceType"],
            "evidence_level": item["evidenceLevel"],
            "evidence_direction": item["evidenceDirection"],
            "significance": item["significance"],
        }
        builder.node(eid, item["name"], "Evidence", source, original=item)
        builder.nodes[eid].update(source_url=url, **attrs)
        citation = item.get("source") or {}
        reference = citation.get("citationId")
        pub_url = None
        if reference and citation.get("sourceType") == "PUBMED":
            pub_id = f"PMID:{reference}"
            pub_url = f"https://pubmed.ncbi.nlm.nih.gov/{reference}/"
        elif reference and citation.get("sourceType") == "ASCO":
            pub_id = f"ASCO:{reference}"
        else:
            pub_id = None
        evidence = (
            [{"reference": pub_id or reference, "source_url": pub_url or url, "original": citation}]
            if citation
            else []
        )

        def assertion(subject, predicate, object_id):
            edge(
                builder,
                source,
                {**prov, "source_url": url},
                subject,
                predicate,
                object_id,
                item["id"],
                item,
                evidence,
                **attrs,
            )

        assertion(eid, "civic:has_disease_context", DISEASE)
        if pub_id:
            builder.node(pub_id, citation.get("citation") or pub_id, "Publication", source, original=citation)
            builder.nodes[pub_id]["source_url"] = pub_url or url
            assertion(eid, "civic:cites", pub_id)
        profile = item["molecularProfile"]
        profile_id = f"CIViC:molecular_profile:{profile['id']}"
        builder.node(profile_id, profile["name"], "MolecularProfile", source, original=profile)
        assertion(eid, "civic:about_molecular_profile", profile_id)
        for variant in profile.get("variants", []):
            vid = f"CIViC:variant:{variant['id']}"
            feature = variant["feature"]
            builder.node(vid, feature["name"] + " " + variant["name"], "Variant", source, original=variant)
            edge(
                builder,
                source,
                prov,
                profile_id,
                "civic:includes_variant",
                vid,
                [profile["id"], variant["id"]],
                variant,
            )
            instance = feature["featureInstance"]
            if instance["__typename"] == "Gene":
                genes = [instance]
            elif instance["__typename"] == "Fusion":
                genes = [g for g in [instance.get("fivePrimeGene"), instance.get("threePrimeGene")] if g]
            else:
                # Unknown features remain losslessly represented on the Variant node.
                genes = []
            for g in genes:
                native_id = (
                    f"NCBIGene:{g['entrezId']}" if g.get("entrezId") else f"CIViC:feature:{feature['id']}"
                )
                gid = gene(builder, source, g.get("hgnc_symbol"), native_id, g)
                edge(builder, source, prov, vid, "civic:variant_of", gid, [variant["id"], gid], variant)
        for therapy in item.get("therapies", []):
            # Multi-therapy evidence stays a grouped evidence item, never separate efficacy claims.
            tid = f"CIViC:therapy:{therapy['id']}"
            builder.node(
                tid,
                therapy["name"],
                "TherapeuticApproach",
                source,
                aliases=[f"NCIT:{therapy['ncitId']}"] if therapy.get("ncitId") else [],
                original=therapy,
            )
            assertion(eid, "civic:mentions_therapy", tid)
