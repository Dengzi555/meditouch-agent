"""Small live check. Never prints credentials, raw error bodies or prompts."""
import json
import os
import time
import urllib.request
import urllib.error
from pathlib import Path
from server import SEEDS, run_agent


def main():
    started = time.perf_counter()
    report = {'base_url':os.environ['MED_AGENT_BASE_URL'], 'requested_model':os.environ['MED_AGENT_MODEL']}
    request = urllib.request.Request(
        report['base_url'].rstrip('/')+'/chat/completions',
        data=json.dumps({'model':report['requested_model'], 'max_tokens':256,
                         'messages':[{'role':'user','content':'Reply with the single word OK.'}],
                         'enable_thinking':False}).encode(),
        headers={'Authorization':'Bearer '+os.environ['MED_AGENT_API_KEY'],'Content-Type':'application/json'})
    try:
        with urllib.request.urlopen(request,timeout=60) as response:
            body = json.load(response)
        report.update(connection='ok', returned_model=body.get('model'),
                      has_content=bool(body.get('choices',[{}])[0].get('message',{}).get('content')))
        result = run_agent('演示措施X存在什么分歧？', docs=SEEDS, use_model=True)
        report.update(rule_status=result['status'], engine=result['engine'],
                      evidence_generation_passed=result['error'] is None and result['engine']=='规则仲裁 + 模型辅助',
                      validation_scope='仅引用ID与格式；不代表医学或逐句语义验证')
    except urllib.error.HTTPError as error:
        report.update(connection='failed', http_status=error.code)
        # No response body or full exception: a server could echo sensitive request data.
    except Exception as error:
        report.update(connection='failed', error_type=type(error).__name__)
    report['elapsed_seconds'] = round(time.perf_counter()-started,2)
    path = Path(__file__).parent/'data/model-connection-check.json'
    path.write_text(json.dumps(report,ensure_ascii=False,indent=2),encoding='utf-8')
    print(json.dumps(report,ensure_ascii=False,indent=2))
    return 0 if report.get('evidence_generation_passed') else 1


if __name__ == '__main__':
    raise SystemExit(main())
