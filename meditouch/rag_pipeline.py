"""Offline evidence-retrieval pilot, not a medical answer generator."""
import argparse
import hashlib
import json
import math
import re
from collections import Counter
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
SOURCE_DIR = ROOT / 'knowledge_sources/common_conditions_20261030'
OUT = ROOT / 'rag_data/hypertension'
DOC_ID = 'nhc-hypertension-food-2023'
VERSION = 'section-pilot-v1'


def compact(text):
    return re.sub(r'\s+', '', text)


def tokens(text):
    return re.findall(r'[a-z0-9]+', text.lower()) + re.findall(
        r'(?=([\u4e00-\u9fff]{2}))', compact(text))


def heading(line):
    if re.match(r'^[一二三四五六七八九十]+、', line): return 1
    if re.match(r'^[（(][一二三四五六七八九十]+[）)]', line): return 2
    return 0


def build():
    pages_path = SOURCE_DIR / (DOC_ID + '.pages.json')
    pages = json.loads(pages_path.read_text(encoding='utf-8'))
    manifest = json.loads((SOURCE_DIR / 'manifest.json').read_text(encoding='utf-8'))
    source = next(r for r in manifest if r['id'] == DOC_ID)
    pdf_hash = hashlib.sha256((SOURCE_DIR / source['file']).read_bytes()).hexdigest()
    if pdf_hash != source['sha256']: raise ValueError('原文件校验值变化，需要重新提取与审核')
    chunks, section_path, spans = [], [], []
    log, page_records = [], []

    def flush():
        nonlocal spans
        if not spans: return
        # A parent heading alone is context for the next subsection, not a useful hit.
        if all(heading(s['text']) for s in spans): return
        text = '\n'.join(s['text'] for s in spans)
        cid = DOC_ID + '-' + hashlib.sha256((VERSION + text).encode()).hexdigest()[:12]
        chunks.append(dict(id=cid, document_id=DOC_ID, text=text,
            section_path=list(section_path), pdf_pages=sorted({s['page'] for s in spans}),
            spans=spans, source_url=source['source_url'], source_file=source['file'],
            source_sha256=pdf_hash, processing_version=VERSION,
            review_status='pending', generation_allowed=False,
            quality_flags=['未医学审核', '数值单位与脚注需对照原文'] +
                          (['第4页脚注在提取顺序中穿插正文，尚未重建关联'] if any(s['page'] == 4 for s in spans) else []) +
                          (['章节超过1200字符，保留完整并待后续切分'] if len(text) > 1200 else [])))
        spans = []

    for page in pages:
        number, raw = page['page'], page['text']
        if not 3 <= number <= 10: continue
        offset, cleaned_lines, removed_page_number = 0, [], False
        for line in raw.splitlines(keepends=True):
            start, end = offset, offset + len(line)
            offset = end
            stripped = line.strip()
            if not stripped: continue
            if not removed_page_number and stripped == str(number - 2):
                removed_page_number = True
                log.append({'page': number, 'operation': 'remove_leading_printed_page_number', 'raw_start': start, 'raw_end': end})
                continue
            if stripped == '成人高血压食养指南（2023 年版）':
                log.append({'page': number, 'operation': 'remove_repeated_document_title', 'raw_start': start, 'raw_end': end})
                continue
            level = heading(stripped)
            if level:
                flush()
                if level == 1: section_path = [stripped]
                else: section_path = section_path[:1] + [stripped]
            # Preserve original text exactly for citation offsets. Clean display only trims lines.
            span = dict(page=number, printed_page=number - 2,
                        raw_start=start, raw_end=end, raw_quote=raw[start:end], text=stripped)
            spans.append(span)
            cleaned_lines.append(stripped)
        page_records.append(dict(page=number, printed_page=number-2,
                                 text='\n'.join(cleaned_lines)))
    flush()
    # Retrieval representation removes layout line breaks; citation spans remain exact.
    for chunk in chunks:
        chunk['retrieval_text'] = compact(chunk['text'])
    OUT.mkdir(parents=True, exist_ok=True)
    for filename, obj in [('chunks.json', chunks), ('clean_pages.json', page_records), ('cleaning_log.json', log)]:
        (OUT / filename).write_text(json.dumps(obj, ensure_ascii=False, indent=2), encoding='utf-8')
    report = dict(processing_version=VERSION, source_sha256=pdf_hash,
        extraction_sha256=hashlib.sha256(pages_path.read_bytes()).hexdigest(),
        selected_pdf_pages=[3, 10], total_pdf_pages=len(pages), chunks=len(chunks),
        characters=sum(len(c['text']) for c in chunks), removed_items=len(log),
        min_chunk_chars=min(map(lambda c: len(c['text']), chunks)),
        max_chunk_chars=max(map(lambda c: len(c['text']), chunks)),
        excluded='封面目录第1—2页；附录第11—56页，含食谱表格，不纳入本轮索引',
        limitations=['未调用模型，未生成或发布医学主张',
                    '第4页脚注顺序及第8页BMI上标需人工核对，未自动修复',
                    '第6页图示未OCR；同页正文包含对应五项原则',
                    '章节整体索引；不为追求固定长度切断条件与例外',
                    '仅删除版式噪声；不猜测修复脚注、上标和数值',
                    '检索文本去空白用于匹配，引用必须使用raw_quote和原文页码',
                    '未验证该2023版本是否现行；未确认AI使用与再分发许可'])
    (OUT / 'build_report.json').write_text(json.dumps(report, ensure_ascii=False, indent=2), encoding='utf-8')
    return report


def search(question, chunks, k=3):
    if not isinstance(question, str) or not question.strip() or len(question) > 20000:
        raise ValueError('问题须为1—20000字符')
    if not chunks: return []
    counts = [Counter(tokens(c.get('retrieval_text', c['text']))) for c in chunks]
    lengths = [sum(c.values()) for c in counts]
    avg = sum(lengths) / len(lengths) or 1
    query = set(tokens(question))
    df = Counter(t for count in counts for t in count)
    ranked = []
    for chunk, count, length in zip(chunks, counts, lengths):
        score = 0
        for term in query:
            freq = count[term]
            if freq:
                idf = math.log(1 + (len(chunks)-df[term]+0.5)/(df[term]+0.5))
                score += idf * freq * 2.2 / (freq + 1.2 * (0.25 + 0.75 * length / avg))
        if score: ranked.append({**chunk, 'score': round(score, 6)})
    return sorted(ranked, key=lambda c: (-c['score'], c['id']))[:k]


if __name__ == '__main__':
    parser = argparse.ArgumentParser()
    parser.add_argument('action', choices=['build', 'search'])
    parser.add_argument('--question')
    args = parser.parse_args()
    if args.action == 'build': result = build()
    else:
        chunks = json.loads((OUT/'chunks.json').read_text(encoding='utf-8'))
        result = {'status': 'retrieval_preview_pending_review', 'generation_allowed': False,
                  'hits': search(args.question, chunks)}
    print(json.dumps(result, ensure_ascii=False, indent=2))
