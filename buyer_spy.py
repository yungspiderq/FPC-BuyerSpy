"""
Шпион покупателя (BuyerSpy) — плагин для FunPay Cardinal v0.1.17+

👁 Показывает, ЧТО покупатель смотрит на FunPay прямо сейчас — ваш лот,
категорию или лот конкурента. Использует официальный метод FunPayAPI
get_buyer_viewing (тот же виджет «сейчас смотрит», что видит продавец в чате).

Возможности:
 • Авто-шпионаж при новом заказе: уведомление «👁 Покупатель сейчас смотрит: ...»
   — видно, сравнивает ли он с конкурентами уже после оплаты (или выбир лот).
 • Авто-шпионаж по первому сообщению в чате (опционально, по умолчанию выключен):
   как только новый покупатель написал — плагин посмотрит, что он разглядывает.
   Один раз на чат, без спама.
 • Ручная команда /spy <ID покупателя> (или просто /spy — последний покупатель).
 • Меню /spy: переключатели режимов, кнопка «следить за последним покупателем».
 • Если покупатель смотрит лот конкурента — можно успеть предложить лучшие условия.

Команды Telegram:
 • /spy [ID покупателя] — проверить, что смотрит покупатель.

Установка: положить этот файл в папку plugins/ и перезапустить Кардинал.
"""

from __future__ import annotations

import json
import logging
import os
import threading
import time
from typing import TYPE_CHECKING

from telebot.types import InlineKeyboardMarkup as K, InlineKeyboardButton as B, CallbackQuery, Message
from telebot.apihelper import ApiTelegramException

try:
    from tg_bot import CBT, static_keyboards as skb, utils as tg_utils
except Exception:
    CBT = None
    skb = None
    tg_utils = None

if TYPE_CHECKING:
    from cardinal import Cardinal

from FunPayAPI.common.enums import MessageTypes

logger = logging.getLogger("FPC.BuyerSpy")

# ══════════════════════════════ МЕТАДАННЫЕ ПЛАГИНА ══════════════════════════════
NAME = "Шпион покупателя (BuyerSpy)"
VERSION = "1.0.0"
DESCRIPTION = ("Показывает, что покупатель смотрит на FunPay прямо сейчас — ваш лот или конкурента. "
               "Авто-проверка при новом заказе и (опционально) по первому сообщению в чате, "
               "ручная команда /spy <ID>. Команда: /spy")
CREDITS = "Qwen (для FunPay Cardinal)"
UUID = "30ae8a86-8388-461b-8834-733496eac0e8"
SETTINGS_PAGE = True

# ══════════════════════════════ НАСТРОЙКИ ПЛАГИНА ═══════════════════════════════
PLUGIN_DIR = os.path.join("storage", "plugins", "buyer_spy")
SETTINGS_PATH = os.path.join(PLUGIN_DIR, "settings.json")

DEFAULT_SETTINGS = {
    "auto_on_order": True,      # шпионить при каждом новом заказе
    "auto_on_first_msg": False, # шпионить по первому сообщению покупателя в чате
    "spied_chats": {},          # {str(chat_id): ts} — чаты, где уже шпионили (анти-спам)
    "last_buyer": {"id": 0, "name": ""},
}
MAX_SPIED_CHATS = 500
BUYER_MSG_TYPES = (None, MessageTypes.NON_SYSTEM, MessageTypes.ORDER_PURCHASED)

_stop_event = threading.Event()
_settings_lock = threading.Lock()
_cardinal: "Cardinal | None" = None


# ══════════════════════════════ УТИЛИТЫ ═════════════════════════════════════════
def _load_settings() -> dict:
    with _settings_lock:
        data = {}
        if os.path.exists(SETTINGS_PATH):
            try:
                with open(SETTINGS_PATH, "r", encoding="utf-8") as f:
                    data = json.load(f)
            except Exception:
                logger.error("BuyerSpy: не удалось прочитать настройки, использую значения по умолчанию.")
                logger.debug("TRACEBACK", exc_info=True)
                data = {}
        settings = json.loads(json.dumps(DEFAULT_SETTINGS))
        if isinstance(data, dict):
            settings.update({k: v for k, v in data.items() if k in settings})
        settings["auto_on_order"] = bool(settings["auto_on_order"])
        settings["auto_on_first_msg"] = bool(settings["auto_on_first_msg"])
        if not isinstance(settings["spied_chats"], dict):
            settings["spied_chats"] = {}
        if not isinstance(settings["last_buyer"], dict):
            settings["last_buyer"] = {"id": 0, "name": ""}
        return settings


def _save_settings(settings: dict) -> None:
    with _settings_lock:
        try:
            os.makedirs(PLUGIN_DIR, exist_ok=True)
            with open(SETTINGS_PATH, "w", encoding="utf-8") as f:
                json.dump(settings, f, ensure_ascii=False, indent=2)
        except Exception:
            logger.error("BuyerSpy: не удалось сохранить настройки.")
            logger.debug("TRACEBACK", exc_info=True)


def _short(text: str, limit: int = 40) -> str:
    text = str(text or "").replace("\n", " ")
    return text if len(text) <= limit else text[:limit - 1] + "…"


def _escape(text) -> str:
    if tg_utils:
        return tg_utils.escape(str(text))
    return str(text).replace("&", "&amp;").replace("<", "&lt;").replace(">", "&gt;")


def _plugin_enabled(crd: "Cardinal") -> bool:
    try:
        return crd.plugins[UUID].enabled
    except Exception:
        return False


def _notify(crd: "Cardinal", text: str) -> None:
    if not crd.telegram:
        return
    kwargs = {"notification_type": tg_utils.NotificationTypes.other} if tg_utils else {}
    try:
        crd.telegram.send_notification(text, **kwargs)
    except Exception:
        logger.debug("TRACEBACK", exc_info=True)


# ══════════════════════════════ ШПИОНАЖ ═════════════════════════════════════════
def _format_viewing(viewing, buyer_id, buyer_name: str, context: str) -> str:
    """Форматирует результат get_buyer_viewing в текст уведомления."""
    head = f"👁 <b>Шпион покупателя</b> ({context})\n\n"
    who = (f"<a href=\"https://funpay.com/users/{buyer_id}/\">{_escape(buyer_name or 'покупатель')}</a>"
           if buyer_id else _escape(buyer_name or "покупатель"))
    if viewing is None:
        return head + f"{who}: не удалось получить данные (запрос не удался)."
    if not getattr(viewing, "is_viewing_lot", False) and not getattr(viewing, "text", None):
        return head + f"{who} сейчас <b>не смотрит</b> конкретный лот (или данные недоступны)."
    text = viewing.text or "(без описания)"
    line = f"{who} сейчас смотрит:\n<a href=\"{_escape(viewing.link or '')}\"><b>{_escape(_short(text, 150))}</b></a>"
    try:
        lot_id = viewing.lot_id
        if lot_id:
            line += f"\n\n🔢 ID лота: <code>{_escape(lot_id)}</code>"
    except Exception:
        pass
    if getattr(viewing, "tag", None):
        line += f"\n🏷 {_escape(_short(viewing.tag, 60))}"
    line += ("\n\n💡 <i>Если это лот конкурента — самое время предложить покупателю "
             "лучшие условия в чате.</i>")
    return head + line


def spy_buyer(crd: "Cardinal", buyer_id, buyer_name: str = "", context: str = "вручную",
              to_chat_id=None) -> str | None:
    """
    Проверяет, что смотрит покупатель. Возвращает текст результата.
    Если to_chat_id указан — отправляет результат напрямую в этот Telegram-чат.
    """
    if not buyer_id:
        text = "❌ Не указан ID покупателя."
        if to_chat_id and crd.telegram:
            try:
                crd.telegram.bot.send_message(to_chat_id, text, parse_mode="HTML")
            except Exception:
                pass
        return None
    # запоминаем последнего покупателя
    try:
        settings = _load_settings()
        settings["last_buyer"] = {"id": int(buyer_id), "name": str(buyer_name or "")}
        _save_settings(settings)
    except Exception:
        logger.debug("TRACEBACK", exc_info=True)
    viewing = None
    try:
        viewing = crd.account.get_buyer_viewing(int(buyer_id))
    except Exception:
        logger.error(f"BuyerSpy: не удалось получить «сейчас смотрит» для покупателя {buyer_id}.")
        logger.debug("TRACEBACK", exc_info=True)
    text = _format_viewing(viewing, buyer_id, buyer_name, context)
    if to_chat_id and crd.telegram:
        try:
            crd.telegram.bot.send_message(to_chat_id, text, parse_mode="HTML",
                                          reply_markup=_again_kb(buyer_id))
        except Exception:
            logger.debug("TRACEBACK", exc_info=True)
    else:
        _notify(crd, text)
    return text


def _again_kb(buyer_id) -> K | None:
    kb = K().add(B("🔄 Проверить еще раз", None, f"{CB_SPY_AGAIN}:{buyer_id}"))
    return kb


def new_order_handler(c: "Cardinal", e, *args) -> None:
    """BIND_TO_NEW_ORDER: шпионит за покупателем нового заказа (в фоне)."""
    try:
        if not _plugin_enabled(c):
            return
        settings = _load_settings()
        if not settings["auto_on_order"]:
            return
        order = getattr(e, "order", None)
        if order is None or not getattr(order, "buyer_id", None):
            return
        threading.Thread(target=spy_buyer,
                         args=(c, order.buyer_id, order.buyer_username or "",
                               f"новый заказ #{order.id}"),
                         daemon=True).start()
    except Exception:
        logger.error("BuyerSpy: ошибка в обработчике нового заказа.")
        logger.debug("TRACEBACK", exc_info=True)


def new_message_handler(c: "Cardinal", e, *args) -> None:
    """BIND_TO_NEW_MESSAGE: шпионит по первому сообщению покупателя в чате (опционально)."""
    try:
        if not _plugin_enabled(c):
            return
        settings = _load_settings()
        if not settings["auto_on_first_msg"]:
            return
        msg = getattr(e, "message", None)
        if msg is None:
            return
        my_id = getattr(c.account, "id", None)
        if getattr(msg, "by_bot", False) or msg.author_id == 0 or \
                (my_id is not None and msg.author_id == my_id):
            return
        if getattr(msg, "type", None) not in BUYER_MSG_TYPES:
            return
        chat_key = str(msg.chat_id)
        if chat_key in settings["spied_chats"]:
            return
        # отмечаем чат как «прошпионенный» (анти-спам), с ограничением размера кэша
        settings["spied_chats"][chat_key] = time.time()
        if len(settings["spied_chats"]) > MAX_SPIED_CHATS:
            ordered = sorted(settings["spied_chats"].items(), key=lambda kv: kv[1])
            settings["spied_chats"] = dict(ordered[-MAX_SPIED_CHATS:])
        _save_settings(settings)
        threading.Thread(target=spy_buyer,
                         args=(c, msg.author_id, msg.author or "", "первое сообщение в чате"),
                         daemon=True).start()
    except Exception:
        logger.error("BuyerSpy: ошибка в обработчике нового сообщения.")
        logger.debug("TRACEBACK", exc_info=True)


# ══════════════════════════ TELEGRAM-ИНTERFACE ══════════════════════════════════
CB_HOME = "spy:home"
CB_ORD_TOG = "spy:ordtog"
CB_MSG_TOG = "spy:msgtog"
CB_LAST = "spy:last"
CB_SPY_AGAIN = "spy:again"   # spy:again:{buyer_id}
CB_NOOP = "spy:noop"


def _home_text(crd: "Cardinal") -> str:
    settings = _load_settings()
    lb = settings["last_buyer"]
    lb_txt = (f"<a href=\"https://funpay.com/users/{lb['id']}/\">{_escape(lb['name'] or str(lb['id']))}</a>"
              if lb.get("id") else "пока никого")
    return (f"👁 <b>{_escape(NAME)}</b> <i>v{VERSION}</i>\n\n"
            f"Показывает, что покупатель смотрит на FunPay <b>прямо сейчас</b> — "
            f"ваш лот, категорию или лот конкурента.\n\n"
            f"🛒 При новом заказе: <b>{'ВКЛ 🟢' if settings['auto_on_order'] else 'ВЫКЛ 🔴'}</b>\n"
            f"💬 По первому сообщению в чате: "
            f"<b>{'ВКЛ 🟢' if settings['auto_on_first_msg'] else 'ВЫКЛ 🔴'}</b>\n"
            f"Последний покупатель: {lb_txt}\n\n"
            f"<i>Ручная проверка: отправьте </i><code>/spy 1234567</code><i> (ID покупателя) "
            f"или просто </i><code>/spy<i> — для последнего.</i>")


def _home_kb() -> K:
    settings = _load_settings()
    kb = K(row_width=1)
    kb.add(B(f"🛒 Авто-шпионаж при заказе: {'ВКЛ' if settings['auto_on_order'] else 'ВЫКЛ'}",
             None, CB_ORD_TOG))
    kb.add(B(f"💬 По первому сообщению: {'ВКЛ' if settings['auto_on_first_msg'] else 'ВЫКЛ'}",
             None, CB_MSG_TOG))
    if settings["last_buyer"].get("id"):
        kb.add(B(f"👀 Что смотрит {_short(settings['last_buyer'].get('name') or 'последний покупатель', 30)}?",
                 None, CB_LAST))
    kb.add(B("🔄 Обновить меню", None, CB_HOME))
    if CBT:
        kb.add(B("🔙 Назад к плагину", None, f"{CBT.EDIT_PLUGIN}:{UUID}:0"))
    return kb


def _answer(bot, c: CallbackQuery, text: str | None = None, alert: bool = False) -> None:
    try:
        bot.answer_callback_query(c.id, text, show_alert=alert)
    except Exception:
        pass


def _edit_or_send(bot, c: CallbackQuery, text: str, kb: K | None) -> None:
    try:
        bot.edit_message_text(text, c.message.chat.id, c.message.id, parse_mode="HTML", reply_markup=kb)
    except ApiTelegramException as e:
        if "message is not modified" not in str(e):
            try:
                bot.send_message(c.message.chat.id, text, parse_mode="HTML", reply_markup=kb)
            except Exception:
                logger.debug("TRACEBACK", exc_info=True)
    except Exception:
        try:
            bot.send_message(c.message.chat.id, text, parse_mode="HTML", reply_markup=kb)
        except Exception:
            logger.debug("TRACEBACK", exc_info=True)


def init(crd: "Cardinal", *args) -> None:
    """Инициализация плагина (BIND_TO_PRE_INIT)."""
    global _cardinal
    _cardinal = crd
    _load_settings()

    tg = crd.telegram
    if tg is not None:
        bot = tg.bot

        def open_home(c: CallbackQuery) -> None:
            _answer(bot, c)
            _edit_or_send(bot, c, _home_text(crd), _home_kb())

        def toggle_order(c: CallbackQuery) -> None:
            settings = _load_settings()
            settings["auto_on_order"] = not settings["auto_on_order"]
            _save_settings(settings)
            _answer(bot, c, f"🛒 Авто-шпионаж при заказе: {'ВКЛ' if settings['auto_on_order'] else 'ВЫКЛ'}")
            _edit_or_send(bot, c, _home_text(crd), _home_kb())

        def toggle_msg(c: CallbackQuery) -> None:
            settings = _load_settings()
            settings["auto_on_first_msg"] = not settings["auto_on_first_msg"]
            _save_settings(settings)
            _answer(bot, c, f"💬 По первому сообщению: "
                            f"{'ВКЛ' if settings['auto_on_first_msg'] else 'ВЫКЛ'}")
            _edit_or_send(bot, c, _home_text(crd), _home_kb())

        def spy_last(c: CallbackQuery) -> None:
            settings = _load_settings()
            lb = settings["last_buyer"]
            if not lb.get("id"):
                _answer(bot, c, "Нет сохраненного последнего покупателя.", alert=True)
                return
            _answer(bot, c, f"👀 Проверяю, что смотрит {lb.get('name') or lb['id']}...")
            threading.Thread(target=spy_buyer,
                             args=(crd, lb["id"], lb.get("name", ""), "по запросу"),
                             kwargs={"to_chat_id": c.message.chat.id}, daemon=True).start()

        def spy_again(c: CallbackQuery) -> None:
            parts = str(c.data).split(":")
            buyer_id = parts[2] if len(parts) > 2 and parts[2].isdigit() else None
            if not buyer_id:
                _answer(bot, c)
                return
            _answer(bot, c, "👀 Проверяю...")
            settings = _load_settings()
            name = settings["last_buyer"].get("name", "") if str(settings["last_buyer"].get("id")) == buyer_id else ""
            threading.Thread(target=spy_buyer, args=(crd, int(buyer_id), name, "по запросу"),
                             kwargs={"to_chat_id": c.message.chat.id}, daemon=True).start()

        def cmd_spy(m: Message) -> None:
            args = str(m.text or "").split(maxsplit=1)
            buyer_id = None
            if len(args) > 1:
                candidate = args[1].strip()
                if candidate.isdigit():
                    buyer_id = int(candidate)
            if buyer_id is None:
                lb = _load_settings()["last_buyer"]
                buyer_id = lb.get("id") or None
                name = lb.get("name", "")
            else:
                name = ""
            if not buyer_id:
                bot.send_message(m.chat.id, "❌ Укажите ID покупателя: <code>/spy 1234567</code>\n\n"
                                            "<i>ID можно найти в ссылке на профиль покупателя: "
                                            "funpay.com/users/<b>ID</b>/</i>", parse_mode="HTML")
                return
            if not crd.account.is_initiated:
                bot.send_message(m.chat.id, "⏳ Аккаунт FunPay еще инициализируется. Попробуйте позже.")
                return
            threading.Thread(target=spy_buyer, args=(crd, buyer_id, name, "по запросу"),
                             kwargs={"to_chat_id": m.chat.id}, daemon=True).start()

        def noop(c: CallbackQuery) -> None:
            _answer(bot, c)

        tg.cbq_handler(open_home, lambda c: c.data.startswith(f"{CBT.PLUGIN_SETTINGS}:{UUID}"))
        tg.cbq_handler(open_home, lambda c: c.data == CB_HOME)
        tg.cbq_handler(toggle_order, lambda c: c.data == CB_ORD_TOG)
        tg.cbq_handler(toggle_msg, lambda c: c.data == CB_MSG_TOG)
        tg.cbq_handler(spy_last, lambda c: c.data == CB_LAST)
        tg.cbq_handler(spy_again, lambda c: c.data.startswith(f"{CB_SPY_AGAIN}:"))
        tg.cbq_handler(noop, lambda c: c.data == CB_NOOP)
        tg.msg_handler(cmd_spy, commands=["spy"])

        try:
            crd.add_telegram_commands(UUID, [("spy", "Что покупатель смотрит сейчас? (/spy ID)", True)])
        except Exception:
            logger.debug("TRACEBACK", exc_info=True)

        logger.info(f"BuyerSpy v{VERSION}: Telegram-меню зарегистрировано (команда /spy).")
    else:
        logger.info(f"BuyerSpy v{VERSION}: Telegram выключен — авто-шпионаж шлет в лог.")


def on_delete(crd: "Cardinal", c=None) -> None:
    """Вызывается при удалении плагина через ПУ (BIND_TO_DELETE)."""
    _stop_event.set()


# ══════════════════════════ ПРИВЯЗКА К СОБЫТИЯМ КАРДИНАЛА ═══════════════════════
BIND_TO_PRE_INIT = [init]
BIND_TO_NEW_ORDER = [new_order_handler]
BIND_TO_NEW_MESSAGE = [new_message_handler]
BIND_TO_DELETE = on_delete
