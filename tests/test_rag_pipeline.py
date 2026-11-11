import json
import unittest
from meditouch.rag_pipeline import OUT, SOURCE_DIR, DOC_ID, search


class RagPilotTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        if not (OUT/'chunks.json').exists():
            raise unittest.SkipTest('先运行 python -m meditouch.rag_pipeline build')
        cls.chunks = json.loads((OUT/'chunks.json').read_text(encoding='utf-8'))
        cls.pages = {p['page']: p['text'] for p in json.loads(
            (SOURCE_DIR/(DOC_ID+'.pages.json')).read_text(encoding='utf-8'))}

    def test_every_citation_is_exact_and_all_body_lines_are_covered(self):
        for c in self.chunks:
            self.assertFalse(c['generation_allowed'])
            self.assertEqual(c['review_status'], 'pending')
            for s in c['spans']:
                self.assertEqual(self.pages[s['page']][s['raw_start']:s['raw_end']], s['raw_quote'])
        # All non-empty source lines except the explicitly logged removals survive.
        log = json.loads((OUT/'cleaning_log.json').read_text(encoding='utf-8'))
        removed = {(s['page'], s['raw_start'], s['raw_end']) for s in log}
        kept = {(s['page'], s['raw_start'], s['raw_end']) for c in self.chunks for s in c['spans']}
        for page in range(3, 11):
            offset = 0
            for line in self.pages[page].splitlines(keepends=True):
                key = page, offset, offset + len(line)
                offset += len(line)
                if line.strip(): self.assertTrue(key in removed or key in kept)

    def test_cross_page_restriction_stays_with_salt_section(self):
        c = next(c for c in self.chunks if '减钠增钾' in c['section_path'][-1])
        self.assertIn(6, c['pdf_pages']); self.assertIn(7, c['pdf_pages'])
        self.assertIn('肾功能不全者补钾前应咨询医生', c['retrieval_text'])

    def test_no_appendix_and_no_unknown_match(self):
        self.assertTrue(all(3 <= p <= 10 for c in self.chunks for p in c['pdf_pages']))
        self.assertEqual(search('zzzzunknown', self.chunks), [])
        with self.assertRaises(ValueError): search(' ', self.chunks)


if __name__ == '__main__': unittest.main()
