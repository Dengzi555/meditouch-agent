"""Browser acceptance against scripts.pharmacy_ui_fixture (only synthetic observations)."""
from pathlib import Path

from playwright.sync_api import expect, sync_playwright


def main():
    with sync_playwright() as p:
        browser = p.chromium.launch(channel='msedge', headless=True)
        context = browser.new_context(viewport={'width': 1440, 'height': 1080}, permissions=['clipboard-read', 'clipboard-write'])
        page = context.new_page()
        errors = []
        page.on('pageerror', lambda error: errors.append(str(error)))
        page.goto('http://127.0.0.1:8767/')
        page.get_by_label('顾客想找什么药？').fill('顾客现在嘴歪、言语不清怎么办')
        page.get_by_role('button', name='发送 ↑').click()
        card = page.locator('[data-card-type="emergency_support"]')
        expect(card).to_be_visible(timeout=10000)
        expect(card.get_by_role('link', name='拨打120', exact=True)).to_have_attribute('href', 'tel:120')
        expect(page.locator('[data-card-type="drug_candidate"]')).to_have_count(0)
        expect(card.get_by_text('呼救：未确认是否已呼救', exact=False)).to_be_visible()
        expect(page.get_by_text('本轮核对完成', exact=True)).to_be_visible()
        card.get_by_label('是否已呼救').select_option('called')
        card.get_by_label('意识反应').select_option('unresponsive')
        card.get_by_label('呼吸', exact=True).select_option('normal')
        card.get_by_label('最早出现症状时间').fill('约10分钟前（合成测试）')
        expect(card.get_by_role('button', name='复制交接记录')).to_be_disabled()
        card.get_by_role('button', name='更新记录', exact=True).click()
        expect(card.get_by_text('呼救：店员确认已拨打120', exact=False)).to_be_visible()
        expect(card.get_by_role('button', name='更新记录', exact=True)).to_be_disabled()
        expect(card.locator('li').filter(has_text='目前记录符合该条件')).to_contain_text('侧卧恢复体位')
        card.get_by_label('呼吸', exact=True).select_option('absent_or_gasping')
        card.get_by_role('button', name='更新记录', exact=True).click()
        expect(card.locator('li').filter(has_text='目前记录符合该条件')).to_contain_text('心肺复苏')
        page.reload()
        card = page.locator('[data-card-type="emergency_support"]')
        expect(card.get_by_label('呼吸', exact=True)).to_have_value('absent_or_gasping')
        expect(card.get_by_label('最早出现症状时间')).to_have_value('约10分钟前（合成测试）')
        expect(page.get_by_role('region', name='一轮对话')).to_have_count(1)
        card.get_by_role('button', name='复制交接记录').click()
        expect(card.get_by_role('button', name='已复制', exact=True)).to_be_visible()
        copied = page.evaluate('navigator.clipboard.readText()')
        assert '店员确认已拨打120' in copied and '约10分钟前' in copied
        assert '已实际采取的措施：未确认' in copied
        assert not errors, errors
        Path('tmp').mkdir(exist_ok=True)
        page.screenshot(path='tmp/emergency-support-ui.png', full_page=True)
        browser.close()
    print('Emergency UI passed: immediate actions, unknown defaults, conditional guidance, save/reload, copy, no inventory cards.')


if __name__ == '__main__':
    main()
