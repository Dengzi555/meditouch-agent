"""Versioned, read-only evidence contract shared by HTTP, MCP and LangGraph.

No API keys, implicit approval, model calls, or database writes here.
"""
import copy
import hashlib
import json
import os
import re
from math import isfinite
from concurrent.futures import ThreadPoolExecutor, wait
from contextlib import closing
from pathlib import Path
import sqlite3
from threading import BoundedSemaphore

from meditouch.rag_pipeline import OUT, SOURCE_DIR, search
from meditouch.hybrid_retrieval import hybrid_search
from meditouch.evidence_applicability import applicability_reason
from meditouch.query_plan import matching_intents, normalize_terms, requested_intents, retrieval_queries

CHILD_QUERY = re.compile(r'儿童|孩子|宝宝|婴幼儿|小儿|小孩|(?<!\d)(?:1[0-7]|[0-9])\s*岁|\d+\s*个月')


def _retry_respiratory_diet_search(question, items, hits, k):
    """Try a focused query once if the top hit misses an explicit dietary intent."""
    symptom = r'发热|发烧|感冒|流感|呼吸道感染'
    diet = r'饮食|补水|喝水|饮水|食物|吃饭'
    if not (re.search(symptom, question) and re.search(diet, question)):
        return hits, None
    if re.search(r'退烧药|用药|吃什么药|布洛芬|对乙酰氨基酚', question):
        return hits, None
    def focused(item):
        title = item.get('source', {}).get('title', '')
        if not re.search(symptom, title):
            return False
        if item.get('audience') == 'child':
            return bool(re.search(diet + r'|补充水分', item['text']))
        return bool(re.search(diet, title))

    if hits and focused(hits[0]):
        return hits, None
    expansion = ' 呼吸道感染期间 饮食 饮水 发热 居家护理'
    retry_query = question[:20000-len(expansion)] + expansion
    retry = search(retry_query, [item for item in items if focused(item)], k)
    retry_ids = {item['id'] for item in retry}
    return (retry + [hit for hit in hits if hit['id'] not in retry_ids])[:k], retry_query

VERSION = 'evidence.v1'
ROOT = Path(__file__).resolve().parent.parent


class EvidenceError(Exception):
    def __init__(self, code, retryable=False):
        self.code, self.retryable = code, retryable
        super().__init__(code)


def validate_request(question, k=3):
    if not isinstance(question, str) or not question.strip() or len(question) > 20000:
        raise ValueError('question 必须是 1—20000 字符的非空字符串')
    if type(k) is not int or not 1 <= k <= 8:
        raise ValueError('k 必须是 1—8 的整数')


def fingerprint(item):
    fields = {k: v for k, v in item.items() if k not in ('score', 'snapshot_sha256', 'retrieval_window')}
    return hashlib.sha256(json.dumps(fields, sort_keys=True, ensure_ascii=False).encode()).hexdigest()


def normalize_document(doc):
    """Legacy documents have no page locator; do not invent page numbers."""
    from meditouch.server import validate_doc
    validate_doc(doc)
    item = dict(id=doc['id'], document_id=doc['id'], text=doc['text'],
        raw_quote=doc['text'], source=dict(title=doc['title'], publisher=doc['publisher'],
        date=doc['date'], url=doc.get('source_url'), sha256=hashlib.sha256(doc['text'].encode()).hexdigest()),
        pdf_pages=[], printed_pages=[], spans=[], section_path=[],
        review_status=doc.get('review_status', 'pending'),
        generation_allowed=doc.get('generation_allowed') is True and doc.get('review_status') == 'approved',
        demo=doc.get('demo', False), claims=doc.get('claims', []),
        supersedes=doc.get('supersedes', []), quality_flags=['旧资料没有页码定位'], score=0.0)
    item['snapshot_sha256'] = fingerprint(item)
    return item


def scope_reason(item, question, context):
    population = context.get('population') or question
    if item.get('audience') == 'child' and not CHILD_QUERY.search(population):
        return 'population_not_established'
    if item.get('audience') == 'adult' and (
        CHILD_QUERY.search(population) or not re.search(r'成人|成年人|大人', population)
        or re.search(r'孕|哺乳', question + ' ' + ' '.join(context.values()))
    ):
        return 'population_not_established'
    if 'applicability_policy' in item:
        return applicability_reason(item, question, context)
    return 'unstructured_scope'


SUPPLEMENT_POOL = ThreadPoolExecutor(max_workers=3, thread_name_prefix='evidence-search')
SUPPLEMENT_SLOTS = BoundedSemaphore(12)
SUPPLEMENT_TIMEOUT = 3


def parallel_supplements(jobs, ranked):
    pending, states = {}, {}
    for intent, target, pool in jobs:
        if not SUPPLEMENT_SLOTS.acquire(blocking=False):
            states[intent] = 'busy'
            continue
        try:
            future = SUPPLEMENT_POOL.submit(ranked, target, pool, 2)
        except RuntimeError:
            SUPPLEMENT_SLOTS.release()
            states[intent] = 'error'
            continue
        future.add_done_callback(lambda _: SUPPLEMENT_SLOTS.release())
        pending[intent] = future
    done, _ = wait(pending.values(), timeout=SUPPLEMENT_TIMEOUT)
    results = {}
    for intent, future in pending.items():
        if future not in done:
            states[intent] = 'timeout'
            future.cancel()
            continue
        try:
            results[intent], _ = future.result()
        except Exception:
            states[intent] = 'error'
    return results, states


def adaptive_hits(question, query, candidates, context, query_hint=None, *, intent_states=None):
    intents = requested_intents(question)
    queries = retrieval_queries(query, intents)
    eligible = [item for item in candidates
                if scope_reason(item, question, context) in ('eligible', 'unstructured_scope')]
    enable_rerank = os.environ.get('MED_AGENT_RERANK') == '1'
    cutoff = os.environ.get('MED_AGENT_RERANK_MIN_SCORE', '').strip() if enable_rerank else ''
    min_score = float(cutoff) if cutoff else None
    if min_score is not None and not isfinite(min_score):
        raise ValueError('MED_AGENT_RERANK_MIN_SCORE must be finite')

    def ranked(term, pool, keep):
        hits, mode = hybrid_search(term, pool, 5 if enable_rerank else keep)
        if enable_rerank and hits and (len(hits) > 1 or min_score is not None):
            from meditouch.reranking import rerank
            try:
                hits = rerank(term, hits, min_score)
                mode += '+rerank'
                if min_score is not None:
                    mode += '+threshold'
            except (ImportError, OSError, RuntimeError, ValueError):
                mode += '+rerank_fallback'
        return hits[:keep], mode

    first, mode = ranked(queries[0], eligible, 3)
    remaining = set(intents)
    chosen = []
    states = intent_states if intent_states is not None else {}

    def cover(hits, stage):
        for hit in hits:
            covered = remaining.intersection(matching_intents(hit, question, intents))
            if covered:
                chosen.append(hit)
                remaining.difference_update(covered)
                states.update(dict.fromkeys(covered, stage))

    cover(first, 'initial')
    executed = queries[:1]
    alternate = query_hint if query_hint and query_hint != query else normalize_terms(query)
    if alternate == query:
        alternate = ''
    if remaining and os.environ.get('MED_AGENT_PARALLEL_SUPPLEMENT', '1') == '1':
        missing_order = [intent for intent in intents if intent in remaining]
        pools = {intent: [] for intent in missing_order}
        seen = {hit['id'] for hit in first}
        for item in eligible:
            if item['id'] not in seen:
                for intent in matching_intents(item, question, missing_order):
                    pools[intent].append(item)
        jobs = []
        for intent in missing_order:
            if not pools[intent]:
                continue
            focus = retrieval_queries(query, [intent])
            target = ' '.join(dict.fromkeys(filter(None, [alternate, focus[-1]])))[:20000]
            jobs.append((intent, target, pools[intent]))
            executed.append(target)
        results, failures = parallel_supplements(jobs, ranked)
        states.update(failures)
        for intent in missing_order:
            if intent in remaining:
                cover(results.get(intent, []), 'supplement')
    elif remaining:
        focus = retrieval_queries(query, [intent for intent in intents if intent in remaining])
        target = ' '.join(dict.fromkeys(filter(None, [alternate, *focus[1:]]))) or query
        target = target[:20000]
        seen = {hit['id'] for hit in first}
        # Apply evidence-purpose filtering to the second pass before ranking.
        focused = [item for item in eligible if item['id'] not in seen and
                   remaining.intersection(matching_intents(item, question, intents))]
        if focused:
            found, _ = ranked(target, focused, 2)
            executed.append(target)
            cover(found, 'supplement')
    elif not intents and alternate:
        found, _ = ranked(alternate, eligible, 3)
        executed.append(alternate)
        merged = {hit['id']: hit for hit in first[:1] + found + first[1:]}
        return list(merged.values())[:3], mode, executed, intents, [], first
    for intent in remaining:
        states.setdefault(intent, 'missing')
    return (chosen if intents else first), mode, executed, intents, sorted(remaining), first


class EvidenceService:
    def __init__(self, database=ROOT/'data/agent.db', chunks_path=OUT/'chunks.json',
                 source_dir=SOURCE_DIR, items=None, web_source_dir=None, reference_paths=None,
                 review_path=None):
        self.database = Path(database)
        self.chunks_path = Path(chunks_path)
        self.source_dir = Path(source_dir)
        # Isolated/custom PDF corpora must not silently acquire unrelated local data.
        from meditouch.web_knowledge import SOURCE_DIR as WEB_SOURCE_DIR
        self.web_source_dir = (Path(web_source_dir) if web_source_dir is not None else
                              WEB_SOURCE_DIR if self.chunks_path == OUT/'chunks.json' and
                              self.source_dir == SOURCE_DIR else None)
        self._injected = copy.deepcopy(items) if items is not None else None
        self.review_path=review_path
        from meditouch.reference_ingestion import SOURCE as REF_SOURCE, OUT as REF_OUT
        self.reference_paths = reference_paths if reference_paths is not None else (
            (REF_SOURCE,REF_OUT) if self.chunks_path==OUT/'chunks.json' and self.source_dir==SOURCE_DIR else None)

    def all_items(self, corpus='real'):
        if corpus not in ('real', 'demo'):
            raise ValueError('corpus 仅支持 real 或 demo')
        if self._injected is not None:
            return copy.deepcopy([i for i in self._injected if bool(i.get('demo')) == (corpus == 'demo')])
        if corpus == 'demo':
            try:
                with closing(sqlite3.connect(self.database.resolve().as_uri()+'?mode=ro', uri=True)) as c:
                    docs = [json.loads(r[0]) for r in c.execute('SELECT body FROM docs ORDER BY id')]
                return [normalize_document(d) for d in docs if d.get('demo') is True]
            except (OSError, sqlite3.Error, ValueError, TypeError, KeyError):
                raise EvidenceError('CORPUS_UNAVAILABLE') from None
        try:
            chunks = json.loads(self.chunks_path.read_text(encoding='utf-8'))
            manifest = {d['id']:d for d in json.loads((self.source_dir/'manifest.json').read_text(encoding='utf-8'))}
            page_cache = {}
            items = []
            for c in chunks:
                doc_id = c['document_id']
                # IDs come from local index, but must not become arbitrary paths.
                if not isinstance(doc_id, str) or not doc_id or any(x in doc_id for x in ('/', '\\', '..')):
                    raise ValueError('invalid document id')
                if doc_id not in page_cache:
                    meta = manifest[doc_id]
                    source_file = Path(meta['file'])
                    if source_file.name != meta['file']: raise ValueError('invalid source filename')
                    actual_hash = hashlib.sha256((self.source_dir/source_file).read_bytes()).hexdigest()
                    if actual_hash != meta['sha256']: raise ValueError('source file changed')
                    pages = json.loads((self.source_dir/(doc_id+'.pages.json')).read_text(encoding='utf-8'))
                    page_cache[doc_id] = {p['page']: p['text'] for p in pages}
                meta = manifest[doc_id]
                if c['source_sha256']!=meta['sha256'] or c['source_url']!=meta['source_url']:
                    raise ValueError('source metadata mismatch')
                spans = c['spans']
                if not spans: raise ValueError('missing locators')
                for s in spans:
                    raw = page_cache[doc_id][s['page']]
                    if (type(s['raw_start']) is not int or type(s['raw_end']) is not int or
                        not 0 <= s['raw_start'] < s['raw_end'] <= len(raw) or
                        raw[s['raw_start']:s['raw_end']] != s['raw_quote']):
                        raise ValueError('citation mismatch')
                if set(c['pdf_pages']) != {s['page'] for s in spans}:
                    raise ValueError('page mismatch')
                text = '\n'.join(s['raw_quote'].strip() for s in spans)
                if c['text'] != text: raise ValueError('chunk text mismatch')
                item = dict(id=c['id'], document_id=doc_id, text=text,
                    raw_quote=''.join(s['raw_quote'] for s in spans),
                    source=dict(title=meta['title'], publisher=meta.get('publisher'),
                        date=meta.get('publication_date'), url=c['source_url'], sha256=c['source_sha256']),
                    pdf_pages=c['pdf_pages'], printed_pages=sorted({s['printed_page'] for s in spans}),
                    spans=spans, section_path=c['section_path'],
                    review_status='approved' if c['review_status']=='approved' and meta['review_status']=='approved' else 'pending',
                    generation_allowed=c['generation_allowed'] is True and c['review_status']=='approved' and meta['review_status']=='approved',
                    claims=c.get('claims', []), supersedes=c.get('supersedes', []), demo=False,
                    quality_flags=c.get('quality_flags', []), score=0.0)
                if 'applicability_policy' in c:
                    item['applicability_policy'] = c['applicability_policy']
                item['snapshot_sha256'] = fingerprint(item)
                items.append(item)
            if self.web_source_dir is not None:
                from meditouch.web_knowledge import load_items
                items.extend(load_items(self.web_source_dir))
            if self.reference_paths is not None:
                from meditouch.reference_ingestion import load_items as load_references
                items.extend(load_references(*self.reference_paths))
            if self.chunks_path == OUT/'chunks.json' and self.source_dir == SOURCE_DIR:
                from meditouch.common_reference_pack import load_items as load_common_references
                from meditouch.symptom_reference_pack import load_items as load_symptom_references
                items.extend(load_common_references())
                items.extend(load_symptom_references())
            if self.review_path is not None:
                from meditouch.reviewed_content import attach_reviews
                items=attach_reviews(items,self.review_path)
            return items
        except FileNotFoundError:
            raise EvidenceError('CORPUS_UNAVAILABLE') from None
        except (OSError, ValueError, TypeError, KeyError):
            raise EvidenceError('CORPUS_INTEGRITY_FAILED') from None

    def search(self, question, k=3, corpus='real', *, adaptive=False, context=None, query_hint=None):
        validate_request(question, k)
        items = self.all_items(corpus)
        from meditouch.patient_context import normalize_cough_question
        query = question
        if corpus == 'real' and normalize_cough_question(question) == '咳嗽怎么办？':
            query = '咳嗽 居家观察 症状监测'
        candidates = items
        context = context or {}
        if corpus == 'real' and not CHILD_QUERY.search(context.get('population') or question):
            candidates = [item for item in items if item.get('audience') != 'child']
        if corpus == 'real' and adaptive:
            # Research-only snapshots are searchable via the evidence endpoint,
            # but must not displace answer evidence in the consultation pool.
            candidates = [item for item in candidates if not item.get('retrieval_only')]
            intent_states = {}
            hits, retrieval_mode, queries, intents, missing, initial = adaptive_hits(
                question, query, candidates, context, query_hint, intent_states=intent_states)
        elif corpus == 'real':
            intents = requested_intents(question)
            queries = retrieval_queries(query, intents)[:5]
            hits, retrieval_mode = hybrid_search(queries[0], candidates, k)
            if len(queries) > 1:
                pool = {item['id']: item for item in hits}
                for variant in queries[1:]:
                    found, _ = hybrid_search(variant, candidates, k)
                    for item in found:
                        pool.setdefault(item['id'], item)
                focused = {}
                for intent in intents:
                    for item in pool.values():
                        if item['id'] not in focused and matching_intents(item, question, [intent]):
                            focused[item['id']] = item
                            break
                focused.update({item_id: item for item_id, item in pool.items() if item_id not in focused})
                hits = list(focused.values())[:k]
        else:
            hits, retrieval_mode = search(query, candidates, k), 'bm25'
        retry_query = None
        if corpus == 'real' and (not adaptive or not intents):
            hits, retry_query = _retry_respiratory_diet_search(question, candidates, hits, k)
        # Retrieve all structured counterclaims / replacement versions with the same
        # explicit scope, even if they fell below top-k. Unstructured conflicts remain unknown.
        scopes = {(a['topic'], a['population'], a['condition'])
                  for i in (hits + initial if corpus == 'real' and adaptive else hits)
                  for a in i.get('claims', [])}
        ids = {i['id'] for i in hits}
        related = [i for i in items if i['id'] not in ids and any(
            (a['topic'], a['population'], a['condition']) in scopes for a in i.get('claims', []))]
        return dict(schema_version=VERSION, status='ok', query=question, corpus=corpus,
            items=hits, related_items=related,
            coverage={'retrieved':len(hits), 'related':len(related), 'indexed':len(items),
                      'retry_query':retry_query,
                      **({'queries':queries, 'intents':intents} if corpus == 'real' else {}),
                      **({'initial_candidates':len(initial), 'missing_intents':missing, 'intent_states':intent_states}
                         if corpus == 'real' and adaptive else {}),
                      'retrieval_mode':retrieval_mode,
                      'conflict_detection':'structured_claims_only'},
            limitations=['相关性不等于医学适用性；无结构化主张不代表无冲突。'])

    def get(self, evidence_id, corpus='real'):
        if not isinstance(evidence_id, str) or not evidence_id or len(evidence_id)>150:
            raise ValueError('无效 evidence_id')
        found = next((i for i in self.all_items(corpus) if i['id']==evidence_id), None)
        if found is None: raise EvidenceError('EVIDENCE_NOT_FOUND')
        return dict(schema_version=VERSION, status='ok', item=found)


def adjudicate(items):
    from meditouch.server import arbitrate
    docs = []
    for i in items:
        if not i.get('claims'): continue
        # Invalid/absent dates must never produce a newer-version decision.
        date = i['source'].get('date') or ''
        import time
        try: time.strptime(date, '%Y-%m-%d')
        except ValueError: date = ''
        claims = [a for a in i['claims'] if a.get('quote') and a['quote'] in i['text']]
        docs.append(dict(id=i['id'], text=i['text'], date=date, demo=i['demo'], claims=claims,
                         supersedes=i['supersedes'] if date else []))
    valid_dates = {d['id'] for d in docs if d['date']}
    for d in docs:
        d['supersedes'] = [target for target in d['supersedes'] if target in valid_dates]
    groups = arbitrate(docs)
    return {'groups':groups, 'unresolved':any(g['status']=='unresolved' for g in groups),
            'retired':sorted({x for g in groups for x in g['retired']}),
            'coverage':'仅覆盖显式结构化主张；不保证全文无冲突。'}


def validate_answer(draft, items, decision, corpus='real', *, question='', context=None):
    """Strict extractive MVP: no unverified paraphrase can become a medical claim."""
    if not isinstance(draft, dict) or set(draft) != {'status', 'citations'}:
        return {'ok':False, 'errors':['ANSWER_SCHEMA'], 'citations':[]}
    if draft['status'] not in ('answer', 'insufficient') or not isinstance(draft['citations'], list):
        return {'ok':False, 'errors':['ANSWER_SCHEMA'], 'citations':[]}
    by_id = {i['id']:i for i in items}
    errors, citations, applicability = [], [], {}
    context = context or {}
    intents = requested_intents(question) if corpus == 'real' else []
    intent_coverage = {}
    if decision['unresolved']: errors.append('UNRESOLVED_CONFLICT')
    if draft['status']=='answer' and not draft['citations']: errors.append('MISSING_CITATIONS')
    if draft['status']=='insufficient' and draft['citations']: errors.append('UNEXPECTED_CITATIONS')
    if len(draft['citations'])>5: errors.append('TOO_MANY_CITATIONS')
    for citation in draft['citations'][:5]:
        if not isinstance(citation, dict) or set(citation) not in ({'evidence_id'}, {'evidence_id','quote'}):
            errors.append('CITATION_SCHEMA'); continue
        i = by_id.get(citation['evidence_id']) if isinstance(citation['evidence_id'],str) else None
        if i is None: errors.append('UNKNOWN_CITATION'); continue
        if i['id'] in decision['retired']: errors.append('RETIRED_CITATION'); continue
        if i['demo'] and corpus!='demo': errors.append('DEMO_CITATION'); continue
        if i.get('retrieval_only'): errors.append('UNREVIEWED_CITATION'); continue
        quote = citation.get('quote', i['raw_quote'])
        # Full retrieved unit required, to prevent dropping a contraindication or qualifier.
        if not isinstance(quote, str) or quote != i['raw_quote']:
            errors.append('QUOTE_NOT_COMPLETE_OR_EXACT'); continue
        if fingerprint(i)!=i['snapshot_sha256']: errors.append('SNAPSHOT_CHANGED'); continue
        reason = scope_reason(i, question, context)
        applicability[i['id']] = reason
        if reason not in ('eligible', 'unstructured_scope'):
            errors.append('EVIDENCE_OUT_OF_SCOPE'); continue
        covered = matching_intents(i, question, intents)
        if intents and not covered:
            continue
        if covered:
            intent_coverage[i['id']] = covered
        citations.append({'evidence_id':i['id'],'quote':quote})
    if intents and draft['status'] == 'answer' and not citations:
        errors.append('EVIDENCE_DOES_NOT_ANSWER_INTENT')
    return {'ok':not errors, 'errors':sorted(set(errors)), 'citations':citations, 'applicability':applicability,
            'intent_coverage':intent_coverage,
            'scope':'完整原文摘录、引用身份、已标注人群和快照校验。'}
