"""Download a curated respiratory-health source pack for later RAG review.

Raw downloads are not automatically indexed or clinically approved.
"""
import hashlib
import io
import json
from concurrent.futures import ThreadPoolExecutor
from datetime import datetime, timezone
from html.parser import HTMLParser
from pathlib import Path
from urllib.request import Request, urlopen

ROOT = Path(__file__).resolve().parent.parent
OUT = ROOT / 'knowledge_sources/respiratory_reference_20261102'
NOTICE = 'https://www.nhc.gov.cn/ylyjs/zcwj/202501/f8fcecca59a048bebc4a71847ce57594.shtml'
SOURCES = [
    dict(id='cq-respiratory-diet-20240118', title='每日科普问答 呼吸道感染期间，饮食有哪些注意事项？',
         publisher='重庆市卫生健康委员会（转载自健康中国）', publication_date='2024-01-18',
         kind='html', role='patient_education',
         url='https://wsjkw.cq.gov.cn/ztzl_242/jkkp/202401/t20240118_12833568.html', anchor='少食多餐'),
    dict(id='beijing-child-fever-care-20261027', title='【清华长庚科普】儿童发热照护指南',
         publisher='北京市卫生健康委员会（清华长庚科普）', publication_date='2026-10-27',
         kind='html', role='patient_education', audience='child',
         url='https://wjw.beijing.gov.cn/bmfw_20143/jkzs/jksh/202610/t20261027_4920321.html', anchor='少量多次补充水分'),
    dict(id='nhc-influenza-guideline-2025', title='流行性感冒诊疗方案（2025年版）',
         publisher='国家卫生健康委办公厅、国家中医药局综合司', publication_date='2025-01-22',
         issued_date='2025-01-20', kind='pdf', role='clinical_reference', landing_url=NOTICE,
         url='https://www.nhc.gov.cn/ylyjs/zcwj/202501/f8fcecca59a048bebc4a71847ce57594/files/1741764832851_94226.pdf',
         anchor='流行性感冒诊疗方案'),
    dict(id='nhc-influenza-notice-2025', title='关于印发流行性感冒诊疗方案（2025年版）的通知',
         publisher='国家卫生健康委员会', publication_date='2025-01-22',
         kind='html', role='publication_notice', url=NOTICE, anchor='流行性感冒诊疗方案'),
    dict(id='cq-respiratory-home-monitoring-2024', title='每日科普问答 呼吸道感染性疾病如何居家监测？',
         publisher='重庆市卫生健康委员会', publication_date='2024-01-17',
         kind='html', role='patient_education',
         url='https://wsjkw.cq.gov.cn/ztzl_242/jkkp/202401/t20240117_12828443.html', anchor='症状监测'),
    dict(id='cq-respiratory-home-observation-2025', title='每日科普问答 呼吸道疾病高发，为避免交叉感染，什么情况下可以适合居家治疗观察？',
         publisher='重庆市卫生健康委员会', publication_date='2025-01-01',
         kind='html', role='patient_education',
         url='https://wsjkw.cq.gov.cn/ztzl_242/jkkp/202501/t20250102_14038341_zzb.html', anchor='症状加重'),
    dict(id='nhc-respiratory-press-20251121', title='国家卫生健康委员会2025年11月21日新闻发布会文字实录',
         publisher='国家卫生健康委员会', publication_date='2025-11-21',
         kind='html', role='expert_public_qa',
         url='https://www.nhc.gov.cn/xcs/c100122/202511/b622555670fa4f2388eb35f1c339195f.shtml', anchor='退烧'),
    dict(id='nhc-respiratory-press-20251219', title='国家卫生健康委员会2025年12月19日新闻发布会文字实录',
         publisher='国家卫生健康委员会', publication_date='2025-12-19',
         kind='html', role='expert_public_qa',
         url='https://www.nhc.gov.cn/xcs/c100122/202512/36f4eefdf88444d2bd571b1a4b6b6ef1.shtml', anchor='复方制剂'),
    dict(id='beijing-flu-prevention-20260918', title='【清华长庚科普】流感如何“未病先防”？9-10月这几件事先做',
         publisher='北京市卫生健康委员会', publication_date='2026-09-18',
         kind='html', role='patient_education',
         url='https://wjw.beijing.gov.cn/bmfw_20143/jkzs/jksh/202609/t20260918_4869326.html', anchor='重复用药'),
    dict(id='beijing-flu-fever-care-20250123', title='【清华长庚科普】甲流高热不退？这些退热方法需知道！',
         publisher='北京市卫生健康委员会', publication_date='2025-01-23',
         kind='html', role='patient_education',
         url='https://wjw.beijing.gov.cn/bmfw_20143/jkzs/jksh/202501/t20250123_3997070.html', anchor='重复用药'),
]


class VisibleText(HTMLParser):
    def __init__(self):
        super().__init__()
        self.hidden = 0
        self.parts = []

    def handle_starttag(self, tag, attrs):
        if tag in ('script', 'style'): self.hidden += 1

    def handle_endtag(self, tag):
        if tag in ('script', 'style'): self.hidden = max(0, self.hidden-1)

    def handle_data(self, data):
        if not self.hidden: self.parts.append(data)


def download(source):
    row = dict(source, review_status='pending', generation_allowed=False, indexed=False,
               collected_at=datetime.now(timezone.utc).isoformat())
    try:
        with urlopen(Request(source['url'], headers={'User-Agent':'Mozilla/5.0'}), timeout=25) as response:
            raw = response.read(20_000_001)
            row['final_url'] = response.geturl()
            row['content_type'] = response.headers.get('Content-Type')
        if len(raw)>20_000_000: raise ValueError('response too large')
        extra = None
        if source['kind']=='pdf':
            if not raw.startswith(b'%PDF-'): raise ValueError('not a PDF')
            from pypdf import PdfReader
            reader = PdfReader(io.BytesIO(raw))
            extra = [{'page':n+1,'text':page.extract_text() or ''} for n,page in enumerate(reader.pages)]
            text = '\n'.join(p['text'] for p in extra)
            row['pdf_pages'] = len(extra)
            row['extracted_characters'] = len(text)
        else:
            html = raw.decode('utf-8-sig')
            parser = VisibleText(); parser.feed(html)
            text = ''.join(parser.parts)
        if source['anchor'] not in ''.join(text.split()): raise ValueError('expected article content missing')
        filename = source['id'] + '.' + source['kind']
        target = OUT / filename
        # Preserve an existing snapshot if the publisher changed its content.
        if target.exists() and target.read_bytes()!=raw:
            filename = source['id']+'-'+hashlib.sha256(raw).hexdigest()[:12]+'.'+source['kind']
            target = OUT / filename
        target.write_bytes(raw)
        row.update(file=filename, bytes=len(raw), sha256=hashlib.sha256(raw).hexdigest(), status='downloaded')
        if extra is not None:
            pages_name = filename+'.pages.json'
            pages_raw = json.dumps(extra,ensure_ascii=False,indent=2).encode('utf-8')
            (OUT/pages_name).write_bytes(pages_raw)
            row.update(extraction_file=pages_name, extraction_sha256=hashlib.sha256(pages_raw).hexdigest(),
                       extraction_review='pending: page text extracted; tables, doses and reading order not verified')
    except Exception as error:
        row.update(status='failed', error=type(error).__name__+': '+str(error))
    return row


def main():
    OUT.mkdir(parents=True,exist_ok=True)
    with ThreadPoolExecutor(max_workers=3) as pool: rows=list(pool.map(download,SOURCES))
    manifest_raw=json.dumps(rows,ensure_ascii=False,indent=2)
    stamp=datetime.now(timezone.utc).strftime('%Y%m%dT%H%M%S%fZ')
    (OUT/('manifest-'+stamp+'.json')).write_text(manifest_raw,encoding='utf-8')
    (OUT/'manifest.json').write_text(manifest_raw,encoding='utf-8')
    lines=['# 呼吸道疾病补充原始资料', '',
           '全部待审核，未自动加入RAG索引。正文抽取、事实标注和临床适用性核验另行进行。', '',
           '| 材料 | 类型/用途 | 状态 | 本地原件 |', '| --- | --- | --- | --- |']
    for row in rows:
        lines.append(f"| [{row['title']}]({row['url']}) | {row['kind']} / {row['role']} | {row['status']} | {row.get('file','—')} |")
    lines += ['', '发布时间采用页面标注；流感方案另记录文件落款日期。网页路径日期不自动当作发布日期。',
              'PDF页码为文件物理页码，尚未核验印刷页码和表格。专家发布会实录不是药品说明书。',
              '未核实当前版本是否已被后续文件取代；公开访问不代表商业再分发授权。']
    (OUT/'README.md').write_text('\n'.join(lines)+'\n',encoding='utf-8')
    print(json.dumps({'directory':str(OUT),'downloaded':sum(r['status']=='downloaded' for r in rows),
                      'failed':[{k:r.get(k) for k in ('id','error')} for r in rows if r['status']=='failed'],
                      'documents':[{k:r.get(k) for k in ('id','bytes','pdf_pages','status')} for r in rows]},ensure_ascii=True))


if __name__=='__main__': main()
