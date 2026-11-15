"""Reload an interrupted chat and continue the same turn from durable graph state."""
from playwright.sync_api import sync_playwright, expect


def main():
    with sync_playwright() as p:
        browser = p.chromium.launch(channel='msedge', headless=True)
        page = browser.new_page(viewport={'width': 1440, 'height': 1000})
        errors = []
        page.on('pageerror', lambda error: errors.append(str(error)))
        page.goto('http://127.0.0.1:8767/')
        page.get_by_label('顾客想找什么药？').fill('演示药乙')
        page.get_by_role('button', name='发送 ↑').click()
        expect(page.get_by_role('button', name='继续处理', exact=True)).to_be_visible(timeout=15000)
        expect(page.locator('[data-card-type="drug_candidate"]')).to_have_count(0)
        page.reload()
        expect(page.get_by_role('button', name='继续处理', exact=True)).to_be_visible()
        page.get_by_role('button', name='继续处理', exact=True).click()
        expect(page.get_by_text('本轮核对完成', exact=True)).to_be_visible(timeout=15000)
        expect(page.get_by_role('region', name='一轮对话')).to_have_count(1)
        expect(page.locator('[data-card-type="drug_candidate"]')).to_have_count(1)
        expect(page.get_by_role('button', name='继续处理', exact=True)).to_have_count(0)
        page.reload()
        expect(page.locator('[data-card-type="drug_candidate"]')).to_have_count(1)
        expect(page.get_by_role('region', name='一轮对话')).to_have_count(1)
        assert not errors, errors
        browser.close()
    print('Recovery UI passed: node failure, reload, resume same turn, fresh inventory, final restore.')


if __name__ == '__main__': main()
