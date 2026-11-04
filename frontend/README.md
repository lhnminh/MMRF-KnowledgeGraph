# ORION frontend overlay

This directory preserves the chat UI as an integration draft. It is not a
standalone Next.js application and is not deployed by the knowledge-graph
Terraform stacks. Repository URLs and revision pins are in `../sources.json`.

To integrate it, copy the contents of `overlay/src/` into `src/` in a separate
checkout of the pinned `mmrf-data-commons` revision. The page uses that app's
`@/` aliases, cohort store, existing BarChart component, and React/Next.js
dependencies. Run the frontend's own dependency installation, type checks, and
build before deploying; those full application checks have not been performed
for this overlay.

The added page is `/orion`, and its server-side route is `/api/orion/chat`.
Configure `ORION_SERVER_URL` on the Next.js server to the agent's streaming
invocation endpoint. If required by that service, configure
`ORION_INTERNAL_TOKEN` server-side; the proxy forwards it as `X-Orion-Token`
and does not expose it to the browser. No agent backend or authentication
deployment is supplied by this directory. Keep the page behind the Virtual
Lab's authenticated sandbox entry point.

The backend must accept `{prompt, history, cohort_filter}` and emit SSE `data:`
frames matching `overlay/src/features/orion/types.ts`: status, tool execution,
result, and error events. This draft's result shape uses `graph.relationships`
and optional synthetic cohort data. The deployed knowledge-graph tools return
the separate contract-version 0.2 envelope with `graph.edges`; they cannot be
used as this streaming endpoint without an agent/adapter. Integrating that
backend remains separate work.

Run the isolated proxy and stream-parser checks from the repository root with
Node.js that supports native TypeScript type stripping (verified with Node 25):

```sh
node --test frontend/tests/proxy.test.mjs frontend/tests/stream.test.mjs
```

These tests exercise request restrictions, server-side token forwarding,
upstream errors, and SSE chunk decoding. They do not verify a rendered UI or a
live AgentCore/Virtual Lab workflow.
