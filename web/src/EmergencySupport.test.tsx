import { describe, expect, it } from 'vitest';
import { renderToStaticMarkup } from 'react-dom/server';
import CopilotCards from './CopilotCards';
import { card, type EmergencyCard } from './protocol';
import { cardText, revealCard } from './streamedCard';

const emergency: EmergencyCard = {
  id: 'emergency', type: 'emergency_support', title: '应急处置与交接', severity: 'emergency',
  original_query: '合成急症测试', symptoms: [], revision: 0, updated_at: '2026-11-15T00:00:00Z', guidance_version: '2026-11-15',
  facts: { call_status: 'unknown', consciousness: 'unknown', breathing: 'unknown', onset: null, last_known_well: null,
    age: null, history: null, medications: null, actions_taken: null, location: null },
  actions: ['立即拨打120', '若无反应且没有正常呼吸，按调度员指导开始心肺复苏'],
  handover: '合成记录：意识未确认，呼吸未确认', sources: [{ title: '来源', url: 'https://www.anzcor.org/' }],
};

describe('emergency support', () => {
  it('shows all urgent instructions immediately even while other cards animate', () => {
    const html = renderToStaticMarkup(<CopilotCards cards={[emergency]} overall="unsafe" loading animate />);
    expect(html).toContain('立即拨打120');
    expect(html).toContain('心肺复苏');
    expect(html).toContain('交接记录');
    expect(html).not.toContain('人工核对');
    expect(html).not.toContain('正在输出');
    expect(cardText(emergency)).toBe('');
    expect(revealCard(emergency, 0)).toBe(emergency);
  });
  it('validates enums, source links and revisions at the protocol boundary', () => {
    expect(card(emergency)).toBe(true);
    expect(card({ ...emergency, facts: { ...emergency.facts, breathing: 'safe' } })).toBe(false);
    expect(card({ ...emergency, sources: [{ title: 'bad', url: 'javascript:alert(1)' }] })).toBe(false);
    expect(card({ ...emergency, revision: -1 })).toBe(false);
    expect(card({ ...emergency, facts: null })).toBe(false);
  });
});
