# Qwen 配置

在 `.env` 中配置普通百炼 API Key（当前接口为北京地域）：

```dotenv
LLM_PROVIDER=dashscope
DASHSCOPE_API_KEY=填入自己的密钥
QWEN_MODEL=qwen3.8-27b
QWEN_ENABLE_THINKING=false
JUDGE_MODEL=
```

`JUDGE_MODEL` 留空时跟随主模型。可按账户额度将 `QWEN_MODEL` 改为 `qwen3.7-flash-2026-07-15` 或 `qwen3.8-flash`。当前不做自动模型回退，也无法读取账户免费额度；额度有效期、地域和模型名称需在百炼控制台确认。

`QWEN_ENABLE_THINKING=false` 为混合思考模型显式关闭思考，以兼容当前非流式模型调用和结构化输出。留空则不发送该参数，使用模型默认行为。变更配置后重启服务。

`401 invalid_api_key` 常见于密钥、地域或计费接口不匹配；`Arrearage` 表示账户欠费或余额状态限制。Coding/Token Plan 专属密钥不应填入这个自建后端的普通 API 配置。

官方参考：[API Key](https://help.aliyun.com/zh/model-studio/get-api-key)、[接口地址](https://help.aliyun.com/zh/model-studio/base-url)、[Chat API](https://help.aliyun.com/zh/model-studio/qwen-api-via-openai-chat-completions)。
