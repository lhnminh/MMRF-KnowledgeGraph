'use strict';
const $ = id => document.getElementById(id);
const filterIds = ['view', 'source', 'entity_type', 'predicate', 'q'];
let data = null, cy = null, controller = null, sequence = 0, timer;
const element = (tag, text) => { const node = document.createElement(tag); if (text !== undefined) node.textContent = text; return node; };
const prettify = value => value === 'opentargets' ? 'Open Targets' : value === 'clinicaltrials_gov' ? 'ClinicalTrials.gov' : value.replace(/^biolink:|^dismech:|^clinicaltrials:/, '').replaceAll('_', ' ');
const overlay = (text, error = false) => { $('overlay').textContent = text; $('overlay').hidden = !text; $('overlay').classList.toggle('error', error); };
function note(text) { $('details').append(element('p', text)); }
function heading(text) { $('details').append(element('h3', text)); }
function link(text, url, parent = $('details')) {
  if (!url || !url.startsWith('https://')) { parent.append(element('p', text)); return; }
  const a = element('a', text); a.href = url; a.target = '_blank'; a.rel = 'noopener noreferrer'; parent.append(a);
}
function showSource(source) {
  const block = element('div'); block.className = 'record';
  block.append(element('strong', source.source_dataset), element('p', `Version: ${source.source_version}`));
  if (source.url) link('Open source dataset / record ↗', source.url, block);
  if (source.basis === 'relationship') block.append(element('p', 'Provenance is recorded on the connected relationship.'));
  $('details').append(block);
}
function home() {
  $('details').replaceChildren(element('h2', 'Explore the recorded evidence'));
  note('Select a node or arrow to inspect its identifiers, source version, and available references.');
  note('Blue arrows show Monarch records; amber arrows show DisMech records; teal arrows show Open Targets drug targets; purple arrows link registered trials through publication identifiers. An association alone does not establish a causal mechanism.');
  if (data) note(`Queried from AWS Neptune at ${new Date(data.meta.fetched_at).toLocaleString()}.`);
}
function highlight(nodeIds, edgeIds) {
  cy.elements().addClass('dim');
  cy.nodes().filter(n => nodeIds.has(n.id())).removeClass('dim');
  cy.edges().filter(e => edgeIds.has(e.id())).removeClass('dim');
}
function selectNode(id) {
  const node = data.graph.nodes.find(n => n.id === id); if (!node) return;
  const incident = data.graph.edges.filter(e => e.source === id || e.target === id);
  $('details').replaceChildren(element('h2', node.label)); note(node.type); $('details').append(element('code', node.id));
  if (node.aliases?.length) note(`Also recorded as: ${node.aliases.join('; ')}`);
  heading('Source provenance'); node.sources.forEach(showSource);
  for (const key of ['brief_title', 'study_phase', 'study_status', 'study_type', 'enrollment_count', 'enrollment_type', 'registry_last_update', 'has_results', 'confidence', 'review_status', 'retrieved_at', 'synthetic']) {
    if (node[key] !== undefined && node[key] !== null) note(`${prettify(key)}: ${node[key]}`);
  }
  if (node.type === 'ClinicalTrial') note('Enrollment and status are reported by the registry. This link does not identify a treatment arm or establish efficacy.');
  heading('Visible connections');
  incident.forEach(edge => {
    const otherId = edge.source === id ? edge.target : edge.source;
    const other = data.graph.nodes.find(n => n.id === otherId);
    const button = element('button', `${prettify(edge.predicate)} · ${other?.label || otherId}`);
    button.className = 'edge-button'; button.type = 'button'; button.onclick = () => selectEdge(edge.id); $('details').append(button);
  });
  highlight(new Set([id, ...incident.flatMap(e => [e.source, e.target])]), new Set(incident.map(e => e.id)));
}
function selectEdge(id) {
  const edge = data.graph.edges.find(e => e.id === id); if (!edge) return;
  const names = new Map(data.graph.nodes.map(n => [n.id, n.label]));
  $('details').replaceChildren(element('h2', prettify(edge.predicate)));
  note(`${names.get(edge.source)} → ${names.get(edge.target)}`);
  $('details').append(element('code', edge.original_id || edge.tool_edge_id));
  showSource({source_dataset: edge.source_dataset, source_version: edge.source_version, url: edge.source_url});
  heading('Relationship metadata');
  for (const key of ['relationship_type', 'identifier_origin', 'edge_origin', 'mapping_basis', 'dismech_source_version', 'clinical_trial_id', 'confidence', 'review_status', 'retrieved_at', 'synthetic', 'frequency', 'chembl_id', 'action_type', 'mechanism_of_action']) {
    if (edge[key] !== undefined && edge[key] !== null) note(`${prettify(key)}: ${edge[key]}`);
  }
  heading('Recorded references');
  if (!edge.evidence_ids.length) note('No evidence reference is recorded for this relationship.');
  edge.evidence_ids.forEach(id => { const ref = data.evidence.find(e => e.id === id); if (ref) { const p = element('p'); link(ref.reference, ref.url, p); $('details').append(p); } });
  highlight(new Set([edge.source, edge.target]), new Set([id]));
}
function updateFacets(facets) {
  for (const [id, key, label] of [['source', 'sources', 'All sources'], ['entity_type', 'entity_types', 'All types'], ['predicate', 'predicates', 'All relationships']]) {
    const current = $(id).value;
    $(id).replaceChildren(new Option(label, ''), ...facets[key].map(value => new Option(prettify(value), value)));
    if ([...$(id).options].some(option => option.value === current)) $(id).value = current;
  }
}
function renderGraph() {
  cy?.destroy();
  const colors = {Disease: '#b8a1ff', PhenotypicFeature: '#80bcf5', Mechanism: '#f7c66b', Gene: '#7de0b3', 'dismech:GeneticFactor': '#7de0b3', Drug: '#ed9bcd', Treatment: '#ed9bcd', ClinicalTrial: '#c4a5ff'};
  const nodes = data.graph.nodes.map(n => ({data: {...n, color: colors[n.type] || '#a9b8ce'}}));
  const edges = data.graph.edges.map(e => ({data: {...e, shortLabel: prettify(e.predicate)}}));
  cy = cytoscape({container: $('graph'), elements: [...nodes, ...edges], minZoom: 0.12, maxZoom: 3,
    style: [
      {selector: 'node', style: {'background-color': 'data(color)', width: 28, height: 28, label: 'data(label)', color: '#dce6f5', 'font-size': 11, 'text-wrap': 'wrap', 'text-max-width': 125, 'text-valign': 'bottom', 'text-margin-y': 7, 'border-width': 1, 'border-color': '#dce6f555'}},
      {selector: 'node[type="Disease"]', style: {width: 52, height: 52, 'font-size': 13, 'font-weight': 700}},
      {selector: 'node[type="Mechanism"]', style: {shape: 'round-rectangle', width: 100, height: 34, 'text-valign': 'center', 'text-margin-y': 0, color: '#261e12', 'text-max-width': 93, 'font-size': 9}},
      {selector: 'node[type="ClinicalTrial"]', style: {shape: 'hexagon', width: 38, height: 38}},
      {selector: 'edge', style: {width: 1.5, 'curve-style': 'bezier', 'target-arrow-shape': 'triangle', 'arrow-scale': 0.8, 'line-color': '#689ed1', 'target-arrow-color': '#689ed1', opacity: 0.7, 'font-size': 8, color: '#b3c3d8', 'text-background-color': '#101b2d', 'text-background-opacity': 0.85, 'text-background-padding': '3px', 'text-rotation': 'autorotate'}},
      {selector: 'edge[source_dataset="dismech"]', style: {'line-color': '#bd9452', 'target-arrow-color': '#bd9452'}},
      {selector: 'edge[source_dataset="opentargets"]', style: {'line-color': '#4bc5b8', 'target-arrow-color': '#4bc5b8'}},
      {selector: 'edge[source_dataset="clinicaltrials_gov"]', style: {'line-color': '#c4a5ff', 'target-arrow-color': '#c4a5ff'}},
      {selector: 'edge[relationship_type="HAS_MECHANISM"]', style: {'line-style': 'dashed'}},
      {selector: '.dim', style: {opacity: 0.14, 'text-opacity': 0.14}},
      {selector: ':selected', style: {'border-width': 3, 'border-color': '#ffffff', 'line-color': '#ffffff', 'target-arrow-color': '#ffffff'}}
    ], layout: {name: 'cose', animate: false, randomize: true, nodeDimensionsIncludeLabels: true, nodeRepulsion: () => 40000, idealEdgeLength: () => 145, numIter: 1100, padding: 45}});
  cy.on('tap', 'node', event => selectNode(event.target.id()));
  cy.on('tap', 'edge', event => selectEdge(event.target.id()));
  cy.on('tap', event => {if (event.target === cy) { cy.elements().removeClass('dim'); home(); }});
  setLabels();
  $('counts').textContent = `${nodes.length} nodes · ${edges.length} relationships${data.meta.truncated ? ' · limited' : ''}`;
  $('node-list').replaceChildren(...data.graph.nodes.slice().sort((a, b) => a.label.localeCompare(b.label)).map(n => {
    const button = element('button', n.label); button.className = 'node-button'; button.type = 'button';
    button.append(element('span', `${n.type} · ${n.id}`)); button.onclick = () => selectNode(n.id); return button;
  }));
  $('limitations').replaceChildren(...data.limitations.map(text => element('li', text)));
  home(); overlay(edges.length ? '' : 'No recorded relationships match these filters. Try another view or reset the filters.');
}
function setLabels() { cy?.edges().style('label', $('labels').getAttribute('aria-pressed') === 'true' ? 'data(shortLabel)' : ''); }
async function loadGraph() {
  controller?.abort(); controller = new AbortController();
  const current = ++sequence, activeController = controller;
  const timeout = setTimeout(() => activeController.abort('timeout'), 27000);
  const params = new URLSearchParams(); filterIds.forEach(id => {if ($(id).value) params.set(id, $(id).value);});
  // Remove stale data immediately so failures never leave an old graph labelled as current.
  data = null; cy?.destroy(); cy = null; $('node-list').replaceChildren(); $('limitations').replaceChildren(); home();
  $('download').disabled = true; $('counts').textContent = 'Loading from Neptune…'; overlay('Loading recorded relationships from Neptune…');
  try {
    const result = await fetch(`/api/graph?${params}`, {signal: activeController.signal, cache: 'no-store'});
    const value = await result.json();
    if (current !== sequence) return;
    if (!result.ok) throw new Error(value.error || `Request failed (${result.status}).`);
    data = value; updateFacets(data.facets); renderGraph(); $('download').disabled = false;
    $('connection-status').textContent = `Connected · refreshed ${new Date(data.meta.fetched_at).toLocaleTimeString()}`;
  } catch (error) {
    if (current !== sequence) return;
    const message = error.name === 'AbortError' || activeController.signal.reason === 'timeout' ? 'The request timed out. Select Refresh to try again.' : error.message;
    overlay(message, true); $('counts').textContent = 'Graph unavailable'; $('connection-status').textContent = message;
  } finally { clearTimeout(timeout); }
}
$('filters').onsubmit = event => event.preventDefault();
filterIds.filter(id => id !== 'q').forEach(id => $(id).onchange = () => {
  clearTimeout(timer);
  if (id === 'source' && $('source').value === 'opentargets' && !['all', 'treatments'].includes($('view').value)) $('view').value = 'treatments';
  if (id === 'source' && $('source').value === 'clinicaltrials_gov' && !['all', 'clinical_trials'].includes($('view').value)) $('view').value = 'clinical_trials';
  loadGraph();
});
$('q').oninput = () => {clearTimeout(timer); timer = setTimeout(loadGraph, 400);};
$('reset').onclick = () => {clearTimeout(timer); filterIds.forEach(id => $(id).value = id === 'view' ? 'all' : ''); loadGraph();};
$('refresh').onclick = loadGraph;
$('fit').onclick = () => cy?.fit(undefined, 40);
$('labels').onclick = () => {$('labels').setAttribute('aria-pressed', $('labels').getAttribute('aria-pressed') !== 'true' ? 'true' : 'false'); setLabels();};
$('download').disabled = true;
$('download').onclick = () => {if (!data) return; const url = URL.createObjectURL(new Blob([JSON.stringify(data, null, 2)], {type: 'application/json'})); const a = element('a'); a.href = url; a.download = 'orion-neptune-filtered-graph.json'; a.click(); setTimeout(() => URL.revokeObjectURL(url), 1000);};
loadGraph();
