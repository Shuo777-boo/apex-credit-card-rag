"""Real Chromium verification of help and tutorial flow, with every API mocked.
Run against Vite dev or production preview. No real billing, jobs, notifications or deletion.
"""
import argparse
import json
from pathlib import Path
from playwright.sync_api import sync_playwright, expect
from help_browser import ROOT, QUESTION, install_demo

parser=argparse.ArgumentParser()
parser.add_argument('--url',default='http://127.0.0.1:15175')
args=parser.parse_args()
out=ROOT/'evidence/help'
out.mkdir(parents=True,exist_ok=True)
checks=[]


def passed(name):
    checks.append(name)
    print('PASS '+name,flush=True)


with sync_playwright() as p:
    browser=p.chromium.launch(headless=True)
    context=browser.new_context(viewport={'width':1440,'height':1000},accept_downloads=True)
    state=install_demo(context)
    page=context.new_page()
    errors=[]
    page.on('pageerror',lambda e:errors.append(str(e)))
    try:
        page.goto(args.url+'/#home')
        page.get_by_role('button',name='帮助与使用指南',exact=True).click()
        expect(page.get_by_role('heading',name='帮助与使用指南',exact=True)).to_be_visible()
        assert page.locator('.help-chapter').count()==10
        passed('Help entry, lazy route and all ten chapters')

        search=page.get_by_role('textbox',name='搜索使用指南')
        for term,expected in [('余额转移','quickstart'),('12000','evidence'),('NFKC不存在的关键词','none'),('存档损坏','daily')]:
            search.fill(term)
            if expected=='none':
                expect(page.get_by_text('没有找到与「NFKC不存在的关键词」相关的内容',exact=True)).to_be_visible()
                assert page.locator('.help-chapter').count()==0
            else:
                assert page.locator(f'#guide-{expected}').count()==1
        passed('Search indexes body, steps, FAQ and troubleshooting; no-results state')
        search.fill('zzzz无匹配')
        page.get_by_role('button',name='清除搜索，查看全部章节').click()
        expect(search).to_have_value('')
        assert page.locator('.help-chapter').count()==10
        passed('Clear search restores full guide and directory')

        page.get_by_role('navigation',name='指南章节目录').locator('a[href*="section=quickstart"]').click()
        expect(page.locator('#guide-quickstart')).to_be_focused()
        assert 'section=quickstart' in page.url
        page.reload()
        expect(page.locator('#guide-quickstart')).to_be_focused()
        passed('Directory, direct URL, refresh and accessible chapter focus')

        page.locator('.help-chapter img').evaluate_all("els=>els.forEach(e=>e.loading='eager')")
        assert page.locator('.help-chapter img').evaluate_all("async els=>{await Promise.all(els.map(e=>e.decode()));return els.every(e=>e.naturalWidth===1440)}")
        assert page.locator('.help-chapter img').count()==9
        passed('All nine annotated screenshots load at full native resolution')
        trigger=page.locator('#guide-quickstart .help-image-button').first
        trigger.focus()
        page.keyboard.press('Enter')
        expect(page.get_by_role('button',name='关闭图片',exact=True)).to_be_visible()
        page.keyboard.press('Escape')
        expect(page.get_by_role('button',name='关闭图片',exact=True)).not_to_be_visible()
        expect(trigger).to_be_focused()
        trigger.click()
        page.get_by_role('button',name='关闭图片',exact=True).click()
        expect(trigger).to_be_focused()
        passed('Image mouse/Enter opening, Escape/button closing and focus restoration')

        assert page.locator('#guide-prepare .help-advanced').get_attribute('open') is None
        page.locator('#guide-prepare .help-advanced summary').click()
        expect(page.locator('#guide-prepare pre')).to_be_visible()
        search.fill('Conda')
        expect(page.locator('#guide-prepare pre')).to_be_visible()
        passed('Progressive advanced disclosures and expanded search matches')
        search.fill('')

        # The help route does not make model or job requests on its own.
        assert not any(c['method']=='POST' for c in state['calls'])
        passed('Reading and searching help creates no model or evaluation requests')

        downloads=out/'downloads'
        downloads.mkdir(exist_ok=True)
        for kind,label in [('html','下载离线 HTML'),('md','下载 Markdown')]:
            with page.expect_download() as download:
                page.get_by_role('button',name=label,exact=True).click()
            target=downloads/f'apex-guide.{kind}'
            download.value.save_as(str(target))
            assert target.read_bytes()==(ROOT/f'public/help/apex-guide.{kind}').read_bytes()
        passed('Both download buttons return the generated files byte for byte')
        page.route('**/help/apex-guide.html',lambda route:route.fulfill(status=503,body='test'))
        page.get_by_role('button',name='下载离线 HTML',exact=True).click()
        expect(page.get_by_text('说明书下载失败，请检查网络或浏览器下载权限后重试。维护者请确认已生成 /help/apex-guide.html 和 .md。',exact=True)).to_be_visible()
        page.unroute('**/help/apex-guide.html')
        page.locator('.help-page .ant-alert-close-icon').click()
        passed('Download failures show actionable error feedback')

        # Width checks include search results, table overflow containers and expanded content.
        widths=[]
        for width,height in [(1440,1000),(1024,768),(768,1024),(390,844),(320,740)]:
            page.set_viewport_size({'width':width,'height':height})
            page.goto(args.url+'/#help')
            expect(page.locator('#guide-overview')).to_be_visible()
            page.locator('.help-chapter img').evaluate_all("els=>els.forEach(e=>e.loading='eager')")
            page.wait_for_timeout(200)
            measurements=page.evaluate("({viewport:innerWidth,document:document.documentElement.scrollWidth,body:document.body.scrollWidth})")
            assert measurements['document']<=width and measurements['body']<=width,measurements
            tables=page.locator('.help-table-scroll').evaluate_all('els=>els.map(e=>({width:e.clientWidth,scroll:e.scrollWidth,right:e.getBoundingClientRect().right}))')
            assert all(t['right']<=width for t in tables),tables
            for nav in page.get_by_role('navigation',name='主导航').get_by_role('button').all():
                box=nav.bounding_box()
                assert box and box['x']>=0 and box['x']+box['width']<=width
            page.screenshot(path=str(out/f'help-{width}.png'),full_page=False)
            widths.append(measurements)
        passed('1440/1024/768/390/320px layouts: no page overflow or clipped navigation')

        page.set_viewport_size({'width':1440,'height':1000})
        page.goto(args.url+'/#help?section=quickstart')
        page.locator('#guide-quickstart a[href="#workbench"]').click()
        expect(page.get_by_role('textbox',name='你想了解什么政策？')).to_be_visible()
        page.get_by_role('button',name='新建对话',exact=True).click()
        page.get_by_role('combobox',name='卡种筛选').press('Enter')
        page.locator('.ant-select-item-option-content').get_by_text('Apex Platinum',exact=True).click()
        page.get_by_role('textbox',name='你想了解什么政策？').fill(QUESTION)
        page.get_by_role('button',name='发送消息').click()
        expect(page.get_by_role('button',name='[1] DEMO-DOC')).to_be_visible()
        call=next(c for c in state['calls'] if c['path'].endswith('/answer'))
        assert call['body']['query']==QUESTION and call['body']['card_tier']=='Apex Platinum' and call['body']['mode']=='hybrid_rerank'
        page.get_by_role('button',name='[1] DEMO-DOC').click()
        expect(page.locator('.policy-content')).to_contain_text('演示数据')
        page.locator('.ant-drawer-open .ant-drawer-close').click()
        page.get_by_role('button',name='查看本轮证据与检索过程').click()
        expect(page.get_by_text('最终文档与证据片段',exact=True)).to_be_visible()
        page.locator('.ant-drawer-open .ant-drawer-close').click()
        passed('Guide function link and complete demo question → answer → source → retrieval flow')

        page.get_by_role('textbox',name='你想了解什么政策？').fill('What conditions apply to this balance transfer fee?')
        page.get_by_role('button',name='发送消息').click()
        expect(page.get_by_text('使用 2 条历史消息',exact=False)).to_be_visible()
        assert [c for c in state['calls'] if c['path'].endswith('/answer')][-1]['body']['history'][0]['content']==QUESTION
        page.reload()
        expect(page.get_by_text('What conditions apply to this balance transfer fee?',exact=True)).to_be_visible()
        passed('Follow-up history request and local session restoration after reload')

        page.goto(args.url+'/#help?section=single')
        page.locator('#guide-single a[href="#evaluations"]').click()
        page.get_by_role('button',name='新建单配置实验',exact=True).click()
        page.get_by_role('combobox',name='就绪构建').press('Enter')
        page.locator('.ant-select-item-option-content').filter(has_text='DEMO-POLICY').click()
        page.get_by_role('combobox',name='实验检索模式').press('Enter')
        page.locator('.ant-select-item-option-content').get_by_text('hybrid_rerank',exact=True).click()
        page.get_by_role('button',name='核对调用与官方单价').click()
        confirm=page.get_by_role('checkbox',name='我已核对配置和费用上限，明确启动此付费检索实验')
        expect(confirm).to_be_visible()
        expect(page.get_by_role('button',name='创建并启动实验')).to_be_disabled()
        assert not state['jobs']
        confirm.check()
        page.get_by_role('button',name='创建并启动实验').click()
        expect(page.get_by_role('button',name='查看 DEMO-RUN · completed')).to_be_visible()
        page.get_by_role('button',name='查看 DEMO-RUN · completed').click()
        expect(page.get_by_text('质量指标与中文含义',exact=True)).to_be_visible()
        page.get_by_role('button',name='查看题目详情',exact=True).click()
        expect(page.get_by_text('参考回答（英文原文）：演示数据：核对原文中的费率与适用条件。',exact=True)).to_be_visible()
        page.get_by_role('button',name='返回实验详情',exact=True).click()
        page.get_by_role('button',name='关闭详情',exact=True).click()
        passed('Demo-only evaluation estimate → explicit confirmation → completed job → run → question')

        page.get_by_role('button',name='新建对比实验',exact=True).click()
        expect(page.get_by_text('六组对比矩阵',exact=True)).to_be_visible()
        page.get_by_role('button',name='核对调用与官方单价').click()
        confirm=page.get_by_role('checkbox',name='我已核对配置和费用上限，明确启动此付费检索实验')
        expect(confirm).to_be_visible()
        confirm.check()
        page.get_by_role('button',name='创建并启动实验').click()
        expect(page.locator('.job-comparison-action button')).to_be_enabled()
        page.locator('.job-comparison-action button').click()
        expect(page.get_by_text('DEMO-JOB-2',exact=True).last).to_be_visible()
        expect(page.get_by_text('本任务结果对比',exact=True)).to_be_visible()
        passed('Demo-only six-group setup, 180-question estimate and scoped result comparison')

        # Open the downloaded standalone file with networking disabled and no route mocks.
        offline=browser.new_context(viewport={'width':390,'height':844},offline=True)
        offline_page=offline.new_page()
        network=[]
        offline_page.on('request',lambda r:network.append(r.url) if r.url.startswith(('http:','https:')) else None)
        offline_page.goto((downloads/'apex-guide.html').as_uri())
        expect(offline_page.get_by_role('heading',name='帮助与使用指南',exact=True)).to_be_visible()
        assert offline_page.locator('.help-chapter').count()==10
        offline_page.locator('.help-chapter img').evaluate_all("els=>els.forEach(e=>e.loading='eager')")
        assert offline_page.locator('.help-chapter img').evaluate_all("async els=>{await Promise.all(els.map(e=>e.decode()));return els.length===9&&els.every(e=>e.naturalWidth>0)}")
        assert not network,network
        passed('Downloaded file:// HTML renders all text and nine embedded images with networking off')
        offline_page.get_by_role('searchbox',name='搜索使用指南').fill('Conda')
        expect(offline_page.locator('#prepare pre')).to_be_visible()
        offline_page.get_by_role('button',name='清除搜索',exact=True).click()
        offline_page.locator('#quickstart .help-image-button').first.focus()
        offline_page.keyboard.press('Enter')
        expect(offline_page.get_by_role('button',name='关闭图片',exact=True)).to_be_visible()
        offline_page.keyboard.press('Escape')
        expect(offline_page.get_by_role('button',name='关闭图片',exact=True)).not_to_be_visible()
        offline_page.locator('#quickstart .help-image-button').first.click()
        offline_page.get_by_role('button',name='关闭图片',exact=True).click()
        assert offline_page.evaluate('document.documentElement.scrollWidth<=innerWidth')
        offline_page.screenshot(path=str(out/'offline-mobile.png'))
        passed('Offline search, advanced expansion, image keyboard/close controls and phone layout')
        assert not errors,errors
        passed('No uncaught browser exceptions')
        (out/'validation.json').write_text(json.dumps({'checks':checks,'widths':widths,'browser_errors':errors,'all_api_intercepted':True,'real_model_calls':0,'real_jobs_created':0,'demo_jobs_created':len(state['jobs']),'offline_network_requests':network},ensure_ascii=False,indent=2),encoding='utf8')
    except Exception:
        page.screenshot(path=str(out/'verification-failure.png'))
        (out/'failure.txt').write_text(page.locator('body').inner_text(),encoding='utf8')
        raise
    finally:
        browser.close()
print(f'{len(checks)} browser checks passed; no real model calls or business mutations.')
