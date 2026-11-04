"""Local, pending-review HTML evidence snapshots. No model calls or medical rules."""
import argparse
import hashlib
import json
import re
from datetime import datetime, timezone
from html import unescape
from html.parser import HTMLParser
from pathlib import Path
from urllib.request import Request, urlopen

ROOT = Path(__file__).parent
SOURCE_DIR = ROOT / 'knowledge_sources/common_respiratory_20261102'
SOURCES = [
    ('cdc-flu-faq-2026', '2026-06-22', 'https://www.chinacdc.cn/jkyj/mygh02/ymkyfjb/lg/202606/t20260622_1837279.html'),
    ('cdc-respiratory-care-2026', '2026-05-11', 'https://www.chinacdc.cn/jkkp/crb/qtcr/202605/t20260511_1835710.html'),
    ('cdc-flu-myths-2025', '2025-02-12', 'https://www.chinacdc.cn/jkkp/crb/bcr/202502/t20250212_304217.html'),
    ('cdc-rhinovirus-2026', '2026-01-09', 'https://www.chinacdc.cn/jkkp/crb/qtcr/202601/t20260109_314548.html'),
]


class ArticleParser(HTMLParser):
    """Extract articleCon only; ignore navigation, scripts and styles."""
    def __init__(self):
        super().__init__(convert_charrefs=True)
        self.depth = 0
        self.hidden = 0
        self.parts = []
        self.matches = 0

    def handle_starttag(self, tag, attrs):
        if tag == 'div':
            if self.depth:
                self.depth += 1
            elif dict(attrs).get('id') == 'articleCon':
                self.depth = 1
                self.matches += 1
        if self.depth:
            if tag in ('script', 'style'): self.hidden += 1
            if tag in ('p', 'div', 'br', 'li', 'h2', 'h3', 'h4'): self.parts.append('\n')

    def handle_endtag(self, tag):
        if self.depth:
            if tag in ('script', 'style'): self.hidden = max(0, self.hidden - 1)
            if tag in ('p', 'div', 'li', 'h2', 'h3', 'h4'): self.parts.append('\n')
            if tag == 'div': self.depth -= 1

    def handle_data(self, data):
        if self.depth and not self.hidden: self.parts.append(data)


def extract_article(raw):
    parser = ArticleParser()
    parser.feed(raw.decode('utf-8-sig'))
    if parser.matches != 1 or parser.depth: raise ValueError('article container missing or malformed')
    text = '\n'.join(line.strip() for line in ''.join(parser.parts).splitlines() if line.strip())
    if len(text) < 30: raise ValueError('empty article or non-article response')
    return text


def load_items(directory=SOURCE_DIR):
    """Re-extract exact local snapshots on read; never trust an edited cached quote."""
    from evidence_service import fingerprint
    directory = Path(directory)
    if not directory.exists(): return []  # optional pack not downloaded on this machine
    manifest = json.loads((directory / 'manifest.json').read_text(encoding='utf-8'))
    items, seen = [], set()
    for meta in manifest:
        if not re.fullmatch(r'[a-z0-9-]+', meta['id']) or meta['id'] in seen:
            raise ValueError('invalid or duplicate document id')
        seen.add(meta['id'])
        if meta['file'] != meta['id'] + '.html': raise ValueError('invalid source filename')
        if meta['source_url'] not in {s[2] for s in SOURCES}: raise ValueError('unrecognized source')
        raw = (directory / meta['file']).read_bytes()
        digest = hashlib.sha256(raw).hexdigest()
        if digest != meta['sha256']: raise ValueError('source snapshot changed')
        text = extract_article(raw)
        if hashlib.sha256(text.encode()).hexdigest() != meta['text_sha256']:
            raise ValueError('extraction changed')
        item = dict(id=meta['id'] + '-article', document_id=meta['id'],
            text=text, raw_quote=text,
            source=dict(title=meta['title'], publisher=meta['publisher'],
                        date=meta['publication_date'], url=meta['source_url'], sha256=digest),
            source_kind='html', locator={'selector':'#articleCon', 'extraction_version':'article-v1'},
            pdf_pages=[], printed_pages=[], section_path=[meta['title']],
            spans=[dict(raw_start=0, raw_end=len(text), raw_quote=text, coordinate='extracted_article_text')],
            review_status='pending', generation_allowed=False, demo=False,
            claims=[], supersedes=[], score=0.0,
            quality_flags=['未医学审核；仅原文预览', '网页正文定位，无PDF页码',
                           '科普文章不等于诊疗规范；时间性表述与用药、消毒、降温建议待逐项审核'])
        item['snapshot_sha256'] = fingerprint(item)
        items.append(item)
    return items


def collect(directory=SOURCE_DIR):
    directory = Path(directory)
    directory.mkdir(parents=True, exist_ok=True)
    manifest = []
    for doc_id, date, url in SOURCES:
        request = Request(url, headers={'User-Agent':'Mozilla/5.0'})
        with urlopen(request, timeout=25) as response:
            raw = response.read(2_000_001)
        if len(raw) > 2_000_000: raise ValueError('source too large')
        text = extract_article(raw)
        html = raw.decode('utf-8-sig')
        match = re.search(r'<h5[^>]*>(.*?)</h5>', html, flags=re.S)
        if not match: raise ValueError('missing article title')
        title = unescape(re.sub('<[^>]+>', '', match[1])).strip()
        if date not in html or not title: raise ValueError('source metadata mismatch')
        filename = doc_id + '.html'
        (directory / filename).write_bytes(raw)
        manifest.append(dict(id=doc_id, title=title, publisher='中国疾病预防控制中心',
            source_url=url, publication_date=date, file=filename,
            collected_at=datetime.now(timezone.utc).isoformat(),
            sha256=hashlib.sha256(raw).hexdigest(), text_sha256=hashlib.sha256(text.encode()).hexdigest(),
            review_status='pending', generation_allowed=False))
    (directory / 'manifest.json').write_text(json.dumps(manifest, ensure_ascii=False, indent=2), encoding='utf-8')
    return {'documents':len(manifest), 'generation_allowed':False, 'review_status':'pending'}


if __name__ == '__main__':
    parser = argparse.ArgumentParser()
    parser.add_argument('action', choices=['collect', 'check'])
    args = parser.parse_args()
    print(json.dumps(collect() if args.action == 'collect' else {'documents':len(load_items())}, ensure_ascii=True))
