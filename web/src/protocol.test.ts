import { describe, expect, it } from 'vitest';
import { card, parseFrame } from './protocol';

describe('SSE card protocol', () => {
  it('accepts comments and validates named JSON events', () => {
    expect(parseFrame(': keep-alive')).toBeNull();
    const value = { schema_version: 'pharmacy.ui.v1', run_id: 'a', seq: 1, event: 'cards.snapshot',
      payload: { safety_status: 'unknown', cards: [] } };
    expect(parseFrame(`event: cards.snapshot\ndata: ${JSON.stringify(value)}`)).toEqual(value);
    expect(() => parseFrame(`event: done\ndata: ${JSON.stringify(value)}`)).toThrow();
  });
  it('rejects malformed and unsupported envelopes', () => {
    expect(() => parseFrame('data: {broken')).toThrow();
    expect(() => parseFrame('data: {"schema_version":"other"}')).toThrow();
  });
  it('rejects a fake sale authorization and malformed inventory fields', () => {
    const value = { id: 'drug:1', title: '候选', type: 'drug_candidate', safety_status: 'unknown',
      availability: 'in_stock', sale_authorized: false,
      drug: { inventory_id: 1, name: '演示药', shelf_location: 'B2', stock: 2, is_rx: true } };
    expect(card(value)).toBe(true);
    expect(card({ ...value, sale_authorized: true })).toBe(false);
    expect(card({ ...value, drug: { ...value.drug, stock: -1 } })).toBe(false);
  });
  it('validates health citations and rejects invented sources or unsafe links', () => {
    const source = { id: 'one', title: '资料', publisher: '测试', url: 'https://medlineplus.gov/fever.html', quote: '原文' };
    const point = { text: '测量体温', highlights: ['体温'], source_ids: ['one'] };
    const health = { id: 'health-answer', type: 'health_answer', title: '健康问答', basis: 'public', disclaimer: '',
      sources: [source], sections: [{ title: '先做什么', items: [point] }], follow_up: null };
    expect(card(health)).toBe(true);
    expect(card({ ...health, sources: [{ ...source, url: 'javascript:alert(1)' }] })).toBe(false);
    expect(card({ ...health, sections: [{ title: '错误', items: [{ ...point, source_ids: ['fake'] }] }] })).toBe(false);
    expect(card({ ...health, sections: [{ title: '错误', items: [{ ...point, highlights: ['原文没有的词'] }] }] })).toBe(false);
  });
});
