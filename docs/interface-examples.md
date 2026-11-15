# 界面示例与截图复现

README中的界面示例使用临时库存、脚本模型和模拟视觉识别服务，展示网页交互流程。健康问答内容取自[NHS Common cold](https://www.nhs.uk/conditions/common-cold/)节选，模型输出在演示服务中固定。

截图经过 FastAPI、LangGraph、SSE 与 React 页面链路。示例商品以“演示药甲”“演示药乙”等名称标识，库存和会话保存在临时目录。

## 运行

安装可选的浏览器工具：

```powershell
.\.venv-agent\Scripts\python.exe -m pip install playwright
```

构建前端后，分别在两个终端执行：

```powershell
.\.venv-agent\Scripts\python.exe -m scripts.documentation_ui_fixture
```

```powershell
.\.venv-agent\Scripts\python.exe -m scripts.capture_documentation_ui
```

演示服务运行在8767端口，浏览器使用本机 Edge。截图保存至 `docs/images/`，涵盖商品查询、禁忌拦截、健康问答、拍照入库、重复提示、出入库、删除确认、应急记录和模型配置。

## 结果口径

截图用于说明页面与交互。检索与工作流性能分别记录在[检索评测](eval-report.md)和[工作流评测](evaluation/pharmacy-agentic-rag-30.md)中。
