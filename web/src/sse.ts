import { parseFrame } from './protocol';

export async function* readSse(body: ReadableStream<Uint8Array>) {
  const reader = body.getReader(), decoder = new TextDecoder();
  let pending = '';
  try {
    while (true) {
      const chunk = await reader.read();
      if (chunk.done) break;
      pending = (pending + decoder.decode(chunk.value, { stream: true })).replace(/\r\n/g, '\n');
      if (pending.length > 2_000_000) throw new Error('事件过大，已停止展示');
      let index: number;
      while ((index = pending.indexOf('\n\n')) >= 0) {
        const message = parseFrame(pending.slice(0, index)); pending = pending.slice(index + 2);
        if (message) yield message;
      }
    }
    pending += decoder.decode();
    if (pending.trim() && !pending.trim().startsWith(':')) throw new Error('SSE 事件不完整');
  } finally { await reader.cancel().catch(() => {}); reader.releaseLock(); }
}
