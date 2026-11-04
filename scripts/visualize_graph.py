"""Render named graph-tool responses as a private local interactive Graphviz snapshot."""

import argparse
import json
import re
import shutil
import subprocess
import textwrap
from datetime import datetime, timezone
from pathlib import Path

FOCUS = {
    "HP:0002653",
    "HP:0002797",
    "HP:0000938",
    "HP:0002756",
    "HP:0002953",
    "HP:0000112",
    "HP:0001919",
    "HP:0000100",
    "HP:0003259",
    "HP:0003072",
    "HP:0001903",
}
COLORS = {"Disease": "#c4b5fd", "Mechanism": "#fcd34d", "PhenotypicFeature": "#93c5fd"}


def read_responses(paths):
    nodes, edges, evidence, limitations, traces = {}, {}, {}, [], []
    for path in paths:
        result = json.loads(path.read_text())
        if result.get("result_type") != "graph":
            raise ValueError(f"{path}: expected a successful named graph-tool response")
        traces.append(result.get("trace", {}))
        for node in result["graph"]["nodes"]:
            # Keep labels/identifiers and provenance; do not redistribute article snippets
            # or source-curator descriptions in the visualization.
            current = nodes.setdefault(node["id"], {k: node[k] for k in ["id", "label", "type"]})
            source = {k: node.get(k, "unrecorded") for k in ["source_dataset", "source_version"]}
            if source not in current.setdefault("sources", []):
                current["sources"].append(source)
        for edge in result["graph"]["edges"]:
            edges[edge["id"]] = {
                k: edge.get(k)
                for k in [
                    "id",
                    "source",
                    "target",
                    "predicate",
                    "edge_origin",
                    "source_dataset",
                    "source_version",
                    "evidence_ids",
                ]
            }
        for item in result.get("evidence", []):
            evidence[item["id"]] = {
                k: item.get(k)
                for k in [
                    "id",
                    "reference",
                    "source_dataset",
                    "source_version",
                ]
            }
        limitations.extend(result.get("limitations", []))
    if not nodes or not edges:
        raise ValueError("Graph has no connected records to visualize")
    if len(nodes) > 500 or len(edges) > 1000:
        raise ValueError("Select a smaller graph for this focused snapshot")
    if any(edge[end] not in nodes for edge in edges.values() for end in ["source", "target"]):
        raise ValueError("A relationship endpoint is missing")
    return {
        "nodes": list(nodes.values()),
        "edges": list(edges.values()),
        "evidence": list(evidence.values()),
        "limitations": list(dict.fromkeys(limitations)),
        "tool_traces": traces,
        "generated_at": datetime.now(timezone.utc).isoformat(),
    }


def diagram(data, mode):
    selected = [
        n
        for n in data["nodes"]
        if mode == "all"
        or n["type"] == "Disease"
        or mode == "clinical"
        and (n["id"] in FOCUS or n["type"] == "Mechanism")
        or mode == "phenotypes"
        and n["type"] == "PhenotypicFeature"
        or mode == "mechanisms"
        and n["type"] == "Mechanism"
    ]
    ids = {node["id"] for node in selected}
    edges = [e for e in data["edges"] if e["source"] in ids and e["target"] in ids]
    node_indexes = {n["id"]: i for i, n in enumerate(data["nodes"])}
    edge_indexes = {e["id"]: i for i, e in enumerate(data["edges"])}
    q = json.dumps
    lines = [
        'digraph G { graph [rankdir=LR, bgcolor="transparent", pad=0.4, nodesep=0.24, ranksep=1.0, fontname="Helvetica"];',
        'node [shape=box, style="rounded,filled", fontname="Helvetica", fontsize=13, margin="0.16,0.11", penwidth=1.3];',
        'edge [fontname="Helvetica", fontsize=10, arrowsize=0.7, fontcolor="#94a3b8", penwidth=1.1];',
    ]
    for node in selected:
        index = node_indexes[node["id"]]
        label = "\n".join(textwrap.wrap(node["label"], 28))
        color = COLORS.get(node["type"], "#cbd5e1")
        lines.append(
            f'n{index} [id="node_{index}", label={q(label)}, tooltip={q(node["id"])}, fillcolor={q(color)}, color={q(color)}, fontcolor="#0f172a"];'
        )
    for edge in edges:
        index = edge_indexes[edge["id"]]
        color = "#60a5fa" if edge["source_dataset"] == "monarch" else "#f59e0b"
        label = "leads to" if edge["predicate"] == "leads_to" else ""
        style = "dashed" if edge["predicate"] == "has_mechanism" else "solid"
        lines.append(
            f'n{node_indexes[edge["source"]]} -> n{node_indexes[edge["target"]]} [id="edge_{index}", label={q(label)}, tooltip={q(edge["predicate"])}, color={q(color)}, style={q(style)}];'
        )
    lines.append("}")
    result = subprocess.run(
        ["dot", "-Tsvg"], input="\n".join(lines), capture_output=True, text=True, check=True
    )
    svg = re.search(r"<svg\b[\s\S]*</svg>", result.stdout).group(0)
    return {"svg": svg, "nodes": len(selected), "edges": len(edges)}


HTML = r"""<!doctype html><html lang="en"><head><meta charset="utf-8">
<meta name="viewport" content="width=device-width,initial-scale=1"><title>ORION · Multiple Myeloma graph</title>
<style>
*{box-sizing:border-box}body{margin:0;background:#080f1e;color:#e2e8f0;font:14px system-ui,sans-serif}header{padding:24px 30px 16px;border-bottom:1px solid #243147;background:#0e182b}.eyebrow{color:#a5b4fc;font-size:12px;font-weight:700;letter-spacing:.13em}h1{font-size:25px;letter-spacing:-.04em;margin:7px 0}p{line-height:1.55;color:#9eafc6}header p{margin:6px 0}.bar{display:flex;gap:10px;align-items:center;flex-wrap:wrap;padding:14px 24px;border-bottom:1px solid #243147}select,input,button{font:inherit;color:#e2e8f0;background:#152239;border:1px solid #34445e;border-radius:8px;padding:9px 12px}input{min-width:190px}button{cursor:pointer}button:hover{background:#243653}button:focus-visible,input:focus-visible,select:focus-visible{outline:2px solid #a5b4fc}main{display:grid;grid-template-columns:minmax(0,1fr) 345px;min-height:calc(100vh - 210px)}#canvas{position:relative;min-height:650px;height:calc(100vh - 210px);overflow:hidden;background:radial-gradient(ellipse at center,#14243b 0,#0a1324 75%);touch-action:none;cursor:grab}#canvas:active{cursor:grabbing}#canvas svg{position:absolute;transform-origin:0 0}#canvas .node,#canvas .edge{cursor:pointer;transition:opacity .15s}#canvas .dim{opacity:.15}#canvas .selected path,#canvas .selected polygon{stroke:#fff;stroke-width:3px}aside{padding:22px;border-left:1px solid #243147;background:#101b2e;overflow:auto;max-height:calc(100vh - 210px)}h2{font-size:18px;margin:0 0 8px;overflow-wrap:anywhere}h3{font-size:12px;text-transform:uppercase;letter-spacing:.1em;color:#9eafc6;margin:24px 0 10px}code{font:12px ui-monospace,monospace;overflow-wrap:anywhere;color:#cbd5e1}a{color:#93c5fd}ul{padding-left:18px;color:#b9c8db;line-height:1.6}.legend{display:flex;gap:15px;flex-wrap:wrap;font-size:12px;color:#b8c7db}.dot{display:inline-block;width:9px;height:9px;border-radius:3px;margin-right:5px}.badge{font-size:12px;color:#b7c5db;padding:5px 9px;background:#1b2b43;border-radius:20px}.record{border-top:1px solid #2a3a51;margin-top:14px;padding-top:14px}.record p{margin:6px 0}.edgebutton{display:block;text-align:left;width:100%;margin:6px 0;padding:9px;font-size:12px;line-height:1.4}.hint{position:absolute;bottom:14px;left:18px;font-size:12px;color:#a7b7ce;pointer-events:none}.notice{font-size:12px;line-height:1.5;padding:14px 24px;color:#a8b6cb;border-top:1px solid #243147}.notice span{color:#fcd34d}.spacer{flex:1}@media(max-width:850px){main{grid-template-columns:1fr}aside{max-height:none;border-left:0;border-top:1px solid #243147}#canvas{height:65vh;min-height:450px}.bar{padding:12px}header{padding:20px}}
</style></head><body>
<header><div class="eyebrow">ORION / KNOWLEDGE GRAPH</div><h1>Multiple Myeloma: features &amp; mechanisms</h1><p>A snapshot queried from your AWS Neptune graph · Monarch phenotype associations and DisMech mechanism paths.</p><p id="snapshot"></p></header>
<div class="bar"><label>View <select id="mode"><option value="clinical">Bone, kidney, calcium &amp; anemia + mechanisms</option><option value="mechanisms">Mechanisms</option><option value="phenotypes">All loaded phenotypes</option><option value="all">Complete focused graph</option></select></label><input id="search" type="search" aria-label="Find a node" placeholder="Find a feature or mechanism"><button id="fit">Fit graph</button><button id="clear">Clear selection</button><button id="save">Download SVG</button><span class="spacer"></span><span class="badge" id="counts"></span></div>
<main><div id="canvas" aria-label="Interactive knowledge graph"><div class="hint">Scroll to zoom · Drag to pan · Click a node or arrow for sources</div></div><aside><div class="legend"><span><i class="dot" style="background:#c4b5fd"></i>Disease</span><span><i class="dot" style="background:#93c5fd"></i>Phenotype</span><span><i class="dot" style="background:#fcd34d"></i>Mechanism</span></div><section id="details"></section></aside></main>
<footer class="notice">Edges preserve the loaded source relationship and direction. A disease–phenotype association does not establish a causal mechanism. <span>Snapshot only: subsequent Neptune changes require regeneration.</span></footer>
<script id="data" type="application/json">__DATA__</script><script>
'use strict';
const data=JSON.parse(document.getElementById('data').textContent),canvas=document.getElementById('canvas'),details=document.getElementById('details'),mode=document.getElementById('mode');
const nodeMap=new Map(data.nodes.map(n=>[n.id,n])),evidence=new Map(data.evidence.map(e=>[e.id,e]));let svg,x=0,y=0,scale=1,drag=null;
const el=(tag,text)=>{const n=document.createElement(tag);if(text!==undefined)n.textContent=text;return n};
document.getElementById('snapshot').textContent=`Generated ${new Date(data.generated_at).toLocaleString()} · ${data.nodes.length} nodes / ${data.edges.length} relationships in this focused snapshot`;
function title(text){details.append(el('h3',text))}function note(text){details.append(el('p',text))}function code(text){details.append(el('code',text))}
function source(dataset,version){const block=el('div');block.className='record';block.append(el('strong',dataset||'Unrecorded source'),el('p',`Version: ${version||'unrecorded'}`));details.append(block)}
function home(){details.replaceChildren(el('h2','Explore the loaded evidence'));note('Click a node or relationship to inspect its identifier, source version and available references.');title('Reading the graph');note('Blue arrows: Monarch phenotype associations. Amber dashed arrows: disease-to-mechanism links. Amber solid arrows: source-reported “leads to” relationships.');title('Data limits');const list=el('ul');for(const item of data.limitations)list.append(el('li',item));details.append(list)}
function refs(ids){title('Recorded references');if(!ids?.length){note('No reference is recorded for this relationship.');return}for(const id of ids){const ref=evidence.get(id);if(!ref)continue;const p=el('p');const match=/^PMID:(\d+)$/.exec(ref.reference||'');if(match){const link=el('a',ref.reference);link.href=`https://pubmed.ncbi.nlm.nih.gov/${match[1]}/`;link.target='_blank';link.rel='noopener noreferrer';p.append(link)}else p.textContent=ref.reference||'Unrecorded reference';details.append(p)}}
function highlight(nodes,edges){svg.querySelectorAll('.node').forEach(n=>n.classList.toggle('dim',!nodes.has(data.nodes[Number(n.id.slice(5))]?.id)));svg.querySelectorAll('.edge').forEach(e=>e.classList.toggle('dim',!edges.has(data.edges[Number(e.id.slice(5))]?.id)))}
function selectNode(index){const n=data.nodes[index],incident=data.edges.filter(e=>e.source===n.id||e.target===n.id);details.replaceChildren(el('h2',n.label));note(n.type);code(n.id);title('Sources');for(const s of n.sources)source(s.source_dataset,s.source_version);title('Recorded connections');for(const e of incident){const other=nodeMap.get(e.source===n.id?e.target:e.source);const b=el('button',`${e.predicate} · ${other?.label||'Unknown'}`);b.className='edgebutton';b.onclick=()=>selectEdge(data.edges.indexOf(e));details.append(b)}highlight(new Set([n.id,...incident.flatMap(e=>[e.source,e.target])]),new Set(incident.map(e=>e.id)))}
function selectEdge(index){const e=data.edges[index];details.replaceChildren(el('h2',e.predicate));note(`${nodeMap.get(e.source)?.label} → ${nodeMap.get(e.target)?.label}`);code(e.id);source(e.source_dataset,e.source_version);refs(e.evidence_ids);highlight(new Set([e.source,e.target]),new Set([e.id]))}
function transform(){svg.style.transform=`translate(${x}px,${y}px) scale(${scale})`}
function fit(){const b=svg.viewBox.baseVal;scale=Math.min((canvas.clientWidth-30)/b.width,(canvas.clientHeight-40)/b.height);x=(canvas.clientWidth-b.width*scale)/2;y=(canvas.clientHeight-b.height*scale)/2;transform()}
function clear(){svg.querySelectorAll('.dim,.selected').forEach(n=>n.classList.remove('dim','selected'));home()}
function render(){svg?.remove();const template=el('template');template.innerHTML=data.views[mode.value].svg;svg=template.content.firstElementChild;const b=svg.viewBox.baseVal;svg.style.width=`${b.width}px`;svg.style.height=`${b.height}px`;canvas.prepend(svg);svg.querySelectorAll('.node').forEach(n=>{n.setAttribute('tabindex','0');n.setAttribute('role','button');n.onclick=()=>selectNode(Number(n.id.slice(5)));n.onkeydown=e=>{if(e.key==='Enter')n.onclick()}});svg.querySelectorAll('.edge').forEach(n=>{n.onclick=()=>selectEdge(Number(n.id.slice(5)))});const view=data.views[mode.value];document.getElementById('counts').textContent=`${view.nodes} nodes · ${view.edges} relationships`;fit();home()}
canvas.addEventListener('wheel',e=>{e.preventDefault();const r=canvas.getBoundingClientRect(),px=e.clientX-r.left,py=e.clientY-r.top,next=Math.max(.08,Math.min(5,scale*Math.exp(-e.deltaY*.001)));x=px-(px-x)*next/scale;y=py-(py-y)*next/scale;scale=next;transform()},{passive:false});
canvas.addEventListener('pointerdown',e=>{if(e.target.closest('.node,.edge'))return;drag={px:e.clientX,py:e.clientY,x,y};canvas.setPointerCapture(e.pointerId)});canvas.addEventListener('pointermove',e=>{if(!drag)return;x=drag.x+e.clientX-drag.px;y=drag.y+e.clientY-drag.py;transform()});canvas.addEventListener('pointerup',()=>drag=null);canvas.addEventListener('pointercancel',()=>drag=null);
mode.onchange=render;document.getElementById('fit').onclick=fit;document.getElementById('clear').onclick=clear;window.addEventListener('resize',fit);
document.getElementById('search').addEventListener('input',e=>{const q=e.target.value.trim().toLowerCase();if(!q){clear();return}const matches=new Set(data.nodes.filter(n=>n.label.toLowerCase().includes(q)||n.id.toLowerCase().includes(q)).map(n=>n.id));const edges=data.edges.filter(e=>matches.has(e.source)||matches.has(e.target));highlight(new Set([...matches,...edges.flatMap(e=>[e.source,e.target])]),new Set(edges.map(e=>e.id)))});
document.getElementById('save').onclick=()=>{const url=URL.createObjectURL(new Blob([data.views[mode.value].svg],{type:'image/svg+xml'})),a=el('a');a.href=url;a.download=`orion-mm-${mode.value}.svg`;a.click();setTimeout(()=>URL.revokeObjectURL(url),1000)};render();
</script></body></html>"""


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("responses", nargs="+", type=Path)
    parser.add_argument("--output", type=Path, default=Path("data/transformed/neptune-graph"))
    args = parser.parse_args()
    if not shutil.which("dot"):
        parser.error("Install Graphviz so the dot command is available")
    data = read_responses(args.responses)
    args.output.mkdir(parents=True, exist_ok=True)
    data["views"] = {view: diagram(data, view) for view in ["clinical", "mechanisms", "phenotypes", "all"]}
    for view, result in data["views"].items():
        (args.output / f"{view}.svg").write_text(result["svg"], encoding="utf-8")
    payload = (
        json.dumps(data, ensure_ascii=False)
        .replace("<", "\\u003c")
        .replace(">", "\\u003e")
        .replace("&", "\\u0026")
    )
    (args.output / "index.html").write_text(HTML.replace("__DATA__", payload), encoding="utf-8")
    print(
        json.dumps(
            {
                "html": str(args.output / "index.html"),
                "nodes": len(data["nodes"]),
                "edges": len(data["edges"]),
                "views": {k: {"nodes": v["nodes"], "edges": v["edges"]} for k, v in data["views"].items()},
            },
            indent=2,
        )
    )


if __name__ == "__main__":
    main()
