"""Collect a small, explicit allowlist of public sources; never modifies agent.db."""
import hashlib
import json
from datetime import datetime
from pathlib import Path
from urllib.request import Request, urlopen
from html.parser import HTMLParser

ROOT = Path(__file__).resolve().parent.parent / 'knowledge_sources' / 'common_conditions_20261030'
SOURCES = [
    ('nhc-food-guides-2023', '三项成人食养指南发布通知', '三高', '发布通知', '2023-01-18', '国家卫生健康委', 'https://www.nhc.gov.cn/sps/c100088/202301/f01895a06c5349ef999f25da833c166d.shtml'),
    ('nhc-hypertension-food-2023', '成人高血压食养指南（2023年版）', '高血压', '食养指南', '2023-01-18', '国家卫生健康委办公厅', 'https://www.nhc.gov.cn/cms-search/downFiles/63f752a17cfd4b4781f744477561866f.pdf'),
    ('nhc-lipids-food-2023', '成人高脂血症食养指南（2023年版）', '血脂异常', '食养指南', '2023-01-18', '国家卫生健康委办公厅', 'https://www.nhc.gov.cn/wjw/c100378/202301/7faed9993c9b45aea158c5335346f282/files/1732844468018_41219.pdf'),
    ('gdcdc-diabetes-food-2023', '成人糖尿病食养指南（2023年版）', '糖尿病', '食养指南官方转载', '2023-01-18', '国家卫生健康委办公厅；广东疾控托管', 'https://cdcp.gd.gov.cn/attachment/0/519/519695/4247361.pdf'),
    ('nhc-diabetes-food-qa-2023', '成人糖尿病食养指南（2023年版）问答', '糖尿病', '指南问答', '2023-01-18', '国家卫生健康委', 'https://www.nhc.gov.cn/wjw/c100378/202301/7faed9993c9b45aea158c5335346f282/files/1732844468167_10535.pdf'),
    ('beijing-cold-flu-2024', '流感和普通感冒有什么区别？', '普通感冒', '健康科普', '2024-01-12', '北京市卫生健康委员会；首都儿科研究所', 'https://wjw.beijing.gov.cn/bmfw_20143/jkzs/jbzs/202401/t20240115_3535496.html'),
    ('nhc-respiratory-20250117', '2025年1月17日新闻发布会文字实录', '普通感冒/流感', '新闻发布会科普，含其他主题', '2025-01-17', '国家卫生健康委', 'https://www.nhc.gov.cn/xcs/c100122/202501/4c0f023143ae4fd39b1a0339dd6bdecb.shtml'),
    ('beijing-gerd-2024', '带你了解胃食管反流病', '胃食管反流', '医院健康科普', '2024-11-01', '北京市卫生健康委员会；北京友谊医院', 'https://wjw.beijing.gov.cn/bmfw_20143/jkzs/jksh/202411/t20241101_3933102.html'),
    ('beijing-gerd-2023', '带您了解胃食管反流病', '胃食管反流', '医院健康科普', '2023-07-03', '北京市卫生健康委员会；北京友谊医院', 'https://wjw.beijing.gov.cn/bmfw_20143/jkzs/jbzs/202307/t20230706_3156764.html'),
]

class TextParser(HTMLParser):
    def __init__(self):
        super().__init__(); self.skip = 0; self.parts = []
    def handle_starttag(self, tag, attrs):
        if tag in ('script', 'style'): self.skip += 1
        if tag in ('p', 'div', 'br', 'li', 'h1', 'h2', 'h3'): self.parts.append('\n')
    def handle_endtag(self, tag):
        if tag in ('script', 'style'): self.skip = max(0, self.skip - 1)
    def handle_data(self, data):
        if not self.skip and data.strip(): self.parts.append(data.strip())

def main():
    from pypdf import PdfReader
    ROOT.mkdir(parents=True, exist_ok=True)
    records = []
    for sid, title, topic, kind, date, publisher, url in SOURCES:
        rec = dict(id=sid, title=title, topic=topic, type=kind, publication_date=date,
                   publisher=publisher, source_url=url, review_status='pending',
                   rights_status='公开可访问；未确认AI使用及再分发许可',
                   version_status='未核实是否最新；不自动声明替代关系',
                   retrieved_at=datetime.now().astimezone().isoformat())
        try:
            ext = '.pdf' if url.endswith('.pdf') else '.html'
            target = ROOT / (sid + ext)
            if target.exists():
                raw = target.read_bytes()
                rec['cache_used'] = True
            else:
                with urlopen(Request(url, headers={'User-Agent': 'CommonConditionsDemoCollector/0.1'}), timeout=20) as response:
                    raw = response.read(25_000_001)
                    rec['resolved_url'] = response.url
                if len(raw) > 25_000_000: raise ValueError('文件超过25MB限制')
                if ext == '.pdf' and not raw.startswith(b'%PDF-'): raise ValueError('响应不是PDF')
                if ext == '.html' and (b'<html' not in raw.lower() and b'<!doctype' not in raw.lower()): raise ValueError('响应不是HTML')
                target.write_bytes(raw)
            rec.update(file=target.name, bytes=len(raw), sha256=hashlib.sha256(raw).hexdigest(), download_status='ok')
            if ext == '.pdf':
                reader = PdfReader(target)
                pages = [{'page': i + 1, 'text': page.extract_text() or ''} for i, page in enumerate(reader.pages)]
                (ROOT / (sid + '.pages.json')).write_text(json.dumps(pages, ensure_ascii=False, indent=2), encoding='utf-8')
                text = '\n\n'.join('[PDF page %s]\n%s' % (p['page'], p['text']) for p in pages)
                rec['pages'] = len(pages)
            else:
                parser = TextParser(); parser.feed(raw.decode('utf-8', errors='replace'))
                text = '\n'.join(line.strip() for line in ''.join(parser.parts).splitlines() if line.strip())
                rec['extraction_note'] = '整页文本含导航和页脚，尚未审核正文范围'
            (ROOT / (sid + '.txt')).write_text(text, encoding='utf-8')
            rec['text_file'] = sid + '.txt'
            rec['text_characters'] = len(text)
            rec['extraction_status'] = 'pending_visual_and_medical_review'
        except Exception as exc:
            rec['error'] = str(exc)
            rec.setdefault('download_status', 'failed')
        records.append(rec)
        print(sid, rec['download_status'], rec.get('bytes', 0), rec.get('error', ''), flush=True)
        (ROOT / 'manifest.json').write_text(json.dumps(records, ensure_ascii=False, indent=2), encoding='utf-8')
    lines = ['# 常见病资料采集清单', '', '采集日期：2026-10-30。原文用于待审核资料整理，尚未导入问答数据库。', '',
             '食养指南不等于完整诊疗指南；科普不等于诊疗建议。糖尿病资料标题为成人糖尿病，不仅凭标题推断仅适用于2型。',
             '未确认最新版本、AI使用或再分发许可；PDF提取文本未完成视觉校对，表格和数字需对照原文。HTML提取含整页导航。',
             '不生成或自动发布医学主张，不自动建立 supersedes。下载失败不会绕过访问限制。', '',
             '| 主题 | 资料 | 类型 | 下载 | 文件 |', '| --- | --- | --- | --- | --- |']
    for r in records:
        lines.append('| %s | [%s](%s) | %s | %s | %s |' % (r['topic'], r['title'], r['source_url'], r['type'], r['download_status'], r.get('file', r.get('error', ''))))
    lines += ['', '## 后续审核', '', '1. 核对文件标题、正文、页码和发布信息。', '2. 检查现行版本、适用地区和人群，以及获取/使用权限。', '3. 提取候选主张并逐句绑定原文页码或章节。', '4. 医学审核通过后另行生成导入文件；当前应用不会自动读取本目录。', '', 'manifest.json 记录来源、SHA256、下载状态和待审核标记；*.pages.json 保留PDF物理页码。']
    (ROOT / 'README.md').write_text('\n'.join(lines) + '\n', encoding='utf-8')

if __name__ == '__main__':
    main()
