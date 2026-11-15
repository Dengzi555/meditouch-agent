"""Isolated browser-test server. Synthetic stock, scripted model, no paid calls.

Run: python -m scripts.pharmacy_ui_fixture. Never use this as the store server.
"""
import json
import os
from pathlib import Path
import tempfile
import time
from io import BytesIO
from PIL import Image

from sqlmodel import Session
import uvicorn

from meditouch.api import create_app
from meditouch.settings import SettingsStore
from meditouch.chat_storage import ChatStore
from meditouch.inventory_models import DrugInventory, DrugDetails, create_inventory_engine, create_inventory_tables


class ScriptedModel:
    def stream(self, messages):
        """Test-only delayed chunks verify actual partial UI before graph completion."""
        response = self.complete(messages)
        value = json.loads(messages[1]['content'])
        if 'source_mode' in value:
            answer = json.loads(response['content'])
            first = answer['sections'][0]
            yield '{"sections":[' + json.dumps(first, ensure_ascii=False) + ','
            time.sleep(1.5)
            yield json.dumps({**first, 'title': '继续了解'}, ensure_ascii=False)
            yield '],"follow_up":' + json.dumps(answer['follow_up'], ensure_ascii=False) + '}'
        else:
            yield response['content']

    def complete(self, messages):
        time.sleep(0.5)  # Test-only latency to exercise loading and cancellation.
        data = json.loads(messages[1]['content'])
        if 'claims' in data:
            return {'role': 'assistant', 'content': json.dumps({'supported': [True] * len(data['claims'])})}
        if 'source_mode' in data:
            result = {'sections': [{'title': '先确认已知信息', 'items': [{'text': '合成测试：先记录已知信息，再继续核对。',
                'highlights': ['记录已知信息'], 'citations': [{'source_id': 'qa-health', 'quote': '先记录已知信息，再继续核对。'}]}]}],
                'follow_up': {'question': '是否记录了测量结果？', 'options': ['已经记录', '尚未记录']}}
            return {'role': 'assistant', 'content': json.dumps(result, ensure_ascii=False)}
        if 'query' in data and ('健康问答测试' in data['query'] or data['query'].startswith('补充信息：')):
            result = {'intent': 'health_qa', 'symptoms': [], 'medical_topics': ['健康测试'],
                      'needs_inventory': False, 'knowledge_queries': []}
            return {'role': 'assistant', 'content': json.dumps(result, ensure_ascii=False)}
        result = ({'drug_name': data['query'], 'customer_tags': []} if 'query' in data else
                  {'script_id': next(iter(data['approved_scripts']))})
        return {'role': 'assistant', 'content': json.dumps(result)}


class ScriptedVision:
    def extract(self, data, mime):
        time.sleep(0.5)
        with Image.open(BytesIO(data)) as image:
            multiple = image.width == 640
        if multiple:
            return {'items': [
                {'name': '演示批量甲', 'brand': '品牌甲', 'is_rx': None,
                 'ingredients': '甲成分', 'specification': '规格甲'},
                {'name': '演示批量乙', 'brand': '品牌乙', 'is_rx': None,
                 'contraindications': '乙禁忌', 'specification': '规格乙'}], 'truncated': False}
        return {'name': '演示入库药', 'brand': None, 'is_rx': None,
                'ingredients': '图片提取的演示成分', 'contraindications': None}


def main():
    with tempfile.TemporaryDirectory(prefix='meditouch-browser-') as directory:
        path = Path(directory) / 'qa-stock.db'
        engine = create_inventory_engine('sqlite:///' + path.as_posix())
        create_inventory_tables(engine)
        with Session(engine) as session:
            first = DrugInventory(name='演示药甲', shelf_location='A1', stock=2)
            session.add(first)
            session.add(DrugInventory(name='演示药乙', shelf_location='3排2号', stock=5, is_rx=True))
            session.commit(); session.refresh(first)
            session.add(DrugDetails(inventory_id=first.id, contraindications='孕妇禁用（合成测试记录）'))
            session.commit()
        os.environ['MED_AGENT_INVENTORY_DB'] = str(path)
        os.environ['MED_AGENT_INVENTORY_STORE_ID'] = 'qa-store'
        try:
            settings = SettingsStore(Path(directory) / '.env', environ={
                'MED_AGENT_BASE_URL': 'https://example.com/v1',
                'MED_AGENT_MODEL': 'scripted-text', 'MED_AGENT_API_KEY': 'fixture-only-key',
                'MED_AGENT_VISION_MODEL': 'scripted-vision'})
            uvicorn.run(create_app(model_factory=ScriptedModel, vision_factory=ScriptedVision,
                                  health_evidence_loader=lambda *args, **kwargs: ([{'id': 'qa-health',
                                      'title': '合成健康问答夹具', 'publisher': '仅供工程测试，非医学资料',
                                      'url': 'https://medlineplus.gov/health-fixture.html', 'kind': 'public',
                                      'text': '先记录已知信息，再继续核对。'}], []),
                                  settings_store=settings, chat_store=ChatStore(Path(directory) / 'qa-chats.db')),
                        host='127.0.0.1', port=8767, log_level='warning')
        finally:
            engine.dispose()


if __name__ == '__main__':
    main()
