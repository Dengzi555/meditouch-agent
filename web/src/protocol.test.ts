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
});
