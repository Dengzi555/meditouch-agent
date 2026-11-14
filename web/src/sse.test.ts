import { describe, expect, it } from 'vitest';
import { readSse } from './sse';

const event = { schema_version: 'pharmacy.ui.v1', run_id: 'a', seq: 1, event: 'progress', payload: { label: '核对孕妇用药' } };
const frame = `: heartbeat\r\n\r\nevent: progress\r\ndata: ${JSON.stringify(event)}\r\n\r\n`;
function body(chunks: Uint8Array[]) {
  return new ReadableStream<Uint8Array>({ start(controller) {
    chunks.forEach(chunk => controller.enqueue(chunk)); controller.close();
  } });
}

describe('incremental SSE reader', () => {
  it('handles every UTF-8 byte and CRLF boundary as a separate network chunk', async () => {
    const bytes = new TextEncoder().encode(frame);
    const values = [];
    for await (const value of readSse(body(Array.from(bytes, byte => Uint8Array.of(byte))))) values.push(value);
    expect(values).toEqual([event]);
  });
  it('handles multiple events in one network chunk', async () => {
    const values = [];
    for await (const value of readSse(body([new TextEncoder().encode(frame + frame)]))) values.push(value);
    expect(values).toHaveLength(2);
  });
  it('rejects a truncated event rather than silently succeeding', async () => {
    await expect(async () => {
      for await (const _ of readSse(body([new TextEncoder().encode('data: {"unfinished":')]))) { /* consume */ }
    }).rejects.toThrow('SSE 事件不完整');
  });
});
