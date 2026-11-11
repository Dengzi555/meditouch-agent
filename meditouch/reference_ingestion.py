"""Deterministic, pending-review ingestion of downloaded respiratory references."""
import hashlib
import json
import re
from html.parser import HTMLParser
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
SOURCE = ROOT/'knowledge_sources/respiratory_reference_20261102'
OUT = ROOT/'rag_data/respiratory_reference'
VERSION = 'reference-sections-v1'


class BodyParser(HTMLParser):
    def __init__(self):
        super().__init__(convert_charrefs=True)
        self.depth=0; self.matches=0; self.hidden=0; self.parts=[]

    def handle_starttag(self, tag, attrs):
        if tag=='div':
            if self.depth: self.depth+=1
            elif 'TRS_UEDITOR' in dict(attrs).get('class','').split():
                self.depth=1; self.matches+=1
        if self.depth:
            if tag in ('script','style'): self.hidden+=1
            if tag in ('p','div','br','li','h2','h3','h4'): self.parts.append('\n')

    def handle_endtag(self, tag):
        if self.depth:
            if tag in ('script','style'): self.hidden=max(0,self.hidden-1)
            if tag in ('p','div','li','h2','h3','h4'): self.parts.append('\n')
            if tag=='div': self.depth-=1

    def handle_data(self, data):
        if self.depth and not self.hidden: self.parts.append(data)


def html_body(raw):
    parser=BodyParser();parser.feed(raw.decode('utf-8-sig'))
    if parser.matches!=1 or parser.depth: raise ValueError('missing or malformed article body')
    text='\n'.join(x.strip() for x in ''.join(parser.parts).splitlines() if x.strip())
    if len(text)<30: raise ValueError('empty body')
    return text


def digest(raw): return hashlib.sha256(raw).hexdigest()


def compile_items(source_dir=SOURCE):
    from meditouch.evidence_service import fingerprint
    directory=Path(source_dir)
    manifest=json.loads((directory/'manifest.json').read_text(encoding='utf-8'))
    items=[]; seen=set()
    for meta in manifest:
        if meta['status']!='downloaded': continue
        doc_id=meta['id']
        if not re.fullmatch('[a-z0-9-]+',doc_id) or doc_id in seen: raise ValueError('invalid or duplicate document id')
        seen.add(doc_id)
        filename=meta['file']
        if Path(filename).name!=filename or '/' in filename or '\\' in filename: raise ValueError('invalid path')
        raw=(directory/filename).read_bytes()
        if digest(raw)!=meta['sha256']: raise ValueError('source changed')
        blocks=[]
        if meta['kind']=='html':
            # Keep each short public article intact: do not detach contraindications
            # or exceptions from its advice merely to meet a token target.
            text=html_body(raw)
            blocks=[(meta['title'],[dict(raw_start=0,raw_end=len(text),raw_quote=text,
                                       coordinate='extracted_article_text')])]
        elif meta['kind']=='pdf':
            name=meta['extraction_file']
            if name!=filename+'.pages.json': raise ValueError('invalid extraction path')
            extracted=(directory/name).read_bytes()
            if digest(extracted)!=meta['extraction_sha256']: raise ValueError('extraction changed')
            pages=json.loads(extracted)
            if [p['page'] for p in pages]!=list(range(1,meta['pdf_pages']+1)):
                raise ValueError('missing or duplicate PDF pages')
            heading='方案引言'; spans=[]
            for page in pages:
                offset=0
                for line in page['text'].splitlines(keepends=True):
                    start=offset;offset+=len(line); stripped=line.strip()
                    if not stripped or stripped==str(page['page']): continue
                    if re.match(r'^[一二三四五六七八九十]+、',stripped):
                        if spans: blocks.append((heading,spans))
                        heading=stripped;spans=[]
                    spans.append(dict(page=page['page'],raw_start=start,raw_end=offset,
                                      raw_quote=line,coordinate='extracted_pdf_page_text'))
            if spans: blocks.append((heading,spans))
        else: raise ValueError('unknown source kind')
        for heading,spans in blocks:
            quote=''.join(s['raw_quote'] for s in spans)
            flags=['未医学审核；禁止生成正式医学建议', '版本有效性和适用人群待核验']
            if meta['kind']=='pdf': flags+=['PDF抽取顺序、表格、数值及剂量需对照原件审核']
            if len(quote)>3000: flags+=['长章节完整保留，未按长度截断；需审核后再细分']
            item=dict(id=doc_id+'-'+digest((VERSION+heading+quote).encode())[:12],
                document_id=doc_id,text=quote,raw_quote=quote,
                source=dict(title=meta['title'],publisher=meta['publisher'],date=meta['publication_date'],
                            url=meta['url'],sha256=meta['sha256']),
                source_kind=meta['kind'],document_role=meta['role'],
                pdf_pages=sorted({s['page'] for s in spans if 'page' in s}),printed_pages=[],
                spans=spans,section_path=[meta['title'],heading],
                locator={'selector':'.TRS_UEDITOR'} if meta['kind']=='html' else {'coordinate':'extracted_pdf_page_text'},
                review_status='pending',generation_allowed=False,demo=False,claims=[],supersedes=[],
                quality_flags=flags,score=0.0,processing_version=VERSION)
            if meta.get('audience') == 'child':
                item['audience'] = 'child'
            if 'applicability_policy' in meta:
                item['applicability_policy'] = meta['applicability_policy']
            item['snapshot_sha256']=fingerprint(item);items.append(item)
    return items


def build(source_dir=SOURCE,out=OUT):
    items=compile_items(source_dir);out=Path(out);out.mkdir(parents=True,exist_ok=True)
    (out/'chunks.json').write_text(json.dumps(items,ensure_ascii=False,indent=2),encoding='utf-8')
    queue=[dict(evidence_id=i['id'],document_id=i['document_id'],section=i['section_path'],
                pdf_pages=i['pdf_pages'],source=i['source'],characters=len(i['raw_quote']),
                review_status='pending',checks=['原文定位','适用人群与例外条件','版本有效性','医疗事实及数值'],
                quality_flags=i['quality_flags']) for i in items]
    (out/'review_queue.json').write_text(json.dumps(queue,ensure_ascii=False,indent=2),encoding='utf-8')
    lines=['# 待审核检索单元','', '| ID | 章节 | PDF物理页 | 字符数 | 状态 |','| --- | --- | --- | --- | --- |']
    lines += [f"| {i['id']} | {i['section_path'][-1]} | {','.join(map(str,i['pdf_pages'])) or '网页正文'} | {len(i['raw_quote'])} | pending |" for i in items]
    lines+=['','每项均须核对来源、定位、适用人群、否定及例外条件、版本、表格和数值。全部禁止正式回答生成。']
    (out/'REVIEW_QUEUE.md').write_text('\n'.join(lines)+'\n',encoding='utf-8')
    return {'documents':len({i['document_id'] for i in items}),'chunks':len(items),'approved':0}


def load_items(source_dir=SOURCE,out=OUT):
    out=Path(out)
    if not out.exists(): return []
    saved=json.loads((out/'chunks.json').read_text(encoding='utf-8'))
    expected=compile_items(source_dir)
    if saved!=expected: raise ValueError('index no longer matches source snapshots; rebuild required')
    return saved


if __name__=='__main__': print(json.dumps(build(),ensure_ascii=True))
