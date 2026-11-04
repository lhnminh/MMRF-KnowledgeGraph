import React, { FormEvent, useRef, useState } from 'react';
import dynamic from 'next/dynamic';
import Link from 'next/link';
import ReactMarkdown from 'react-markdown';
import { convertFilterSetToGqlFilter, useCoreSelector } from '@gen3/core';
import { selectCurrentCohortCaseFilters } from '@/core/utils';
import { COHORT_FILTER_INDEX } from '@/core';
import { withClientBasePath } from '@/lib/basePath';
import { openModalWithCohortFilterRepresentation } from '@/features/cases/CasesCohortButton/CasesCohortButton';
import { readEvents } from './stream';
import type { AgentEvent, HistoryTurn, Result, Trace } from './types';
import styles from './OrionChat.module.css';

// Reuses the pinned Virtual Lab Plotly component. Data comes from the typed API adapter.
const BarChart = dynamic(() => import('@/features/charts/BarChart'), { ssr: false });
const EXAMPLE = 'How is TP53 connected to multiple myeloma, what evidence supports the relationship, and what does the synthetic Virtual Lab cohort show?';

function SourceLink({ url, children }: { url?: string; children: React.ReactNode }) {
  if (!url || !/^https?:\/\//i.test(url)) return <span>{children}</span>;
  return <a href={url} target="_blank" rel="noopener noreferrer">{children} ↗</a>;
}

function Findings({ result, index, ask }: { result: Result; index: number; ask: (prompt: string) => void }) {
  const graph = result.graph;
  const names = new Map(graph.nodes.map(n => [n.id, n.name]));
  const cohort = result.cohort;
  const genes = cohort?.visualization.genes ?? [];
  return <>
    <div className={styles.answer}><ReactMarkdown>{result.answer}</ReactMarkdown></div>
    {graph.relationships.length > 0 && <section className={styles.panel} aria-label="Graph paths">
      <div className={styles.panelHeading}><h3>Connections in the graph</h3><span>{graph.relationships.length} relationships</span></div>
      <p className={styles.muted}>Association paths show connections. They do not establish causation.</p>
      {graph.paths.slice(0, 5).map((p, i) => <div className={styles.path} key={i}>
        {p.node_ids.map((id, j) => <React.Fragment key={id}>
          {j > 0 && <span title={graph.relationships.find(r => r.id === p.relationship_ids[j - 1])?.predicate}>↔</span>}
          <button title={id} onClick={() => ask(`Use get_graph_neighborhood for ${id} with maximum_depth 1 and explain its evidence.`)}>{names.get(id) ?? id}</button>
        </React.Fragment>)}
      </div>)}
      <details><summary>Relationship direction and source predicates</summary>
        <div className={styles.tableScroll}><table><thead><tr><th>From</th><th>Predicate</th><th>To</th></tr></thead><tbody>
          {graph.relationships.map(r => <tr key={r.id}><td>{names.get(r.subject) ?? r.subject}</td><td>{r.predicate}</td><td>{names.get(r.object) ?? r.object}</td></tr>)}
        </tbody></table></div>
      </details>
    </section>}
    {cohort && <section className={styles.panel} aria-label="Synthetic cohort visualization">
      <div className={styles.panelHeading}><h3>Synthetic cohort</h3><span className={styles.synthetic}>FABRICATED DATA</span></div>
      <p>{cohort.empty ? 'No cases match this selection.' : `${cohort.gene}: ${cohort.mutated_cases} of ${cohort.total_cases} cases (${cohort.percentage}%) have a panel mutation.`}</p>
      {!cohort.empty && <>
        <div className={styles.chart}><BarChart divId={`orion-mutations-${index}`} data={{
          datasets: [{ x: genes.map((_, i) => i), y: genes.map(g => 100 * g.count / cohort.total_cases), marker: { color: '#298b88' }, customdata: genes.map(g => [g.symbol, g.count, cohort.total_cases]), hovertemplate: '%{customdata[0]}: %{customdata[1]} / %{customdata[2]} synthetic cases<extra></extra>' }],
          tickvals: genes.map((_, i) => i), ticktext: genes.map(g => g.symbol), yAxisTitle: '% of synthetic cases',
        }} marginBottom={70} /></div>
        <details><summary>Accessible chart data</summary><ul>{genes.map(g => <li key={g.symbol}>{g.symbol}: {g.count} / {cohort.total_cases}</li>)}</ul></details>
        <button className={styles.secondary} onClick={() => openModalWithCohortFilterRepresentation({ mode: 'and', root: { case_id: { field: 'case_id', operator: 'in', operands: cohort.case_ids } } })}>Save this selection in Virtual Lab</button>
        <Link className={styles.secondary} href={`/genes/${cohort.gene}`}>Open existing gene view</Link>
      </>}
      <p className={styles.muted}>Guppy cohort selection + ProteinPaint mutation counts. Fabricated observations are not population estimates.</p>
    </section>}
    {graph.relationships.length > 0 && <section className={styles.panel} aria-label="Evidence and provenance">
      <div className={styles.panelHeading}><h3>Evidence & provenance</h3><span>PUBLIC BIOLOGY</span></div>
      <div className={styles.evidence}>{graph.relationships.map(r => <details key={r.id}>
        <summary>{names.get(r.subject) ?? r.subject} → {names.get(r.object) ?? r.object}<small>{r.source} · {r.evidence.length} reference records</small></summary>
        <p><SourceLink url={r.source_url}>Source record</SourceLink> · Retrieved: {r.retrieved_at}</p>
        <p className={styles.muted}>{r.review_status} · Confidence: {r.confidence}</p>
        <code>{r.id}</code>
        {r.evidence.length ? r.evidence.map((e, i) => <div key={i}><SourceLink url={e.source_url ?? (e.reference?.startsWith('PMID:') ? `https://pubmed.ncbi.nlm.nih.gov/${e.reference.slice(5)}/` : undefined)}>{e.reference ?? 'Source evidence'}</SourceLink><p>{e.summary ?? e.explanation ?? 'See source record for supporting evidence.'}</p></div>) : <p>No publication reference was supplied for this relationship.</p>}
      </details>)}</div>
    </section>}
    <p className={styles.disclaimer}>{result.disclaimer}</p>
  </>;
}

type Entry = { question: string; result: Result };
export default function OrionChat() {
  const [prompt, setPrompt] = useState('');
  const [entries, setEntries] = useState<Entry[]>([]);
  const [traces, setTraces] = useState<Trace[]>([]);
  const [busy, setBusy] = useState(false);
  const [pendingQuestion, setPendingQuestion] = useState('');
  const [error, setError] = useState('');
  const [mode, setMode] = useState('Ready to explore');
  const [selection, setSelection] = useState('current');
  const active = useRef(false);
  const currentCohort = useCoreSelector(state => selectCurrentCohortCaseFilters(state, COHORT_FILTER_INDEX));

  async function ask(question: string) {
    if (active.current || !question.trim()) return;
    active.current = true; setBusy(true); setError(''); setTraces([]); setPendingQuestion(question); setPrompt('');
    const history: HistoryTurn[] = entries.slice(-6).flatMap(e => [{ role: 'user' as const, content: e.question }, { role: 'assistant' as const, content: e.result.answer.slice(0, 6000) }]);
    let received = false;
    try {
      const filter = selection === 'current' ? convertFilterSetToGqlFilter(currentCohort) : selection === 'all' ? {} : { in: { 'demographic.gender': [selection] } };
      const response = await fetch(withClientBasePath('/api/orion/chat'), { method: 'POST', headers: { 'Content-Type': 'application/json' }, body: JSON.stringify({ prompt: question, history, cohort_filter: filter ?? {} }) });
      if (!response.ok || !response.body) {
        const detail = await response.json().catch(() => null);
        throw new Error(detail?.error ?? 'ORION is unavailable. Check the agent service and try again.');
      }
      await readEvents(response.body, (event: AgentEvent) => {
        if (event.event === 'status') setMode(`${event.mode === 'rehearsal' ? 'Scripted rehearsal' : 'Amazon Bedrock'} · ${event.graph_backend === 'local' ? 'local graph' : 'Neptune'}`);
        if (event.event === 'tool_start') setTraces(t => [...t, { id: event.id, name: event.name, state: 'running' }]);
        if (event.event === 'tool_end') setTraces(t => t.map(x => x.id === event.id ? { ...x, state: event.status, empty: event.empty, message: event.error?.message } : x));
        if (event.event === 'error') throw new Error(event.error.message);
        if (event.event === 'result') { received = true; setEntries(e => [...e, { question, result: event }]); }
      });
      if (!received) throw new Error('The agent stream ended before an answer arrived. Please retry.');
    } catch (e) { setError(e instanceof Error ? e.message : 'The request failed.'); setPrompt(question); }
    finally { active.current = false; setBusy(false); setPendingQuestion(''); }
  }

  function submit(e: FormEvent) { e.preventDefault(); void ask(prompt); }
  return <main className={styles.shell}>
    <header className={styles.header}><div><p className={styles.eyebrow}>VIRTUAL LAB / RESEARCH ASSISTANT</p><h1>ORION<span>Explore the evidence.</span></h1><p>Connect myeloma biology to a synthetic cohort, with sources at every step.</p></div><span className={styles.status}>{mode}</span></header>
    <div className={styles.notice}>Research sandbox · Synthetic cohort data · No diagnosis or treatment recommendations</div>
    {!entries.length && !busy && <section className={styles.welcome}><span className={styles.orbit}>✦</span><h2>From a question to its evidence</h2><p>Start with TP53, KRAS, NRAS or BRAF. Follow the graph, inspect the publications, and explore the same cohort in Virtual Lab.</p><button onClick={() => void ask(EXAMPLE)}>Explore TP53 and the synthetic cohort <span>↗</span></button></section>}
    <div aria-live="polite" aria-busy={busy}>
      {entries.map((entry, i) => <article key={i} className={styles.turn}><div className={styles.question}><small>YOU</small><p>{entry.question}</p></div><div className={styles.response}><div className={styles.agentLabel}>✦ ORION <span>{entry.result.mode === 'rehearsal' ? 'SCRIPTED REHEARSAL' : 'BEDROCK'}</span></div><Findings result={entry.result} index={i} ask={q => void ask(q)} /></div></article>)}
      {pendingQuestion && <div className={styles.question}><small>YOU</small><p>{pendingQuestion}</p></div>}
      {!!traces.length && <section className={styles.trace} aria-label="Tool execution status"><h3>{busy ? 'Following the evidence…' : 'Tools used in the latest response'}</h3>{traces.map(t => <div key={t.id}><span className={styles[t.state]}>{t.state === 'running' ? '◌' : t.state === 'success' ? '✓' : '!'}</span><code>{t.name}</code><span>{t.message ?? (t.empty ? 'No results' : t.state)}</span></div>)}</section>}
      {error && <div role="alert" className={styles.error}>{error}</div>}
    </div>
    {!!entries.length && !busy && <button className={styles.followup} onClick={() => void ask('Show related genes with similar phenotypes.')}>Show related genes with similar phenotypes →</button>}
    <form className={styles.composer} onSubmit={submit}>
      <label htmlFor="orion-prompt">Ask ORION</label><textarea id="orion-prompt" placeholder="Ask about a gene, mechanism, or its evidence…" value={prompt} maxLength={4000} onChange={e => setPrompt(e.target.value)} disabled={busy} rows={3} />
      <div><label>Cohort <select aria-label="Synthetic cohort selection" value={selection} disabled={busy} onChange={e => setSelection(e.target.value)}><option value="current">Current Virtual Lab cohort</option><option value="all">All synthetic cases</option><option value="female">Female synthetic cases</option><option value="male">Male synthetic cases</option></select></label><button type="submit" disabled={busy || !prompt.trim()}>{busy ? 'Working…' : 'Ask ORION ↑'}</button></div>
      <small>Supports gender filters and explicit synthetic case selections. Other current-cohort filters produce a clear validation error.</small>
    </form>
  </main>;
}
