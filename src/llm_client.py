"""GLM / OpenAI 兼容的统一 LLM 客户端封装。

提供两个高层接口：
- chat_json: 要求模型返回严格 JSON（用于打分评估）
- chat_text:  返回纯文本（用于推文生成）
"""
from __future__ import annotations

import json
import re
from typing import Any

from openai import OpenAI

from .config import Config


class LLMClient:
    """对 OpenAI SDK 的薄封装，适配智谱 GLM 兼容接口。"""

    def __init__(self) -> None:
        Config.validate()
        self._client = OpenAI(
            api_key=Config.api_key,
            base_url=Config.base_url,
        )
        self.model = Config.model

    def _chat(self, system: str, user: str, temperature: float = 0.4) -> str:
        """底层调用，返回模型文本回复。"""
        resp = self._client.chat.completions.create(
            model=self.model,
            messages=[
                {"role": "system", "content": system},
                {"role": "user", "content": user},
            ],
            temperature=temperature,
        )
        return (resp.choices[0].message.content or "").strip()

    def chat_text(self, system: str, user: str, temperature: float = 0.6) -> str:
        """普通文本回复。"""
        return self._chat(system, user, temperature)

    def chat_json(self, system: str, user: str, temperature: float = 0.2) -> dict[str, Any]:
        """要求返回 JSON。自动剥离 ```json 代码块与多余文本。"""
        raw = self._chat(system, user, temperature)
        return _extract_json(raw)


def _extract_json(text: str) -> dict[str, Any]:
    """从模型回复中稳健地抽取 JSON 对象。

    依次尝试：直接解析 -> 提取 ```json``` 代码块 -> 提取首个 {...}。
    """
    text = text.strip()
    # 1. 直接整体解析
    try:
        return json.loads(text)
    except json.JSONDecodeError:
        pass

    # 2. 提取 ```json ... ``` 代码块
    m = re.search(r"```(?:json)?\s*(\{.*?\})\s*```", text, re.DOTALL)
    if m:
        try:
            return json.loads(m.group(1))
        except json.JSONDecodeError:
            pass

    # 3. 提取首个 { ... }（贪婪到最后一层）
    m = re.search(r"\{.*\}", text, re.DOTALL)
    if m:
        try:
            return json.loads(m.group(0))
        except json.JSONDecodeError:
            pass

    raise ValueError(f"无法从模型回复中解析 JSON。原文前500字：\n{text[:500]}")
