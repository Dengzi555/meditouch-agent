"""Real SDK stdio integration smoke test against an isolated synthetic database."""
import asyncio
import json
import sqlite3
import sys
import tempfile
from pathlib import Path
from mcp import ClientSession, StdioServerParameters
from mcp.client.stdio import stdio_client
from server import SEEDS


async def main():
    with tempfile.TemporaryDirectory() as temp:
        database = Path(temp) / 'test.db'
        with sqlite3.connect(database) as c:
            c.execute('CREATE TABLE docs(id TEXT PRIMARY KEY, body TEXT)')
            c.executemany('INSERT INTO docs VALUES (?, ?)', [(d['id'], json.dumps(d)) for d in SEEDS])
        c.close()
        before = database.read_bytes()
        params = StdioServerParameters(command=sys.executable, args=[
            str(Path(__file__).parent / 'medical_mcp.py'), '--db', str(database)],
            env={'PYTHONIOENCODING': 'utf-8'})
        async with stdio_client(params) as (read, write):
            async with ClientSession(read, write) as session:
                await session.initialize()
                listing = await session.list_tools()
                assert {t.name for t in listing.tools} == {
                    'search_evidence', 'analyze_evidence', 'get_evidence_source'}
                async def call(name, args):
                    result = await session.call_tool(name, args)
                    assert not result.isError, result
                    return json.loads(result.content[0].text)
                found = await call('search_evidence', {'question': '演示措施X'})
                assert found['count'] == 3
                answer = await call('analyze_evidence', {'question': '演示措施X'})
                assert answer['status'] == 'unresolved'
                assert answer['trace'][1]['result']['groups'][0]['retired'] == ['demo-old']
                source = await call('get_evidence_source', {'source_id': 'demo-new'})
                assert source['source']['id'] == 'demo-new'
                empty = await call('analyze_evidence', {'question': 'zzzzunknown'})
                assert empty['status'] == 'insufficient'
                for name, args in [('analyze_evidence', {'question': ''}),
                                   ('get_evidence_source', {'source_id': '../server.py'}),
                                   ('delete_documents', {})]:
                    error = await session.call_tool(name, args)
                    assert error.isError
        assert database.read_bytes() == before
        print('PASS: MCP initialize/list, 3 tools, conflict/retirement, insufficient, errors, read-only')


if __name__ == '__main__':
    asyncio.run(asyncio.wait_for(main(), timeout=45))
