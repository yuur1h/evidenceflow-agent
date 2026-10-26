# 本地复现记录

## 2026-10-27，macOS（0.3.0）

来源：yuur1h/evidenceflow-agent 的 Git 克隆。macOS 15（Apple Silicon），系统 Python 3.9 不满足要求，使用 uv 0.13 安装 Python 3.12 并按 `uv.lock` 建立 `.venv`（`./dev.sh setup`）。

已验证：`./dev.sh test` 通过（92 个测试 + smoke 检查）；`/visualize`、`/docs`、`/openapi.json` 返回 200；缺少密钥时 `/stream` 返回 Error 事件并结束。未配置任何模型或搜索密钥，没有联网运行；评测集未执行。

---

## 2026-09-16，Windows（0.2.0）

来源：yuur1h/evidenceflow-agent 的 main 分支源码 ZIP（不含 Git 历史）。

## 环境与启动

Windows，Python 3.12.14。`.venv` 复用桌面运行时的系统包；其余依赖按 requirements.txt 安装。实际版本见 requirements.local-lock.txt，因此该环境依赖本机 Python 运行时路径。

在本目录 PowerShell 中运行：

```powershell
.\start-local.ps1
```

访问 http://127.0.0.1:8000/visualize 。

## 已验证

- requirements.txt 安装完成，pip check 通过。
- /visualize、/docs、/openapi.json 返回 HTTP 200。
- 状态图依赖调度和 coverage 检查通过。
- 修复 /stream 后台异常未结束队列、请求无限等待的问题；缺少密钥时现返回 Error SSE 并结束。
- `python smoke_local.py` 可重复上述离线检查。

## 联网测试与当前限制

2026-09-16：普通百炼密钥配合北京接口成功调用 qwen-max（13 token）。一个禁用重规划和对抗复核的最小 Wikipedia 研究样例完成运行（3,398 token），但只得到候选条目，没有有效来源证据，最终正确标记为 partial。未宣称研究答案验证通过。

all-MiniLM-L6-v2 成功下载并加载，缓存位于 D 盘；语义检索质量仍待评测。OpenAI 适配通过模拟 SDK 传输测试，未做真实 OpenAI 联网验证。qwen3.8-27b 和两个 Flash 模型的请求被账户 Arrearage 错误阻断，未完成可用性验证。

搜索服务和 Jina 密钥未配置，工具能力受限。仓库未附带默认 test/data_with_answer.jsonl 数据集，因此没有准确率评测结果。引用与陈述的一致性检查仍未实现。

## 可重复检查

```powershell
python -m pytest tests -q
python smoke_local.py
```

测试使用模拟模型/工具，不需要真实密钥。smoke_local.py 临时清空所选提供商的密钥，验证错误流会正常结束，不发起真实模型调用。

## 版本管理

当前发布范围见 CHANGELOG.md 与 VERSION。保留原 GitHub 仓库提交历史；.env、日志、数据库、虚拟环境和本机依赖快照不进入版本库。requirements.local-lock.txt 仅是本机环境记录，包含运行时专属包，不是可移植安装锁文件。
