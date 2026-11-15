"""Duplicate import warning, edit navigation and explicit override on isolated stock."""
from playwright.sync_api import sync_playwright, expect


def main():
    with sync_playwright() as p:
        browser = p.chromium.launch(channel='msedge', headless=True)
        page = browser.new_page(viewport={'width': 1440, 'height': 1000})
        errors = []
        page.on('pageerror', lambda error: errors.append(str(error)))
        page.goto('http://127.0.0.1:8767/')
        page.wait_for_load_state('networkidle')
        page.get_by_role('button', name='拍照入库', exact=True).click()
        page.get_by_label('药名 *', exact=True).fill('演示药乙')
        warning = page.get_by_role('complementary', name='仓库已有商品')
        expect(warning).to_be_visible()
        expect(warning).to_contain_text('3排2号')
        expect(page.get_by_role('button', name='确认入库', exact=True)).to_be_disabled()
        warning.get_by_role('button', name='编辑已有商品', exact=False).click()
        dialog = page.get_by_role('dialog', name='编辑药品资料')
        expect(dialog).to_be_visible()
        expect(dialog.get_by_label('药名', exact=True)).to_have_value('演示药乙')
        dialog.get_by_role('button', name='取消', exact=True).click()
        page.get_by_role('button', name='拍照入库', exact=True).click()
        page.get_by_label('药名 *', exact=True).fill('演示药乙')
        page.get_by_label('货架号 *', exact=True).fill('B3')
        page.get_by_label('库存数量 *', exact=True).fill('2')
        page.get_by_role('combobox').select_option('true')
        expect(warning).to_be_visible()
        page.get_by_label('我已知仓库有该药品，仍要新建一条记录').check()
        page.get_by_role('button', name='确认入库', exact=True).click()
        expect(page.get_by_text('入库成功：', exact=False)).to_be_visible()
        response = page.request.get('http://127.0.0.1:8767/api/pharmacy/import_matches',
                                    params={'store_id': 'qa-store', 'name': '演示药乙'})
        assert len(response.json()['items']) == 2
        assert not errors, errors
        browser.close()
    print('Duplicate import UI passed: warning, disabled submit, direct edit, explicit new record.')


if __name__ == '__main__':
    main()
