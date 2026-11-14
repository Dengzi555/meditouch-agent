# Mock 库存初始化

```powershell
.\.venv-agent\Scripts\python.exe seed_data.py
```

默认写入独立的 `data/pharmacy_inventory_mock.db`，不修改 `data/pharmacy_inventory.db`。也可用 `--db` 指定独立演示库路径。脚本会创建两张表，用 SQLModel Session 在同一事务内关联插入，并使用 `flush()` 获取外键 ID。

| 药名 | 品牌 | 货架 | 库存 | 处方标记 | Mock 禁忌字段 |
| --- | --- | --- | --- | --- | --- |
| 布洛芬缓释胶囊 | 芬必得 | 3排2号 | 15 | false | NULL |
| 复方感冒灵颗粒 | 999 | 1排1号 | 10 | false | 孕妇禁用 |
| 板蓝根颗粒 | 白云山 | 2排3号 | 20 | false | NULL |
| 阿莫西林胶囊 | 联邦 | 5排1号 | 0 | true | NULL |

以上是按需求构造的演示记录，品牌、处方分类和禁忌均未据真实产品说明书核实。未提供的成分、规格保留为空，不推测复方成分。

## 幂等与隔离

- 按药名、品牌和货架定位已有条目，重跑跳过，不重置人工修改过的库存或详情。
- 详情缺失时补一条，通过 inventory_id 关联；不删除或清空任何表。
- 同一事务写入全部数据，并通过 SQLite 写锁避免两个 seed 进程同时重复插入。
- 演示库使用 SQLite application_id 标记。已有未标记数据或其他业务表的数据库会被拒绝，避免把 Mock 混进真实资料。
- 后端在配置接口、SSE 响应头和卡片中标识 Mock，前端显示演示提示。此标记不改变安全核查逻辑。

## 连接页面

```powershell
$env:MED_AGENT_INVENTORY_DB = (Resolve-Path 'data/pharmacy_inventory_mock.db').Path
$env:MED_AGENT_INVENTORY_STORE_ID = 'local-store'
.\.venv-agent\Scripts\python.exe server.py
```

刷新 `http://127.0.0.1:8765/`，确认显示“演示库存已连接”和“演示数据”。模型配置沿用原启动方式；没有模型时可直接输入药名片段进行字面查询。

| 输入与标签 | 预期表现 |
| --- | --- |
| 布洛芬 | 有货；禁忌缺失，黄色待核对，不能演示医学安全通过 |
| 颗粒 + 孕妇 | 复方感冒灵被红卡拦截；同一查询条件重查后，板蓝根仅为黄色待核对候选 |
| 板蓝根 | 有货；禁忌缺失，黄色待核对，不认定为孕妇安全替代药 |
| 阿莫西林 | 灰色缺货候选，同时保留处方药审核和禁忌未知提示 |

空禁忌绝不对应绿色。绿色渲染只应使用独立 UI 测试中明确设置的合成通过状态，不得通过修改这些库存记录绕过安全工具。

实现：`scripts/seed_data.py`，根目录 `seed_data.py` 是入口。测试：`python -m unittest tests.test_seed_data tests.test_api_sse -v`。
