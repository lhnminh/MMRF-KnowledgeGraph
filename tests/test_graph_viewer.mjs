import assert from 'node:assert/strict';
import { readFileSync } from 'node:fs';
import { test } from 'node:test';
import { runInNewContext } from 'node:vm';

test('viewer loads without credentials, supports refresh, and opens Open Targets in the treatment view', async () => {
  const html = readFileSync(new URL('../graph_api/static/index.html', import.meta.url), 'utf8');
  const script = readFileSync(new URL('../graph_api/static/app.js', import.meta.url), 'utf8');
  const element = () => ({
    value: '', textContent: '', append() {}, replaceChildren() {},
    classList: { toggle() {} },
  });
  const elements = new Map([...html.matchAll(/\bid="([^"]+)"/g)].map((match) => [match[1], element()]));
  elements.get('view').value = 'clinical';
  const calls = [];
  runInNewContext(script, {
    AbortController, URLSearchParams, setTimeout, clearTimeout,
    document: {
      getElementById(id) {
        assert.ok(elements.has(id), `Viewer references a missing HTML element: ${id}`);
        return elements.get(id);
      },
      createElement: element,
    },
    async fetch(url, options) {
      calls.push({ url, options });
      throw new Error('Synthetic connection failure');
    },
  });
  await new Promise(setImmediate);
  assert.equal(calls.length, 1);
  assert.equal(calls[0].url, '/api/graph?view=clinical');
  assert.equal(calls[0].options.headers, undefined);
  assert.equal(elements.get('connection-status').textContent, 'Synthetic connection failure');

  await elements.get('refresh').onclick();
  assert.equal(calls.length, 2);

  elements.get('source').value = 'opentargets';
  elements.get('source').onchange();
  await new Promise(setImmediate);
  assert.equal(elements.get('view').value, 'treatments');
  assert.equal(calls[2].url, '/api/graph?view=treatments&source=opentargets');
  assert.ok(calls.every(({ options }) => options.headers === undefined));

  elements.get('source').value = 'clinicaltrials_gov';
  elements.get('source').onchange();
  await new Promise(setImmediate);
  assert.equal(elements.get('view').value, 'clinical_trials');
  assert.equal(calls[3].url, '/api/graph?view=clinical_trials&source=clinicaltrials_gov');
  assert.ok(html.includes('value="clinical_trials"'));
});
