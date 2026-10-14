"""LiteLLM-based provider."""

import json_repair
from typing import Any
from loguru import logger
import litellm
from minibot.i18n import t
from minibot.providers.base import LLMProvider, LLMResponse, ToolCallRequest


class LiteLLMProvider(LLMProvider):
    """基於 LiteLLM 的 Provider，支援 MiniMax 及其他多種 LLM。"""

    def __init__(
        self,
        api_key: str,
        model: str,
        api_base: str | None = None,
        env_key: str = "MINIMAX_API_KEY",
        litellm_prefix: str = "",
    ):
        super().__init__(api_key, api_base)
        self.model = model
        self.litellm_prefix = litellm_prefix
        # 憑證一律透過 chat() 的 `api_key` / `api_base` 參數傳給 LiteLLM，
        # 不寫入 os.environ：寫入會讓金鑰洩漏給子行程，且 api_base 的變數名
        # 與實際 provider 無關（見 #3）。
        # `env_key` 保留僅為相容既有呼叫端，實際不再產生任何環境變數。
        litellm.drop_params = True

    def _prefixed_model(self, model: str | None) -> str:
        """取得加上 prefix 的完整模型名稱。"""
        m = model or self.model
        if self.litellm_prefix and not m.startswith(self.litellm_prefix + "/"):
            return f"{self.litellm_prefix}/{m}"
        return m

    async def chat(
        self,
        messages: list[dict],
        tools: list[dict] | None = None,
        model: str | None = None,
        max_tokens: int = 4096,
        temperature: float = 0.7,
    ) -> LLMResponse:
        """呼叫 LLM 並回傳標準化回應。"""
        kwargs: dict[str, Any] = {
            "model": self._prefixed_model(model),
            "messages": messages,
            "max_tokens": max_tokens,
            "temperature": temperature,
        }
        if self.api_base:
            kwargs["api_base"] = self.api_base
        if self.api_key:
            kwargs["api_key"] = self.api_key
        if tools:
            kwargs["tools"] = tools
            kwargs["tool_choice"] = "auto"

        try:
            resp = await litellm.acompletion(**kwargs)
            choice = resp.choices[0]
            msg = choice.message

            tool_calls = []
            if msg.tool_calls:
                for tc in msg.tool_calls:
                    args = tc.function.arguments
                    if isinstance(args, str):
                        args = json_repair.loads(args)
                    tool_calls.append(ToolCallRequest(
                        id=tc.id, name=tc.function.name, arguments=args
                    ))

            return LLMResponse(
                content=msg.content,
                tool_calls=tool_calls,
                finish_reason=choice.finish_reason or "stop",
                usage=dict(resp.usage) if resp.usage else {},
            )
        except Exception as e:
            logger.error("LLM call failed: {}", e)
            return LLMResponse(content=t("error.llm_request_failed"))

    def get_default_model(self) -> str:
        return self.model
