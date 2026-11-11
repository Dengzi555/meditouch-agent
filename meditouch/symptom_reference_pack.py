"""Download and index NHS text for the ten real-symptom retrieval topics."""
import argparse
import json
import re
from datetime import date
from html.parser import HTMLParser
from pathlib import Path
from urllib.request import urlopen

from meditouch.reference_ingestion import digest

ROOT = Path(__file__).resolve().parent.parent
SOURCE = ROOT / 'knowledge_sources/symptom_references'
INDEX = ROOT / 'rag_data/symptom_references/chunks.json'
NHS = 'https://www.nhs.uk'
TERMS_URL = NHS + '/our-policies/terms-and-conditions/'
DOCUMENTS = (
    ('eyelid-swelling', '/symptoms/eyelid-problems/', '眼皮肿|眼睑肿|眼皮浮肿'),
    ('abdominal-pain', '/symptoms/stomach-ache/', '腹痛|肚子疼|肚子痛|胃痛'),
    ('headache', '/symptoms/headaches/', '头痛|头疼|脑袋疼'),
    ('ankle-swelling', '/conditions/oedema/', '脚踝肿|脚脖子肿|踝部水肿|脚肿'),
    ('common-cold', '/conditions/common-cold/', '普通感冒|感冒|伤风'),
    ('adult-fever', '/symptoms/fever-in-adults/', '发烧|发热|体温高'),
    ('pediatric-fever', '/symptoms/fever-in-children/', '儿童发热|孩子发烧|小孩发烧|宝宝发热|发烧|发热'),
    ('rash', '/conditions/hives/', '皮疹|风团|荨麻疹|起疹子'),
    ('knee-pain', '/symptoms/knee-pain/', '膝关节痛|膝盖疼|膝盖痛|膝疼'),
    ('chest-tightness', '/symptoms/chest-pain/', '胸闷|胸口发紧|胸口紧|胸痛'),
    ('gout', '/conditions/gout/', '痛风|痛风发作'),
    ('chickenpox', '/conditions/chickenpox/', '水痘|出水痘'),
    ('migraine', '/conditions/migraine/', '偏头痛|偏头疼'),
    ('sprains', '/conditions/sprains-and-strains/', '扭伤|崴脚|拉伤'),
    ('flu', '/conditions/flu/', '流感|流行性感冒'),
    ('sinusitis', '/conditions/sinusitis-sinus-infection/', '鼻窦炎|鼻窦感染'),
    ('tonsillitis', '/conditions/tonsillitis/', '扁桃体炎|扁桃体发炎'),
    ('contact-dermatitis', '/conditions/contact-dermatitis/', '接触性皮炎'),
    ('back-pain', '/conditions/back-pain/', '腰背痛|背疼|腰疼'),
    ('hypertension', '/conditions/high-blood-pressure-hypertension/', '高血压|血压偏高'),
)
HEADING_TERMS = (
    (r'medical help|advice:|action required', '就医 什么情况 危险信号'),
    (r'causes?|types of', '原因 怎么回事 常见原因'),
    (r'symptoms|check|what is', '症状 常见症状 有什么症状 有哪些表现 测量'),
    (r'ease|yourself|what to do|at home', '缓解 居家 护理 怎么办'),
    (r'prevent|avoid spreading', '预防 传播'),
)


class Article(HTMLParser):
    def __init__(self):
        super().__init__()
        self.active = False
        self.tag = None
        self.anchor = ''
        self.parts = []
        self.title = ''
        self.sections = []
        self.heading = 'Overview'
        self.section_anchor = ''
        self.blocks = []

    def flush(self):
        if self.blocks:
            self.sections.append((self.heading, self.section_anchor, '\n'.join(self.blocks)))
            self.blocks = []

    def handle_starttag(self, tag, attrs):
        if tag == 'main':
            self.active = True
        if self.active and tag in {'h1', 'h2', 'h3', 'p', 'li', 'th', 'td'} and not self.tag:
            self.tag = tag
            self.anchor = dict(attrs).get('id') or ''
            self.parts = []

    def handle_data(self, data):
        if self.tag:
            self.parts.append(data)

    def handle_endtag(self, tag):
        if tag == 'main':
            self.flush()
            self.active = False
        if self.tag != tag:
            return
        text = ' '.join(''.join(self.parts).split())
        self.tag = None
        if not text:
            return
        if tag == 'h1':
            self.title = text
        elif tag == 'h2':
            self.flush()
            self.heading, self.section_anchor = text, self.anchor
        else:
            self.blocks.append(text)


def build(download=False):
    from meditouch.evidence_service import fingerprint

    SOURCE.mkdir(parents=True, exist_ok=True)
    manifest_path = SOURCE / 'manifest.json'
    existing = {} if download else {m['id']: m for m in json.loads(manifest_path.read_text(encoding='utf-8'))}
    items, manifest = [], []
    for doc, path, aliases in DOCUMENTS:
        url = NHS + path
        filename = f'{doc}.html'
        snapshot = SOURCE / filename
        if download:
            with urlopen(url, timeout=12) as response:
                if not response.url.startswith(NHS + '/'):
                    raise ValueError(f'Unexpected source: {doc}')
                raw = response.read(2_000_001)
            if len(raw) > 2_000_000:
                raise ValueError(f'Oversized source: {doc}')
            retrieved = date.today().isoformat()
        else:
            raw = snapshot.read_bytes()
            if digest(raw) != existing[doc]['sha256']:
                raise ValueError(f'Snapshot changed: {doc}')
            retrieved = existing[doc]['retrieved_on']
        parser = Article()
        parser.feed(raw.decode('utf-8-sig'))
        if not parser.title or len(parser.sections) < 2:
            raise ValueError(f'Missing article sections: {doc}')
        if download:
            snapshot.write_bytes(raw)
        checksum = digest(raw)
        manifest.append(dict(id=doc, file=filename, title=parser.title, url=url, sha256=checksum,
                             retrieved_on=retrieved, license_url=TERMS_URL, review_status='pending'))
        for number, (heading, anchor, quote) in enumerate(parser.sections, 1):
            if len(quote) < 40 or re.search(r'page last reviewed|more in |related conditions|^video:', heading, re.I):
                continue
            section_url = url + '#' + anchor if re.fullmatch(r'[a-z0-9-]+', anchor) else url
            terms = ' '.join(words for pattern, words in HEADING_TERMS if re.search(pattern, heading, re.I))
            retrieval_text = f'{aliases.replace("|", " ")} {terms} {heading}'
            item = dict(id=f'nhs-symptom-{doc}-{number}', document_id=f'nhs-symptom-{doc}',
                        text=f'{retrieval_text}\n{quote}', retrieval_text=retrieval_text, raw_quote=quote,
                        source=dict(title=parser.title, publisher=f'Information from the NHS website · {retrieved}',
                                    date=retrieved, url=section_url, sha256=checksum),
                        source_kind='html', document_role='reference', pdf_pages=[], printed_pages=[],
                        spans=[dict(coordinate='extracted_html_section', section=number,
                                    raw_start=0, raw_end=len(quote), raw_quote=quote)],
                        section_path=[parser.title, heading], review_status='pending',
                        generation_allowed=False, retrieval_only=True, demo=False, claims=[], supersedes=[],
                        applicability_policy={'question_terms': aliases.split('|')},
                        quality_flags=['英文公开原文；待审核；英国就医流程不可直接套用'], score=0.0)
            if doc in ('adult-fever', 'pediatric-fever'):
                item['audience'] = 'adult' if doc == 'adult-fever' else 'child'
            item['snapshot_sha256'] = fingerprint(item)
            items.append(item)
    if download:
        manifest_path.write_text(json.dumps(manifest, ensure_ascii=False, indent=2), encoding='utf-8')
    INDEX.parent.mkdir(parents=True, exist_ok=True)
    temporary = INDEX.with_suffix('.tmp')
    temporary.write_text(json.dumps(items, ensure_ascii=False, indent=2), encoding='utf-8')
    temporary.replace(INDEX)
    return items


def load_items():
    if not INDEX.exists():
        return []
    from meditouch.evidence_service import fingerprint

    items = json.loads(INDEX.read_text(encoding='utf-8'))
    manifest = {m['id']: m for m in json.loads((SOURCE / 'manifest.json').read_text(encoding='utf-8'))}
    hashes = {doc: digest((SOURCE / f'{doc}.html').read_bytes()) for doc, _, _ in DOCUMENTS}
    for item in items:
        doc = item['document_id'].removeprefix('nhs-symptom-')
        if (hashes[doc] != manifest[doc]['sha256'] or hashes[doc] != item['source']['sha256']
                or item['snapshot_sha256'] != fingerprint(item)
                or item['review_status'] != 'pending' or item['generation_allowed']
                or not item.get('retrieval_only')):
            raise ValueError(f'Invalid symptom snapshot: {item["id"]}')
    return items


if __name__ == '__main__':
    cli = argparse.ArgumentParser()
    cli.add_argument('--download', action='store_true')
    print(f'Indexed {len(build(cli.parse_args().download))} public sections')
