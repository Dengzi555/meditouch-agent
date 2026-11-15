"""Partial answer must appear before the final section on the isolated SSE fixture."""
from playwright.sync_api import sync_playwright, expect


def main():
    with sync_playwright() as p:
        browser = p.chromium.launch(channel='msedge', headless=True)
        page = browser.new_page(viewport={'width': 1440, 'height': 1000})
        page.goto('http://127.0.0.1:8767/')
        page.get_by_label('顾客想找什么药？').fill('健康问答测试')
        page.get_by_role('button', name='发送 ↑').click()
        answer = page.locator('[data-card-type="health_answer"]')
        expect(answer.get_by_role('heading', name='先确认已知信息', exact=True)).to_be_visible(timeout=15000)
        expect(answer.get_by_role('heading', name='继续了解', exact=True)).to_have_count(0)
        expect(page.get_by_text('本轮核对完成', exact=True)).to_have_count(0)
        expect(answer.get_by_role('heading', name='继续了解', exact=True)).to_be_visible(timeout=15000)
        expect(page.get_by_text('本轮核对完成', exact=True)).to_be_visible(timeout=15000)
        page.reload()
        expect(answer.get_by_role('heading', name='继续了解', exact=True)).to_be_visible()
        browser.close()
    print('Real SSE partial answer passed: first section visible before generation completes; final saved.')


if __name__ == '__main__':
    main()
