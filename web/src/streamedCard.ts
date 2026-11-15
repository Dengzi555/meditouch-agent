import type { Card } from './protocol';

export function cardText(item: Card): string {
  if (item.type === 'health_answer') return item.sections.map(s => s.title + s.items.map(p => p.text).join('')).join('');
  if (item.type === 'notice') return item.message;
  if (item.type === 'staff_script') return item.text;
  return '';
}

export function revealCard(item: Card, count: number): Card {
  let remaining = count;
  const take = (text: string) => {
    const chars = Array.from(text);
    const result = chars.slice(0, Math.max(0, remaining)).join('');
    remaining -= chars.length;
    return result;
  };
  if (item.type === 'notice') return { ...item, message: take(item.message) };
  if (item.type === 'staff_script') return { ...item, text: take(item.text) };
  if (item.type === 'health_answer') return { ...item,
    sections: item.sections.flatMap(section => {
      if (remaining <= 0) return [];
      const title = take(section.title);
      const items = section.items.flatMap(point => {
        if (remaining <= 0) return [];
        const text = take(point.text);
        return [{ ...point, text, source_ids: text === point.text ? point.source_ids : [] }];
      });
      return [{ title, items }];
    }), follow_up: count >= Array.from(cardText(item)).length ? item.follow_up : null,
    sources: count >= Array.from(cardText(item)).length ? item.sources : [],
  };
  return item;
}
