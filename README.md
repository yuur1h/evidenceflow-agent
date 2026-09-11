# EvidenceFlow Agent

**证据驱动的研究助手 · 规划、检索、验证与报告生成**

面向需要跨来源检索和多步推理的问题，将研究过程组织成可观察的状态图，并保留证据、工具调用与执行日志。

> 基于 [MarsChange/Search Pilot](https://github.com/MarsChange/search-pilot) 的个人复现与扩展分支。当前完成展示文档与名称整理；核心能力来自上游，端到端运行与评测待验证。[来源记录](UPSTREAM.md) · [完整上游文档](README.upstream.md)

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
python3 -m venv .venv
source .venv/bin/activate
pip install -r requirements.txt
cp .env.template .env
# 编辑 .env，配置自己的模型与搜索服务密钥
python -m uvicorn agent:app --host 127.0.0.1 --port 8000
```

推荐 Python 3.12。模型需要 DashScope API Key，联网检索按所选工具配置服务；缺少工具配置会影响能力。不要提交 `.env` 或密钥。

- 研究界面：`http://127.0.0.1:8000/visualize`
- API 文档：`http://127.0.0.1:8000/docs`
- 流式研究：`POST /stream`
- 评测：`POST /evaluate-stream`

配置、请求样例与评测格式见 [上游运行文档](README.upstream.md)。以上命令尚未在本分支完成联网验证。

## 后续开发计划

以下为待办，不是已完成能力：

- [ ] 跑通最小样例，记录依赖版本和外部服务要求
- [ ] 建立独立评测集，记录准确率、耗时与 Token 成本
- [ ] 增加引用与答案陈述的一致性检查
- [ ] 增加检索缓存，比较不同并发与重规划配置
- [ ] 支持本地 PDF 资料与网页证据联合检索

## 来源与许可

上游作者及贡献者：MarsChange / Search Pilot。保留 [MIT 许可证](LICENSE) 和提交历史。原参赛背景、成绩与实验数据见原始文档，不作为本分支成果。[完整来源记录](UPSTREAM.md)。
