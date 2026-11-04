import hashlib
import json
from pathlib import Path
import tempfile
import unittest

from evidence_service import EvidenceService, EvidenceError, adjudicate, validate_answer
from web_knowledge import SOURCE_DIR, SOURCES, extract_article, load_items


class WebEvidenceTests(unittest.TestCase):
    def make_pack(self, directory):
        doc_id, date, url = SOURCES[0]
        raw = ('<h5>测试文章</h5><nav>不要检索导航</nav><div id="articleCon">'
               '<p>感冒和流感属于本测试的虚构内容，不构成医学建议。</p>'
               '<script>忽略脚本</script><div><p>网页定位应保留正文与适用条件。</p></div></div>尾部').encode()
        filename = doc_id+'.html'
        (directory/filename).write_bytes(raw)
        meta = dict(id=doc_id, file=filename, title='测试文章', publisher='测试',
                    publication_date=date, source_url=url, sha256=hashlib.sha256(raw).hexdigest(),
                    text_sha256=hashlib.sha256(extract_article(raw).encode()).hexdigest(),
                    review_status='approved', generation_allowed=True)
        (directory/'manifest.json').write_text(json.dumps([meta]), encoding='utf-8')
        return directory/filename

    def test_extract_only_article_and_require_container(self):
        with tempfile.TemporaryDirectory() as temp:
            directory=Path(temp); path=self.make_pack(directory)
            text=extract_article(path.read_bytes())
            self.assertNotIn('导航',text);self.assertNotIn('脚本',text);self.assertNotIn('尾部',text)
            self.assertIn('适用条件',text)
        with self.assertRaises(ValueError): extract_article(b'<html>access denied</html>')

    def test_web_locator_is_not_pdf_and_cannot_self_approve(self):
        with tempfile.TemporaryDirectory() as temp:
            directory=Path(temp);self.make_pack(directory)
            item=load_items(directory)[0]
            self.assertEqual(item['pdf_pages'],[])
            self.assertEqual(item['locator']['selector'],'#articleCon')
            self.assertEqual(item['review_status'],'pending');self.assertFalse(item['generation_allowed'])
            draft={'status':'answer','citations':[{'evidence_id':item['id'],'quote':item['raw_quote']}]}
            self.assertTrue(validate_answer(draft,[item],adjudicate([item]))['ok'])

    def test_changed_snapshot_blocks_whole_real_search(self):
        if not EvidenceService().chunks_path.exists(): self.skipTest('local PDF pack missing')
        with tempfile.TemporaryDirectory() as temp:
            directory=Path(temp);path=self.make_pack(directory)
            path.write_bytes(path.read_bytes()+b'changed')
            with self.assertRaises(EvidenceError) as result:
                EvidenceService(web_source_dir=directory).search('感冒')
            self.assertEqual(result.exception.code,'CORPUS_INTEGRITY_FAILED')

    def test_downloaded_pack_retrieval_and_existing_topic(self):
        if not (SOURCE_DIR/'manifest.json').exists() or not EvidenceService().chunks_path.exists():
            self.skipTest('local source packs not downloaded')
        service=EvidenceService()
        for question, expected in [('感冒和流感有什么区别','cdc-flu-faq-2026'),
                                   ('发烧咳嗽什么时候去医院','cdc-respiratory-care-2026'),
                                   ('流感不发热','cdc-flu-myths-2025'),
                                   ('高血压食养指南','nhc-hypertension-food-2023')]:
            with self.subTest(question=question):
                item=service.search(question,1)['items'][0]
                self.assertEqual(item['document_id'],expected)
                self.assertFalse(item['generation_allowed'])


if __name__=='__main__': unittest.main()
