"""Минимальный клиент OpenAI-совместимого Chat Completions API — только стандартная библиотека.

Отдельный SDK не подключаем: агенту нужен один вызов POST /chat/completions,
а любой OpenAI-совместимый сервер (OpenAI, OpenRouter, локальный vLLM/Ollama
с openai-роутером) задаётся через agent_llm_base_url в config.json.
"""
import base64
import json
import logging
import os
import urllib.error
import urllib.request

log = logging.getLogger(__name__)


class LLMError(RuntimeError):
    """Запрос к модели не удался: нет ключа, нет сети, ошибка API."""


def api_key(cfg) -> str:
    key = (cfg.get("agent_llm_api_key") or "").strip() or os.environ.get("OPENAI_API_KEY", "")
    if not key:
        raise LLMError("Ключ LLM API не задан: agent_llm_api_key в config.json "
                       "или переменная окружения OPENAI_API_KEY")
    return key


def image_content(png_bytes: bytes) -> dict:
    """Скриншот -> элемент content для мультимодального сообщения."""
    b64 = base64.b64encode(png_bytes).decode("ascii")
    return {"type": "image_url", "image_url": {"url": f"data:image/png;base64,{b64}"}}


def chat(cfg, messages, timeout=90) -> str:
    """Один вызов chat completions. Возвращает текст ответа модели."""
    url = cfg.get("agent_llm_base_url", "https://api.openai.com/v1").rstrip("/") + "/chat/completions"
    body = json.dumps({
        "model": cfg.get("agent_llm_model", "gpt-4o"),
        "messages": messages,
        "temperature": 0.1,     # исполнителю команд креативность вредит
        "max_tokens": 500,
    }).encode("utf-8")
    request = urllib.request.Request(url, data=body, method="POST", headers={
        "Content-Type": "application/json",
        "Authorization": f"Bearer {api_key(cfg)}",
    })
    try:
        with urllib.request.urlopen(request, timeout=timeout) as resp:
            payload = json.loads(resp.read().decode("utf-8"))
    except urllib.error.HTTPError as e:
        detail = e.read().decode("utf-8", errors="replace")[:300]
        raise LLMError(f"LLM API вернул ошибку {e.code}: {detail}")
    except urllib.error.URLError as e:
        raise LLMError(f"Нет связи с LLM API ({url}): {e.reason}. Агенту нужен интернет.")
    try:
        return payload["choices"][0]["message"]["content"] or ""
    except (KeyError, IndexError):
        raise LLMError(f"Неожиданный ответ LLM API: {str(payload)[:300]}")
