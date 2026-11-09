"""Thin multimodal model client. Defaults to DeepSeek via its OpenAI-compatible API.

Any OpenAI-compatible endpoint with image input can be used by changing
--base-url / --model (or REEL2REPORT_BASE_URL / REEL2REPORT_MODEL).
"""

from __future__ import annotations

import base64
import json
import os
import re
import threading
import time
from pathlib import Path

DEFAULT_BASE_URL = "https://api.deepseek.com"
DEFAULT_MODEL = "deepseek-flash"


IMAGE_DETAIL = os.environ.get("REEL2REPORT_DETAIL", "auto")


def image_part(path: Path, detail: str | None = None) -> dict:
    data = base64.b64encode(path.read_bytes()).decode()
    url = {"url": f"data:image/jpeg;base64,{data}"}
    if (detail or IMAGE_DETAIL) != "auto":
        url["detail"] = detail or IMAGE_DETAIL
    return {"type": "image_url", "image_url": url}


def text_part(text: str) -> dict:
    return {"type": "text", "text": text}


def parse_json(text: str) -> dict:
    text = text.strip()
    fence = re.search(r"```(?:json)?\s*(.*?)```", text, re.S)
    if fence:
        text = fence.group(1).strip()
    try:
        return json.loads(text)
    except json.JSONDecodeError:
        start, end = text.find("{"), text.rfind("}")
        if start >= 0 and end > start:
            return json.loads(text[start : end + 1])
        raise


class OutputTruncated(RuntimeError):
    """The model hit max_tokens; callers can retry with a smaller request."""


class LLM:
    def __init__(self, model: str | None = None, base_url: str | None = None,
                 api_key: str | None = None, temperature: float = 0.2):
        from openai import OpenAI  # imported lazily so --no-ai works without it

        key = api_key or os.environ.get("DEEPSEEK_API_KEY") or os.environ.get("REEL2REPORT_API_KEY")
        if not key:
            raise RuntimeError("未设置 API key：请设置环境变量 DEEPSEEK_API_KEY。")
        self.model = model or os.environ.get("REEL2REPORT_MODEL") or DEFAULT_MODEL
        self.client = OpenAI(
            api_key=key,
            base_url=base_url or os.environ.get("REEL2REPORT_BASE_URL") or DEFAULT_BASE_URL,
        )
        self.temperature = temperature
        self._json_mode = True
        self._max_tokens_ok = True
        self._thinking_ok = True
        self.usage = {"prompt_tokens": 0, "completion_tokens": 0, "cache_hit_tokens": 0,
                      "reasoning_tokens": 0, "requests": 0, "images": 0}
        self._lock = threading.Lock()

    def chat_json(self, system: str, content: list[dict], retries: int = 3,
                  max_tokens: int | None = 8000, thinking: bool | None = False) -> dict:
        """thinking: None = provider default; False/True asks a reasoning model to skip/use
        its hidden reasoning (DeepSeek's `thinking` switch). Ignored by endpoints without it."""
        messages = [{"role": "system", "content": system},
                    {"role": "user", "content": content}]
        last_err: Exception | None = None
        for attempt in range(retries):
            kwargs = {"model": self.model, "messages": messages, "temperature": self.temperature}
            if self._json_mode:
                kwargs["response_format"] = {"type": "json_object"}
            if max_tokens and self._max_tokens_ok:
                kwargs["max_tokens"] = max_tokens
            if thinking is not None and self._thinking_ok:
                kwargs["extra_body"] = {"thinking": {"type": "enabled" if thinking else "disabled"}}
            try:
                resp = self.client.chat.completions.create(**kwargs)
            except Exception as e:  # noqa: BLE001
                msg = str(e).lower()
                if self._json_mode and "response_format" in msg:
                    self._json_mode = False  # endpoint lacks JSON mode; rely on the prompt
                    continue
                if self._thinking_ok and thinking is not None and "thinking" in msg:
                    self._thinking_ok = False  # endpoint has no thinking switch
                    continue
                if self._max_tokens_ok and "max_tokens" in msg:
                    self._max_tokens_ok = False  # use the endpoint's default output limit
                    continue
                last_err = e
                time.sleep(2 ** attempt)
                continue
            if resp.usage:
                with self._lock:  # batches may run in threads
                    u = resp.usage
                    self.usage["prompt_tokens"] += u.prompt_tokens or 0
                    self.usage["completion_tokens"] += u.completion_tokens or 0
                    self.usage["cache_hit_tokens"] += getattr(u, "prompt_cache_hit_tokens", 0) or 0
                    details = getattr(u, "completion_tokens_details", None)
                    self.usage["reasoning_tokens"] += getattr(details, "reasoning_tokens", 0) or 0
                    self.usage["requests"] += 1
                    self.usage["images"] += sum(1 for c in content if c.get("type") == "image_url")
            choice = resp.choices[0]
            try:
                return parse_json(choice.message.content or "")
            except (json.JSONDecodeError, ValueError) as e:
                if choice.finish_reason == "length":
                    raise OutputTruncated("输出被截断（超过 max_tokens）") from e
                last_err = e
        raise RuntimeError(f"模型调用失败：{last_err}")
