"""Capture genuine frontend controls with isolated demo data and numbered annotations.
Usage: python scripts/capture-help.py --url http://127.0.0.1:15175
Requires a static preview server, Python playwright and Pillow (QA tools only).
"""
import argparse
import io
import json
from PIL import Image, ImageDraw, ImageFont
from playwright.sync_api import sync_playwright, expect
from help_browser import ROOT, QUESTION, install_demo

parser = argparse.ArgumentParser()
parser.add_argument('--url', default='http://127.0.0.1:15175')
args = parser.parse_args()
output = ROOT / 'public/help/images'
raw = ROOT / 'evidence/help/raw'
output.mkdir(parents=True, exist_ok=True)
raw.mkdir(parents=True, exist_ok=True)
font = ImageFont.truetype('C:/Windows/Fonts/arialbd.ttf', 21)
manifest = []

with sync_playwright() as p:
    browser = p.chromium.launch(headless=True)
    context = browser.new_context(viewport={'width': 1440, 'height': 1000}, device_scale_factor=1)
    state = install_demo(context)
    page = context.new_page()
    errors = []
    page.on('pageerror', lambda e: errors.append(str(e)))

    def capture(name, locators):
        page.wait_for_timeout(500)
        boxes = [loc.bounding_box() for loc in locators]
        data = page.screenshot(path=str(raw / f'{name}.png'), animations='disabled')
        im = Image.open(io.BytesIO(data)).convert('RGB')
        draw = ImageDraw.Draw(im)
        for i, box in enumerate(boxes, 1):
            assert box and box['y'] < 968, (name, box)
            x, y, w, h = box['x'], box['y'], box['width'], box['height']
            draw.rounded_rectangle((max(1,x-3), max(1,y-3), min(1438,x+w+3), min(966,y+h+3)), radius=5, outline='#ef672a', width=3)
            cx, cy = max(17,min(1420,x+5)), max(17,y+5)
            draw.ellipse((cx-15,cy-15,cx+15,cy+15), fill='#dd4b13', outline='white', width=2)
            draw.text((cx, cy),str(i),font=font,fill='white',anchor='mm')
        im.save(output / f'{name}.webp', 'WEBP', quality=88, method=6)
        manifest.append({'file':f'{name}.webp','url':page.url,'viewport':[1440,1000],'annotations':boxes,'demo_data':True})

    page.goto(args.url)
    expect(page.get_by_text('活动构建 DEMO-POLICY')).to_be_visible()
    capture('home', [page.get_by_role('navigation', name='主导航'), page.locator('.home-stats .ant-card').first, page.locator('.home-welcome a').filter(has_text='开始对话')])
    page.get_by_role('button',name='对话工作台',exact=True).click()
    page.get_by_role('button',name='新建对话',exact=True).click()
    page.get_by_role('combobox',name='卡种筛选').press('Enter')
    page.locator('.ant-select-item-option-content').get_by_text('Apex Platinum',exact=True).click()
    page.get_by_role('textbox',name='你想了解什么政策？').fill(QUESTION)
    capture('compose',[page.get_by_role('button',name='新建对话',exact=True),page.locator('.chat-composer .ant-space').first,page.get_by_role('textbox',name='你想了解什么政策？'),page.get_by_role('button',name='发送消息')])
    page.get_by_role('button',name='发送消息').click()
    expect(page.get_by_role('button',name='[1] DEMO-DOC')).to_be_visible()
    capture('answer',[page.get_by_role('button',name='[1] DEMO-DOC'),page.get_by_role('button',name='查看本轮证据与检索过程')])
    page.get_by_role('button',name='[1] DEMO-DOC').click()
    expect(page.locator('.policy-content')).to_be_visible()
    capture('source',[page.locator('.ant-drawer-open .ant-descriptions'),page.locator('.policy-content')])
    page.locator('.ant-drawer-open .ant-drawer-close').click()
    page.get_by_role('button',name='查看本轮证据与检索过程').click()
    expect(page.get_by_text('最终文档与证据片段',exact=True)).to_be_visible()
    capture('evidence',[page.locator('.retrieval-details .ant-card').first,page.get_by_text('检索阶段排名',exact=True),page.get_by_text('实测耗时',exact=True)])
    page.locator('.ant-drawer-open .ant-drawer-close').click()
    page.get_by_role('button',name='评测实验',exact=True).click()
    page.get_by_role('button',name='新建单配置实验',exact=True).click()
    page.get_by_role('combobox',name='就绪构建').press('Enter')
    page.locator('.ant-select-item-option-content').filter(has_text='DEMO-POLICY').click()
    page.get_by_role('combobox',name='实验检索模式').press('Enter')
    page.locator('.ant-select-item-option-content').get_by_text('hybrid_rerank',exact=True).click()
    page.get_by_role('button',name='核对调用与官方单价').click()
    expect(page.get_by_role('checkbox',name='我已核对配置和费用上限，明确启动此付费检索实验')).to_be_visible()
    capture('estimate',[page.locator('.job-form-grid'),page.locator('.job-estimate'),page.locator('.ant-modal .ant-checkbox-wrapper'),page.locator('.ant-modal-footer')])
    page.locator('.ant-modal-footer button').first.click()
    page.get_by_role('button',name='查看详情',exact=True).first.click()
    expect(page.get_by_text('质量指标与中文含义',exact=True)).to_be_visible()
    capture('report',[page.locator('.ant-modal-body .ant-space').first,page.get_by_text('质量指标与中文含义',exact=True)])
    page.locator('.report-two-columns').first.scroll_into_view_if_needed()
    capture('report-latency',[page.locator('.report-two-columns').first,page.get_by_text('题目：成功 30 / 总数 30 · 失败 0',exact=True)])
    page.get_by_role('button',name='关闭详情',exact=True).click()
    page.get_by_role('button',name='对话工作台',exact=True).click()
    page.get_by_role('button',name='新建对话',exact=True).click()
    state['fail_answer']=True
    page.get_by_role('textbox',name='你想了解什么政策？').fill(QUESTION)
    page.get_by_role('button',name='发送消息').click()
    expect(page.get_by_text('生成失败，已保留成功检索到的证据',exact=True)).to_be_visible()
    capture('error',[page.locator('.chat-turn .ant-alert').first,page.get_by_role('button',name='查看本轮证据与检索过程')])
    assert not errors, errors
    assert not any(c['method']=='POST' and c['path']=='/api/v1/evaluation-jobs' for c in state['calls'])
    (ROOT / 'evidence/help/screenshot-manifest.json').write_text(json.dumps({'captures':manifest,'browser_errors':errors,'all_api_intercepted':True,'real_model_calls':0},ensure_ascii=False,indent=2),encoding='utf8')
    browser.close()
print(f'Captured {len(manifest)} annotated screenshots from real frontend; demo data only.')
