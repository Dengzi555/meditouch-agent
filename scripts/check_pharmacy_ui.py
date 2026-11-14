"""Browser checks against an already running isolated QA app on port 8767.

Requires optional Playwright + Edge. Never uses real stock or paid model calls.
"""
from pathlib import Path
from io import BytesIO

from PIL import Image, ImageDraw

from playwright.sync_api import sync_playwright, expect


def main():
    with sync_playwright() as playwright:
        browser = playwright.chromium.launch(channel='msedge', headless=True)
        page = browser.new_page(viewport={'width': 1440, 'height': 1050})
        errors = []
        page.on('pageerror', lambda error: errors.append(str(error)))
        try:
            page.goto('http://127.0.0.1:8767/')
            page.wait_for_load_state('networkidle')
            page.get_by_role('button', name='本地门店设置', exact=True).click()
            page.get_by_role('button', name='API 配置', exact=False).click()
            settings = page.get_by_role('dialog', name='API 配置')
            expect(settings).to_be_visible()
            expect(settings.locator('#MED_AGENT_API_KEY')).to_have_value('')
            expect(settings.locator('#MED_AGENT_MODEL')).to_have_value('scripted-text')
            settings.locator('#MED_AGENT_MODEL').fill('scripted-text-updated')
            settings.locator('#MED_AGENT_API_KEY').fill('fixture-new-key')
            settings.get_by_role('button', name='保存配置', exact=True).click()
            expect(settings.get_by_role('status')).to_contain_text('已保存到本地 .env')
            expect(settings.locator('#MED_AGENT_API_KEY')).to_have_value('')
            settings.get_by_role('button', name='关闭 API 配置', exact=True).click()
            page.get_by_role('button', name='本地门店设置', exact=True).click()
            page.get_by_role('button', name='API 配置', exact=False).click()
            settings = page.get_by_role('dialog', name='API 配置')
            expect(settings.locator('#MED_AGENT_MODEL')).to_have_value('scripted-text-updated')
            expect(settings.locator('#MED_AGENT_API_KEY')).to_have_value('')
            page.screenshot(path=str(Path(__file__).resolve().parent.parent / 'docs/images/api-settings.png'))
            settings.get_by_role('button', name='关闭 API 配置', exact=True).click()
            expect(page.get_by_text('库存已连接', exact=False)).to_be_visible()
            page.get_by_text('已知情况', exact=False).click()
            page.get_by_label('孕妇', exact=True).check()
            page.get_by_label('顾客想找什么药？').fill('演示药')
            page.get_by_role('button', name='发送 ↑').click()
            expect(page.get_by_role('status')).to_be_visible()
            expect(page.get_by_text('本轮核对完成').last).to_be_visible(timeout=15000)
            expect(page.locator('[data-card-type="intercept_warning"]')).to_contain_text('演示药甲')
            expect(page.locator('[data-card-type="drug_candidate"]')).to_contain_text('演示药乙')
            expect(page.locator('[data-card-type="drug_candidate"]')).to_contain_text('3排2号')
            expect(page.locator('[data-card-type="drug_candidate"]')).to_contain_text('处方药需药师审核')
            expect(page.locator('[data-card-type="drug_candidate"]').get_by_text('本店有货 · 待核对', exact=True)).to_be_visible()
            expect(page.locator('[data-card-type="drug_candidate"]').get_by_text('处方属性', exact=True)).to_be_visible()
            expect(page.locator('[data-card-type="missing_information"]')).to_contain_text('请人工核对病史')
            expect(page.locator('[data-card-type="staff_script"]')).to_be_visible()
            expect(page.get_by_label('顾客想找什么药？')).to_have_value('')
            path = Path(__file__).resolve().parent.parent / 'docs/images/pharmacy-copilot-cards.png'
            page.screenshot(path=str(path), full_page=True)

            page.get_by_role('button', name='收起侧栏').click()
            expect(page.get_by_role('navigation', name='聊天记录')).to_have_count(0)
            page.get_by_role('button', name='展开侧栏').click()
            expect(page.get_by_role('navigation', name='聊天记录').get_by_role('button')).to_have_count(1)

            page.get_by_label('顾客想找什么药？').fill('不存在商品')
            page.get_by_role('button', name='发送 ↑').click()
            latest = page.get_by_role('region', name='一轮对话').last
            expect(latest.locator('[data-card-type="drug_candidate"]')).to_have_count(0)
            expect(page.get_by_role('status')).to_be_visible()
            expect(latest.get_by_text('本轮核对完成')).to_be_visible(timeout=15000)
            expect(latest.get_by_text('本店未查询到的药品', exact=True)).to_be_visible()
            expect(latest.locator('[data-card-type="drug_candidate"]')).to_have_count(0)
            expect(page.get_by_role('region', name='一轮对话')).to_have_count(2)
            expect(page.get_by_role('navigation', name='聊天记录').get_by_role('button')).to_have_count(1)

            # A new consultation isolates the transcript; old history remains selectable.
            page.get_by_role('button', name='新咨询', exact=True).click()
            page.get_by_label('顾客想找什么药？').fill('演示药')
            page.get_by_role('button', name='发送 ↑').click()
            page.get_by_role('button', name='新咨询', exact=True).click()
            page.get_by_label('顾客想找什么药？').fill('不存在商品')
            page.get_by_role('button', name='发送 ↑').click()
            expect(page.get_by_text('本轮核对完成')).to_be_visible(timeout=15000)
            expect(page.locator('[data-card-type="drug_candidate"]')).to_have_count(0)

            page.get_by_label('顾客想找什么药？').fill('演示药')
            page.get_by_role('button', name='发送 ↑').click()
            page.get_by_role('button', name='停止 ■', exact=True).click()
            expect(page.get_by_role('alert')).to_contain_text('已停止')
            expect(page.locator('[data-card-type="drug_candidate"]')).to_have_count(0)

            page.route('**/api/agent/stream', lambda route: route.fulfill(status=503,
                       content_type='application/json', body='{"detail":"测试连接失败"}'))
            page.get_by_label('顾客想找什么药？').fill('演示药')
            page.get_by_role('button', name='发送 ↑').click()
            expect(page.get_by_role('region', name='一轮对话').last.get_by_role('alert')).to_contain_text('测试连接失败')
            expect(page.locator('[data-card-type="drug_candidate"]')).to_have_count(0)
            assert not errors, errors

            page.get_by_role('button', name='拍照入库', exact=True).click()
            picture = Image.new('RGB', (400, 260), '#eef3e7')
            ImageDraw.Draw(picture).text((45, 110), 'MOCK PACKAGE / UI TEST ONLY', fill='#285c42')
            image = BytesIO(); picture.save(image, format='PNG')
            page.get_by_test_id('image-picker').set_input_files({
                'name': 'mock-box.png', 'mimeType': 'image/png', 'buffer': image.getvalue()})
            expect(page.get_by_text('正在识别，尚未入库…')).to_be_visible()
            expect(page.get_by_label('库存数量 *', exact=True)).to_have_value('')
            expect(page.get_by_label('货架号 *', exact=True)).to_have_value('')
            page.get_by_label('库存数量 *', exact=True).fill('7')
            page.get_by_label('货架号 *', exact=True).fill('C7')
            expect(page.get_by_label('药名 *', exact=True)).to_have_value('演示入库药', timeout=10000)
            expect(page.get_by_label('品牌', exact=True)).to_have_value('')
            expect(page.get_by_label('品牌', exact=True)).to_have_attribute('placeholder', '选填')
            expect(page.get_by_role('combobox', name='处方属性 *', exact=True)).to_have_value('')
            expect(page.get_by_label('库存数量 *', exact=True)).to_have_value('7')
            page.get_by_label('药名 *', exact=True).fill('员工确认演示药')
            page.get_by_label('品牌', exact=True).fill('人工品牌')
            page.get_by_role('combobox', name='处方属性 *', exact=True).select_option('false')
            page.screenshot(path=str(path.with_name('pharmacy-inventory-import.png')), full_page=True)
            page.get_by_role('button', name='确认入库', exact=True).click()
            expect(page.get_by_role('status')).to_contain_text('入库成功：员工确认演示药')
            expect(page.get_by_label('药名 *', exact=True)).to_have_value('')
            expect(page.get_by_label('库存数量 *', exact=True)).to_have_value('')
            expect(page.get_by_label('货架号 *', exact=True)).to_have_value('')
            expect(page.get_by_alt_text('待识别的药盒照片')).to_have_count(0)
            page.get_by_role('button', name='新咨询', exact=True).click()
            page.unroute('**/api/agent/stream')
            page.get_by_label('顾客想找什么药？').fill('员工确认演示药')
            page.get_by_role('button', name='发送 ↑').click()
            expect(page.get_by_text('本轮核对完成')).to_be_visible(timeout=15000)
            candidate = page.locator('[data-card-type="drug_candidate"]')
            expect(candidate).to_contain_text('员工确认演示药')
            expect(candidate).to_contain_text('C7')
            page.get_by_role('button', name='拍照入库', exact=True).click()
            picture = Image.new('RGB', (640, 320), 'white')
            image = BytesIO(); picture.save(image, format='PNG')
            page.get_by_test_id('image-picker').set_input_files({
                'name': 'multiple.png', 'mimeType': 'image/png', 'buffer': image.getvalue()})
            expect(page.get_by_label('药名 *', exact=True)).to_have_value('演示批量甲', timeout=10000)
            page.get_by_label('货架号 *', exact=True).fill('A8')
            page.get_by_label('库存数量 *', exact=True).fill('3')
            page.get_by_role('combobox', name='处方属性 *', exact=True).select_option('false')
            page.get_by_text('2. 演示批量乙', exact=True).click()
            expect(page.get_by_label('药名 *', exact=True)).to_have_value('演示批量乙')
            expect(page.get_by_label('主要成分', exact=True)).to_have_value('')
            expect(page.get_by_label('规格', exact=True)).to_have_value('规格乙')
            expect(page.get_by_label('货架号 *', exact=True)).to_have_value('')
            expect(page.get_by_label('库存数量 *', exact=True)).to_have_value('')
            page.get_by_label('货架号 *', exact=True).fill('B8')
            page.get_by_label('库存数量 *', exact=True).fill('4')
            page.get_by_role('combobox', name='处方属性 *', exact=True).select_option('true')
            page.screenshot(path=str(path.with_name('pharmacy-inventory-import-multi.png')), full_page=True)
            page.get_by_role('button', name='确认入库', exact=True).click()
            expect(page.get_by_role('status')).to_contain_text('入库成功：演示批量乙')
            expect(page.get_by_label('药名 *', exact=True)).to_have_value('演示批量甲')
            expect(page.get_by_label('库存数量 *', exact=True)).to_have_value('3')
            expect(page.get_by_label('货架号 *', exact=True)).to_have_value('A8')
            page.get_by_role('button', name='确认入库', exact=True).click()
            expect(page.get_by_role('status')).to_contain_text('入库成功：演示批量甲')
            expect(page.get_by_label('药名 *', exact=True)).to_have_value('')
            expect(page.get_by_alt_text('待识别的药盒照片')).to_have_count(0)
            page.get_by_role('button', name='新咨询', exact=True).click()
            page.get_by_role('button', name='收起侧栏').click()
            page.set_viewport_size({'width': 390, 'height': 844})
            assert page.evaluate('document.documentElement.scrollWidth <= window.innerWidth')
            expect(page.get_by_label('顾客想找什么药？')).to_be_in_viewport()
            assert not errors, errors
            print('Browser checks passed: SSE cards, reset, cancellation, OCR prefill, manual override, import and readback.')
        finally:
            browser.close()


if __name__ == '__main__':
    main()
