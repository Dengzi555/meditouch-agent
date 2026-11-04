"""Minimal compatible Chat Completions adapter, with native tool calls.

Only env-provided credentials. Never expose server error bodies or exception text.
"""
import json
import os
import urllib.error
import urllib.request


class ModelError(Exception):
    pass


class ModelGateway:
    def __init__(self, timeout=35):
        self.timeout = timeout

    def complete(self, messages, tools=None, tool_choice='auto'):
        key = os.getenv('MED_AGENT_API_KEY')
        if not key: raise ModelError('MODEL_NOT_CONFIGURED')
        payload = dict(model=os.getenv('MED_AGENT_MODEL','gpt-4.1-mini'),
                       messages=messages, temperature=0, max_tokens=1600)
        if tools:
            payload.update(tools=tools, tool_choice=tool_choice, parallel_tool_calls=False)
        # This provider requires disabling thinking for bounded tool-use checks.
        if os.getenv('MED_AGENT_DISABLE_THINKING','1')=='1' and 'deepseek' in payload['model'].lower():
            payload['enable_thinking'] = False
        url = os.getenv('MED_AGENT_BASE_URL','https://api.openai.com/v1').rstrip('/')+'/chat/completions'
        req = urllib.request.Request(url, json.dumps(payload).encode(),
            {'Authorization':'Bearer '+key,'Content-Type':'application/json'})
        try:
            with urllib.request.urlopen(req, timeout=self.timeout) as response:
                data = json.loads(response.read(2_000_001))
            message = data['choices'][0]['message']
            if not isinstance(message, dict): raise ValueError()
            # Store only fields accepted by the next compatible completion request.
            return {k:message[k] for k in ('role','content','tool_calls') if k in message}
        except urllib.error.HTTPError as exc:
            raise ModelError('MODEL_HTTP_'+str(exc.code)) from None
        except Exception:
            raise ModelError('MODEL_UNAVAILABLE_OR_INVALID_RESPONSE') from None


def tool_schema(name, description, properties, required):
    return {'type':'function','function':{'name':name,'description':description,
        'parameters':{'type':'object','properties':properties,'required':required,'additionalProperties':False}}}


PING = tool_schema('ping','Return the supplied non-sensitive nonce to verify the tool roundtrip.',
                   {'nonce':{'type':'string'}},['nonce'])


def check_roundtrip(model=None):
    """Two real completions separated by an actual local tool execution."""
    import secrets
    model = model or ModelGateway()
    nonce = secrets.token_hex(6)
    messages = [{'role':'system','content':'Call ping with the nonce given by the user. After the tool result, reply only with its receipt.'},
                {'role':'user','content':'nonce='+nonce}]
    first = model.complete(messages,[PING],tool_choice={'type':'function','function':{'name':'ping'}})
    calls = first.get('tool_calls') or []
    if len(calls)!=1 or calls[0]['function']['name']!='ping': raise ModelError('PING_NOT_CALLED')
    call = calls[0]
    if json.loads(call['function']['arguments'])!={'nonce':nonce}: raise ModelError('PING_ARGUMENT_MISMATCH')
    # The receipt is created only after the model's first response.
    receipt = 'received-'+secrets.token_hex(8)
    observation = {'nonce':nonce,'receipt':receipt}
    messages += [first, {'role':'tool','tool_call_id':call['id'],'content':json.dumps(observation)}]
    final = model.complete(messages)
    ok = final.get('content','').strip()==receipt
    return {'ok':ok,'model_calls':2,'tool_calls':1,'tool_result_consumed':ok,
            'scope':'仅验证原生 tool_calls → 本地执行 → tool 结果 → 模型后续响应；非医学评测。'}
