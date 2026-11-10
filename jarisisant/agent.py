"""Диалог: хранит историю и отправляет запросы выбранной модели."""

from __future__ import annotations

from datetime import datetime
from typing import Callable

from . import config, providers
from .tools import Toolbox

MAX_HISTORY = 20  # сколько последних сообщений помнит ассистент

SYSTEM_PROMPT = """\
Ты Jarisisant, ассистент на компьютере пользователя с Windows. Отвечай на языке \
пользователя, коротко и по делу, без разметки Markdown: ответы показываются простым текстом.

Действия на компьютере выполняй инструментами: открыть сайт или видео, запустить программу, \
создать, прочитать или удалить файл. Не говори, что действие сделано, пока инструмент не вернул \
успешный результат. Если инструмент вернул ошибку или пользователь отказался, так и скажи. \
Если просьба неоднозначна в том, что удалять или перезаписывать, сначала уточни.

На обычные вопросы и в разговоре отвечай без инструментов.

Сейчас: {now}."""


class Agent:
    def __init__(self, confirm: Callable[[str], bool], on_tool: Callable[[str], None]):
        self.profiles = config.profiles()
        self.profile_key = config.default_profile()
        self.history: list[dict] = []
        self._toolbox = Toolbox(confirm=confirm, on_tool=on_tool)

    @property
    def profile(self) -> config.Profile:
        return self.profiles[self.profile_key]

    def ask(self, text: str) -> str:
        """Отправить запрос текущей модели. Модель меняется только через profile_key."""
        system = SYSTEM_PROMPT.format(now=datetime.now().strftime("%d.%m.%Y %H:%M"))
        pending = self.history + [{"role": "user", "text": text}]
        reply = providers.run(self.profile, system, pending, self._toolbox) or "(пустой ответ)"
        # В историю запрос попадает только вместе с ответом, чтобы после ошибки не осталось
        # двух сообщений пользователя подряд.
        self.history = (pending + [{"role": "assistant", "text": reply}])[-MAX_HISTORY:]
        return reply

    def reset(self) -> None:
        self.history = []
