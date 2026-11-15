"""Delete synthetic stock only, through the real confirmation dialog on the isolated fixture."""
from playwright.sync_api import expect, sync_playwright


def main():
    with sync_playwright() as p:
        browser = p.chromium.launch(channel='msedge', headless=True)
        page = browser.new_page(viewport={'width': 1440, 'height': 1000})
        errors = []
        page.on('pageerror', lambda error: errors.append(str(error)))
        page.goto('http://127.0.0.1:8767/')
        page.get_by_role('button', name='库存管理', exact=True).click()
        row = page.get_by_role('row').filter(has_text='演示药甲')
        expect(row).to_be_visible()
        row.get_by_role('button', name='删除', exact=True).click()
        dialog = page.get_by_role('dialog', name='确认删除此药品？')
        expect(dialog).to_be_visible()
        expect(dialog).to_contain_text('演示药甲')
        expect(dialog).to_contain_text('当前库存 2')
        expect(dialog).to_contain_text('历史出入库记录保留')
        dialog.get_by_role('button', name='取消', exact=True).click()
        expect(dialog).to_have_count(0)
        expect(row).to_be_visible()
        row.get_by_role('button', name='删除', exact=True).click()
        dialog.get_by_role('button', name='确认删除', exact=True).click()
        expect(dialog).to_have_count(0)
        expect(row).to_have_count(0)
        expect(page.get_by_role('status')).to_contain_text('已删除 演示药甲')
        page.reload()
        page.get_by_role('button', name='库存管理', exact=True).click()
        expect(page.get_by_role('row').filter(has_text='演示药甲')).to_have_count(0)
        expect(page.get_by_role('row').filter(has_text='演示药乙')).to_be_visible()
        page.get_by_role('button', name='新咨询').click()
        page.get_by_label('顾客想找什么药？').fill('演示药甲')
        page.get_by_role('button', name='发送 ↑').click()
        expect(page.get_by_text('本轮核对完成', exact=True)).to_be_visible(timeout=15000)
        expect(page.locator('[data-card-type="drug_candidate"]')).to_have_count(0)
        assert not errors, errors
        browser.close()
    print('PASS: confirmation details, cancel, delete, reload persistence and Agent lookup exclusion; synthetic inventory only.')


if __name__ == '__main__':
    main()
