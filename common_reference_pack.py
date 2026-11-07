"""Build and verify the curated Chinese common-cold and diarrhea evidence pack."""
import json
from pathlib import Path
import re

from reference_ingestion import digest, html_body

ROOT = Path(__file__).parent
SOURCE = ROOT / 'knowledge_sources/common_cold_and_diarrhea_references'
INDEX = ROOT / 'rag_data/common_cold_and_diarrhea/chunks.json'

# Explicit boundaries preserve complete recommendations and avoid indexing PDF
# tables, bibliography, and weight-based treatment regimens as patient guidance.
DOCUMENTS = (
    ('adult-common-cold-guideline-2023', 'common-cold/adult-common-cold-guideline-2023.pdf',
     '成人普通感冒诊断和治疗临床实践指南（2023）', '中国医师协会急诊医师分会急诊感染学组',
     '2023-03', 'adult', ['感冒'], (
         (10, '推荐意见14:', r'证据总\s*结:', '成人普通感冒复方制剂', 'medication'),
     )),
    ('nhc-child-infectious-diarrhea-2020', 'diarrhea/nhc-child-infectious-diarrhea-guideline.pdf',
     '儿童急性感染性腹泻病诊疗规范（2020年版）', '国家卫生健康委员会', '2020', 'child',
     ['腹泻', '拉肚子', '拉稀', '胃肠炎', '脱水'], (
         (6, '（二）入院标准。', '七、治疗', '入院标准', 'triage'),
         (6, '1.口服补液。', '患儿自腹泻开始', '口服补液原则', 'care'),
     )),
    ('beijing-child-gastroenteritis-2023', 'diarrhea/beijing-child-gastroenteritis-care.html',
     '孩子得了急性胃肠炎怎么办？', '北京市卫生健康委员会', '2023-07-25', 'child',
     ['腹泻', '拉肚子', '拉稀', '胃肠炎', '脱水'], (
         (None, '自己能采取哪些措施缓解症状？', '急性胃肠炎患者需要就诊吗？', '居家照护', 'care'),
         (None, '急性胃肠炎患者需要就诊吗？', '如何治疗？', '何时就诊', 'triage'),
     )),
)


def build():
    from pypdf import PdfReader
    from evidence_service import fingerprint

    sources = {item['file']: item for item in json.loads((SOURCE / 'sources.json').read_text(encoding='utf-8'))}
    items = []
    for doc, file, title, publisher, date, audience, terms, sections in DOCUMENTS:
        source = sources[file]
        path = SOURCE / file
        raw = path.read_bytes()
        if digest(raw) != source['sha256']:
            raise ValueError(f'Source checksum mismatch: {file}')
        pdf = PdfReader(path) if file.endswith('.pdf') else None
        for number, (page, start_marker, end_pattern, heading, intent) in enumerate(sections, 1):
            body = pdf.pages[page - 1].extract_text() if pdf else html_body(raw)
            start = body.index(start_marker)
            end = start + re.search(end_pattern, body[start:]).start()
            quote = body[start:end].rstrip()
            # PDF line breaks and inter-character spaces are layout artifacts.
            text = quote
            if pdf:
                text = re.sub(r'(?<=[\u3400-\u9fff])\s+(?=[\u3400-\u9fff])', '', quote)
                text = re.sub(r'\s*\n\s*', '', text)
            item = dict(id=f'{doc}-{number}', document_id=doc, text=text, raw_quote=quote,
                        source=dict(title=title, publisher=publisher, date=date,
                                    url=source['url'], sha256=source['sha256']),
                        source_kind='pdf' if pdf else 'html', document_role='reference',
                        pdf_pages=[page] if page else [], printed_pages=[],
                        spans=[dict(page=page, raw_start=start, raw_end=start + len(quote), raw_quote=quote,
                                    coordinate='extracted_pdf_page_text' if pdf else 'extracted_article_text')],
                        section_path=[title, heading], audience=audience, evidence_intents=[intent],
                        applicability_policy=dict(question_terms=terms), review_status='pending',
                        generation_allowed=False, demo=False, claims=[], supersedes=[],
                        quality_flags=['公开资料摘录，未医学审核'], score=0.0)
            item['snapshot_sha256'] = fingerprint(item)
            items.append(item)
    INDEX.parent.mkdir(parents=True, exist_ok=True)
    temporary = INDEX.with_suffix('.tmp')
    temporary.write_text(json.dumps(items, ensure_ascii=False, indent=2), encoding='utf-8')
    temporary.replace(INDEX)
    return items


def load_items():
    if not INDEX.exists():
        return []
    from evidence_service import fingerprint

    items = json.loads(INDEX.read_text(encoding='utf-8'))
    expected_ids = {f'{doc}-{number}' for doc, *_, sections in DOCUMENTS
                    for number in range(1, len(sections) + 1)}
    if len(items) != len(expected_ids) or {item['id'] for item in items} != expected_ids:
        raise ValueError('Missing or unexpected evidence sections')
    for document, file, *_ in DOCUMENTS:
        sha256 = digest((SOURCE / file).read_bytes())
        for item in (item for item in items if item['document_id'] == document):
            if item['source']['sha256'] != sha256 or item['snapshot_sha256'] != fingerprint(item):
                raise ValueError(f'Invalid evidence snapshot: {item["id"]}')
    return items


if __name__ == '__main__':
    print(f'Indexed {len(build())} reference sections.')
