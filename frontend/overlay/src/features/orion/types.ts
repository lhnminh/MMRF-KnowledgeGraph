export type HistoryTurn = { role: 'user' | 'assistant'; content: string };
export type GraphNode = { id: string; name: string; entity_type: string };
export type Evidence = { reference?: string; source_url?: string; summary?: string; explanation?: string; snippet?: string };
export type Relationship = {
  id: string; subject: string; object: string; predicate: string; source: string;
  source_url: string; retrieved_at: string; review_status: string; confidence: string;
  evidence: Evidence[];
};
export type Graph = {
  nodes: GraphNode[]; relationships: Relationship[];
  paths: { node_ids: string[]; relationship_ids: string[] }[];
};
export type Cohort = {
  data_kind: 'synthetic'; gene: string; total_cases: number; mutated_cases: number;
  percentage: number | null; empty: boolean; case_ids: string[];
  visualization: { component: 'VirtualLabBarChart'; genes: { symbol: string; count: number }[]; total_cases: number };
};
export type Result = {
  answer: string; mode: string; graph_backend: string; graph: Graph;
  cohort: Cohort | null; disclaimer: string;
};
export type Trace = { id: string; name: string; state: 'running' | 'success' | 'error'; empty?: boolean; message?: string };
export type AgentEvent =
  | ({ event: 'result' } & Result)
  | { event: 'status'; mode: string; graph_backend: string }
  | { event: 'tool_start'; id: string; name: string; arguments: Record<string, unknown> }
  | { event: 'tool_end'; id: string; name: string; status: 'success' | 'error'; empty: boolean; error?: { message: string } }
  | { event: 'error'; error: { code: string; message: string; retryable: boolean } };
