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
    def complete(self, messages):
        time.sleep(0.5)  # Test-only latency to exercise loading and cancellation.
        data = json.loads(messages[1]['content'])
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
                                  settings_store=settings, chat_store=ChatStore(Path(directory) / 'qa-chats.db')),
                        host='127.0.0.1', port=8767, log_level='warning')
        finally:
            engine.dispose()


if __name__ == '__main__':
    main()
