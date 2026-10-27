"""Provider selection without constructing clients or revealing credentials."""
import os


def selected_provider() -> str:
    provider = os.getenv("LLM_PROVIDER", "dashscope").strip().lower()
    if provider not in PROVIDERS:
        raise ValueError("LLM_PROVIDER must be one of " + ", ".join(PROVIDERS))
    return provider


# provider -> (key variable, model variable, default model, display name)
PROVIDERS = {
    "openai": ("OPENAI_API_KEY", "OPENAI_MODEL", "gpt-6-astra", "OpenAI LLM"),
    "dashscope": ("DASHSCOPE_API_KEY", "QWEN_MODEL", "qwen-max", "DashScope/Qwen LLM"),
    "deepseek": ("DEEPSEEK_API_KEY", "DEEPSEEK_MODEL", "deepseek-chat", "DeepSeek LLM"),
}


def key_variable() -> str:
    return PROVIDERS[selected_provider()][0]


def model_name() -> str:
    _, model_variable, default, _ = PROVIDERS[selected_provider()]
    return os.getenv(model_variable, "").strip() or default


def display_name() -> str:
    return PROVIDERS[selected_provider()][3]


def model_configured() -> bool:
    return bool(os.getenv(key_variable(), "").strip())
