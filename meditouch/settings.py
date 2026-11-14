"""Local API settings: allowlisted values, redacted reads and atomic .env persistence."""
import json
import os
from pathlib import Path
import re
import tempfile
from threading import RLock
from urllib.parse import urlsplit

ROOT = Path(__file__).resolve().parent.parent
LOCK = RLock()
FIELDS = [
    ('MED_AGENT_BASE_URL', '接口地址', '文本模型', 'url', 'https://api.openai.com/v1'),
    ('MED_AGENT_MODEL', '模型名称', '文本模型', 'text', 'gpt-4.1-mini'),
    ('MED_AGENT_API_KEY', 'API Key', '文本模型', 'secret', ''),
    ('MED_AGENT_VISION_BASE_URL', '接口地址', '视觉识别', 'url', ''),
    ('MED_AGENT_VISION_MODEL', '模型名称', '视觉识别', 'text', ''),
    ('MED_AGENT_VISION_API_KEY', 'API Key', '视觉识别', 'secret', ''),
    ('MED_AGENT_NHS_API_KEY', 'NHS API Key', '公开知识与搜索', 'secret', ''),
    ('MED_AGENT_SHOWAPI_APP_KEY', '易源 App Key', '公开知识与搜索', 'secret', ''),
    ('MED_AGENT_SHOWAPI_DETAIL', '易源疾病详情接口', '公开知识与搜索', 'boolean', '0'),
    ('MED_AGENT_DEEPSEEK_SEARCH_KEY', '联网搜索 API Key', '公开知识与搜索', 'secret', ''),
    ('MED_AGENT_PUBLIC_KNOWLEDGE', '公开知识查询开关', '公开知识与搜索', 'boolean', '1'),
]
SPECS = {key: (label, group, kind, default) for key, label, group, kind, default in FIELDS}


def read_env(path):
    """Read single-line dotenv values without interpolation or executing any content."""
    if not path.exists():
        return {}, []
    if path.stat().st_size > 256_000:
        raise ValueError('配置文件过大，请先人工整理')
    lines = path.read_text(encoding='utf-8-sig').splitlines()
    result = {}
    for line in lines:
        match = re.match(r'^\s*(?:export\s+)?([A-Za-z_][A-Za-z0-9_]*)\s*=\s*(.*)$', line)
        if not match:
            continue
        key, raw = match.groups()
        raw = raw.strip()
        if raw.startswith('"'):
            try:
                value, end = json.JSONDecoder().raw_decode(raw)
            except ValueError:
                raise ValueError('配置文件含无法解析的双引号值，请先人工整理') from None
            if not isinstance(value, str) or raw[end:].strip() and not raw[end:].lstrip().startswith('#'):
                raise ValueError('配置文件格式不正确')
        elif raw.startswith("'"):
            end = raw.find("'", 1)
            if end < 0 or raw[end+1:].strip() and not raw[end+1:].lstrip().startswith('#'):
                raise ValueError('配置文件格式不正确')
            value = raw[1:end]
        else:
            value = re.split(r'\s+#', raw, maxsplit=1)[0].strip()
        result[key] = value
    return result, lines


def load_local_env(path=None):
    """Saved API settings override launcher defaults; other env values keep process precedence."""
    values, _ = read_env(Path(path) if path is not None else ROOT / '.env')
    for key, value in values.items():
        if key in SPECS:
            os.environ[key] = value
        elif key not in os.environ:
            os.environ[key] = value


class SettingsStore:
    def __init__(self, path=None, environ=None):
        self.path = Path(path) if path is not None else ROOT / '.env'
        self.environ = os.environ if environ is None else environ

    def snapshot(self):
        with LOCK:
            fields = []
            for key, label, group, kind, default in FIELDS:
                value = self.environ.get(key, default)
                fields.append({'key': key, 'label': label, 'group': group, 'kind': kind,
                               'configured': bool(value), 'value': '' if kind == 'secret' else value})
            return {'fields': fields, 'storage': '.env', 'plaintext': True,
                    'note': '这些是知识检索链路的可选接口；是否调用由当前执行图决定，保存配置不等于启用或验证接口。'}

    def save(self, updates):
        if not isinstance(updates, dict) or set(updates) - set(SPECS):
            raise ValueError('包含不支持的配置项')
        for key, value in updates.items():
            if not isinstance(value, str) or len(value) > 4096 or any(c in value for c in '\r\n\x00'):
                raise ValueError('配置值必须是单行文本，且不能超过4096字符')
            kind = SPECS[key][2]
            if value != value.strip():
                raise ValueError('配置值前后不能包含空格')
            if kind == 'boolean' and value not in ('0', '1'):
                raise ValueError('开关值只能为0或1')
            if kind == 'url' and value:
                try:
                    url = urlsplit(value)
                    _ = url.port
                    valid = url.scheme in ('http', 'https') and url.hostname and not (
                        url.username or url.password or url.query or url.fragment)
                except ValueError:
                    valid = False
                if not valid:
                    raise ValueError('接口地址须为 HTTP/HTTPS 地址，不能包含账号、密钥或查询参数')
        with LOCK:
            _, lines = read_env(self.path)
            # Migrate currently loaded API settings only on an explicit Save action.
            effective = {key: self.environ.get(key, spec[3]) for key, spec in SPECS.items()}
            effective.update(updates)
            kept = [line for line in lines if not any(re.match(
                r'^\s*(?:export\s+)?' + re.escape(key) + r'\s*=', line) for key in SPECS)]
            content = '\n'.join(kept).rstrip() + '\n\n# API settings managed by the local UI\n'
            content += '\n'.join(key + '=' + json.dumps(value, ensure_ascii=False)
                                 for key, value in effective.items()) + '\n'
            self.path.parent.mkdir(parents=True, exist_ok=True)
            temporary = None
            try:
                descriptor, temporary = tempfile.mkstemp(prefix='.env-', dir=self.path.parent)
                with os.fdopen(descriptor, 'w', encoding='utf-8', newline='\n') as file:
                    file.write(content)
                    file.flush()
                    os.fsync(file.fileno())
                os.chmod(temporary, 0o600)
                os.replace(temporary, self.path)
            finally:
                if temporary and os.path.exists(temporary):
                    os.unlink(temporary)
            self.environ.update(effective)
            return self.snapshot()
