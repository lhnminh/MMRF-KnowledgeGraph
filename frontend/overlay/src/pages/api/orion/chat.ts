import type { NextApiRequest, NextApiResponse } from 'next';

export const config = { api: { bodyParser: { sizeLimit: '128kb' }, responseLimit: false } };

export default async function handler(req: NextApiRequest, res: NextApiResponse) {
  if (req.method !== 'POST') { res.setHeader('Allow', 'POST'); return res.status(405).json({ error: 'Use POST' }); }
  try {
    // The inspected sandbox gateway rewrites Host to frontend:3000 and sets the browser host here.
    const forwarded = req.headers['x-forwarded-host'];
    const expectedHost = (Array.isArray(forwarded) ? forwarded[0] : forwarded)?.split(',')[0].trim() ?? req.headers.host;
    if (req.headers.origin && new URL(req.headers.origin).host !== expectedHost) return res.status(403).json({ error: 'Origin is not allowed' });
  } catch { return res.status(403).json({ error: 'Origin is not allowed' }); }
  const target = process.env.ORION_SERVER_URL;
  if (!target) return res.status(503).json({ error: 'ORION agent service is not configured' });
  const controller = new AbortController();
  const timer = setTimeout(() => controller.abort(), 240_000);
  res.on('close', () => controller.abort());
  try {
    // Fixed server-configured URL. Browser and model cannot choose destinations or credentials.
    const upstream = await fetch(target, { method: 'POST', redirect: 'error', headers: { 'Content-Type': 'application/json', 'X-Orion-Token': process.env.ORION_INTERNAL_TOKEN ?? '' }, body: JSON.stringify(req.body), signal: controller.signal });
    if (!upstream.ok || !upstream.body) return res.status(upstream.status === 422 ? 400 : 502).json({ error: upstream.status === 422 ? 'Unsupported prompt, history, or cohort filter. Use gender filters or explicit synthetic case selections.' : 'ORION agent service is unavailable' });
    res.setHeader('Content-Type', 'text/event-stream');
    res.setHeader('Cache-Control', 'no-cache, no-transform');
    res.setHeader('X-Accel-Buffering', 'no');
    res.flushHeaders();
    const reader = upstream.body.getReader();
    try {
      while (!res.destroyed) {
        const { value, done } = await reader.read();
        if (done) break;
        if (!res.write(value)) await new Promise<void>(resolve => { res.once('drain', resolve); res.once('close', resolve); });
      }
    } finally { await reader.cancel().catch(() => undefined); }
    res.end();
  } catch {
    if (!res.headersSent) res.status(502).json({ error: 'The agent connection failed or timed out. Please retry.' });
    else if (!res.destroyed) res.end(`data: ${JSON.stringify({ event: 'error', error: { code: 'connection_failed', message: 'The agent connection ended unexpectedly. Please retry.', retryable: true } })}\n\n`);
  } finally { clearTimeout(timer); }
}
