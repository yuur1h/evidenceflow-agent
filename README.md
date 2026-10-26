# EvidenceFlow Agent

**证据驱动的研究助手 · 规划、检索、验证与报告生成**

面向需要跨来源检索和多步推理的问题，将研究过程组织成可观察的状态图，并保留证据、工具调用与执行日志。

> 基于 [MarsChange/Search Pilot](https://github.com/MarsChange/search-pilot) 的个人复现与扩展分支。当前版本 **0.3.0**：新增引用溯源核验、评测集与评测脚本，修复提前停止与重规划逻辑。离线回归通过；尚未用真实模型跑评测集，准确率与成本未测得。[版本记录](CHANGELOG.md) · [来源记录](UPSTREAM.md) · [完整上游文档](README.upstream.md)

## 工作流程

```text
用户问题 → 研究计划 → 状态图调度 → 并发检索与网页分析
                                ↓
                          证据记忆与覆盖检查
                                ↓
                         必要时重规划 → 研究报告
```

## 现有模块

| 模块 | 上游已有实现 |
| --- | --- |
| 规划与调度 | 研究状态依赖、并发执行、覆盖检查与重规划 |
| 证据存储 | SQLite 共享记忆、向量检索与上下文压缩 |
| 工具接入 | 搜索、网页解析及按配置启用的浏览器和沙箱工具 |
| 展示与接口 | FastAPI、SSE 事件流、研究过程可视化 |
| 评测 | JSONL 样本、LLM Judge、逐题记忆隔离和断点续跑 |

## 快速开始

```bash
git clone https://github.com/yuur1h/evidenceflow-agent.git
cd evidenceflow-agent
./dev.sh setup            # 需要 uv；按 uv.lock 创建 .venv（Python 3.12）
cp .env.template .env
# 编辑 .env，配置自己的模型与搜索服务密钥
./start-local.sh          # Windows 用 .\start-local.ps1
```

没有 uv 时可用 `python3.12 -m venv .venv && .venv/bin/pip install -r requirements.txt`。离线检查：`./dev.sh test`。模型支持 OpenAI（默认模板）、DashScope 或 DeepSeek。设置 `LLM_PROVIDER` 并填写对应密钥，详见 [OpenAI 配置](docs/openai-setup.md)；DeepSeek 只需 `LLM_PROVIDER="deepseek"` 和 `DEEPSEEK_API_KEY`，默认模型 `deepseek-chat`。联网检索按所选工具配置服务；缺少工具配置会影响能力。不要提交 `.env` 或密钥。

- 研究界面：`http://127.0.0.1:8000/visualize`
- API 文档：`http://127.0.0.1:8000/docs`
- 流式研究：`POST /stream`
- 评测：`POST /evaluate-stream`

Qwen 配置见 [Qwen 接入说明](docs/qwen-setup.md)。配置、请求样例与评测格式见 [上游运行文档](README.upstream.md)。本分支实际验证范围见 [复现记录](REPRODUCTION.md)。

## 已完成的改进

### 本地优化第一步：任务模式

研究页面新增任务类型选择；API 的 `task_mode` 支持：

| 值 | 任务 | 输出 |
| --- | --- | --- |
| `multi_hop`（默认） | 多跳线索问答 | 简洁答案，报告中保留 JSON 依据 |
| `fact_check` | 声明核查 | 包含支持、反驳证据和局限的 Markdown |
| `research` | 专题或比较研究 | 包含范围、分析、来源和局限的 Markdown |

`POST /`、`POST /stream`、`POST /ag-ui` 均可传入 `task_mode`。

```json
{"question":"比较两种储能技术的适用场景与局限", "task_mode":"research"}
```

旧请求和现有实体问答评测继续使用 `multi_hop`。模式需显式选择，当前没有自动分类或交互式澄清功能。事实核查和专题研究不使用实体短答案提前停止规则。

离线验证：`.\.venv\Scripts\python.exe -m pytest tests/test_task_modes.py -q`。
测试使用模拟模型和工具，仅验证流程与输出路由；真实模型效果仍待配置密钥后评测。

### 本地优化第二步：证据充分性

已区分 partial 与 success，增加有效来源与时效范围门槛，修复依赖阻塞时的补救流程。API 使用 `metadata.research_status` 和 `coverage_complete` 区分流程结束与研究完成；证据不足时明确返回未完成结果。详见 [第二步记录](docs/optimization-step-2.md)。

### 本地优化第三步：引用溯源核验与评测集

每条引用的 URL 必须出现在该节点的工具输出中，陈述与来源文本做重叠核验，弱支持条目由模型复判；未核验或不被支持的引用降级并不计入覆盖。同时修复了多跳提前停止不可达、兜底重规划丢弃冲突、记忆 ID 不稳定等问题，并加入 25 题评测集与 `evaluation/run_benchmark.py`。详见 [第三步记录](docs/optimization-step-3.md)。评测集尚未用真实模型运行。

以下为待办，不是已完成能力：

- [ ] 用真实模型与搜索服务跑通评测集，记录准确率、耗时与 Token 成本
- [ ] 置信度校准：用评测结果校正 `confidence` 与正确率的关系
- [ ] 引用核验从词元重叠升级为蕴含判断，并评测复判准确性
- [ ] 增加检索缓存，比较不同并发与重规划配置
- [ ] 支持本地 PDF 资料与网页证据联合检索

## 来源与许可

上游作者及贡献者：MarsChange / Search Pilot。保留 [MIT 许可证](LICENSE) 和提交历史。原参赛背景、成绩与实验数据见原始文档，不作为本分支成果。[完整来源记录](UPSTREAM.md)。
