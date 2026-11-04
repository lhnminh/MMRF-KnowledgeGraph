# Visualize the current Neptune graph

The local interactive snapshot is `data/transformed/neptune-graph/index.html`.
Open it in a browser:

```sh
open data/transformed/neptune-graph/index.html
```

It has four views: clinical features plus mechanisms, mechanisms, all loaded
phenotypes, and the complete focused graph. Scroll to zoom, drag to pan, and
click a node or relationship for identifiers, source versions and recorded
references. The source arrows retain their recorded direction. A phenotype
association is distinct from a source-reported mechanism.

The 2026-11-04 snapshot contains 43 nodes and 49 relationships returned by the
named `kg_get_phenotypes` and `kg_get_mechanism_paths` tools. This focuses on
Multiple Myeloma phenotypes and mechanisms; other Monarch association types
are outside these tool responses. The current deployed tools do not return
Open Targets or CIViC relationships.

## Refresh from AWS

Requirements: the authenticated `mmrf-hackathon` profile, Python 3.12+, and
Graphviz's `dot` command (already available on this workstation).

```sh
aws lambda invoke \
  --function-name mmrf-kg-kg-graph \
  --profile mmrf-hackathon --region us-west-2 \
  --cli-binary-format raw-in-base64-out \
  --payload '{"tool":"kg_get_phenotypes","disease_id":"MONDO:0009693","limit":100}' \
  /tmp/orion-phenotype-graph.json

aws lambda invoke \
  --function-name mmrf-kg-kg-graph \
  --profile mmrf-hackathon --region us-west-2 \
  --cli-binary-format raw-in-base64-out \
  --payload '{"tool":"kg_get_mechanism_paths","disease_id":"MONDO:0009693"}' \
  /tmp/orion-mechanism-graph.json

.venv/bin/python scripts/visualize_graph.py \
  /tmp/orion-phenotype-graph.json \
  /tmp/orion-mechanism-graph.json
```

If the session expired, first run
`aws login --profile mmrf-hackathon --region us-west-2`.

The generator makes one self-contained HTML file and four SVG files. It has
no remote JavaScript dependencies and makes no AWS calls from the browser.
Its timestamp records when the saved responses were rendered. Fetch fresh
responses when regenerating a current snapshot. Artifact output stays under
the ignored `data/transformed/` directory; verify source data terms before
redistributing derived graph artifacts. Article snippets and source-curator
descriptions are omitted from the visualization.

## Visualize in an AWS notebook

In a Neptune Workbench notebook connected to `mmrf-kg-cluster` in `us-west-2`,
run a cell returning paths, then select its Graph tab:

```cypher
%%opencypher
MATCH p=(d:Disease {id:'MONDO:0009693'})-[*1..2]->(other)
RETURN p
LIMIT 100
```

This queries the current database, while the local HTML file is a snapshot.
The Lambda Test console displays JSON and does not render a network diagram.

AWS references: [notebook magics](https://docs.aws.amazon.com/neptune/latest/userguide/notebooks-magics.html)
and [graph visualization](https://docs.aws.amazon.com/neptune/latest/userguide/notebooks-visualization.html).
