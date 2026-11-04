import type { AgentEvent } from './types';

/** Handles arbitrary UTF-8 and SSE frame boundaries, including the final decoder flush. */
export async function readEvents(body: ReadableStream<Uint8Array>, onEvent: (event: AgentEvent) => void) {
  const reader = body.getReader();
  const decoder = new TextDecoder();
  let pending = '';
  try {
    while (true) {
      const { value, done } = await reader.read();
      pending += done ? decoder.decode() : decoder.decode(value, { stream: true });
      pending = pending.replace(/\r\n/g, '\n');
      let end: number;
      while ((end = pending.indexOf('\n\n')) >= 0) {
        const frame = pending.slice(0, end);
        pending = pending.slice(end + 2);
        const data = frame.split('\n').filter(line => line.startsWith('data:')).map(line => line.slice(5).trimStart()).join('\n');
        if (data) onEvent(JSON.parse(data) as AgentEvent);
      }
      if (done) break;
      if (pending.length > 2_000_000) throw new Error('Agent response exceeded the demo limit');
    }
    if (pending.trim()) throw new Error('Agent response ended before the final event');
  } catch (error) {
    await reader.cancel().catch(() => undefined);
    throw error;
  } finally {
    reader.releaseLock();
  }
}
