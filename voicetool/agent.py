"""Голосовой агент: «Алиса, сделай …» -> LLM решает шаги -> клики и ввод на компьютере.

Цикл наблюдение-действие: скриншот экрана и результат прошлого шага уходят модели,
она отвечает ОДНИМ действием в JSON, действие выполняется — и так до done/fail,
лимита шагов или таймаута. Никаких зашитых сценариев под конкретные приложения:
модель сама комбинирует примитивы из computer.py.

Безопасность (жёстко, не опционально):
  - необратимые команды (удаление, платежи, пароли, установка программ) требуют
    голосового подтверждения — и по тексту команды, и по решению модели;
  - стоп-слово прерывает выполнение между шагами;
  - каждая команда ограничена agent_max_steps шагами и agent_timeout_seconds секундами;
  - каждый шаг с таймстампом пишется в agent_log.txt.
"""
import datetime
import json
import logging
import re
import threading
import time

from . import computer, llm
from .text import matches_phrase

log = logging.getLogger(__name__)

# Категории необратимых действий (ТЗ): удаление данных, платежи, учётные/системные
# настройки, установка/удаление программ. Проверяется и текст команды пользователя,
# и описание действия от модели — двойной заслон.
IRREVERSIBLE_RE = re.compile(
    r"(удал|стер|стир|сотр|очист|форматир|снес|снос"           # удаление данных
    r"|куп|оплат|плат[её]ж|перевед|перевод.{0,12}(денег|средств)|подписк"  # деньги
    r"|парол|учётн|учетн|права.{0,8}доступ|администратор"       # учётные данные
    r"|установ|деинстал|uninstall|install"                      # программы
    r"|delete|remove|format|purchase|payment|password)",
    re.IGNORECASE)

SYSTEM_PROMPT = """Ты — агент, управляющий компьютером пользователя на Windows, чтобы выполнить его голосовую команду.
Тебе дают команду, скриншот экрана и результат предыдущего действия. Отвечай СТРОГО одним JSON-объектом без пояснений вокруг — одно следующее действие:

{"action": "open_app", "name": "название программы"} — запустить программу (telegram, notepad, calc, steam...)
{"action": "open_url", "url": "https://... или steam://rungameid/570"} — открыть ссылку или Steam-игру
{"action": "focus_window", "title": "часть заголовка окна"} — активировать уже открытое окно
{"action": "click", "x": 123, "y": 456, "button": "left", "double": false} — клик по координатам НА СКРИНШОТЕ
{"action": "type", "text": "текст", "enter": false} — набрать текст в активное поле (enter: true = отправить)
{"action": "key", "combo": "ctrl+f"} — нажать клавишу или сочетание (enter, esc, tab, ctrl+k...)
{"action": "wait", "seconds": 1.5} — подождать (программа запускается, окно грузится)
{"action": "done", "message": "что сделано"} — команда выполнена
{"action": "fail", "message": "почему невозможно"} — выполнить нельзя (программа не установлена и т.п.)

Правила:
- Одно действие за ответ. Смотри на скриншот: что реально на экране, то и есть правда.
- Мессенджеры (Telegram и т.п.) — только через их интерфейс: открой окно, найди чат поиском (ctrl+k или клик в поиск), набери имя, выбери чат, набери текст, отправь.
- После запуска программы подожди (wait), затем проверь по скриншоту, что она открылась.
- Если нужный элемент не виден на экране или программа не открылась после ожидания — не гадай и не кликай вслепую, отвечай fail с объяснением.
- Если действие необратимо (удаление файлов, покупка, смена пароля, установка/удаление программ) — добавь в JSON "irreversible": true.
- Текст сообщений пиши от первого лица пользователя, без кавычек вокруг.
"""

DONE, FAILED, STOPPED, UNCONFIRMED = "done", "failed", "stopped", "unconfirmed"


def is_irreversible(text: str) -> bool:
    return bool(IRREVERSIBLE_RE.search(text or ""))


class AgentLog:
    """agent_log.txt: что услышали, что решила модель, что сделали — с таймстампами."""

    def __init__(self, data_dir):
        self.path = data_dir / "agent_log.txt"

    def write(self, kind, text):
        stamp = datetime.datetime.now().isoformat(timespec="seconds")
        line = f"{stamp}  [{kind}] {text}\n"
        try:
            self.path.parent.mkdir(parents=True, exist_ok=True)
            with open(self.path, "a", encoding="utf-8") as f:
                f.write(line)
        except OSError:
            log.exception("Не удалось записать agent_log.txt")
        log.info("agent: [%s] %s", kind, text)


class Agent:
    """Одна команда = один вызов run(). Объект можно переиспользовать.

    on_event(stage, text) — прогресс наружу (CLI печатает, GUI показывает).
    confirm() -> bool — блокирующий запрос голосового подтверждения (даёт вызывающий).
    computer_mod и chat_fn подменяются в тестах.
    """

    def __init__(self, cfg, on_event=None, confirm=None, computer_mod=None, chat_fn=None):
        self.cfg = cfg
        self.on_event = on_event or (lambda stage, text: None)
        self.confirm = confirm
        self.computer = computer_mod or computer
        self.chat = chat_fn or (lambda messages: llm.chat(cfg, messages))
        self.log = AgentLog(cfg.data_dir)
        self._stop = threading.Event()

    def stop(self):
        """Стоп-слово: прервать выполнение немедленно (проверяется между шагами)."""
        self._stop.set()

    # --- главный цикл ---------------------------------------------------------

    def run(self, command: str) -> str:
        """Выполнить голосовую команду. Возвращает done|failed|stopped|unconfirmed."""
        self._stop.clear()
        command = (command or "").strip()
        self.log.write("команда", command)
        self._emit("start", command)

        if is_irreversible(command) and not self._confirmed(f"команда: {command}"):
            return UNCONFIRMED

        deadline = time.monotonic() + float(self.cfg.get("agent_timeout_seconds", 180))
        max_steps = int(self.cfg.get("agent_max_steps", 20))
        messages = [{"role": "system", "content": SYSTEM_PROMPT},
                    {"role": "user", "content": f"Команда пользователя: {command}"}]
        last_result = "начинаю"

        for step in range(1, max_steps + 1):
            if self._stop.is_set():
                return self._finish(STOPPED, "остановлено стоп-словом")
            if time.monotonic() > deadline:
                return self._finish(FAILED, f"таймаут {self.cfg.get('agent_timeout_seconds')} с — "
                                            "команда не была завершена")

            messages.append(self._observation(step, last_result))
            try:
                reply = self.chat(messages)
            except llm.LLMError as e:
                return self._finish(FAILED, f"ошибка LLM: {e}")
            messages.append({"role": "assistant", "content": reply})
            self._trim(messages)

            action = _parse_action(reply)
            if action is None:
                last_result = "ошибка: ответ не является JSON-действием, повтори строго по формату"
                self.log.write("модель", f"нечитаемый ответ: {reply[:200]}")
                continue
            self.log.write("модель", json.dumps(action, ensure_ascii=False))

            name = action.get("action", "")
            if name == "done":
                return self._finish(DONE, action.get("message", "готово"))
            if name == "fail":
                return self._finish(FAILED, action.get("message", "не удалось"))

            described = _describe(action)
            if action.get("irreversible") or is_irreversible(described):
                if not self._confirmed(f"шаг: {described}"):
                    return UNCONFIRMED
            if self._stop.is_set():
                return self._finish(STOPPED, "остановлено стоп-словом")

            self._emit("action", described)
            try:
                last_result = self._execute(action) or "сделано"
                self.log.write("действие", f"{described} -> {last_result}")
            except (computer.ComputerError, ValueError, TypeError) as e:
                last_result = f"ошибка: {e}"
                self.log.write("ошибка", f"{described} -> {e}")
            time.sleep(float(self.cfg.get("agent_step_pause", 0.6)))

        return self._finish(FAILED, f"достигнут лимит {max_steps} шагов — команда не была завершена")

    # --- шаги -------------------------------------------------------------------

    def _execute(self, action) -> str:
        name = action.get("action")
        if name == "open_app":
            return self.computer.open_app(str(action.get("name", "")).strip())
        if name == "open_url":
            return self.computer.open_url(str(action.get("url", "")).strip())
        if name == "focus_window":
            title = self.computer.focus_window_by_title(str(action.get("title", "")).strip())
            return f"активно окно «{title}»"
        if name == "click":
            x, y = self.computer.click(int(action["x"]), int(action["y"]),
                                       button=action.get("button", "left"),
                                       double=bool(action.get("double")),
                                       screenshot_size=self._last_shot_size)
            return f"клик по ({x}, {y})"
        if name == "type":
            text = str(action.get("text", ""))
            self.computer.type_text(text, press_enter=bool(action.get("enter")))
            return f"набрано: {text[:80]}"
        if name == "key":
            self.computer.press_keys(str(action.get("combo", "")))
            return f"нажато {action.get('combo')}"
        if name == "wait":
            seconds = min(10.0, max(0.1, float(action.get("seconds", 1))))
            time.sleep(seconds)
            return f"подождал {seconds:g} с"
        raise computer.ComputerError(f"Неизвестное действие: {name!r}")

    _last_shot_size = None

    def _observation(self, step, last_result):
        """Сообщение модели: результат шага + свежий скриншот (если включён и доступен)."""
        content = [{"type": "text",
                    "text": f"Шаг {step}. Результат предыдущего действия: {last_result}. "
                            f"Что делаем дальше? Ответь одним JSON."}]
        if self.cfg.get("agent_send_screenshots", True):
            try:
                png, w, h = self.computer.screenshot()
                self._last_shot_size = (w, h)
                content.append(llm.image_content(png))
                content[0]["text"] += f" Скриншот экрана {w}x{h} приложен."
            except computer.ComputerError as e:
                content[0]["text"] += f" Скриншот недоступен: {e}."
        return {"role": "user", "content": content}

    @staticmethod
    def _trim(messages, keep=8):
        """Старые скриншоты выбрасываем — платить за них повторно нет смысла."""
        for msg in messages[:-keep]:
            if isinstance(msg.get("content"), list):
                msg["content"] = [c for c in msg["content"] if c.get("type") == "text"]

    # --- подтверждение и завершение -----------------------------------------------

    def _confirmed(self, what) -> bool:
        """Необратимое действие: спросить голосом. Нет колбэка = отказ (безопасный дефолт)."""
        phrase = self.cfg.get("agent_confirm_phrase", "да подтверждаю")
        self.log.write("подтверждение", f"требуется для необратимого действия ({what})")
        self._emit("confirm", f"Это необратимое действие. Скажите «{phrase}», чтобы выполнить.")
        if not self.confirm:
            self.log.write("подтверждение", "невозможно запросить — отказ")
            self._finish(UNCONFIRMED, "необратимое действие без подтверждения не выполняется")
            return False
        ok = bool(self.confirm())
        self.log.write("подтверждение", "получено" if ok else "не получено — отменено")
        if not ok:
            self._finish(UNCONFIRMED, "подтверждение не получено — команда отменена")
        return ok

    def _finish(self, status, message):
        self.log.write("итог", f"{status}: {message}")
        self._emit(status, message)
        return status

    def _emit(self, stage, text):
        try:
            self.on_event(stage, text)
        except Exception:
            log.exception("Ошибка в обработчике событий агента")


def _parse_action(reply: str):
    """Достать JSON-действие из ответа модели (терпимо к ```json-обёрткам)."""
    if not reply:
        return None
    text = reply.strip()
    if text.startswith("```"):
        text = re.sub(r"^```[a-z]*\s*|\s*```$", "", text, flags=re.IGNORECASE)
    start = text.find("{")
    if start < 0:
        return None
    depth = 0
    for i, ch in enumerate(text[start:], start):
        if ch == "{":
            depth += 1
        elif ch == "}":
            depth -= 1
            if depth == 0:
                try:
                    data = json.loads(text[start:i + 1])
                except json.JSONDecodeError:
                    return None
                return data if isinstance(data, dict) and data.get("action") else None
    return None


def _describe(action) -> str:
    name = action.get("action", "?")
    detail = {k: v for k, v in action.items() if k not in ("action", "irreversible")}
    return f"{name} {json.dumps(detail, ensure_ascii=False)}" if detail else name


def is_stop_phrase(text, cfg) -> bool:
    return matches_phrase(text, cfg.get("agent_stop_word", "стоп"))


def is_confirm_phrase(text, cfg) -> bool:
    return matches_phrase(text, cfg.get("agent_confirm_phrase", "да подтверждаю"))
