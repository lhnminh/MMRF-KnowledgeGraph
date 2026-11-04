# ORION ingestion pipeline

This diagram describes the implemented AWS ingestion paths in `us-west-2`.
All paths use bounded public source records and the existing private S3 bucket
and Neptune cluster. Solid arrows carry data; dashed arrows trigger processing.

```mermaid
flowchart TB
    Monarch["Monarch API<br/>MM and JMML associations"]
    DisMech["DisMech<br/>Pinned MM and JMML YAML records"]
    OpenTargets["Open Targets<br/>Targets of drugs named in DisMech"]
    PubMed["PubMed<br/>PMID to structured NCT cross-references"]
    Registry["ClinicalTrials.gov API<br/>Exact registered study records"]

    Schedule["EventBridge Scheduler<br/>Daily at 02:00 UTC"]
    Manual["Manual source refresh"]
    Fetch["Source-fetch Lambda<br/>MM Monarch and DisMech only"]
    Scripts["Python source fetchers<br/>MM, JMML and drug targets"]
    Trials["Python clinical-trial fetcher<br/>DisMech citations to PubMed to registry"]

    subgraph S3["Private S3 bucket - same Region as Neptune"]
        DailyFiles["Daily immutable snapshot<br/>Original responses and manifest<br/>raw/source-pipeline/runs/run-id/"]
        FixedFiles["Staged loader inputs<br/>raw/monarch_*.json<br/>raw/dismech_*.yaml and provenance<br/>raw/drug_targets.json<br/>raw/clinical_trials.json and provenance"]
        TrialArchive["Immutable trial archive<br/>Registry records, PubMed XML and provenance<br/>raw/clinical_trials/snapshots/snapshot-id/"]
        Result["Daily result.json<br/>Run ID, counts and validation status"]
    end

    subgraph VPC["Existing private VPC - S3 reads through gateway endpoint"]
        DailyIngest["Pipeline-ingest Lambda<br/>Verify manifest and artifact hashes<br/>Transform, upsert and validate in one transaction"]
        CoreIngest["Core ingest Lambda<br/>Validate selected source inputs<br/>Transform and upsert using typed mappings"]
        Neptune[("Amazon Neptune property graph<br/>Parameterized openCypher MERGE<br/>Identifiers, evidence and source provenance")]
        Read["Read-only graph Lambda"]
    end

    Validation["Read-back validation<br/>Source versions, registry fields<br/>Relationship identities and evidence"]
    View["Graph explorer and API<br/>AgentCore tools"]
    Notebook["Neptune notebook<br/>VPC connectivity required"]

    Schedule -.-> Fetch
    Manual -.-> Fetch
    Monarch -->|"MM subset"| Fetch
    DisMech -->|"Pinned MM record"| Fetch
    Fetch -->|"Artifacts first, manifest last"| DailyFiles
    Fetch -.->|"Direct invocation with manifest receipt"| DailyIngest
    DailyFiles --> DailyIngest
    DailyIngest --> Neptune
    DailyIngest -->|"Validated result"| Fetch
    Fetch --> Result

    Manual -.-> Scripts
    Monarch --> Scripts
    DisMech --> Scripts
    OpenTargets --> Scripts
    Scripts -->|"Local raw files, then explicit S3 upload"| FixedFiles
    Manual -.-> CoreIngest
    FixedFiles --> CoreIngest
    CoreIngest --> Neptune

    Manual -.-> Trials
    FixedFiles -->|"Pinned DisMech treatment citations"| Trials
    PubMed -->|"Explicit NCT identifiers"| Trials
    Registry -->|"Registry metadata"| Trials
    Trials --> TrialArchive
    Trials -->|"Publish snapshot and checksum sidecar"| FixedFiles

    Neptune --> Validation
    FixedFiles -->|"Compare stored evidence and trial metadata"| Validation
    Neptune --> Read
    Read --> View
    Neptune --> Notebook
```

The **daily path** refreshes only the Multiple Myeloma Monarch/DisMech subset.
The fetch Lambda invokes ingestion directly; no Step Functions workflow or S3
event notification is involved. The manifest commits a complete snapshot, and
`result.json` records the validation result. A delivery acknowledgement alone
does not establish ingestion success.

The **manual path** stages MM/JMML records and optional drug targets, then
explicitly invokes `mmrf-kg-ingest`. Clinical trials use the same loader with
`clinical_trials: true`. Their fetcher reads the staged DisMech records, resolves
only structured PubMed registry accessions, archives the original responses,
and publishes a checksummed snapshot. Registry records remain a separate source
from DisMech biological evidence. Trial refresh is currently manual.

The **graph writes** are small incremental upserts into an existing graph.
Daily refreshes validate relationship counts within the transaction and read
them back after commit. Manual loads report graph counts; the dedicated
validation scripts compare complete treatment evidence and trial metadata
against S3. Repeated loads merge existing identifiers and do not automatically
delete records removed upstream. Larger initial datasets should use Neptune's
bulk loader; that is a separate path from this incremental implementation.

Raw source records stay in private S3. Original IDs, source versions, citation
references, retrieval timestamps and checksums identify where data came from.
The explorer displays metadata and source links; it omits article quotations.

Run and deployment details:

- [Daily source pipeline](source-pipeline.md).
- [Complete DisMech treatment evidence](dismech-treatment-evidence.md).
- [Clinical-trial fetch, ingestion and validation](clinical-trials.md).
- [Graph API and explorer](graph-api.md).
