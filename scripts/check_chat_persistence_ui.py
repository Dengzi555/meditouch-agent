"""Browser regression against the isolated fixture on 8767. Never uses the store DB or paid models."""
from urllib.parse import parse_qs, urlsplit

from playwright.sync_api import sync_playwright, expect


def main():
    with sync_playwright() as playwright:
        browser = playwright.chromium.launch(channel='msedge', headless=True)
        page = browser.new_page(viewport={'width': 1440, 'height': 960})
        errors = []
        page.on('pageerror', lambda error: errors.append(str(error)))
        try:
            page.goto('http://127.0.0.1:8767/')
            page.wait_for_load_state('networkidle')
            composer = page.get_by_label('顾客想找什么药？')
            composer.fill('演示药')
            page.get_by_role('button', name='发送 ↑', exact=True).click()
            expect(page.get_by_text('本轮核对完成').last).to_be_visible(timeout=15000)
            session_id = parse_qs(urlsplit(page.url).query)['chat'][0]
            expect(page.locator('[data-card-type="drug_candidate"]')).to_have_count(2)

            page.reload()
            page.wait_for_load_state('networkidle')
            expect(page.get_by_role('region', name='一轮对话')).to_have_count(1)
            expect(page.locator('[data-card-type="drug_candidate"]')).to_have_count(2)
            expect(page.get_by_text('本轮核对完成')).to_be_visible()
            expect(page.get_by_role('navigation', name='聊天记录').get_by_role('button')).to_have_count(1)
            composer.fill('演示药')
            page.get_by_role('button', name='发送 ↑', exact=True).click()
            expect(page.get_by_role('region', name='一轮对话').last.get_by_text('本轮核对完成')).to_be_visible(timeout=15000)
            expect(page.get_by_role('region', name='一轮对话')).to_have_count(2)
            assert parse_qs(urlsplit(page.url).query)['chat'][0] == session_id

            page.get_by_role('button', name='新咨询', exact=True).click()
            composer.fill('不存在商品')
            page.get_by_role('button', name='发送 ↑', exact=True).click()
            expect(page.get_by_text('本轮核对完成')).to_be_visible(timeout=15000)
            expect(page.get_by_role('navigation', name='聊天记录').get_by_role('button')).to_have_count(2)
            page.get_by_role('navigation', name='聊天记录').get_by_role('button', name='演示药', exact=True).click()
            expect(page.get_by_role('region', name='一轮对话')).to_have_count(2)
            assert parse_qs(urlsplit(page.url).query)['chat'][0] == session_id

            # A fresh tab has no React state but still loads the server's chat list.
            fresh = browser.new_page()
            fresh.goto('http://127.0.0.1:8767/')
            fresh.wait_for_load_state('networkidle')
            expect(fresh.get_by_role('navigation', name='聊天记录').get_by_role('button')).to_have_count(2)
            fresh.close()

            composer.fill('演示药')
            page.get_by_role('button', name='发送 ↑', exact=True).click()
            expect(page.get_by_text('正在处理', exact=True)).to_be_visible(timeout=5000)
            page.get_by_role('button', name='停止 ■', exact=True).click()
            page.reload()
            page.wait_for_load_state('networkidle')
            expect(page.get_by_role('region', name='一轮对话')).to_have_count(3)
            last = page.get_by_role('region', name='一轮对话').last
            expect(last.locator('[data-card-type="drug_candidate"]')).to_have_count(0)
            expect(last.get_by_text('本轮核对完成')).to_have_count(0)
            assert not errors, errors
            print('Chat persistence checks passed: refresh, continued session, switching, fresh tab and incomplete turns.')
        finally:
            browser.close()


if __name__ == '__main__':
    main()
