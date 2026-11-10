"""Инструменты ассистента: браузер, программы, файлы.

Файловые операции разрешены только внутри папок из allowed_roots().
Удаление идёт в корзину и только после подтверждения пользователя.
"""

from __future__ import annotations

import os
import re
import urllib.parse
import urllib.request
import webbrowser
import winreg
from pathlib import Path
from typing import Callable

from send2trash import send2trash

from . import config

MAX_READ_CHARS = 20_000

APP_ALIASES = {
    "блокнот": "notepad.exe",
    "notepad": "notepad.exe",
    "калькулятор": "calc.exe",
    "calculator": "calc.exe",
    "проводник": "explorer.exe",
    "explorer": "explorer.exe",
    "paint": "mspaint.exe",
    "диспетчер задач": "taskmgr.exe",
    "task manager": "taskmgr.exe",
    "настройки": "ms-settings:",
    "параметры": "ms-settings:",
    "settings": "ms-settings:",
    "терминал": "wt.exe",
    "terminal": "wt.exe",
}


class ToolError(Exception):
    """Понятная ошибка, текст которой возвращается модели."""


def _shell_folder(value_name: str, fallback: str) -> Path:
    try:
        with winreg.OpenKey(
            winreg.HKEY_CURRENT_USER,
            r"Software\Microsoft\Windows\CurrentVersion\Explorer\User Shell Folders",
        ) as key:
            raw, _ = winreg.QueryValueEx(key, value_name)
            return Path(os.path.expandvars(raw))
    except OSError:
        return Path.home() / fallback


def desktop() -> Path:
    return _shell_folder("Desktop", "Desktop")


def allowed_roots() -> list[Path]:
    roots = [
        desktop(),
        _shell_folder("Personal", "Documents"),
        _shell_folder("{374DE290-123F-4565-9164-39C4925E467B}", "Downloads"),
        *config.extra_allowed_dirs(),
    ]
    return [r.resolve() for r in roots]


def resolve_path(raw: str) -> Path:
    """Относительные пути считаются от рабочего стола; выход за разрешённые папки запрещён."""
    if not raw or not raw.strip():
        raise ToolError("Путь не указан.")
    path = Path(os.path.expandvars(raw.strip())).expanduser()
    if not path.is_absolute():
        path = desktop() / path
    path = path.resolve()
    if not any(path == root or path.is_relative_to(root) for root in allowed_roots()):
        allowed = "; ".join(str(r) for r in allowed_roots())
        raise ToolError(f"Путь {path} вне разрешённых папок. Разрешены: {allowed}")
    return path


def _open_url(url: str) -> str:
    url = url.strip()
    if not re.match(r"^[a-zA-Z][a-zA-Z0-9+.-]*://", url):
        url = "https://" + url
    if urllib.parse.urlparse(url).scheme not in ("http", "https"):
        raise ToolError("Можно открывать только ссылки http и https.")
    webbrowser.open(url)
    return f"Открыто в браузере: {url}"


def _search_web(query: str) -> str:
    return _open_url("https://www.google.com/search?q=" + urllib.parse.quote_plus(query))


def _play_youtube(query: str) -> str:
    results = "https://www.youtube.com/results?search_query=" + urllib.parse.quote_plus(query)
    try:
        request = urllib.request.Request(results, headers={"User-Agent": "Mozilla/5.0"})
        with urllib.request.urlopen(request, timeout=6) as response:
            page = response.read(600_000).decode("utf-8", errors="ignore")
        match = re.search(r'"videoId":"([\w-]{11})"', page)
    except OSError:
        match = None
    if match:
        webbrowser.open("https://www.youtube.com/watch?v=" + match.group(1))
        return f"Включено первое видео по запросу «{query}»."
    webbrowser.open(results)
    return f"Открыты результаты поиска YouTube по запросу «{query}» (первое видео определить не удалось)."


def _start_menu_shortcut(name: str) -> Path | None:
    folders = [
        Path(os.environ.get("APPDATA", "")) / "Microsoft/Windows/Start Menu/Programs",
        Path(os.environ.get("PROGRAMDATA", "")) / "Microsoft/Windows/Start Menu/Programs",
    ]
    wanted = name.casefold()
    matches = [
        link
        for folder in folders
        if folder.is_dir()
        for link in folder.rglob("*.lnk")
        if wanted in link.stem.casefold()
    ]
    # Самое короткое имя ближе всего к запросу ("Chrome" лучше, чем "Chrome Remote Desktop").
    return min(matches, key=lambda p: len(p.stem), default=None)


def _open_app(name: str) -> str:
    wanted = name.strip().casefold()
    if not wanted:
        raise ToolError("Название программы не указано.")
    if wanted in ("браузер", "browser"):
        webbrowser.open("https://www.google.com")
        return "Браузер открыт."
    target = APP_ALIASES.get(wanted)
    if target is None:
        shortcut = _start_menu_shortcut(wanted)
        if shortcut is None:
            raise ToolError(f"Программа «{name}» не найдена в меню «Пуск».")
        target = str(shortcut)
    try:
        os.startfile(target)
    except OSError as error:
        raise ToolError(f"Не удалось запустить «{name}»: {error}") from error
    return f"Запущено: {Path(target).stem if target.endswith('.lnk') else name}"


def _list_folder(path: str) -> str:
    folder = resolve_path(path)
    if not folder.is_dir():
        raise ToolError(f"Папка не найдена: {folder}")
    entries = sorted(folder.iterdir(), key=lambda p: (p.is_file(), p.name.casefold()))
    lines = [("[папка] " if e.is_dir() else "") + e.name for e in entries[:200]]
    more = f"\n... и ещё {len(entries) - 200}" if len(entries) > 200 else ""
    return f"{folder}:\n" + ("\n".join(lines) if lines else "(пусто)") + more


def _create_folder(path: str) -> str:
    folder = resolve_path(path)
    folder.mkdir(parents=True, exist_ok=True)
    return f"Папка создана: {folder}"


def _read_file(path: str) -> str:
    file = resolve_path(path)
    if not file.is_file():
        raise ToolError(f"Файл не найден: {file}")
    try:
        text = file.read_text(encoding="utf-8")
    except UnicodeDecodeError as error:
        raise ToolError("Это не текстовый файл в кодировке UTF-8.") from error
    if len(text) > MAX_READ_CHARS:
        return text[:MAX_READ_CHARS] + f"\n... (показано {MAX_READ_CHARS} из {len(text)} символов)"
    return text


class Toolbox:
    """Набор инструментов. confirm(текст) -> bool спрашивает пользователя перед опасным действием."""

    def __init__(self, confirm: Callable[[str], bool], on_tool: Callable[[str], None]):
        self._confirm = confirm
        self._on_tool = on_tool
        roots = "; ".join(str(r) for r in allowed_roots())
        path_note = (
            f" Разрешённые папки: {roots}. Относительный путь считается от рабочего стола."
        )
        self._tools: dict[str, tuple[str, dict, Callable[..., str]]] = {
            "open_url": (
                "Открыть сайт или ссылку в браузере.",
                {"url": {"type": "string", "description": "Адрес, например youtube.com"}},
                _open_url,
            ),
            "search_web": (
                "Открыть в браузере поиск Google по запросу.",
                {"query": {"type": "string"}},
                _search_web,
            ),
            "play_youtube": (
                "Найти видео на YouTube по запросу и включить первое найденное.",
                {"query": {"type": "string"}},
                _play_youtube,
            ),
            "open_app": (
                "Запустить программу по названию (например: блокнот, калькулятор, браузер, "
                "проводник, Chrome, Telegram). Ищет в меню «Пуск».",
                {"name": {"type": "string"}},
                _open_app,
            ),
            "list_folder": (
                "Показать содержимое папки." + path_note,
                {"path": {"type": "string"}},
                _list_folder,
            ),
            "create_folder": (
                "Создать папку." + path_note,
                {"path": {"type": "string"}},
                _create_folder,
            ),
            "read_file": (
                "Прочитать текстовый файл." + path_note,
                {"path": {"type": "string"}},
                _read_file,
            ),
            "write_file": (
                "Создать текстовый файл с указанным содержимым. Существующий файл будет "
                "перезаписан только после подтверждения пользователя." + path_note,
                {
                    "path": {"type": "string"},
                    "content": {"type": "string", "description": "Текст файла"},
                },
                self._write_file,
            ),
            "delete_to_trash": (
                "Удалить файл или папку в корзину. Приложение само спросит у пользователя "
                "подтверждение." + path_note,
                {"path": {"type": "string"}},
                self._delete_to_trash,
            ),
        }

    def _write_file(self, path: str, content: str) -> str:
        file = resolve_path(path)
        if file.is_dir():
            raise ToolError(f"{file} это папка, а не файл.")
        if file.exists() and not self._confirm(f"Перезаписать файл?\n\n{file}"):
            return "Пользователь отказался перезаписывать файл."
        file.parent.mkdir(parents=True, exist_ok=True)
        file.write_text(content, encoding="utf-8")
        return f"Файл записан: {file}"

    def _delete_to_trash(self, path: str) -> str:
        target = resolve_path(path)
        if target in allowed_roots():
            raise ToolError("Эту папку удалять нельзя.")
        if not target.exists():
            raise ToolError(f"Не найдено: {target}")
        kind = "папку со всем содержимым" if target.is_dir() else "файл"
        if not self._confirm(f"Удалить {kind} в корзину?\n\n{target}"):
            return "Пользователь отказался от удаления."
        send2trash(target)
        return f"Перемещено в корзину: {target}"

    def call(self, name: str, args: object) -> str:
        """Выполнить инструмент. Всегда возвращает текст: результат или описание ошибки."""
        if name not in self._tools:
            return f"Ошибка: неизвестный инструмент {name}."
        _, params, function = self._tools[name]
        if not isinstance(args, dict) or set(args) != set(params):
            return f"Ошибка: инструменту {name} нужны аргументы: {', '.join(params)}."
        if not all(isinstance(value, str) for value in args.values()):
            return f"Ошибка: все аргументы инструмента {name} должны быть строками."
        shown = ", ".join(f"{k}={v[:80]!r}" for k, v in args.items())
        self._on_tool(f"{name}({shown})")
        try:
            return function(**args)
        except ToolError as error:
            return f"Ошибка: {error}"
        except OSError as error:
            return f"Ошибка системы: {error}"

    def _schema(self, params: dict) -> dict:
        return {
            "type": "object",
            "properties": params,
            "required": list(params),
            "additionalProperties": False,
        }

    def openai_schema(self) -> list[dict]:
        return [
            {
                "type": "function",
                "function": {
                    "name": name,
                    "description": description,
                    "parameters": self._schema(params),
                },
            }
            for name, (description, params, _) in self._tools.items()
        ]

    def anthropic_schema(self) -> list[dict]:
        return [
            {"name": name, "description": description, "input_schema": self._schema(params)}
            for name, (description, params, _) in self._tools.items()
        ]
