import { test } from 'node:test';
import assert from 'node:assert/strict';
import { readEvents } from '../overlay/src/features/orion/stream.ts';

test('SSE parser survives one-byte chunks and split UTF-8 codepoints', async () => {
  const bytes = new TextEncoder().encode('data: {"event":"status","mode":"bedrock","graph_backend":"neptune"}\n\ndata: {"event":"result","answer":"TP53 → myeloma"}\n\n');
  const stream = new ReadableStream({ start(c) { for (const b of bytes) c.enqueue(Uint8Array.of(b)); c.close(); } });
  const events = [];
  await readEvents(stream, e => events.push(e));
  assert.equal(events.length, 2);
  assert.equal(events[1].answer, 'TP53 → myeloma');
});

test('truncated final response is an error', async () => {
  const stream = new ReadableStream({ start(c) { c.enqueue(new TextEncoder().encode('data: {"event":"result"}')); c.close(); } });
  await assert.rejects(readEvents(stream, () => {}), /before the final event/);
});

test('malformed event is an error', async () => {
  const stream = new ReadableStream({ start(c) { c.enqueue(new TextEncoder().encode('data: broken\n\n')); c.close(); } });
  await assert.rejects(readEvents(stream, () => {}), SyntaxError);
});
