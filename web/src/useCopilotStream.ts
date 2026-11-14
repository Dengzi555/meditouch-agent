import { useCallback, useEffect, useRef, useState } from 'react';
import { card, safety } from './protocol';
import type { Card, Safety } from './protocol';
import { readSse } from './sse';

export type CopilotPayload = {
  question: string;
  session_id?: string;
  turn_id?: string;
  history?: {role: 'user' | 'assistant'; content: string}[];
  workspace: { store_id: string; task: 'reception' | 'drug_lookup' | 'safety_check';
    customer: { customer_tags: string[] } };
};

export function useCopilotStream(endpoint = '/api/agent/stream') {
  const [cards, setCards] = useState<Card[]>([]);
  const [overall, setOverall] = useState<Safety>('unknown');
  const [stages, setStages] = useState<string[]>([]);
  const [loading, setLoading] = useState(false);
  const [completed, setCompleted] = useState(false);
  const [error, setError] = useState('');
  const [dataMode, setDataMode] = useState<'mock' | 'real' | 'unknown'>('unknown');
  const controller = useRef<AbortController | null>(null);
  const epoch = useRef(0);
  useEffect(() => () => { epoch.current++; controller.current?.abort(); }, []);

  const cancel = useCallback(() => {
    epoch.current++; controller.current?.abort();
    setCards([]); setOverall('unknown'); setLoading(false); setCompleted(false);
    setError('已停止，未完成的结果不可作为拿药依据');
  }, []);

  const start = useCallback(async (payload: CopilotPayload) => {
    controller.current?.abort();
    const current = ++epoch.current;
    const abort = new AbortController(); controller.current = abort;
    // Reset synchronously before fetch: no previous recommendations during a new query.
    setCards([]); setStages([]); setOverall('unknown'); setError(''); setLoading(true); setCompleted(false);
    let lastSeq = 0, runId = '', done = false;
    try {
      const response = await fetch(endpoint, { method: 'POST', signal: abort.signal,
        headers: { 'Content-Type': 'application/json', Accept: 'text/event-stream' },
        body: JSON.stringify(payload) });
      if (!response.ok) {
        const body: unknown = await response.json().catch(() => null);
        const detail = typeof body === 'object' && body !== null && 'detail' in body ? body.detail : null;
        throw new Error(typeof detail === 'string' ? detail : `请求未成功（${response.status}），请核对输入或配置`);
      }
      if (!response.body || !response.headers.get('content-type')?.includes('text/event-stream')) {
        throw new Error('服务未返回 SSE，已停止解析');
      }
      const mode = response.headers.get('x-inventory-data-mode');
      setDataMode(mode === 'mock' || mode === 'real' ? mode : 'unknown');
      for await (const message of readSse(response.body)) {
            if (current !== epoch.current) return;
            if (runId && runId !== message.run_id) throw new Error('混入其他任务事件');
            runId = message.run_id;
            if (message.seq <= lastSeq) continue;
            lastSeq = message.seq;
            if (message.event === 'progress' && typeof message.payload.label === 'string') {
              const label = message.payload.label;
              const attempt = message.payload.attempt;
              setStages(previous => [...previous, `${label}${typeof attempt === 'number' && attempt > 0 ? ` · 第${attempt}轮` : ''}`]);
            } else if (message.event === 'cards.snapshot') {
              const snapshot = message.payload;
              if (!safety(snapshot.safety_status) || !Array.isArray(snapshot.cards) || !snapshot.cards.every(card)) {
                throw new Error('卡片格式异常，已停止展示');
              }
              if (new Set(snapshot.cards.map(item => item.id)).size !== snapshot.cards.length) throw new Error('卡片编号重复');
              setOverall(snapshot.safety_status); setCards(snapshot.cards);
            } else if (message.event === 'error') {
              throw new Error(typeof message.payload.message === 'string' ? message.payload.message : '处理失败');
            } else if (message.event === 'done') {
              if (message.payload.status !== 'completed' || message.payload.sale_authorized !== false) {
                throw new Error('任务状态异常，请交药师确认');
              }
              done = true; setCompleted(true); break;
            }
      }
      if (!done) throw new Error('连接中断，结果未完成，请重新核对');
    } catch (cause) {
      if (current !== epoch.current) return;
      setCards([]); setOverall('unknown'); setCompleted(false);
      setError(abort.signal.aborted ? '已停止，结果不可作为拿药依据' : cause instanceof Error ? cause.message : '连接失败');
    } finally { if (current === epoch.current) setLoading(false); }
  }, [endpoint]);

  return { cards, overall, stages, loading, completed, error, dataMode, start, cancel };
}
