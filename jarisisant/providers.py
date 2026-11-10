"""Вызов моделей: OpenAI-совместимые (Ollama, Gemini, DeepSeek) и Claude."""

from __future__ import annotations

import json
import os
import re

import anthropic
import openai

from . import config
from .config import Profile
from .tools import Toolbox

MAX_STEPS = 8  # сколько раз подряд модель может вызвать инструменты за один ответ
TOO_MANY_STEPS = "Остановился: слишком много действий подряд. Уточните задачу."


class ProviderError(Exception):
    """Ошибка модели с понятным пользователю текстом."""


def run(profile: Profile, system: str, history: list[dict], toolbox: Toolbox) -> str:
    """history: [{"role": "user"|"assistant", "text": str}], последним идёт новый запрос."""
    if profile.kind == "anthropic":
        return _run_claude(profile, system, history, toolbox)
    return _run_openai(profile, system, history, toolbox)


def _run_openai(profile: Profile, system: str, history: list[dict], toolbox: Toolbox) -> str:
    if profile.api_key_env:
        api_key = os.environ.get(profile.api_key_env, "").strip()
        if not api_key:
            raise ProviderError(f"Не задан ключ {profile.api_key_env} в файле .env.")
    else:
        api_key = "ollama"  # Ollama ключ не проверяет, но SDK требует непустую строку

    client = openai.OpenAI(api_key=api_key, base_url=profile.base_url, timeout=180, max_retries=1)
    messages: list[dict] = [{"role": "system", "content": system}]
    messages += [{"role": m["role"], "content": m["text"]} for m in history]

    try:
        for _ in range(MAX_STEPS):
            response = client.chat.completions.create(
                model=profile.model, messages=messages, tools=toolbox.openai_schema()
            )
            message = response.choices[0].message
            if not message.tool_calls:
                return _strip_think(message.content or "")
            messages.append(
                {
                    "role": "assistant",
                    "content": message.content or "",
                    # Вызовы возвращаем как есть: Gemini кладёт в них служебные поля.
                    "tool_calls": [c.model_dump(exclude_none=True) for c in message.tool_calls],
                }
            )
            for call in message.tool_calls:
                try:
                    args = json.loads(call.function.arguments or "{}")
                except json.JSONDecodeError:
                    result = "Ошибка: аргументы инструмента должны быть корректным JSON."
                else:
                    result = toolbox.call(call.function.name, args)
                messages.append({"role": "tool", "tool_call_id": call.id, "content": result})
        return TOO_MANY_STEPS
    except openai.AuthenticationError as error:
        raise ProviderError(f"Ключ {profile.api_key_env} не принят сервисом.") from error
    except openai.NotFoundError as error:
        hint = f" Скачайте её: ollama pull {profile.model}" if not profile.api_key_env else ""
        raise ProviderError(f"Модель {profile.model} не найдена.{hint}") from error
    except openai.RateLimitError as error:
        raise ProviderError(
            "Превышен лимит запросов или закончился баланс. Подождите или выберите другую модель."
        ) from error
    except openai.APIStatusError as error:
        raise ProviderError(f"Ошибка сервиса ({error.status_code}): {error.message}") from error
    except openai.APIConnectionError as error:
        if not profile.api_key_env:
            raise ProviderError("Ollama не отвечает. Запустите приложение Ollama.") from error
        raise ProviderError("Нет связи с сервисом. Проверьте интернет.") from error


def _strip_think(text: str) -> str:
    """Некоторые локальные модели пишут рассуждения прямо в ответ внутри <think>."""
    return re.sub(r"<think>.*?</think>", "", text, flags=re.DOTALL).strip()


def _run_claude(profile: Profile, system: str, history: list[dict], toolbox: Toolbox) -> str:
    messages: list[dict] = [{"role": m["role"], "content": m["text"]} for m in history]
    try:
        client = anthropic.Anthropic()  # ключ берётся из ANTHROPIC_API_KEY
        for _ in range(MAX_STEPS):
            response = client.beta.messages.create(
                model=profile.model,
                max_tokens=16000,
                system=system,
                tools=toolbox.anthropic_schema(),
                messages=messages,
                output_config={"effort": config.claude_effort()},
                # Если модель отклонит запрос, сервер сам повторит его на запасной модели.
                betas=["server-side-fallback-2026-07-01"],
                fallbacks="default",
            )
            if response.stop_reason == "refusal":
                return "Claude отказался выполнять этот запрос."
            if response.stop_reason == "pause_turn":
                messages.append({"role": "assistant", "content": response.content})
                continue
            if response.stop_reason != "tool_use":
                text = "".join(b.text for b in response.content if b.type == "text").strip()
                if response.stop_reason == "max_tokens":
                    text += "\n(ответ обрезан: достигнут предел длины)"
                return text
            # Ответ возвращаем целиком: блоки размышлений нельзя выбрасывать.
            messages.append({"role": "assistant", "content": response.content})
            messages.append(
                {
                    "role": "user",
                    "content": [
                        {
                            "type": "tool_result",
                            "tool_use_id": block.id,
                            "content": toolbox.call(block.name, block.input),
                        }
                        for block in response.content
                        if block.type == "tool_use"
                    ],
                }
            )
        return TOO_MANY_STEPS
    except anthropic.AuthenticationError as error:
        raise ProviderError("Ключ ANTHROPIC_API_KEY не принят.") from error
    except anthropic.PermissionDeniedError as error:
        raise ProviderError("У ключа Claude нет доступа к этой модели.") from error
    except anthropic.NotFoundError as error:
        raise ProviderError(f"Модель {profile.model} не найдена.") from error
    except anthropic.RateLimitError as error:
        raise ProviderError("Превышен лимит запросов Claude. Подождите немного.") from error
    except anthropic.APIStatusError as error:
        raise ProviderError(f"Ошибка Claude ({error.status_code}): {error.message}") from error
    except anthropic.APIConnectionError as error:
        raise ProviderError("Нет связи с Claude. Проверьте интернет.") from error
    except anthropic.AnthropicError as error:
        raise ProviderError(
            f"Claude не настроен: впишите ANTHROPIC_API_KEY в файл .env. ({error})"
        ) from error
