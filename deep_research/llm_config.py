"""Provider selection without constructing clients or revealing credentials."""
import os


def selected_provider() -> str:
    provider = os.getenv("LLM_PROVIDER", "dashscope").strip().lower()
    if provider not in {"openai", "dashscope"}:
        raise ValueError("LLM_PROVIDER must be openai or dashscope")
    return provider


def key_variable() -> str:
    return "OPENAI_API_KEY" if selected_provider() == "openai" else "DASHSCOPE_API_KEY"


def model_name() -> str:
    if selected_provider() == "openai":
        return os.getenv("OPENAI_MODEL", "").strip() or "gpt-6-astra"
    return os.getenv("QWEN_MODEL", "").strip() or "qwen-max"


def model_configured() -> bool:
    return bool(os.getenv(key_variable(), "").strip())
