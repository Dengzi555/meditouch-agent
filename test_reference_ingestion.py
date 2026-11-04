import hashlib
import json
from pathlib import Path
import tempfile
import unittest

from evidence_service import EvidenceService, EvidenceError
from reference_ingestion import SOURCE, OUT, build, compile_items, html_body, load_items


class ReferenceTests(unittest.TestCase):
    def make_pack(self,directory):
        raw=('<nav>不应检索的导航</nav><div class="TRS_UEDITOR"><p>测试科普，适用于虚构人群。</p>'
             '<p>不适用于条件B；出现情况C须进一步核实，不能删除这个限制条件。</p>'
             '<script>恶意脚本</script></div><footer>页脚</footer>').encode()
        file='test-source.html';(directory/file).write_bytes(raw)
        meta=dict(id='test-source',status='downloaded',file=file,kind='html',title='测试',publisher='测试',
                  publication_date='2026-01-01',url='https://example.invalid/test',role='patient_education',
                  sha256=hashlib.sha256(raw).hexdigest())
        (directory/'manifest.json').write_text(json.dumps([meta]),encoding='utf-8')
        return raw

    def test_article_retains_exceptions_and_rejects_non_article(self):
        with tempfile.TemporaryDirectory() as temp:
            source=Path(temp)/'source';source.mkdir();raw=self.make_pack(source)
            out=Path(temp)/'index';report=build(source,out);item=load_items(source,out)[0]
            self.assertEqual(report['chunks'],1);self.assertIn('不适用于条件B',item['raw_quote'])
            for noise in ['导航','脚本','页脚']:self.assertNotIn(noise,item['text'])
            self.assertEqual(item['pdf_pages'],[]);self.assertFalse(item['generation_allowed'])
        with self.assertRaises(ValueError):html_body(b'<html>access denied</html>')

    def test_tampered_index_or_source_cannot_be_silently_used(self):
        with tempfile.TemporaryDirectory() as temp:
            source=Path(temp)/'source';source.mkdir();self.make_pack(source)
            out=Path(temp)/'index';build(source,out)
            chunks=json.loads((out/'chunks.json').read_text(encoding='utf-8'));chunks[0]['generation_allowed']=True
            (out/'chunks.json').write_text(json.dumps(chunks),encoding='utf-8')
            with self.assertRaises(ValueError):load_items(source,out)
            build(source,out);(source/'test-source.html').write_bytes(b'changed')
            with self.assertRaises(ValueError):load_items(source,out)

    def test_downloaded_pdf_spans_and_review_queue(self):
        if not (SOURCE/'manifest.json').exists() or not (OUT/'chunks.json').exists():
            self.skipTest('downloaded references not available')
        items=load_items()
        self.assertIn('nhc-influenza-guideline-2025', {i['document_id'] for i in items})
        queue=json.loads((OUT/'review_queue.json').read_text(encoding='utf-8'))
        self.assertEqual({i['id'] for i in items},{q['evidence_id'] for q in queue})
        for i in items:
            self.assertEqual(i['raw_quote'],''.join(s['raw_quote'] for s in i['spans']))
            self.assertEqual(i['review_status'],'pending');self.assertFalse(i['generation_allowed'])
        pdfs=[i for i in items if i['source_kind']=='pdf']
        treatment=next(i for i in pdfs if i['section_path'][-1]=='十、治疗')
        self.assertIn(17,treatment['pdf_pages']);self.assertIn(8,treatment['pdf_pages'])
        pages=json.loads((SOURCE/'nhc-influenza-guideline-2025.pdf.pages.json').read_text(encoding='utf-8'))
        for i in pdfs:
            for span in i['spans']:
                self.assertEqual(pages[span['page']-1]['text'][span['raw_start']:span['raw_end']],span['raw_quote'])

    def test_fever_diet_retrieval_respects_child_scope(self):
        if not (OUT/'chunks.json').exists() or not EvidenceService().chunks_path.exists():
            self.skipTest('local source packs not downloaded')
        service = EvidenceService()
        diet_source = 'cq-respiratory-diet-20240118'
        child_source = 'beijing-child-fever-care-20261027'
        available = {item['document_id'] for item in service.all_items()}
        if not {diet_source, child_source} <= available:
            self.skipTest('new diet and child fever references not downloaded')
        diet = service.search('发烧时的饮食和补水建议是什么？')
        self.assertEqual(diet['items'][0]['document_id'], diet_source)
        self.assertFalse(diet['items'][0]['generation_allowed'])
        for query in ('发烧吃什么药', '28岁发热时怎么补水'):
            result = service.search(query, k=8)
            self.assertNotIn(child_source, {item['document_id'] for item in result['items']})
        child = service.search('孩子发热时怎么补水')
        self.assertIn(child_source, {item['document_id'] for item in child['items']})


if __name__=='__main__':unittest.main()
