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
        self.usage = {"prompt_tokens": 0, "completion_tokens": 0}
        self._lock = threading.Lock()

    def chat_json(self, system: str, content: list[dict], retries: int = 3) -> dict:
        messages = [{"role": "system", "content": system},
                    {"role": "user", "content": content}]
        last_err: Exception | None = None
        for attempt in range(retries):
            kwargs = {"model": self.model, "messages": messages, "temperature": self.temperature}
            if self._json_mode:
                kwargs["response_format"] = {"type": "json_object"}
            try:
                resp = self.client.chat.completions.create(**kwargs)
            except Exception as e:  # noqa: BLE001
                msg = str(e).lower()
                if self._json_mode and "response_format" in msg:
                    self._json_mode = False  # endpoint lacks JSON mode; rely on the prompt
                    continue
                last_err = e
                time.sleep(2 ** attempt)
                continue
            if resp.usage:
                with self._lock:  # batches may run in threads
                    self.usage["prompt_tokens"] += resp.usage.prompt_tokens or 0
                    self.usage["completion_tokens"] += resp.usage.completion_tokens or 0
            try:
                return parse_json(resp.choices[0].message.content or "")
            except (json.JSONDecodeError, ValueError) as e:
                last_err = e
        raise RuntimeError(f"模型调用失败：{last_err}")
