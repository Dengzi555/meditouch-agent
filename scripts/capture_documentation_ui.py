"""Refresh README screenshots against documentation_ui_fixture on 8767 only."""
from io import BytesIO
from pathlib import Path

from PIL import Image, ImageDraw
from playwright.sync_api import expect, sync_playwright

OUTPUT = Path(__file__).resolve().parent.parent / 'docs/images'


def main():
    OUTPUT.mkdir(parents=True, exist_ok=True)
    with sync_playwright() as p:
        browser = p.chromium.launch(channel='msedge', headless=True)
        page = browser.new_page(viewport={'width': 1440, 'height': 1080}, device_scale_factor=1)
        errors = []
        page.on('pageerror', lambda error: errors.append(str(error)))
        page.goto('http://127.0.0.1:8767/')
        page.wait_for_load_state('networkidle')

        def shot(name, top=False):
            page.locator('[class*="importPage"]').evaluate_all('(els) => els.forEach(el => { el.scrollTop = 0; })')
            if top:
                page.locator('[class*="transcript"]').evaluate('(el) => { el.scrollTop = 0; }')
            page.screenshot(path=str(OUTPUT / name))

        def ask(question, pregnant=False):
            page.get_by_role('button', name='新咨询', exact=True).click()
            if pregnant:
                page.get_by_text('已知情况', exact=False).click()
                page.get_by_label('孕妇', exact=True).check()
            page.get_by_label('顾客想找什么药？').fill(question)
            page.get_by_role('button', name='发送 ↑').click()
            expect(page.get_by_text('本轮核对完成', exact=True)).to_be_visible(timeout=15000)

        shot('home.png')
        ask('演示药', pregnant=True)
        expect(page.locator('[data-card-type="drug_candidate"]')).to_be_visible()
        shot('pharmacy-copilot-cards.png', top=True)
        ask('感冒有哪些常见症状？')
        health = page.locator('[data-card-type="health_answer"]')
        expect(health).to_be_visible()
        expect(health.get_by_role('button', name='鼻塞、流鼻涕', exact=True)).to_be_enabled(timeout=15000)
        shot('pharmacy-health-answer.png', top=True)

        ask('顾客现在嘴歪、言语不清怎么办')
        emergency = page.locator('[data-card-type="emergency_support"]')
        expect(emergency).to_be_visible()
        shot('pharmacy-emergency-guidance.png', top=True)
        emergency.get_by_label('是否已呼救', exact=True).select_option('called')
        emergency.get_by_label('意识反应', exact=True).select_option('responsive')
        emergency.get_by_label('呼吸', exact=True).select_option('normal')
        emergency.get_by_label('最早出现症状时间').fill('约10分钟前（演示记录）')
        emergency.get_by_role('button', name='更新记录', exact=True).click()
        expect(emergency.get_by_role('button', name='复制交接记录')).to_be_enabled()
        emergency.locator('pre').scroll_into_view_if_needed()
        shot('pharmacy-emergency-handover.png')

        page.get_by_role('button', name='库存管理', exact=True).click()
        row = page.get_by_role('row').filter(has_text='演示药乙')
        row.get_by_role('button', name='编辑资料', exact=True).click()
        dialog = page.get_by_role('dialog', name='编辑药品资料')
        dialog.get_by_label('库存计数单位').select_option('盒')
        dialog.get_by_label('规格', exact=True).fill('演示规格5粒/盒')
        dialog.get_by_role('button', name='保存资料', exact=True).click()
        expect(dialog).to_have_count(0)
        shot('pharmacy-inventory-management.png')
        row.get_by_role('button', name='删除', exact=True).click()
        dialog = page.get_by_role('dialog', name='确认删除此药品？')
        expect(dialog).to_be_visible()
        shot('pharmacy-inventory-delete.png')
        dialog.get_by_role('button', name='取消', exact=True).click()
        row.get_by_role('button', name='出入库', exact=True).click()
        dialog = page.get_by_role('dialog', name='登记出入库')
        dialog.get_by_label('登记数量', exact=False).fill('2')
        dialog.get_by_label('登记原因', exact=True).fill('柜台售出两盒（演示）')
        shot('pharmacy-stock-movement.png')
        dialog.get_by_role('button', name='确认登记', exact=True).click()
        expect(dialog).to_have_count(0)
        row.get_by_role('button', name='记录', exact=True).click()
        expect(page.get_by_role('table', name='库存变动记录')).to_contain_text('5 → 3')
        shot('pharmacy-stock-ledger.png')

        page.get_by_role('button', name='拍照入库', exact=True).click()
        page.set_viewport_size({'width': 1440, 'height': 1300})
        page.get_by_label('药名 *', exact=True).fill('演示药乙')
        expect(page.get_by_role('complementary', name='仓库已有商品')).to_be_visible()
        shot('pharmacy-duplicate-import.png')
        page.get_by_label('药名 *', exact=True).fill('')
        picture = Image.new('RGB', (640, 320), '#f4f4f8')
        draw = ImageDraw.Draw(picture)
        for x, label in [(25, 'DEMO A'), (335, 'DEMO B')]:
            draw.rounded_rectangle((x, 40, x + 275, 265), radius=10, fill='white', outline='#b7b0d0', width=3)
            draw.rectangle((x + 3, 43, x + 272, 100), fill='#5545bc')
            draw.text((x + 30, 130), label, fill='#292741', font_size=30)
            draw.text((x + 30, 185), 'SYNTHETIC PACKAGE', fill='#6f6885', font_size=16)
        buffer = BytesIO(); picture.save(buffer, format='PNG')
        page.get_by_test_id('image-picker').set_input_files({'name': 'demo-packages.png', 'mimeType': 'image/png', 'buffer': buffer.getvalue()})
        expect(page.get_by_label('药名 *', exact=True)).to_have_value('演示批量甲', timeout=10000)
        page.get_by_label('货架号 *', exact=True).fill('B3')
        page.get_by_label('库存数量 *', exact=True).fill('12')
        page.get_by_role('combobox', name='处方属性 *', exact=True).select_option('false')
        shot('pharmacy-inventory-import-multi.png')
        # Single-package screenshot uses the same actual upload flow and a separately generated image.
        single = Image.new('RGB', (600, 320), 'white')
        draw = ImageDraw.Draw(single); draw.text((100, 130), 'DEMO PACKAGE', fill='#5545bc', font_size=34)
        buffer = BytesIO(); single.save(buffer, format='PNG')
        page.get_by_test_id('image-picker').set_input_files({'name': 'demo-package.png', 'mimeType': 'image/png', 'buffer': buffer.getvalue()})
        expect(page.get_by_label('药名 *', exact=True)).to_have_value('演示入库药', timeout=10000)
        shot('pharmacy-inventory-import.png')
        page.set_viewport_size({'width': 1440, 'height': 1080})
        page.get_by_role('button', name='本地门店设置', exact=True).click()
        page.get_by_role('button', name='API 配置', exact=False).click()
        expect(page.get_by_role('dialog', name='API 配置')).to_be_visible()
        shot('api-settings.png')
        assert not errors, errors
        browser.close()
    print('Refreshed 13 screenshots from isolated React/FastAPI fixture; no real inventory or paid model calls.')


if __name__ == '__main__':
    main()
