import { describe, expect, it } from 'vitest';
import { renderToStaticMarkup } from 'react-dom/server';
import CopilotCards from './CopilotCards';
import type { Card } from './protocol';

describe('removed review panels', () => {
  it('shows actual progress below processing without an unrelated inventory placeholder', () => {
    const html = renderToStaticMarkup(<CopilotCards cards={[]} overall="unknown" loading
      stages={['正在识别问题与顾客信息', '已识别：健康问答']} />);
    expect(html).toContain('实时处理进度');
    expect(html).toContain('正在生成回答');
    expect(html).not.toContain('正在查询本店库存并核对禁忌');
    const completed = renderToStaticMarkup(<CopilotCards cards={[]} overall="unknown" loading={false}
      stages={['已识别：健康问答']} />);
    expect(completed).not.toContain('实时处理进度');
  });
  it('hides retired review cards restored from older conversations', () => {
    const cards: Card[] = [{ id: 'missing', type: 'missing_information', title: '需要人工核对',
      message: '请人工核对病史', reason: '缺少禁忌信息' },
      { id: 'old-script', type: 'staff_script', title: '店员话术', text: '我先帮您核对病史，再请药师确认。' }];
    const html = renderToStaticMarkup(<CopilotCards cards={cards} overall="unknown" loading={false} />);
    expect(html).not.toContain('人工核对');
    expect(html).not.toContain('missing_information');
  });

  it('keeps inventory visible without turning unknown into a safe recommendation', () => {
    const cards: Card[] = [{ id: 'drug:1', type: 'drug_candidate', title: '本店商品',
      drug: { inventory_id: 1, name: '演示商品', shelf_location: 'A1', stock: 3, is_rx: true },
      safety_status: 'unknown', availability: 'in_stock', sale_authorized: false }];
    const html = renderToStaticMarkup(<CopilotCards cards={cards} overall="unknown" loading={false} />);
    expect(html).toContain('演示商品');
    expect(html).toContain('本店有货');
    expect(html).toContain('处方药需药师审核');
    expect(html).not.toContain('待核对');
    expect(html).not.toContain('核查通过候选');
    expect(renderToStaticMarkup(<CopilotCards cards={cards} overall="unsafe" loading={false} />))
      .not.toContain('演示商品');
  });
});
