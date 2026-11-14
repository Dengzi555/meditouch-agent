import type { Card, Safety } from './protocol';
import { card, safety } from './protocol';

export type Turn = { id: string; question: string; cards: Card[]; overall: Safety;
  stages: string[]; error: string; completed: boolean; loading: boolean; status?: string };
export type Chat = { id: string; title: string; turns: Turn[]; customer?: {customer_tags?: string[]} };

async function read(response: Response) {
  const value = await response.json();
  if (!response.ok) throw new Error(typeof value.detail === 'string' ? value.detail : '无法读取聊天记录');
  return value;
}

export function chatUrl(id: string | null) {
  const url = new URL(window.location.href);
  if (id) url.searchParams.set('chat', id); else url.searchParams.delete('chat');
  window.history.replaceState(null, '', url);
}

export async function listChats(store: string, signal?: AbortSignal): Promise<Chat[]> {
  const value = await read(await fetch(`/api/chats?store_id=${encodeURIComponent(store)}`, {signal}));
  if (!Array.isArray(value.sessions)) throw new Error('聊天记录格式异常');
  return value.sessions.map((item: Record<string, unknown>) => {
    if (typeof item.id !== 'string' || typeof item.title !== 'string') throw new Error('会话格式异常');
    return {id: item.id, title: item.title, turns: []};
  });
}

export async function createChat(store: string): Promise<Chat> {
  const value = await read(await fetch('/api/chats', {method: 'POST',
    headers: {'Content-Type': 'application/json'}, body: JSON.stringify({store_id: store})}));
  if (typeof value.id !== 'string' || typeof value.title !== 'string') throw new Error('会话创建结果异常');
  return {id: value.id, title: value.title, turns: []};
}

export async function loadChat(id: string, store: string, signal?: AbortSignal): Promise<Chat> {
  const value = await read(await fetch(`/api/chats/${encodeURIComponent(id)}?store_id=${encodeURIComponent(store)}`, {signal}));
  if (value.id !== id || typeof value.title !== 'string' || !Array.isArray(value.turns)) throw new Error('会话格式异常');
  for (const turn of value.turns) {
    if (typeof turn.id !== 'string' || typeof turn.question !== 'string' || !Array.isArray(turn.cards)
        || !turn.cards.every(card) || !safety(turn.overall) || !Array.isArray(turn.stages)
        || !turn.stages.every((stage: unknown) => typeof stage === 'string')
        || typeof turn.error !== 'string' || typeof turn.completed !== 'boolean') throw new Error('消息格式异常');
    turn.loading = false;
  }
  return value;
}
