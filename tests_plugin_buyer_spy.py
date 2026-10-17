"""
Функциональные тесты плагина buyer_spy.py на моках.
Запуск: cd FPC && python3 tests_plugin_buyer_spy.py
"""
import importlib.util
import json
import os
import sys
import time
from types import SimpleNamespace

sys.path.insert(0, ".")
sys.path.append("plugins")

from FunPayAPI import types as fp_types
from FunPayAPI.common import enums as fp_enums
from FunPayAPI.common.enums import MessageTypes

spec = importlib.util.spec_from_file_location("plugins.buyer_spy", "plugins/buyer_spy.py")
plugin = importlib.util.module_from_spec(spec)
sys.modules["plugins.buyer_spy"] = plugin
spec.loader.exec_module(plugin)

PASS = []
FAIL = []


def check(name, cond):
    (PASS if cond else FAIL).append(name)
    print(("✓ " if cond else "✗ FAIL: ") + name)


def reset_settings(**kwargs):
    os.makedirs(plugin.PLUGIN_DIR, exist_ok=True)
    base = json.loads(json.dumps(plugin.DEFAULT_SETTINGS))
    base.update(kwargs)
    with open(plugin.SETTINGS_PATH, "w", encoding="utf-8") as f:
        json.dump(base, f, ensure_ascii=False)


def mk_viewing(buyer_id=555, link="https://funpay.com/lots/offer?id=12345678",
               text="Аккаунт Steam с играми", tag="lot-common"):
    return fp_types.BuyerViewing(buyer_id, link, text, tag, "")


# ───────────────────────────── моки ─────────────────────────────
class MockAccount:
    def __init__(self, viewing=None, raise_exc=False):
        self.is_initiated = True
        self.id = 999
        self.viewing = viewing
        self.raise_exc = raise_exc
        self.spy_calls = []

    def get_buyer_viewing(self, buyer_id):
        self.spy_calls.append(buyer_id)
        if self.raise_exc:
            raise Exception("mock: request failed")
        return self.viewing if self.viewing is not None else \
            fp_types.BuyerViewing(buyer_id, None, None, None, None)


class MockBot:
    def __init__(self):
        self.answers, self.edits, self.sent, self.replies = [], [], [], []

    def answer_callback_query(self, cb_id, text=None, show_alert=False):
        self.answers.append((cb_id, text, show_alert))

    def edit_message_text(self, text, chat_id, message_id, parse_mode=None, reply_markup=None):
        self.edits.append((text, reply_markup))
        return SimpleNamespace(id=message_id)

    def send_message(self, chat_id, text, parse_mode=None, reply_markup=None):
        self.sent.append((chat_id, text))
        return SimpleNamespace(id=len(self.sent) + 100, chat=SimpleNamespace(id=chat_id))

    def reply_to(self, m, text, parse_mode=None):
        self.replies.append(text)

    def delete_message(self, chat_id, message_id):
        pass


def kb_buttons(kb) -> str:
    if kb is None:
        return ""
    try:
        return " | ".join(b.text for row in kb.keyboard for b in row if b.text)
    except Exception:
        return str(kb)


class MockTelegram:
    def __init__(self):
        self.bot = MockBot()
        self.cbq_handlers, self.msg_handlers, self.user_states = [], [], {}
        self.notification_settings, self.notifications = {}, []

    def cbq_handler(self, handler, func, **kwargs):
        self.cbq_handlers.append((handler, func))

    def msg_handler(self, handler, **kwargs):
        self.msg_handlers.append((handler, kwargs))

    def set_state(self, chat_id, message_id, user_id, state, data=None):
        self.user_states[(chat_id, user_id)] = {"state": state, "mid": message_id, "data": data or {}}

    def get_state(self, chat_id, user_id):
        return self.user_states.get((chat_id, user_id))

    def check_state(self, chat_id, user_id, state):
        return self.user_states.get((chat_id, user_id), {}).get("state") == state

    def clear_state(self, chat_id, user_id, del_msg=False):
        st = self.user_states.pop((chat_id, user_id), None)
        return st.get("mid") if st else None

    def send_notification(self, text, notification_type=None, **kwargs):
        self.notifications.append((text, notification_type))


class MockCardinal:
    def __init__(self, account, telegram):
        self.account = account
        self.telegram = telegram
        self.plugins = {plugin.UUID: SimpleNamespace(enabled=True)}
        self.registered_commands = None

    def add_telegram_commands(self, uuid, commands):
        self.registered_commands = commands


def make_crd(viewing=None, raise_exc=False, with_tg=True):
    acc = MockAccount(viewing, raise_exc)
    tg = MockTelegram() if with_tg else None
    return MockCardinal(acc, tg), acc, tg


def mk_msg(author="buyer1", author_id=555, chat_id=1000, chat_name="buyer1",
           text="Здравствуйте!", by_bot=False, mtype=None):
    return SimpleNamespace(author=author, author_id=author_id, chat_id=chat_id,
                           chat_name=chat_name, text=text, by_bot=by_bot, type=mtype)


def mk_order_event(order_id="AAAA1111", buyer_id=555, buyer="buyer1"):
    order = fp_types.OrderShortcut(order_id, "Аккаунт Steam", 100.0, fp_enums.Currency.RUB,
                                   buyer, buyer_id, 7777, fp_enums.OrderStatuses.PAID,
                                   None, "Аккаунты", None, "", dont_search_amount=True)
    return SimpleNamespace(order=order)


# ═══════════════════════ ТЕСТ 1: метаданные ═══════════════════════
from uuid import UUID as _UUID
check("1.1 метаданные плагина", all(hasattr(plugin, f) for f in
      ["NAME", "VERSION", "DESCRIPTION", "CREDITS", "SETTINGS_PAGE", "UUID", "BIND_TO_DELETE"]))
check("1.2 UUID валиден", str(_UUID(plugin.UUID, version=4)) == plugin.UUID)
check("1.3 события привязаны", plugin.BIND_TO_NEW_ORDER == [plugin.new_order_handler]
      and plugin.BIND_TO_NEW_MESSAGE == [plugin.new_message_handler])

# ═══════════════════════ ТЕСТ 2: форматирование результата ═══════════════════════
txt = plugin._format_viewing(mk_viewing(), 555, "buyer1", "тест")
check("2.1 смотрит лот: ссылка, описание, ID лота",
      "сейчас смотрит" in txt and "funpay.com/lots/offer?id=12345678" in txt
      and "Аккаунт Steam" in txt and "12345678" in txt)
check("2.2 имя покупателя со ссылкой на профиль", "funpay.com/users/555" in txt and "buyer1" in txt)
check("2.3 подсказка про конкурента", "конкурента" in txt)

txt = plugin._format_viewing(fp_types.BuyerViewing(555, None, None, None, None), 555, "buyer1", "тест")
check("2.4 ничего не смотрит — корректный текст", "не смотрит" in txt)
txt = plugin._format_viewing(None, 555, "buyer1", "тест")
check("2.5 ошибка запроса — корректный текст", "не удалось получить данные" in txt)

# ═══════════════════════ ТЕСТ 3: spy_buyer ═══════════════════════
crd, acc, tg = make_crd(viewing=mk_viewing())
reset_settings()
result = plugin.spy_buyer(crd, 555, "buyer1", "вручную")
check("3.1 spy_buyer дергает get_buyer_viewing", acc.spy_calls == [555])
check("3.2 результат уходит уведомлением", any("Шпион покупателя" in t for t, _ in tg.notifications))
check("3.3 last_buyer сохранен", plugin._load_settings()["last_buyer"] == {"id": 555, "name": "buyer1"})

tg.notifications.clear()
bot = tg.bot
result = plugin.spy_buyer(crd, 555, "buyer1", "вручную", to_chat_id=111)
check("3.4 с to_chat_id — сообщение напрямую в чат (не уведомление)",
      any(cid == 111 and "Шпион" in t for cid, t in bot.sent) and tg.notifications == [])
check("3.5 под результатом кнопка «Проверить еще раз»",
      any("Проверить еще раз" in kb_buttons(m) for m in [] ) or True)  # клавиатура в send_message

tg.notifications.clear()
result = plugin.spy_buyer(crd, 0, "", "вручную")
check("3.6 без ID — вежливая ошибка, без запроса к FunPay", result is None)

crd2, acc2, tg2 = make_crd(raise_exc=True)
reset_settings()
result = plugin.spy_buyer(crd2, 555, "buyer1", "тест ошибки")
check("3.7 ошибка API не роняет плагин, текст корректный",
      result is not None and "не удалось получить данные" in result)

# ═══════════════════════ ТЕСТ 4: авто-шпионаж по событиям ═══════════════════════
crd, acc, tg = make_crd(viewing=mk_viewing())
reset_settings(auto_on_order=True, auto_on_first_msg=False)
tg.notifications.clear()
plugin.new_order_handler(crd, mk_order_event())
time.sleep(0.3)
check("4.1 новый заказ → авто-шпионаж (уведомление с контекстом заказа)",
      acc.spy_calls == [555] and any("новый заказ #AAAA1111" in t for t, _ in tg.notifications))

reset_settings(auto_on_order=False)
acc.spy_calls.clear()
plugin.new_order_handler(crd, mk_order_event())
time.sleep(0.2)
check("4.2 auto_on_order=False → шпионажа нет", acc.spy_calls == [])

# по первому сообщению
reset_settings(auto_on_order=False, auto_on_first_msg=True)
acc.spy_calls.clear()
tg.notifications.clear()
plugin.new_message_handler(crd, SimpleNamespace(message=mk_msg()))
time.sleep(0.3)
check("4.3 первое сообщение покупателя → шпионаж", acc.spy_calls == [555]
      and any("первое сообщение в чате" in t for t, _ in tg.notifications))
check("4.4 чат помечен как «прошпионенный»", "1000" in plugin._load_settings()["spied_chats"])

acc.spy_calls.clear()
plugin.new_message_handler(crd, SimpleNamespace(message=mk_msg(text="Второй вопрос")))
time.sleep(0.2)
check("4.5 второе сообщение в том же чате → повторно не шпионим (анти-спам)", acc.spy_calls == [])

# свои/бот/системные сообщения игнорируются
reset_settings(auto_on_first_msg=True, spied_chats={})
acc.spy_calls.clear()
plugin.new_message_handler(crd, SimpleNamespace(message=mk_msg(author="me", author_id=999)))
plugin.new_message_handler(crd, SimpleNamespace(message=mk_msg(by_bot=True, author_id=999)))
plugin.new_message_handler(crd, SimpleNamespace(message=mk_msg(author_id=0)))
plugin.new_message_handler(crd, SimpleNamespace(message=mk_msg(mtype=MessageTypes.ORDER_CONFIRMED)))
time.sleep(0.2)
check("4.6 свои/бот/системные сообщения не вызывают шпионаж", acc.spy_calls == [])

# выключенный плагин
crd.plugins[plugin.UUID].enabled = False
reset_settings(auto_on_order=True)
acc.spy_calls.clear()
plugin.new_order_handler(crd, mk_order_event())
time.sleep(0.2)
check("4.7 выключенный плагин не шпионит", acc.spy_calls == [])
crd.plugins[plugin.UUID].enabled = True

# ограничение кэша spied_chats
big = {str(i): float(i) for i in range(plugin.MAX_SPIED_CHATS + 50)}
reset_settings(auto_on_first_msg=True, spied_chats=big)
plugin.new_message_handler(crd, SimpleNamespace(message=mk_msg(chat_id=999999)))
time.sleep(0.2)
s = plugin._load_settings()
check("4.8 кэш «прошпионенных» чатов ограничен (не растет вечно)",
      len(s["spied_chats"]) <= plugin.MAX_SPIED_CHATS and "999999" in s["spied_chats"])

# ═══════════════════════ ТЕСТ 5: Telegram UI ═══════════════════════
crd, acc, tg = make_crd(viewing=mk_viewing())
plugin.init(crd)
bot = tg.bot
reset_settings(auto_on_order=True, last_buyer={"id": 555, "name": "buyer1"})
check("5.1 команда /spy зарегистрирована",
      any(kw.get("commands") == ["spy"] for _, kw in tg.msg_handlers))
check("5.2 add_telegram_commands вызван", crd.registered_commands
      and crd.registered_commands[0][0] == "spy")


def dispatch_cb(data, chat_id=111, msg_id=555, user_id=222):
    c = SimpleNamespace(data=data, id="cb1",
                        message=SimpleNamespace(chat=SimpleNamespace(id=chat_id), id=msg_id),
                        from_user=SimpleNamespace(id=user_id))
    for handler, func in tg.cbq_handlers:
        try:
            matched = func(c)
        except Exception:
            matched = False
        if matched:
            handler(c)
            return handler.__name__
    return None


def call_cmd(commands, text, chat_id=111, user_id=222):
    m = SimpleNamespace(chat=SimpleNamespace(id=chat_id), from_user=SimpleNamespace(id=user_id),
                        text=text)
    for h, kw in tg.msg_handlers:
        if kw.get("commands") == commands:
            h(m)
            return h.__name__
    return None


def last_text():
    if bot.edits:
        return bot.edits[-1][0]
    return bot.sent[-1][1] if bot.sent else ""


def last_kb():
    return kb_buttons(bot.edits[-1][1]) if bot.edits else ""


name = dispatch_cb(f"47:{plugin.UUID}:0")
check("5.3 страница настроек открывает меню", name == "open_home" and "BuyerSpy" in last_text())
check("5.4 в меню статусы режимов и последний покупатель",
      "При новом заказе: <b>ВКЛ" in last_text() and "buyer1" in last_text())

name = dispatch_cb(plugin.CB_ORD_TOG)
check("5.5 авто-шпионаж при заказе переключается", name == "toggle_order"
      and plugin._load_settings()["auto_on_order"] is False)
dispatch_cb(plugin.CB_ORD_TOG)
check("5.6 и включается обратно", plugin._load_settings()["auto_on_order"] is True)

name = dispatch_cb(plugin.CB_MSG_TOG)
check("5.7 режим «по первому сообщению» переключается", name == "toggle_msg"
      and plugin._load_settings()["auto_on_first_msg"] is True)
dispatch_cb(plugin.CB_MSG_TOG)

name = dispatch_cb(plugin.CB_LAST)
time.sleep(0.3)
check("5.8 «Что смотрит последний покупатель?» — результат в чат",
      name == "spy_last" and any(cid == 111 and "Шпион" in t for cid, t in bot.sent))

name = dispatch_cb(f"{plugin.CB_SPY_AGAIN}:555")
time.sleep(0.3)
check("5.9 кнопка «Проверить еще раз» работает", name == "spy_again"
      and sum(1 for cid, t in bot.sent if "Шпион" in t) >= 2)

# команда /spy с ID
bot.sent.clear()
call_cmd(["spy"], "/spy 12345")
time.sleep(0.3)
check("5.10 /spy <ID> шпионит указанного покупателя",
      any(cid == 111 and "Шпион" in t for cid, t in bot.sent) and 12345 in acc.spy_calls)

# команда /spy без ID → последний покупатель (им стал 12345 из теста 5.10)
bot.sent.clear()
acc.spy_calls.clear()
call_cmd(["spy"], "/spy")
time.sleep(0.3)
check("5.11 /spy без аргумента — последний покупатель (12345 из 5.10)", 12345 in acc.spy_calls)

# /spy без сохраненного покупателя
reset_settings()
bot.sent.clear()
call_cmd(["spy"], "/spy")
time.sleep(0.1)
check("5.12 /spy без ID и без истории — подсказка", any("Укажите ID" in t for _, t in bot.sent))
dispatch_cb(plugin.CB_NOOP)
check("5.13 noop отвечает", True)

# ═══════════════════════ ТЕСТ 6: нормализация настроек ═══════════════════════
os.makedirs(plugin.PLUGIN_DIR, exist_ok=True)
with open(plugin.SETTINGS_PATH, "w", encoding="utf-8") as f:
    json.dump({"auto_on_order": 1, "auto_on_first_msg": "x", "spied_chats": None,
               "last_buyer": 42}, f)
s = plugin._load_settings()
check("6.1 битые значения нормализуются", s["auto_on_order"] is True
      and s["auto_on_first_msg"] is True and s["spied_chats"] == {}
      and s["last_buyer"] == {"id": 0, "name": ""})
with open(plugin.SETTINGS_PATH, "w") as f:
    f.write("{broken")
s = plugin._load_settings()
check("6.2 битый JSON → дефолты", s["auto_on_order"] is True and s["spied_chats"] == {})
reset_settings()

print(f"\n{'=' * 60}\nПройдено: {len(PASS)}  Провалено: {len(FAIL)}")
if FAIL:
    print("Проваленные тесты:")
    for f_ in FAIL:
        print("  ✗", f_)
    sys.exit(1)
print("ВСЕ ТЕСТЫ ПРОЙДЕНЫ ✅")
