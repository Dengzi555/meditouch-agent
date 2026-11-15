"""Verify live text grows over frames and completed history is immediately readable."""
from playwright.sync_api import sync_playwright, expect
from meditouch.pharmacy_ui import ui_event, encode_sse


def main():
    with sync_playwright() as p:
        browser = p.chromium.launch(channel='msedge', headless=True)
        page = browser.new_page(viewport={'width': 1440, 'height': 1000})
        page.goto('http://127.0.0.1:8767/')
        page.get_by_label('顾客想找什么药？').fill('健康问答测试')
        page.get_by_role('button', name='发送 ↑').click()
        full = '合成测试：先记录已知信息，再继续核对。'
        page.wait_for_function('''full => {
            const point = document.querySelector('[data-card-type="health_answer"] li');
            return point && point.textContent.trim().length > 0
                && point.textContent.trim().length < full.length;
        }''', arg=full, timeout=15000)
        expect(page.get_by_label('正在输出', exact=True)).to_have_count(1)
        expect(page.get_by_text('本轮核对完成', exact=True)).to_be_visible(timeout=15000)
        expect(page.get_by_label('正在输出', exact=True)).to_have_count(0, timeout=15000)
        expect(page.locator('[data-card-type="health_answer"] li').first).to_contain_text(full)
        page.reload()
        expect(page.locator('[data-card-type="health_answer"] li').first).to_contain_text(full)
        expect(page.get_by_label('正在输出', exact=True)).to_have_count(0)
        # Exercise the actual race: cards and done arrive in ONE buffered HTTP response.
        page.get_by_label('新咨询', exact=True).click()
        paragraph = '这段合成文本仅验证连续展示，不提供医学建议。' * 8
        card = {'id': 'health-answer', 'type': 'health_answer', 'title': '健康问答',
                'basis': 'model_knowledge', 'disclaimer': '', 'sections': [
                    {'title': '合成展示测试', 'items': [{'text': paragraph, 'highlights': [], 'source_ids': []}]}],
                'sources': [], 'follow_up': None}
        body = b''.join(encode_sse(event) for event in [
            ui_event('buffered-qa', 1, 'progress', {'label': '合成生成完成'}),
            ui_event('buffered-qa', 2, 'cards.snapshot', {'safety_status': 'unknown', 'cards': [card]}),
            ui_event('buffered-qa', 3, 'done', {'status': 'completed', 'sale_authorized': False})])
        page.route('**/api/agent/stream', lambda route: route.fulfill(
            status=200, content_type='text/event-stream', body=body))
        page.get_by_label('顾客想找什么药？').fill('整包返回测试')
        page.get_by_role('button', name='发送 ↑').click()
        page.wait_for_function('''() => {
            const point = document.querySelector('[data-card-type="health_answer"] li');
            return point && point.textContent.trim().length > 5 && point.textContent.trim().length < 30;
        }''', timeout=15000)
        expect(page.get_by_text('本轮核对完成', exact=True)).not_to_be_visible()
        expect(page.get_by_label('正在输出', exact=True)).to_have_count(1)
        expect(page.get_by_label('正在输出', exact=True)).to_have_count(0, timeout=15000)
        expect(page.get_by_text('本轮核对完成', exact=True)).to_be_visible()
        expect(page.locator('[data-card-type="health_answer"] li')).to_contain_text(paragraph)
        browser.close()
    print('Smooth answer passed: partial characters, live cursor, final content and history restore.')


if __name__ == '__main__':
    main()
