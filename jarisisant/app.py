"""Окно чата и иконка в трее."""

from __future__ import annotations

import ctypes
import queue
import threading
import tkinter as tk
from tkinter import messagebox, ttk

from .agent import Agent
from .providers import ProviderError

TITLE = "Jarisisant"

BG = "#1e1f22"
PANEL = "#2b2d31"
PANEL_HOVER = "#383a40"
BORDER = "#3a3d44"
TEXT = "#e6e7ea"
MUTED = "#8b8f98"
ACCENT = "#6ea8fe"
ACCENT_HOVER = "#8fbcff"
ON_ACCENT = "#0b1220"
BOT = "#7ee0a1"
ERROR = "#f28b82"
SELECTION = "#3b5b8c"

FONT = ("Segoe UI", 11)
FONT_BOLD = ("Segoe UI", 10, "bold")
FONT_SMALL = ("Segoe UI", 9)
HINT = "Enter: отправить  ·  Shift+Enter: новая строка"

# Tk узнаёт Ctrl+V/C/X по букве, поэтому в русской раскладке (Ctrl+М/С/Ч) они не срабатывают.
# Код физической клавиши от раскладки не зависит.
_KEY_A, _KEY_C, _KEY_V, _KEY_X = 65, 67, 86, 88
_CLIPBOARD_EVENTS = {_KEY_C: "<<Copy>>", _KEY_V: "<<Paste>>", _KEY_X: "<<Cut>>"}


def _ctrl_action(keycode: int, keysym: str) -> str | None:
    """Что сделать по Ctrl+клавиша: "select_all", имя события буфера обмена или None."""
    if keycode == _KEY_A:
        return "select_all"
    if keysym.lower() in ("c", "v", "x"):
        return None  # латинская раскладка: Tk обработает сам
    return _CLIPBOARD_EVENTS.get(keycode)


def _enable_shortcuts(widget: tk.Text) -> None:
    """Ctrl+C/V/X/A в любой раскладке и меню по правой кнопке мыши."""

    def on_ctrl_key(event):
        action = _ctrl_action(event.keycode, event.keysym)
        if action is None:
            return None
        if action == "select_all":
            widget.tag_add("sel", "1.0", "end-1c")
        else:
            widget.event_generate(action)
        return "break"

    widget.bind("<Control-KeyPress>", on_ctrl_key)

    editable = str(widget.cget("state")) == "normal"
    menu = tk.Menu(
        widget, tearoff=0, bg=PANEL, fg=TEXT, activebackground=ACCENT,
        activeforeground=ON_ACCENT, borderwidth=0,
    )
    if editable:
        menu.add_command(label="Вырезать", command=lambda: widget.event_generate("<<Cut>>"))
    menu.add_command(label="Копировать", command=lambda: widget.event_generate("<<Copy>>"))
    if editable:
        menu.add_command(label="Вставить", command=lambda: widget.event_generate("<<Paste>>"))

    def on_right_click(event):
        widget.focus_set()
        menu.tk_popup(event.x_root, event.y_root)

    widget.bind("<Button-3>", on_right_click)


def _flat_button(parent, text: str, command, accent: bool = False) -> tk.Button:
    normal, hover, fg = (ACCENT, ACCENT_HOVER, ON_ACCENT) if accent else (PANEL, PANEL_HOVER, TEXT)
    button = tk.Button(
        parent, text=text, command=command, bg=normal, fg=fg, activebackground=hover,
        activeforeground=fg, disabledforeground=MUTED, relief="flat", borderwidth=0,
        font=FONT_BOLD if accent else FONT_SMALL, padx=14, pady=6, cursor="hand2",
    )
    button.bind("<Enter>", lambda _: button.configure(bg=hover))
    button.bind("<Leave>", lambda _: button.configure(bg=normal))
    return button


class App:
    def __init__(self, smoke: bool = False):
        self.root = tk.Tk()
        self.root.title(TITLE)
        self.root.geometry("600x720")
        self.root.minsize(400, 440)
        self.root.configure(bg=BG)

        # Всё, что приходит из других потоков, идёт через очередь в главный поток.
        self.events: queue.Queue = queue.Queue()
        self.busy = False
        self.agent = Agent(confirm=self._confirm, on_tool=lambda t: self.events.put(("tool", t)))

        self._build_ui()
        self._dark_title_bar()
        self.tray = self._start_tray()
        self.root.protocol("WM_DELETE_WINDOW", self._hide if self.tray else self._quit)
        self.root.after(100, self._pump)
        self._say("info", "Закрытие окна сворачивает ассистента в трей.")
        if smoke:
            self.root.after(1500, self._quit)

    # ---------- интерфейс ----------

    def _build_ui(self) -> None:
        top = tk.Frame(self.root, bg=BG)
        top.pack(fill="x", padx=14, pady=(12, 6))
        tk.Label(top, text="Модель", bg=BG, fg=MUTED, font=FONT_SMALL).pack(side="left")

        self.model_var = tk.StringVar(value=self.agent.profile_key)
        self.model_button = tk.Menubutton(
            top, bg=PANEL, fg=TEXT, activebackground=PANEL_HOVER, activeforeground=TEXT,
            relief="flat", borderwidth=0, font=FONT_SMALL, padx=12, pady=6, cursor="hand2",
        )
        model_menu = tk.Menu(
            self.model_button, tearoff=0, bg=PANEL, fg=TEXT, activebackground=ACCENT,
            activeforeground=ON_ACCENT, selectcolor=TEXT, borderwidth=0,
        )
        for key, profile in self.agent.profiles.items():
            model_menu.add_radiobutton(
                label=profile.label, value=key, variable=self.model_var,
                command=lambda key=key: self._set_profile(key),
            )
        self.model_button.configure(menu=model_menu)
        self.model_button.pack(side="left", padx=10)
        _flat_button(top, "Новый диалог", self._new_chat).pack(side="right")

        # Нижняя часть упакована раньше чата, чтобы при сжатии окна поле ввода не пропадало.
        bottom = tk.Frame(self.root, bg=BG)
        bottom.pack(side="bottom", fill="x", padx=14, pady=(6, 12))
        self.hint = tk.Label(bottom, text=HINT, bg=BG, fg=MUTED, font=("Segoe UI", 8), anchor="w")
        self.hint.pack(side="bottom", fill="x", pady=(6, 0))
        self.send_button = _flat_button(bottom, "Отправить", self._send, accent=True)
        self.send_button.pack(side="right", fill="y", padx=(8, 0))
        input_box = tk.Frame(bottom, bg=PANEL, highlightthickness=1, highlightbackground=BORDER,
                             highlightcolor=ACCENT)
        input_box.pack(side="left", fill="x", expand=True)
        self.entry = tk.Text(
            input_box, height=3, wrap="word", bg=PANEL, fg=TEXT, insertbackground=TEXT,
            selectbackground=SELECTION, selectforeground=TEXT, relief="flat", borderwidth=0,
            highlightthickness=0, font=FONT, padx=10, pady=8, undo=True,
        )
        self.entry.pack(fill="both", expand=True)
        self.entry.bind("<Return>", self._on_enter)
        self.entry.bind("<Shift-Return>", lambda _: None)  # обычный перенос строки
        _enable_shortcuts(self.entry)

        middle = tk.Frame(self.root, bg=BG)
        middle.pack(fill="both", expand=True, padx=(14, 6), pady=4)
        style = ttk.Style(self.root)
        style.theme_use("clam")
        style.configure(
            "Dark.Vertical.TScrollbar", background=PANEL_HOVER, troughcolor=BG, bordercolor=BG,
            lightcolor=PANEL_HOVER, darkcolor=PANEL_HOVER, arrowcolor=MUTED, relief="flat",
        )
        style.map("Dark.Vertical.TScrollbar", background=[("active", MUTED)])
        scrollbar = ttk.Scrollbar(middle, orient="vertical", style="Dark.Vertical.TScrollbar")
        scrollbar.pack(side="right", fill="y")
        self.chat = tk.Text(
            middle, wrap="word", state="disabled", bg=BG, fg=TEXT, relief="flat", borderwidth=0,
            highlightthickness=0, font=FONT, padx=4, pady=4, spacing3=2, cursor="arrow",
            selectbackground=SELECTION, selectforeground=TEXT, yscrollcommand=scrollbar.set,
        )
        self.chat.pack(side="left", fill="both", expand=True)
        scrollbar.configure(command=self.chat.yview)
        self.chat.tag_configure("name_user", foreground=ACCENT, font=FONT_BOLD, spacing1=14)
        self.chat.tag_configure("name_bot", foreground=BOT, font=FONT_BOLD, spacing1=14)
        self.chat.tag_configure("tool", foreground=MUTED, font=("Consolas", 9), lmargin1=10)
        self.chat.tag_configure("info", foreground=MUTED, font=("Segoe UI", 9, "italic"), spacing1=6)
        self.chat.tag_configure("error", foreground=ERROR, spacing1=6)
        _enable_shortcuts(self.chat)

        self._refresh_model_button()
        self.entry.focus_set()

    def _dark_title_bar(self) -> None:
        """Тёмный заголовок окна в Windows 10/11; на других системах просто ничего не делает."""
        try:
            self.root.update_idletasks()
            window = ctypes.windll.user32.GetParent(self.root.winfo_id())
            enabled = ctypes.c_int(1)
            ctypes.windll.dwmapi.DwmSetWindowAttribute(
                window, 20, ctypes.byref(enabled), ctypes.sizeof(enabled)
            )
        except (AttributeError, OSError):
            pass

    def _refresh_model_button(self) -> None:
        self.model_button.configure(text=self.agent.profile.label + "  ▾")
        self.model_var.set(self.agent.profile_key)

    def _say(self, kind: str, text: str) -> None:
        self.chat.configure(state="normal")
        if kind == "user":
            self.chat.insert("end", "Вы\n", "name_user")
            self.chat.insert("end", text + "\n")
        elif kind == "assistant":
            self.chat.insert("end", TITLE + "\n", "name_bot")
            self.chat.insert("end", text + "\n")
        else:
            self.chat.insert("end", text + "\n", kind)
        self.chat.configure(state="disabled")
        self.chat.see("end")

    def _set_busy(self, busy: bool) -> None:
        self.busy = busy
        self.send_button.configure(state="disabled" if busy else "normal")
        self.hint.configure(
            text=f"{self.agent.profile.label} думает..." if busy else HINT,
            fg=ACCENT if busy else MUTED,
        )

    # ---------- действия ----------

    def _on_enter(self, _event) -> str:
        self._send()
        return "break"  # иначе в поле ввода останется перенос строки

    def _send(self) -> None:
        text = self.entry.get("1.0", "end").strip()
        if self.busy or not text:
            return
        self.entry.delete("1.0", "end")
        self._say("user", text)
        self._set_busy(True)
        threading.Thread(target=self._work, args=(text,), daemon=True).start()

    def _work(self, text: str) -> None:
        try:
            self.events.put(("assistant", self.agent.ask(text)))
        except ProviderError as error:
            self.events.put(("error", str(error)))
        except Exception as error:  # чтобы окно не зависло в состоянии «думает» навсегда
            self.events.put(("error", f"Непредвиденная ошибка: {error!r}"))
        finally:
            self.events.put(("done", None))

    def _pump(self) -> None:
        while True:
            try:
                kind, payload = self.events.get_nowait()
            except queue.Empty:
                break
            if kind == "call":
                payload()
            elif kind == "done":
                self._set_busy(False)
            elif kind == "tool":
                self._say("tool", "> " + payload)
            else:
                self._say(kind, payload)
        self.root.after(100, self._pump)

    def _confirm(self, text: str) -> bool:
        """Вызывается из рабочего потока: показывает вопрос в главном и ждёт ответа."""
        answered = threading.Event()
        answer = {"ok": False}

        def ask() -> None:
            self._show()
            answer["ok"] = messagebox.askyesno(TITLE, text, parent=self.root, default="no")
            answered.set()

        self.events.put(("call", ask))
        answered.wait()
        return answer["ok"]

    def _set_profile(self, key: str) -> None:
        changed = key != self.agent.profile_key
        self.agent.profile_key = key
        self._refresh_model_button()
        if changed:
            self._say("info", f"Модель: {self.agent.profile.label}")
            if self.tray:
                self.tray.update_menu()

    def _new_chat(self) -> None:
        if self.busy:
            return
        self.agent.reset()
        self.chat.configure(state="normal")
        self.chat.delete("1.0", "end")
        self.chat.configure(state="disabled")
        self._say("info", "Новый диалог.")

    # ---------- окно и трей ----------

    def _show(self) -> None:
        self.root.deiconify()
        self.root.lift()
        self.root.focus_force()
        self.entry.focus_set()

    def _hide(self) -> None:
        self.root.withdraw()

    def _quit(self) -> None:
        if self.tray:
            self.tray.stop()
        self.root.destroy()

    def _start_tray(self):
        try:
            import pystray
            from PIL import Image, ImageDraw, ImageFont

            image = Image.new("RGBA", (64, 64), (0, 0, 0, 0))
            draw = ImageDraw.Draw(image)
            draw.ellipse((2, 2, 62, 62), fill=(37, 99, 235, 255))
            draw.text((32, 32), "J", fill="white", anchor="mm", font=ImageFont.load_default(size=40))

            def on_main(function):
                # Меню трея живёт в своём потоке, поэтому действия передаём в главный.
                return lambda icon, item: self.events.put(("call", function))

            def model_item(key: str, label: str):
                return pystray.MenuItem(
                    label,
                    on_main(lambda: self._set_profile(key)),
                    checked=lambda item: self.agent.profile_key == key,
                    radio=True,
                )

            menu = pystray.Menu(
                pystray.MenuItem("Открыть", on_main(self._show), default=True),
                pystray.MenuItem(
                    "Модель",
                    pystray.Menu(*(model_item(k, p.label) for k, p in self.agent.profiles.items())),
                ),
                pystray.Menu.SEPARATOR,
                pystray.MenuItem("Выход", on_main(self._quit)),
            )
            icon = pystray.Icon("jarisisant", image, TITLE, menu)
            icon.run_detached()
            return icon
        except Exception:  # без трея приложение остаётся рабочим: окно просто закрывается
            return None


def main(smoke: bool = False) -> None:
    App(smoke=smoke).root.mainloop()
