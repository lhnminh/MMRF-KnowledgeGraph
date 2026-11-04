import { test } from 'node:test';
import assert from 'node:assert/strict';
import { EventEmitter } from 'node:events';
import handler from '../overlay/src/pages/api/orion/chat.ts';

class Reply extends EventEmitter {
  code = 200;
  headers = {};
  headersSent = false;
  destroyed = false;
  chunks = [];
  status(code) { this.code = code; return this; }
  json(value) { this.value = value; return this; }
  setHeader(k, v) { this.headers[k] = v; }
  flushHeaders() { this.headersSent = true; }
  write(value) { this.chunks.push(Buffer.from(value)); return true; }
  end(value) { if (value) this.write(value); }
}
const request = (extra = {}) => ({ method: 'POST', headers: { host: 'sandbox.invalid', origin: 'https://sandbox.invalid' }, body: { prompt: 'TP53' }, ...extra });

test('proxy only accepts POST and the same origin', async () => {
  const res = new Reply();
  await handler(request({ method: 'GET' }), res);
  assert.equal(res.code, 405);
  const denied = new Reply();
  await handler(request({ headers: { host: 'sandbox.invalid', origin: 'https://outside.invalid' } }), denied);
  assert.equal(denied.code, 403);
  const malformed = new Reply();
  await handler(request({ headers: { host: 'sandbox.invalid', origin: 'invalid' } }), malformed);
  assert.equal(malformed.code, 403);
});

test('proxy streams events and keeps the token server-side', async () => {
  process.env.ORION_SERVER_URL = 'http://orion:8080/invocations';
  process.env.ORION_INTERNAL_TOKEN = 'synthetic-test-only';
  const original = globalThis.fetch;
  globalThis.fetch = async (url, options) => {
    assert.equal(url, process.env.ORION_SERVER_URL);
    assert.equal(options.headers['X-Orion-Token'], 'synthetic-test-only');
    assert.equal(options.redirect, 'error');
    return new Response('data: {"event":"status"}\n\ndata: {"event":"result"}\n\n', { status: 200 });
  };
  try {
    const res = new Reply();
    await handler(request({ headers: { host: 'frontend:3000', origin: 'https://sandbox.invalid', 'x-forwarded-host': 'sandbox.invalid' } }), res);
    assert.match(Buffer.concat(res.chunks).toString(), /"event":"result"/);
    assert.equal(res.headers['Content-Type'], 'text/event-stream');
    assert.ok(!JSON.stringify(res.headers).includes('synthetic-test-only'));
  } finally { globalThis.fetch = original; delete process.env.ORION_INTERNAL_TOKEN; }
});

test('upstream validation errors become a clear browser error', async () => {
  process.env.ORION_SERVER_URL = 'http://orion:8080/invocations';
  const original = globalThis.fetch;
  globalThis.fetch = async () => new Response('{}', { status: 422 });
  try {
    const res = new Reply();
    await handler(request(), res);
    assert.equal(res.code, 400);
    assert.match(res.value.error, /cohort filter/);
  } finally { globalThis.fetch = original; }
});
