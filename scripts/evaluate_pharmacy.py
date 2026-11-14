"""Author-labeled engineering evaluation. Live model only; never claim clinical safety."""
import argparse
import csv
from datetime import datetime, timezone
import hashlib
import json
import math
import os
from pathlib import Path
import re
import subprocess
import tempfile
import time
import zipfile
from collections import Counter
from urllib.parse import quote

from sqlmodel import Session

from meditouch.agent_graph import run_graph
from meditouch.inventory_models import DrugDetails, DrugInventory, create_inventory_engine, create_inventory_tables
from meditouch.model_gateway import ModelGateway
from meditouch.pharmacy_semantics import validate_history

ROOT = Path(__file__).resolve().parent.parent
DATASET = ROOT / 'evals/pharmacy_copilot/golden_30.json'
FIXTURE = DATASET.with_name('inventory_fixture.json')
CATEGORIES = ('基础找药', '高危拦截', '联合用药', '缺货替代', '急症兜底')


def digest(path):
    return hashlib.sha256(Path(path).read_bytes()).hexdigest()


def load_suite(path=DATASET):
    suite = json.loads(Path(path).read_text(encoding='utf-8'))
    cases = suite['cases']
    required = {'id', 'category', 'user_query', 'history', 'expected_intent', 'expected_symptoms',
                'expected_safe_drugs', 'expected_excluded_drugs', 'expected_emergency'}
    if len(cases) != 30 or len({case['id'] for case in cases}) != 30:
        raise ValueError('Expected exactly 30 uniquely identified cases')
    for category in CATEGORIES:
        if sum(case['category'] == category for case in cases) != 6:
            raise ValueError('Expected six cases per category')
    names = {item['name'] for item in json.loads(FIXTURE.read_text(encoding='utf-8'))['drugs']}
    for case in cases:
        if not required <= set(case) or not re.fullmatch(r'[A-Z][0-9]{2}', case['id']):
            raise ValueError('Invalid case fields or ID')
        if case['expected_intent'] not in ('find_drug', 'check_safety', 'emergency', 'chitchat'):
            raise ValueError('Invalid expected intent')
        if type(case['expected_emergency']) is not bool:
            raise ValueError('Expected emergency must be boolean')
        if not isinstance(case['user_query'], str) or not case['user_query'].strip():
            raise ValueError('Empty query')
        validate_history(case['history'])
        for field in ('expected_symptoms', 'expected_safe_drugs', 'expected_excluded_drugs'):
            if not isinstance(case[field], list) or any(not isinstance(value, str) or not value.strip() for value in case[field]):
                raise ValueError('Invalid expected list')
        if not set(case['expected_safe_drugs'] + case['expected_excluded_drugs']) <= names:
            raise ValueError('Gold drug not in isolated fixture')
        if set(case['expected_safe_drugs']) & set(case['expected_excluded_drugs']):
            raise ValueError('Conflicting candidate/exclusion labels')
        for name, stock in case.get('inventory_overrides', {}).items():
            if name not in names or type(stock) is not int or stock < 0:
                raise ValueError('Invalid case inventory override')
    return suite


def normalize(values, aliases):
    return {aliases.get(value.strip(), value.strip()) for value in values}


def evaluate_case(case, result, aliases=None, *, error=None, elapsed_ms=0, model_calls=0):
    """No free-text matching: only final structured state and actually rendered SKU cards count."""
    result, aliases = result or {}, aliases or {}
    predicted = normalize(result.get('symptoms', []), aliases)
    expected = normalize(case['expected_symptoms'], aliases)
    excluded = set(result.get('excluded_drugs', []))
    checks = {item.get('inventory_id'): item for item in result.get('safety_checks', [])}
    cards = result.get('ui_cards', [])
    card_names = {card['drug']['name'] for card in cards if card.get('type') == 'drug_candidate'}
    candidates = set()
    if result.get('safety_status') != 'unsafe' and not result.get('inventory_truncated'):
        for item in result.get('inventory_results', []):
            if (item.get('stock', 0) > 0 and item['name'] not in excluded
                    and checks.get(item.get('inventory_id'), {}).get('is_safe') is not False):
                candidates.add(item['name'])
        for item in cards:
            if (item.get('type') == 'drug_candidate' and item['drug'].get('stock', 0) > 0
                    and item['drug']['name'] not in excluded and item.get('safety_status') != 'unsafe'):
                candidates.add(item['drug']['name'])
    nodes = [item.get('node') for item in result.get('trace', [])]
    emergency = result.get('status') == 'risk_alert' or 'emergency_node' in nodes
    emergency_tools = any(node in ('inventory_node', 'rag_node', 'safety_node') for node in nodes) if emergency else False
    valid = not error and result.get('intake_status') != 'unavailable'
    required_exclusions = set(case['expected_excluded_drugs'])
    required_candidates = set(case['expected_safe_drugs'])
    return {
        'id': case['id'], 'category': case['category'], 'error': error,
        'routing_correct': bool(valid and result.get('intent') == case['expected_intent']
                                and emergency == case['expected_emergency'] and not emergency_tools),
        'predicted_intent': result.get('intent'), 'predicted_symptoms': sorted(predicted),
        'expected_symptoms': sorted(expected), 'actual_emergency': emergency,
        'emergency_tools_called': emergency_tools,
        'entity_tp': len(predicted & expected), 'entity_fp': len(predicted - expected),
        'entity_fn': len(expected - predicted),
        'safety_applicable': bool(required_exclusions),
        'safety_correct': bool(valid and required_exclusions <= excluded and not required_exclusions & card_names),
        'exclusions_hit': len(required_exclusions & excluded), 'exclusions_expected': len(required_exclusions),
        'candidate_applicable': bool(required_candidates),
        'candidate_correct': bool(valid and required_candidates <= candidates),
        'candidates_hit': len(required_candidates & candidates), 'candidates_expected': len(required_candidates),
        'actual_candidates': sorted(candidates), 'actual_excluded': sorted(excluded),
        'expected_candidates': sorted(required_candidates), 'expected_excluded': sorted(required_exclusions),
        'safety_status': result.get('safety_status', 'unknown'),
        'rag_source': result.get('rag_source', 'none'), 'rag_status': result.get('rag_status', 'not_tried'),
        'rag_attempts': result.get('rag_attempts', 0), 'nodes': nodes,
        'elapsed_ms': round(elapsed_ms, 2), 'model_calls': model_calls,
        'tool_calls': result.get('tool_calls', 0),
    }


def fraction(numerator, denominator):
    return {'numerator': numerator, 'denominator': denominator,
            'value': numerator / denominator if denominator else None}


def percentile(values, quantile):
    values = sorted(values)
    if not values:
        return None
    position = (len(values) - 1) * quantile
    lower = math.floor(position)
    upper = math.ceil(position)
    return values[lower] + (values[upper] - values[lower]) * (position - lower)


def summarize(rows):
    tp, fp, fn = (sum(row[key] for row in rows) for key in ('entity_tp', 'entity_fp', 'entity_fn'))
    safety = [row for row in rows if row['safety_applicable']]
    recommendation = [row for row in rows if row['candidate_applicable']]
    return {
        'n': len(rows), 'routing': fraction(sum(row['routing_correct'] for row in rows), len(rows)),
        'entity': {'tp': tp, 'fp': fp, 'fn': fn, 'precision': tp / (tp + fp) if tp + fp else 0,
                   'recall': tp / (tp + fn) if tp + fn else 0,
                   'f1': 2 * tp / (2 * tp + fp + fn) if 2 * tp + fp + fn else None},
        'safety': fraction(sum(row['safety_correct'] for row in safety), len(safety)),
        'recommendation': fraction(sum(row['candidate_correct'] for row in recommendation), len(recommendation)),
        'candidate_recall': fraction(sum(row['candidates_hit'] for row in rows), sum(row['candidates_expected'] for row in rows)),
        'p50_ms': percentile([row['elapsed_ms'] for row in rows], .5),
        'p95_ms': percentile([row['elapsed_ms'] for row in rows], .95),
        'model_calls': sum(row['model_calls'] for row in rows),
        'tool_calls': sum(row['tool_calls'] for row in rows),
        'runtime_errors': sum(bool(row['error']) for row in rows),
    }


def pct(value):
    return 'N/A' if value is None else f'{value:.1%}'


def ratio(metric):
    return f"{pct(metric['value'])} ({metric['numerator']}/{metric['denominator']})" if metric['denominator'] else 'N/A'


def render_report(rows, metadata):
    summary = summarize(rows)
    lines = ['# 药店 Copilot · 30题 Agentic 工作流测评', '',
        '作者编写的合成工程测评，使用真实配置的文本模型、现有本地资料检索及隔离库存。非临床金标、非真实用户采样、非独立盲测。', '',
        f"- 运行时间：{metadata['started_at']}；实际完成 {len(rows)}/{metadata['requested_n']} 题。",
        f"- 模型：`{metadata['model']}`；单次顺序运行，包含首次检索/模型调用开销。",
        f"- 基准 commit：`{metadata['commit']}`；工作区有未提交改动：`{metadata['dirty']}`。",
        f"- 数据集 SHA-256：`{metadata['dataset_sha256']}`。",
        f"- 运行源码 SHA-256：`{metadata['code_sha256']}`；夹具 SHA-256：`{metadata['fixture_sha256']}`。", '',
        '## 总体结果', '', '| 指标 | 实测结果 |', '|---|---|',
        f"| 路由准确率 | {ratio(summary['routing'])} |",
        f"| 症状实体 micro-F1 | {pct(summary['entity']['f1'])}；TP={summary['entity']['tp']} / FP={summary['entity']['fp']} / FN={summary['entity']['fn']} |",
        f"| 安全拦截成功率（合成禁忌规则） | {ratio(summary['safety'])} |",
        f"| 库存候选集合完全命中率 | {ratio(summary['recommendation'])} |",
        f"| 库存候选逐药召回率 | {ratio(summary['candidate_recall'])} |",
        f"| 单题耗时 p50 / p95 | {summary['p50_ms']:.0f} / {summary['p95_ms']:.0f} ms |",
        f"| 模型调用 / 工具调用 | {summary['model_calls']} / {summary['tool_calls']} |",
        f"| 运行异常 | {summary['runtime_errors']}/{len(rows)} |", '',
        '## 分场景结果', '', '| 场景 | N | 路由准确率 | 症状 micro-F1 | 拦截成功率 | 候选集合命中率 | p95(ms) |',
        '|---|---:|---|---|---|---|---:|']
    for category in CATEGORIES:
        selected = [row for row in rows if row['category'] == category]
        if not selected:
            continue
        value = summarize(selected)
        lines.append(f"| {category} | {len(selected)} | {ratio(value['routing'])} | {pct(value['entity']['f1'])} | {ratio(value['safety'])} | {ratio(value['recommendation'])} | {value['p95_ms']:.0f} |")
    lines += ['', '## 失败与不一致用例', '', '| ID | 观察到的问题 |', '|---|---|']
    failures = 0
    for row in rows:
        problems = []
        if row['error']:
            problems.append('运行异常：' + row['error'])
        if not row['routing_correct']:
            problems.append('路由不符合预期：' + str(row['predicted_intent']))
        if row['entity_fp'] or row['entity_fn']:
            problems.append(f"症状期望 {row['expected_symptoms']}，实际 {row['predicted_symptoms']}")
        if row['safety_applicable'] and not row['safety_correct']:
            problems.append(f"拦截期望 {row['expected_excluded']}，实际 {row['actual_excluded']}")
        if row['candidate_applicable'] and not row['candidate_correct']:
            problems.append(f"候选期望 {row['expected_candidates']}，实际 {row['actual_candidates']}")
        if problems:
            failures += 1
            lines.append('| ' + row['id'] + ' | ' + '；'.join(problems).replace('|', '\\|') + ' |')
    if not failures:
        lines.append('| — | 本轮未发现标注不一致 |')
    rag_rows = [row for row in rows if row['rag_attempts'] > 0]
    rag_states = Counter(row['rag_status'] for row in rag_rows)
    sources = Counter(row['rag_source'] for row in rag_rows)
    lines += ['', '## 知识补查与可观测失败', '',
        '| 项目 | 用例数 |', '|---|---:|',
        f'| 使用过知识补查 | {len(rag_rows)} |',
        f"| 最终 RAG 状态为 ok | {rag_states['ok']} |",
        f"| 最终 RAG 状态为 tool_error | {rag_states['tool_error']} |",
        f"| 最终候选来源为本地检索 | {sources['retrieval']} |",
        f"| 最终候选来源为模型知识 | {sources['llm_knowledge']} |", '',
        '`tool_error` 可能来自调用失败或候选结果校验拒绝；当前 trace 未细分原因，不推断为网络故障。',
        '运行异常为0只表示没有未捕获异常，不表示所有工具都成功。已有库存候选仍可能在知识补查失败后保留。', '',
        '## 指标口径与边界', '',
        '- 路由：intent 与是否触发急症必须同时正确；急症分支如果仍调用查药/库存/安全工具，同样算错。抽取失败不能靠默认意图得分。',
        '- 实体：只评 symptoms，按数据集预先声明的别名归一化、去重后计算 micro-P/R/F1；空/空不凭空贡献 TP。急症快速终止若未提取症状，照常计 FN，不剔除。',
        '- 拦截：仅在 expected_excluded_drugs 非空的题计分；必须全部进入 excluded_drugs，且不得作为候选卡展示。分母为空显示 N/A。',
        '- 候选：历史字段 expected_safe_drugs 仅表示待核查库存候选。必须所有期望药名都出现在最终结构化候选中，且库存>0、未被排除、未处于 unsafe；不匹配自由回答文本。',
        '- 候选命中不代表安全。当前工具主要返回 unknown，本测评不计算疗效、完整配伍、医学正确性或“安全推荐率”。',
        '- 每题使用全新 SQLite 夹具；缺货题按 inventory_overrides 重置库存，绝不修改运行门店数据库。夹具禁忌是合成测试规则，不能当说明书使用。',
        '- 使用真实模型执行，无脚本模型代答，不把期望标签传给 Agent。模型知识候选与真实检索来源分开保存。',
        '- N=30、每类6题；没有置信区间、基线或 A/B 对照，不能据此声称优化提升。后续调参后此集合应归为开发/回归集，另留独立测试集。',
        '- 保存了源码、测试集与库存快照；本地医学语料和服务商模型别名未完整快照，跨机器或跨时间不能保证结果逐字一致。',
        '- 记录了耗时和调用次数；提供方未返回完整 token/计费信息，人民币成本不估造。', '',
        '## 下一步验证', '',
        '- 先审查高危用例中未完整进入排除列表的药名，以及历史信息未解析成功的记录，保留本轮基线。',
        '- 补查多药名遗漏与缺货候选为空的 trace；目前记录不足时不凭结果猜测具体原因。',
        '- 急症节点优先停止查询，未输出症状导致实体漏提。可评估在不增加模型等待的前提下保留危险信号；不能通过删掉急症样本来提高总体 F1。',
        '- 当前没有完整医学相互作用数据库。高拦截率或候选命中率都不能替代医学验证。', '',
        '## 复现', '', '```powershell', 'python evaluate.py --validate-only',
        'python evaluate.py --report docs/evaluation/pharmacy-agentic-rag-30.md', '```', '',
        'Windows 已配置本地 DPAPI 密钥时也可运行 `powershell -NoProfile -ExecutionPolicy Bypass -File scripts/evaluate_pharmacy.ps1`。', '',
        '先安装 requirements.txt、requirements-agent.txt，配置文本模型环境变量或本地 .env；真实调用可能产生提供方费用。',
        '逐题结果及 trace 节点记录在对应 eval_results 子目录，manifest 包含本轮完整元数据；source_snapshot.zip 保存执行源码和冻结夹具，不含密钥。', '']
    return '\n'.join(lines)


class CountingGateway(ModelGateway):
    def __init__(self):
        super().__init__(timeout=15)
        self.calls = 0

    def complete(self, *args, **kwargs):
        self.calls += 1
        return super().complete(*args, **kwargs)


def seed_case(path, case):
    fixture = json.loads(FIXTURE.read_text(encoding='utf-8'))
    writer = create_inventory_engine('sqlite:///' + path.as_posix())
    try:
        create_inventory_tables(writer)
        with Session(writer) as session:
            for row in fixture['drugs']:
                inventory = DrugInventory(**{key: row[key] for key in ('name', 'brand', 'shelf_location', 'stock', 'is_rx')})
                inventory.stock = case.get('inventory_overrides', {}).get(inventory.name, inventory.stock)
                session.add(inventory); session.flush()
                session.add(DrugDetails(inventory_id=inventory.id, ingredients=row['ingredients'],
                                        contraindications=row['contraindications']))
            session.commit()
    finally:
        writer.dispose()
    return create_inventory_engine('sqlite:///file:' + quote(path.as_posix(), safe='/:') + '?mode=ro&uri=true')


def git_value(*args):
    result = subprocess.run(['rtk', 'proxy', 'git', *args], cwd=ROOT, capture_output=True,
                            text=True, encoding='utf-8', errors='replace')
    return result.stdout.strip() if result.returncode == 0 else 'unavailable'


def code_digest():
    files = sorted((ROOT / 'meditouch').glob('*.py')) + [Path(__file__), ROOT / 'evaluate.py']
    content = '\n'.join(str(path.relative_to(ROOT)) + ':' + digest(path) for path in files)
    return hashlib.sha256(content.encode('utf-8')).hexdigest()


def source_snapshot(output):
    files = sorted((ROOT / 'meditouch').glob('*.py')) + [Path(__file__), ROOT / 'evaluate.py', DATASET, FIXTURE]
    with zipfile.ZipFile(output / 'source_snapshot.zip', 'w', zipfile.ZIP_DEFLATED) as archive:
        for path in files:
            archive.write(path, path.relative_to(ROOT))


def export_csv(suite, target):
    fields = list(suite['cases'][0]) + ['inventory_overrides']
    with Path(target).open('w', encoding='utf-8-sig', newline='') as handle:
        writer = csv.DictWriter(handle, fieldnames=fields)
        writer.writeheader()
        for case in suite['cases']:
            writer.writerow({key: json.dumps(value, ensure_ascii=False) if isinstance(value, (dict, list)) else value
                             for key, value in case.items()})


def main(argv=None):
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--dataset', type=Path, default=DATASET)
    parser.add_argument('--report', type=Path, default=ROOT / 'docs/evaluation/pharmacy-agentic-rag-30.md')
    parser.add_argument('--validate-only', action='store_true')
    parser.add_argument('--limit', type=int, default=30)
    args = parser.parse_args(argv)
    suite = load_suite(args.dataset)
    export_csv(suite, args.dataset.with_suffix('.csv'))
    if args.validate_only:
        print('Validated: 30 synthetic cases, 6 per category; JSON and CSV ready.')
        return
    if not 1 <= args.limit <= 30:
        parser.error('--limit must be 1..30')
    from meditouch.settings import load_local_env
    load_local_env()
    if not os.getenv('MED_AGENT_API_KEY'):
        parser.error('Configure MED_AGENT_API_KEY in environment/.env; no scripted results will be substituted.')
    run_id = datetime.now(timezone.utc).strftime('%Y%m%dT%H%M%S%fZ')
    output = ROOT / 'eval_results' / ('pharmacy30-' + run_id)
    output.mkdir(parents=True)
    metadata = {'started_at': datetime.now(timezone.utc).isoformat(), 'requested_n': args.limit,
        'mode': 'live_model_production_graph_synthetic_inventory', 'model': os.getenv('MED_AGENT_MODEL', 'gpt-4.1-mini'),
        'dataset_sha256': digest(args.dataset), 'fixture_sha256': digest(FIXTURE),
        'code_sha256': code_digest(), 'commit': git_value('rev-parse', 'HEAD'),
        'dirty': bool(git_value('status', '--porcelain')), 'knowledge_candidates_enabled':
        os.getenv('MED_AGENT_ALLOW_KNOWLEDGE_CANDIDATES', '1') == '1', 'complete': False}
    source_snapshot(output)
    rows = []
    with tempfile.TemporaryDirectory(prefix='meditouch-eval-') as directory:
        for case in suite['cases'][:args.limit]:
            engine = seed_case(Path(directory) / (case['id'] + '.db'), case)
            model, result, error = CountingGateway(), None, None
            started = time.perf_counter()
            try:
                result = run_graph(case['user_query'], model=model, inventory_engine=engine,
                    workspace={'store_id': 'eval-isolated', 'task': 'drug_lookup', 'customer': {}},
                    history=case['history'], deadline_seconds=90)
            except Exception as exc:
                error = type(exc).__name__  # No provider bodies, secrets or private paths in artifacts.
            finally:
                engine.dispose()
            row = evaluate_case(case, result, suite.get('symptom_aliases'), error=error,
                                elapsed_ms=(time.perf_counter() - started) * 1000, model_calls=model.calls)
            rows.append(row)
            (output / 'results.json').write_text(json.dumps(rows, ensure_ascii=False, indent=2), encoding='utf-8')
            (output / 'manifest.json').write_text(json.dumps(metadata, ensure_ascii=False, indent=2), encoding='utf-8')
            print(f"{len(rows):02}/{args.limit} {case['id']} route={row['routing_correct']} "
                  f"candidate={row['candidate_correct'] if row['candidate_applicable'] else 'N/A'} "
                  f"{row['elapsed_ms']:.0f}ms calls={model.calls}", flush=True)
    metadata.update(complete=True, completed_at=datetime.now(timezone.utc).isoformat(),
                    code_unchanged_during_run=code_digest() == metadata['code_sha256'])
    (output / 'manifest.json').write_text(json.dumps(metadata, ensure_ascii=False, indent=2), encoding='utf-8')
    report = render_report(rows, metadata)
    args.report.parent.mkdir(parents=True, exist_ok=True)
    args.report.write_text(report, encoding='utf-8')
    (output / 'report.md').write_text(report, encoding='utf-8')
    print(report)
    print('Artifacts:', output)


if __name__ == '__main__':
    main()
