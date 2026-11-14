"""Stock-management browser acceptance against the isolated fixture, never live stock."""
from pathlib import Path
from playwright.sync_api import sync_playwright, expect


def main():
    with sync_playwright() as p:
        browser = p.chromium.launch(channel='msedge', headless=True)
        page = browser.new_page(viewport={'width': 1600, 'height': 1050})
        errors = []
        page.on('pageerror', lambda error: errors.append(str(error)))
        page.goto('http://127.0.0.1:8767/')
        page.get_by_role('button', name='库存管理', exact=True).click()
        expect(page.get_by_role('table', name='本店药品库存')).to_be_visible()
        row = page.get_by_role('row').filter(has=page.get_by_text('演示药乙', exact=True))
        expect(row).to_contain_text('5')
        row.get_by_role('button', name='出入库', exact=True).click()
        dialog = page.get_by_role('dialog', name='登记出入库')
        expect(dialog.get_by_role('alert')).to_contain_text('计数单位')
        expect(dialog.get_by_role('button', name='确认登记')).to_be_disabled()
        dialog.get_by_role('button', name='取消', exact=True).click()
        row.get_by_role('button', name='编辑资料').click()
        dialog = page.get_by_role('dialog', name='编辑药品资料')
        dialog.get_by_label('库存计数单位').select_option('盒')
        dialog.get_by_label('规格', exact=True).fill('演示规格5粒/盒')
        dialog.get_by_label('主要成分').fill('合成演示成分')
        dialog.get_by_role('button', name='保存资料').click()
        expect(page.get_by_role('dialog')).to_have_count(0)
        expect(row).to_contain_text('演示规格5粒/盒')
        row.get_by_role('button', name='出入库', exact=True).click()
        dialog = page.get_by_role('dialog', name='登记出入库')
        dialog.get_by_label('登记数量', exact=False).fill('2')
        dialog.get_by_label('登记原因').fill('合成售出两盒')
        dialog.get_by_role('button', name='确认登记').click()
        expect(page.get_by_role('dialog')).to_have_count(0)
        expect(page.get_by_role('status')).to_contain_text('登记后库存 3 盒')
        expect(row).to_contain_text('3')
        row.get_by_role('button', name='记录', exact=True).click()
        ledger = page.get_by_role('table', name='库存变动记录')
        expect(ledger).to_contain_text('合成售出两盒')
        expect(ledger).to_contain_text('5 → 3')
        expect(ledger).to_contain_text('-2')
        page.get_by_role('tab', name='药品库存').click()
        row.get_by_role('button', name='出入库', exact=True).click()
        dialog = page.get_by_role('dialog', name='登记出入库')
        dialog.get_by_label('登记类型').select_option('restock')
        dialog.get_by_label('登记数量', exact=False).fill('2')
        dialog.get_by_label('登记原因').fill('合成补货两盒')
        dialog.get_by_role('button', name='确认登记').click()
        expect(page.get_by_role('dialog')).to_have_count(0)
        expect(page.get_by_role('status')).to_contain_text('登记后库存 5 盒')
        page.get_by_label('搜索库存').fill('3排2号')
        expect(page.get_by_role('row')).to_have_count(2)
        page.get_by_label('搜索库存').fill('不存在的商品')
        expect(page.get_by_text('没有匹配的药品，试试其他关键词或新增入库。')).to_be_visible()
        page.get_by_label('搜索库存').fill('')
        expect(page.get_by_role('row')).to_have_count(3)
        output = Path(__file__).resolve().parent.parent / 'docs/images/pharmacy-inventory-management.png'
        page.screenshot(path=str(output), full_page=True)
        page.set_viewport_size({'width': 390, 'height': 844})
        page.get_by_role('button', name='收起侧栏').click()
        expect(page.get_by_role('table')).to_be_visible()
        assert page.evaluate('document.documentElement.scrollWidth <= innerWidth'), 'page overflow on mobile'
        assert not errors, errors
        browser.close()
    print('Inventory UI passed: unit confirmation, editing, sale, restock, ledger, search, mobile.')


if __name__ == '__main__':
    main()
