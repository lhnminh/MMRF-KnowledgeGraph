"""VPC-connected bulk loader and fixed validation queries; deployed as Lambda."""

import os

import boto3
from botocore.config import Config


def handler(event, context):
    client = boto3.client(
        "neptunedata",
        endpoint_url=os.environ["NEPTUNE_ENDPOINT"],
        config=Config(connect_timeout=5, read_timeout=30, retries={"max_attempts": 2}),
    )
    action = event.get("action")
    if action == "load":
        # The caller cannot substitute a bucket, role, format, or arbitrary file prefix.
        import re

        version = event.get("version", "")
        if not re.fullmatch(r"[a-f0-9]{24}", version):
            raise ValueError("Invalid dataset content hash")
        return client.start_loader_job(
            source=f"s3://{os.environ['GRAPH_BUCKET']}/graph/{version}/",
            format="opencypher",
            s3BucketRegion=os.environ["AWS_REGION"],
            iamRoleArn=os.environ["LOADER_ROLE_ARN"],
            mode="AUTO",
            failOnError=True,
            parallelism="LOW",
            updateSingleCardinalityProperties=True,
        )
    if action == "status":
        return client.get_loader_job_status(
            loadId=event["load_id"], details=True, errors=True, errorsPerPage=20
        )
    if action == "identity_map":
        rows = client.execute_open_cypher_query(
            openCypherQuery="MATCH (n) RETURN n.id AS logical_id, id(n) AS neptune_id, labels(n) AS labels LIMIT 10001"
        )["results"]
        if len(rows) > 10000:
            raise ValueError("Existing graph exceeds the bounded hackathon identity-map limit")
        return rows
    if action == "validate":
        queries = {
            "nodes": "MATCH (n:Entity) RETURN count(n) AS count",
            "relationships": "MATCH ()-[r:RELATIONSHIP]->() RETURN count(r) AS count",
            "tp53": "MATCH (g:Entity)-[r:RELATIONSHIP]-(d:Entity) WHERE g.id=$gene AND d.id=$disease RETURN r.id AS relationship_id, r.source_url AS source_url, r.evidence_json AS evidence_json LIMIT 10",
            "missing_provenance": "MATCH ()-[r:RELATIONSHIP]->() WHERE r.source_url IS NULL OR r.retrieved_at IS NULL OR r.review_status IS NULL RETURN count(r) AS count",
            "relationship_sources": "MATCH ()-[r:RELATIONSHIP]->() RETURN r.source AS source, count(r) AS count ORDER BY source",
            "civic_review_statuses": "MATCH (e:Evidence) RETURN e.review_status AS status, count(e) AS count ORDER BY status",
            "duplicate_node_ids": "MATCH (n:Entity) WHERE n.id IS NOT NULL WITH n.id AS id, count(n) AS c WHERE c>1 RETURN count(*) AS count",
        }
        import json

        return {
            name: client.execute_open_cypher_query(
                openCypherQuery=query,
                parameters=json.dumps({"gene": "HGNC:11998", "disease": "MONDO:0009693"}),
            )["results"]
            for name, query in queries.items()
        }
    raise ValueError("Allowed actions: load, status, identity_map, validate")
