# OpenAI 接入

在项目 `.env` 中设置：

```env
LLM_PROVIDER="openai"
OPENAI_API_KEY="填写自己的 OpenAI API 密钥"
OPENAI_MODEL="gpt-6-astra"
OPENAI_MAX_OUTPUT_TOKENS=8192
OPENAI_REASONING_EFFORT=""
JUDGE_MODEL=""
```

保存后重启 `start-local.ps1`。密钥不要发到聊天、写进前端或提交 GitHub。模型名称可替换为账号支持的 Responses 文本模型；默认名称来自官方示例，并不保证每个账号都有访问权限。API 单独计费，当前未进行真实调用或成本/效果评测。

## 接入范围

- 主流程的规划、研究、压缩、报告和红蓝审阅通过统一工厂选择模型服务。
- 网页分析和 API/CLI 评测裁判使用相同提供商；JUDGE_MODEL 可覆盖裁判模型名称。
- OpenAI 使用 Responses API，显式使用官方地址，不读取第三方 OPENAI_BASE_URL；DashScope 保留 Chat Completions。
- Responses 的 input/output token 统计转换为项目已有的 prompt/completion token 字段；包括模型输出中的推理 token 用量。
- 本次不启用 OpenAI 内置搜索。联网检索仍通过现有 Serper/IQS、Wikipedia 等工具，Jina 负责网页读取。仅填写 OpenAI 密钥不会自动启用全部检索工具。
- 本次不迁移未被当前 FastAPI 主流程使用的旧 agent_loop/agui 实现，也不替换本地 embedding。

## 参数与错误

为避免将原 Qwen 的采样参数传给不支持它们的推理模型，OpenAI 适配层不传 temperature/top_p。推理强度可在 OPENAI_REASONING_EFFORT 中显式配置，留空使用模型默认设置。

单次输出上限默认 8192（包括推理 token），不是整次研究的费用上限。输出截断、失败或无文本会抛出错误，交给现有失败/partial 流程处理，不当作成功结果。默认 store=false，按官方数据政策处理，并不代表零数据保留。

`/env-status` 显示当前提供商、模型和密钥是否配置，不显示密钥本身。此状态不代表已验证密钥有效、账户余额或模型权限。

切回阿里云：设置 `LLM_PROVIDER="dashscope"`，使用原 DASHSCOPE_API_KEY / QWEN_MODEL；两种提供商之间不会自动回退或交换密钥。

## 验证

`python -m pytest tests -q` 使用模拟服务和 SDK 内存 HTTP transport，无需密钥，不访问网络。

官方参考：[Responses API](https://developers.openai.com/api/reference/python/resources/responses/methods/create)、[API 入门](https://developers.openai.com/api/docs/quickstart)。
