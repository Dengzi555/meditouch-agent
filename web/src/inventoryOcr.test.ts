import { describe, expect, it } from 'vitest';
import { parseOcrResponse } from './inventoryOcr';

const drug = {name: '演示药', brand: null, is_rx: null, ingredients: null, contraindications: null};
const flags = {extraction_only: true, needs_confirmation: true};

describe('multi-drug OCR protocol', () => {
  it('keeps different products and unknown prescription flags separate', () => {
    const result = parseOcrResponse({...flags, items: [
      {...drug, name: '甲', specification: '规格甲'}, {...drug, name: '乙', brand: '品牌乙', is_rx: true}], truncated: true});
    expect(result.items).toHaveLength(2);
    expect(result.items[0].specification).toBe('规格甲');
    expect(result.items[1].specification).toBeNull();
    expect(result.items[0].is_rx).toBeNull();
    expect(result.items[1].is_rx).toBe(true);
    expect(result.truncated).toBe(true);
  });
  it('accepts a legacy single item and an empty recognition result', () => {
    expect(parseOcrResponse({...flags, ...drug}).items).toHaveLength(1);
    expect(parseOcrResponse({...flags, items: []}).items).toHaveLength(0);
  });
  it('rejects invalid items and missing confirmation flags instead of taking only the first item', () => {
    expect(() => parseOcrResponse({...flags, items: [drug, {...drug, is_rx: 'false'}]})).toThrow();
    expect(() => parseOcrResponse({items: [drug]})).toThrow();
    expect(() => parseOcrResponse({...flags, items: Array(13).fill(drug)})).toThrow();
  });
});
