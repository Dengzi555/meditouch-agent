"""Publish reproducible evaluation summaries from completed local runs."""
import hashlib
import json
from pathlib import Path
import zipfile

from scripts.eval_retrieval_ab import report_table
from scripts.evaluate_pharmacy import render_report, summarize

ROOT = Path(__file__).resolve().parent.parent
PUBLIC = ROOT / 'docs/evaluation'
RESULTS = ROOT / 'eval_results'


def read(path):
    return json.loads(path.read_text(encoding='utf-8-sig'))


def save(path, value):
    path.write_text(json.dumps(value, ensure_ascii=False, indent=2) + '\n', encoding='utf-8')


def freeze_retrieval(manifest, source, suite):
    """Verify the measured code still matches before exporting its source snapshot."""
    for name, checksum in manifest['runtime_code_sha256'].items():
        if hashlib.sha256((ROOT / name).read_bytes()).hexdigest() != checksum:
            raise ValueError('Measured source differs: ' + name)
    if hashlib.sha256(suite.read_bytes()).hexdigest() != manifest['suite_sha256']:
        raise ValueError('Measured suite differs: ' + str(suite))
    source.with_suffix('.suite.json').write_bytes(suite.read_bytes())
    with zipfile.ZipFile(source.with_suffix('.snapshot.zip'), 'w', zipfile.ZIP_DEFLATED) as archive:
        for name in manifest['runtime_code_sha256']:
            archive.write(ROOT / name, name)
        archive.write(suite, str(suite.relative_to(ROOT)))


def retrieval_report(manifest, title, scope):
    text = f"# {title}\n\n运行完成：`{manifest['completed_utc']}`。\n\n"
    text += report_table(manifest['summary']) + '\n\n## 评测配置\n\n'
    text += scope + '\n\n'
    text += (f"- 代码提交：`{manifest['git_head']}`；执行代码指纹保存在汇总JSON。\n"
             f"- 模型：`{manifest['embedding']}`、`{manifest['reranker']}`。\n"
             f"- 每题候选数量：Top-{manifest['k']}；预热后顺序运行{manifest['repeats']}轮。\n"
             '- 命中率按不同题目统计；P50/P95按全部计时请求统计，单位ms。\n'
             '- 重排输入为同一混合Top-5候选池，指标为段落检索命中与排序。\n')
    for heading, key in [('问法分组', 'by_kind'), ('问法分组', 'by_variant'), ('集合划分', 'by_split')]:
        if key in manifest:
            text += '\n## ' + heading + '\n'
            for name, values in manifest[key].items():
                text += '\n### ' + name + '\n\n' + report_table(values) + '\n'
    return text


def update_readme(index):
    readme = ROOT / 'README.md'
    text = readme.read_text(encoding='utf-8')
    start, end = text.index('## 测试与评测'), text.index('## 文档')
    real = index['retrieval_100']['summary']
    synthetic = index['retrieval_200']['summary']
    workflow = index['pharmacy_30']['summary']
    section = ('## 测试与评测\n\n```powershell\n'
        '.\\.venv-agent\\Scripts\\python.exe -m unittest discover -s tests -t .\n'
        'npm --prefix web test\nnpm --prefix web run build\n```\n\n'
        '自动化测试覆盖意图路由、工具调用、引用校验、流式协议、并发一致性、节点恢复和应急记录。\n\n'
        '| 评测集 | 运行日期 | 结果 | 报告 |\n'
        '| --- | --- | --- | --- |\n')
    section += (f"| 100题公开资料段落检索 | {index['retrieval_100']['completed_utc'][:10]} | "
        f"混合 Hit@5 {real[1]['hit_at_5']/100:.1%}；重排 Hit@1 {real[2]['hit_at_1']/100:.1%} | [检索评测](docs/eval-report.md) |\n")
    section += (f"| 200题合成检索回归 | {index['retrieval_200']['completed_utc'][:10]} | "
        f"混合 Hit@5 {synthetic[1]['hit_at_5']/200:.1%}；重排 Hit@1 {synthetic[2]['hit_at_1']/200:.1%} | [回归评测](docs/evaluation/retrieval-200.md) |\n")
    section += (f"| 30条药店工作流场景 | {index['pharmacy_30']['completed_utc'][:10]} | "
        f"路由 {workflow['routing']['value']:.1%}；拦截 {workflow['safety']['numerator']}/{workflow['safety']['denominator']}；"
        f"候选全命中 {workflow['recommendation']['numerator']}/{workflow['recommendation']['denominator']} | "
        '[工作流评测](docs/evaluation/pharmacy-agentic-rag-30.md) |\n\n'
        '[评测数据汇总](docs/evaluation/evaluation-summary.json)\n\n')
    readme.write_text(text[:start] + section + text[end:], encoding='utf-8')


def main():
    real = read(RESULTS / 'retrieval_ab_source.manifest.json')
    synthetic = read(RESULTS / 'retrieval_symptom_200.manifest.json')
    if not real.get('completed_utc') or not synthetic.get('complete'):
        raise ValueError('Retrieval run is incomplete')
    runs = sorted(RESULTS.glob('pharmacy30-*/manifest.json'))
    if not runs:
        raise ValueError('No workflow result')
    workflow_path = runs[-1]
    workflow = read(workflow_path)
    rows = read(workflow_path.with_name('results.json'))
    if not workflow.get('complete') or len(rows) != 30 or not workflow.get('code_unchanged_during_run'):
        raise ValueError('Workflow run incomplete or source changed during execution')
    if {real['summary'][0]['questions'], synthetic['summary'][0]['questions']} != {100, 200}:
        raise ValueError('Unexpected retrieval denominators')
    PUBLIC.mkdir(parents=True, exist_ok=True)
    freeze_retrieval(real, RESULTS / 'retrieval_ab_source.csv', ROOT / 'eval_data/real_retrieval_100.json')
    freeze_retrieval(synthetic, RESULTS / 'retrieval_symptom_200.csv', ROOT / 'eval_data/retrieval_suite_200.json')
    save(PUBLIC / 'retrieval-100-summary.json', real)
    save(PUBLIC / 'retrieval-200-summary.json', synthetic)
    workflow['summary'] = summarize(rows)
    save(PUBLIC / 'pharmacy-30-summary.json', {**workflow, 'cases': rows})
    real_report = retrieval_report(real, '公开医疗资料段落检索：100题',
        f"{len(real['by_topic'])}个主题、100个不同小节，直接与口语问法各50题；检索池包含{real['indexed_units']}个资料单元。"
        '问题按NHS公开小节编写，金标为精确段落ID，运行前核对原文SHA-256。金标由项目作者标注。')
    real_report += ('\n## 运行与产物\n\n'
        '```powershell\npython -m scripts.eval_retrieval_ab --repeats 3\n```\n\n'
        '需要本地公开资料快照、BGE与重排模型；依赖见 `requirements-agent.txt`。\n\n'
        '题集：`eval_data/real_retrieval_100.json`。逐题排名、耗时、语料和执行代码快照保存于 `eval_results/retrieval_ab_source.*`。'
        '公开汇总：[100题JSON](evaluation/retrieval-100-summary.json)。\n\n'
        '[200题合成检索回归](evaluation/retrieval-200.md) · [30题工作流评测](evaluation/pharmacy-agentic-rag-30.md)\n\n'
        '完成三套运行后，可执行 `python -m scripts.publish_evaluation_reports` 生成公开报告、JSON汇总与README结果表。\n')
    (ROOT / 'docs/eval-report.md').write_text(real_report, encoding='utf-8')
    synthetic_report = retrieval_report(synthetic, '合成检索回归：200题',
        '10类症状、30篇合成资料、120个段落；标准问法80题、口语60题、长尾60题。'
        '开发集160题、回归集40题，金标为每题对应的段落ID。')
    synthetic_report += ('\n## 运行与产物\n\n```powershell\n'
        'python -m scripts.eval_retrieval_symptom_200 --repeats 3\n```\n\n'
        '逐题排名、计时、代码及题集快照保存在 `eval_results/retrieval_symptom_200.*`。'
        '汇总：[200题JSON](retrieval-200-summary.json)。\n')
    (PUBLIC / 'retrieval-200.md').write_text(synthetic_report, encoding='utf-8')
    (PUBLIC / 'pharmacy-agentic-rag-30.md').write_text(render_report(rows, workflow), encoding='utf-8')
    index = {'retrieval_100': {'completed_utc': real['completed_utc'], 'summary': real['summary']},
             'retrieval_200': {'completed_utc': synthetic['completed_utc'], 'summary': synthetic['summary']},
             'pharmacy_30': {'completed_utc': workflow['completed_at'], 'summary': workflow['summary']}}
    save(PUBLIC / 'evaluation-summary.json', index)
    update_readme(index)
    print(json.dumps(index, ensure_ascii=False, indent=2))


if __name__ == '__main__':
    main()
