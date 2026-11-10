"""Окно чата и иконка в трее."""

from __future__ import annotations

import queue
import threading
import tkinter as tk
from tkinter import messagebox, ttk
from tkinter.scrolledtext import ScrolledText

from .agent import Agent
from .providers import ProviderError

TITLE = "Jarisisant"


class App:
    def __init__(self, smoke: bool = False):
        self.root = tk.Tk()
        self.root.title(TITLE)
        self.root.geometry("560x680")
        self.root.minsize(380, 420)

        # Всё, что приходит из других потоков, идёт через очередь в главный поток.
        self.events: queue.Queue = queue.Queue()
        self.busy = False
        self.agent = Agent(confirm=self._confirm, on_tool=lambda t: self.events.put(("tool", t)))

        self._build_ui()
        self.tray = self._start_tray()
        self.root.protocol("WM_DELETE_WINDOW", self._hide if self.tray else self._quit)
        self.root.after(100, self._pump)
        self._say("info", f"Модель: {self.agent.profile.label}. Закрытие окна сворачивает в трей.")
        if smoke:
            self.root.after(1500, self._quit)

    # ---------- интерфейс ----------

    def _build_ui(self) -> None:
        top = ttk.Frame(self.root, padding=(10, 10, 10, 4))
        top.pack(fill="x")
        ttk.Label(top, text="Модель:").pack(side="left")
        self._labels = {p.label: key for key, p in self.agent.profiles.items()}
        self.model_box = ttk.Combobox(top, state="readonly", values=list(self._labels), width=26)
        self.model_box.set(self.agent.profile.label)
        self.model_box.bind("<<ComboboxSelected>>", self._on_model_picked)
        self.model_box.pack(side="left", padx=8)
        ttk.Button(top, text="Новый диалог", command=self._new_chat).pack(side="right")

        self.chat = ScrolledText(self.root, wrap="word", state="disabled", font=("Segoe UI", 11))
        self.chat.pack(fill="both", expand=True, padx=10, pady=4)
        self.chat.tag_configure("user", foreground="#1d4ed8", spacing1=8)
        self.chat.tag_configure("assistant", spacing1=4)
        self.chat.tag_configure("tool", foreground="#6b7280", font=("Consolas", 9))
        self.chat.tag_configure("info", foreground="#6b7280", font=("Segoe UI", 9, "italic"))
        self.chat.tag_configure("error", foreground="#b91c1c")

        bottom = ttk.Frame(self.root, padding=(10, 4, 10, 10))
        bottom.pack(fill="x")
        self.entry = ttk.Entry(bottom, font=("Segoe UI", 11))
        self.entry.pack(side="left", fill="x", expand=True)
        self.entry.bind("<Return>", lambda _: self._send())
        self.send_button = ttk.Button(bottom, text="Отправить", command=self._send)
        self.send_button.pack(side="left", padx=(8, 0))
        self.entry.focus_set()

    def _say(self, tag: str, text: str) -> None:
        self.chat.configure(state="normal")
        self.chat.insert("end", text + "\n", tag)
        self.chat.configure(state="disabled")
        self.chat.see("end")

    def _set_busy(self, busy: bool) -> None:
        self.busy = busy
        self.send_button.configure(state="disabled" if busy else "normal")
        self.root.title(f"{TITLE} (думает...)" if busy else TITLE)

    # ---------- действия ----------

    def _send(self) -> None:
        text = self.entry.get().strip()
        if self.busy or not text:
            return
        self.entry.delete(0, "end")
        self._say("user", "Вы: " + text)
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
                self._say("tool", "  > " + payload)
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

    def _on_model_picked(self, _event=None) -> None:
        self._set_profile(self._labels[self.model_box.get()])

    def _set_profile(self, key: str) -> None:
        if key == self.agent.profile_key:
            return
        self.agent.profile_key = key
        self.model_box.set(self.agent.profile.label)
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
        self._say("info", f"Новый диалог. Модель: {self.agent.profile.label}")

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
