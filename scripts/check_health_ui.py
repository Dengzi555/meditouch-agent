"""Health-answer SSE, citation disclosure, follow-up and persistence in the isolated QA app."""
from playwright.sync_api import sync_playwright, expect


def main():
    with sync_playwright() as p:
        browser = p.chromium.launch(channel='msedge', headless=True)
        page = browser.new_page(viewport={'width': 1440, 'height': 1000})
        errors = []
        page.on('pageerror', lambda error: errors.append(str(error)))
        page.goto('http://127.0.0.1:8767/')
        page.get_by_label('顾客想找什么药？').fill('健康问答测试')
        page.get_by_role('button', name='发送 ↑').click()
        expect(page.get_by_text('本轮核对完成', exact=True)).to_be_visible(timeout=15000)
        health = page.locator('[data-card-type="health_answer"]')
        expect(health).to_have_count(1)
        expect(health.locator('strong')).to_contain_text('记录已知信息')
        expect(page.locator('[data-card-type="drug_candidate"]')).to_have_count(0)
        expect(page.locator('[data-card-type="missing_information"]')).to_have_count(0)
        expect(health.get_by_text('合成健康问答夹具', exact=False).last).not_to_be_visible()
        health.get_by_text('参考资料 · 1 条', exact=True).click()
        expect(health.locator('details').get_by_role('link', name='合成健康问答夹具', exact=False)).to_be_visible()
        health.get_by_role('button', name='已经记录', exact=True).click()
        expect(page.get_by_role('region', name='一轮对话')).to_have_count(2)
        expect(page.get_by_text('本轮核对完成', exact=True)).to_have_count(2, timeout=15000)
        page.reload()
        expect(page.locator('[data-card-type="health_answer"]')).to_have_count(2)
        page.set_viewport_size({'width': 390, 'height': 844})
        page.get_by_role('button', name='收起侧栏').click()
        assert page.evaluate('document.documentElement.scrollWidth <= innerWidth')
        assert not errors, errors
        browser.close()
    print('Health UI passed: real SSE, source disclosure, highlight, follow-up, restored chat, mobile.')


if __name__ == '__main__':
    main()
