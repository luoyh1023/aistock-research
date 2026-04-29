"""
多模型路由层 — 复用自 AITeam/fundamental-analyst，扩展了 prompt caching 支持。
支持两种模式：
- claude-code: 调用本地 `claude -p`（用 Claude Code 订阅额度，无需 API Key）
- api: 通过 LiteLLM 调用 Claude/GPT/DeepSeek 等（需对应 API Key）
"""

import os
import shutil
import subprocess
from dataclasses import dataclass

MODEL_PRESETS = {
    "claude-opus":   "anthropic/claude-opus-4-7",
    "claude-sonnet": "anthropic/claude-sonnet-4-6",
    "claude-haiku":  "anthropic/claude-haiku-4-5-20251001",
    "gpt-4o":        "openai/gpt-4o",
    "gpt-4o-mini":   "openai/gpt-4o-mini",
    "deepseek":      "deepseek/deepseek-chat",
}

CLAUDE_CODE_MODELS = {
    "claude-opus":   "claude-opus-4-7",
    "claude-sonnet": "claude-sonnet-4-6",
    "claude-haiku":  "claude-haiku-4-5-20251001",
}

DEFAULT_MODEL = "claude-sonnet"


@dataclass
class CompletionResult:
    content: str
    model: str
    input_tokens: int
    output_tokens: int
    cached_tokens: int = 0   # prompt cache 命中的 token 数


def _find_claude_cli() -> str:
    path = shutil.which("claude")
    if path:
        return path
    for candidate in [os.path.expanduser("~/.local/bin/claude"), "/usr/local/bin/claude"]:
        if os.path.isfile(candidate):
            return candidate
    return "claude"


def _complete_via_claude_code(prompt: str, system: str, model: str) -> CompletionResult:
    model_id = CLAUDE_CODE_MODELS.get(model, "claude-sonnet-4-6")
    full_prompt = f"{system}\n\n{prompt}" if system else prompt
    claude_bin = _find_claude_cli()

    result = subprocess.run(
        [claude_bin, "-p", "--model", model_id],
        input=full_prompt,
        capture_output=True,
        text=True,
        timeout=300,
    )
    if result.returncode != 0:
        detail = result.stderr.strip() or result.stdout.strip() or "(无输出)"
        raise RuntimeError(f"claude -p 调用失败 (code={result.returncode}): {detail}")

    return CompletionResult(content=result.stdout.strip(), model=f"claude-code/{model_id}",
                            input_tokens=-1, output_tokens=-1)


def _complete_via_api(prompt: str, system: str, model: str,
                      temperature: float, max_tokens: int) -> CompletionResult:
    import litellm
    litellm.set_verbose = False

    model_id = MODEL_PRESETS.get(model, model)
    messages = []
    if system:
        messages.append({"role": "system", "content": system})
    messages.append({"role": "user", "content": prompt})

    response = litellm.completion(model=model_id, messages=messages,
                                  temperature=temperature, max_tokens=max_tokens)
    usage = response.usage
    cached = getattr(usage, "prompt_tokens_details", {})
    cached_tokens = getattr(cached, "cached_tokens", 0) if cached else 0

    return CompletionResult(
        content=response.choices[0].message.content,
        model=model_id,
        input_tokens=usage.prompt_tokens,
        output_tokens=usage.completion_tokens,
        cached_tokens=cached_tokens,
    )


def complete(
    prompt: str,
    system: str = "",
    model: str = DEFAULT_MODEL,
    temperature: float = 0.3,
    max_tokens: int = 4096,
    backend: str = "auto",
) -> CompletionResult:
    """
    统一调用入口。
    backend: "auto" | "claude-code" | "api"
    auto 模式下 Claude 系列优先走 claude-code（免费），其他走 api。
    """
    if backend == "claude-code":
        use_cc = True
    elif backend == "api":
        use_cc = False
    else:
        use_cc = model in CLAUDE_CODE_MODELS

    if use_cc:
        return _complete_via_claude_code(prompt, system, model)
    return _complete_via_api(prompt, system, model, temperature, max_tokens)


def list_models() -> list[str]:
    return list(MODEL_PRESETS.keys())
