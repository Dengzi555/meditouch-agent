import { describe, expect, it } from 'vitest';
import { cardText, revealCard } from './streamedCard';
import type { HealthCard } from './protocol';

const item: HealthCard = { id: 'health-answer', type: 'health_answer', title: '健康问答',
  basis: 'public', disclaimer: '', sections: [{ title: '处理', items: [
    { text: '已校验的内容😊', highlights: ['内容'], source_ids: ['s'] },
  ] }], sources: [{ id: 's', title: '资料', publisher: 'fixture', url: '', quote: '原文' }],
  follow_up: { question: '补充？', options: ['甲', '乙'] } };

describe('smooth rendering of verified streamed cards', () => {
  it('reveals received text incrementally without splitting unicode characters', () => {
    expect(cardText(item)).toBe('处理已校验的内容😊');
    const partial = revealCard(item, 5) as HealthCard;
    expect(partial.sections[0].items[0].text).toBe('已校验');
    expect(partial.sections[0].items[0].source_ids).toEqual([]);
    expect(partial.follow_up).toBeNull();
    expect(partial.sources).toEqual([]);
    const complete = revealCard(item, 100) as HealthCard;
    expect(complete).toEqual(item);
  });
  it('does not animate inventory facts or change their safety status', () => {
    const card = { id: 'n', type: 'intercept_warning' as const, title: '拦截', items: [{ name: '药品', reason: '禁忌' }] };
    expect(revealCard(card, 0)).toBe(card);
  });
});
