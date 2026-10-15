#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""Обед в школе.

Ольга Борисовна каждый день кидает опрос в тему «ЗАКАЗ ОБЕДА» родительского
чата (голос до 9:00). Сторож ловит опрос, показывает малому (и папе) карточку
с кнопками-блюдами в отдельном боте, по нажатиям голосует в опросе от имени
папы. Не ответили: напоминает, к дедлайну голосует по умолчанию.

Два клиента Telethon в одном процессе: user (аккаунт папы) и bot (Bot API).
"""
import asyncio
import datetime as dt
import json
import logging
import os
import re
import sys
from zoneinfo import ZoneInfo

sys.path.insert(0, "/srv/lunch")
import config  # noqa: E402

from telethon import Button, TelegramClient, events, functions, utils  # noqa: E402
from telethon.tl.types import MessageMediaPoll, PeerChannel  # noqa: E402

MSK = ZoneInfo("Europe/Moscow")
DAYS = ("понедельник", "вторник", "среда", "четверг", "пятница", "суббота", "воскресенье")
HOME = "/srv/lunch"
STATE_FILE = os.path.join(HOME, "state.json")
CHAT_PEER = utils.get_peer_id(PeerChannel(config.CHAT_ID))

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s %(levelname)s %(message)s",
    datefmt="%d.%m %H:%M:%S",
)
log = logging.getLogger("lunch")

user = TelegramClient(os.path.join(HOME, "user"), config.TG_API_ID, config.TG_API_HASH)
bot = TelegramClient(os.path.join(HOME, "bot"), config.TG_API_ID, config.TG_API_HASH)

# ---------------------------------------------------------------- состояние
# state["poll"] = {
#   msg_id, date (YYYY-MM-DD), question, dishes: [..5 названий..],
#   options: [..bytes-as-str..], sel: {"soup": idx|-1, "main": idx, "side": idx},
#   skip: bool, confirmed: bool, voted: [idx...], cards: {chat_id: msg_id},
#   reminded: [tag...], default_used: bool }
state = {"poll": None, "known": {}}


def load_state():
    global state
    try:
        with open(STATE_FILE, encoding="utf-8") as f:
            state = json.load(f)
    except FileNotFoundError:
        pass
    state.setdefault("poll", None)
    state.setdefault("known", {})


def save_state():
    tmp = STATE_FILE + ".tmp"
    with open(tmp, "w", encoding="utf-8") as f:
        json.dump(state, f, ensure_ascii=False, indent=1)
    os.replace(tmp, STATE_FILE)


def now_msk():
    return dt.datetime.now(MSK)


def allowed_ids():
    ids = {config.DAD_ID}
    if getattr(config, "KID_ID", None):
        ids.add(config.KID_ID)
    return ids


# ---------------------------------------------------------------- опрос
DATE_RE = re.compile(r"(\d{2})\.(\d{2})\.(\d{4})")


def poll_from_message(m):
    """MessageMediaPoll → dict нашего формата или None, если это не заказ обеда."""
    if not isinstance(m.media, MessageMediaPoll):
        return None
    p = m.media.poll
    q = p.question.text if hasattr(p.question, "text") else p.question
    if "обед" not in q.lower():
        return None
    md = DATE_RE.search(q)
    if not md:
        return None
    d, mo, y = md.groups()
    date = f"{y}-{mo}-{d}"
    dishes, options = [], []
    for a in p.answers:
        t = a.text.text if hasattr(a.text, "text") else a.text
        if "результат" in t.lower():
            continue  # «Результаты опроса (просмотр)» — служебный пункт
        dishes.append(t.strip())
        options.append(a.option.decode())
    if len(dishes) < 3:
        return None
    # что уже проголосовано с аккаунта папы (если голосовал руками)
    voted = []
    r = m.media.results
    if r and r.results:
        chosen = {rr.option.decode() for rr in r.results if rr.chosen}
        voted = [i for i, o in enumerate(options) if o in chosen]
    return {
        "msg_id": m.id, "date": date, "question": q,
        "dishes": dishes, "options": options,
        "sel": {}, "skip": False, "confirmed": False,
        "voted": voted, "cards": {}, "reminded": [], "default_used": False,
    }


def groups(poll):
    """Разбивка блюд: суп = [0], вторые = [1,2], гарниры = [3,4] (по позиции)."""
    n = len(poll["dishes"])
    return {"soup": [0], "main": list(range(1, min(3, n))), "side": list(range(3, n))}


def selection_indexes(poll):
    idx = []
    for g in ("soup", "main", "side"):
        v = poll["sel"].get(g)
        if v is not None and v >= 0:
            idx.append(v)
    return sorted(idx)


def selection_complete(poll):
    return bool(selection_indexes(poll))


def deadline(poll):
    y, m, d = map(int, poll["date"].split("-"))
    return dt.datetime(y, m, d, config.DEADLINE_HOUR, 0, tzinfo=MSK)


def is_closed(poll):
    return now_msk() >= deadline(poll)


# ---------------------------------------------------------------- карточка
def card_text(poll):
    y, m, d = poll["date"].split("-")
    lines = [f"🍽 **Обед на {d}.{m}.{y}**", ""]
    if is_closed(poll):
        lines.append("⏰ Приём заказов закрыт (после 9:00).")
    elif poll["skip"]:
        lines.append("🚫 В школу не идёт, обед не заказан.")
    else:
        lines.append("Отметь, что будешь, и жми «Заказать».")
    lines.append("")
    if poll["skip"]:
        status = "🚫 Заказ снят."
    elif poll["confirmed"]:
        names = [poll["dishes"][i] for i in poll["voted"]]
        status = "✅ Заказано: " + (" + ".join(names) if names else "ничего")
        if poll.get("default_used"):
            status += " (по умолчанию, никто не выбрал)"
    elif poll["voted"]:
        names = [poll["dishes"][i] for i in poll["voted"]]
        status = "✅ Уже проголосовано руками: " + " + ".join(names)
    else:
        status = "❌ Ещё не заказано."
    lines.append(status)
    return "\n".join(lines)


def card_buttons(poll):
    if is_closed(poll):
        return None
    g = groups(poll)
    sel = poll["sel"]
    rows = []

    def b(label, data, on):
        return Button.inline(("✅ " if on else "") + label, data.encode())

    rows.append([b(poll["dishes"][i], f"soup:{i}", sel.get("soup") == i) for i in g["soup"]])
    rows.append([b(poll["dishes"][i], f"main:{i}", sel.get("main") == i) for i in g["main"]])
    rows.append([b(poll["dishes"][i], f"side:{i}", sel.get("side") == i) for i in g["side"]])
    if poll["skip"]:
        rows.append([Button.inline("↩️ Всё-таки иду в школу", b"unskip")])
    else:
        rows.append([
            Button.inline("🚫 Не иду в школу", b"skip"),
            Button.inline("📨 Заказать", b"order"),
        ])
    return rows


async def send_cards(poll, targets=None):
    """Разослать карточку всем разрешённым (или конкретным) чатам."""
    for cid in (targets or allowed_ids()):
        try:
            msg = await bot.send_message(cid, card_text(poll), buttons=card_buttons(poll))
            poll["cards"][str(cid)] = msg.id
        except Exception as e:  # noqa: BLE001
            log.warning("карточка в %s не ушла: %s", cid, e)
    save_state()


async def refresh_cards(poll):
    for cid, mid in list(poll["cards"].items()):
        try:
            await bot.edit_message(int(cid), mid, card_text(poll), buttons=card_buttons(poll))
        except Exception as e:  # noqa: BLE001
            if "not modified" not in str(e).lower():
                log.warning("карточка %s/%s не обновилась: %s", cid, mid, e)


async def notify(text, targets=None):
    for cid in (targets or allowed_ids()):
        try:
            await bot.send_message(cid, text)
        except Exception as e:  # noqa: BLE001
            log.warning("уведомление в %s не ушло: %s", cid, e)


# ---------------------------------------------------------------- голосование
async def vote(poll, indexes):
    opts = [poll["options"][i].encode() for i in indexes]
    await user(functions.messages.SendVoteRequest(
        peer=PeerChannel(config.CHAT_ID), msg_id=poll["msg_id"], options=opts))
    poll["voted"] = sorted(indexes)
    poll["confirmed"] = True
    save_state()
    log.info("проголосовано %s: %s", poll["date"], [poll["dishes"][i] for i in indexes])


async def retract(poll):
    await user(functions.messages.SendVoteRequest(
        peer=PeerChannel(config.CHAT_ID), msg_id=poll["msg_id"], options=[]))
    poll["voted"] = []
    save_state()
    log.info("голос снят %s", poll["date"])


# ---------------------------------------------------------------- новый опрос
async def on_new_poll(m):
    poll = poll_from_message(m)
    if not poll:
        return
    if poll["date"] < now_msk().strftime("%Y-%m-%d"):
        log.info("опрос %s уже в прошлом, игнор", poll["date"])
        return
    cur = state.get("poll")
    if cur and cur["msg_id"] == poll["msg_id"]:
        return
    log.info("новый опрос %s (msg %s): %s", poll["date"], poll["msg_id"], poll["dishes"])
    wd = dt.date.fromisoformat(poll["date"]).weekday()
    if wd in getattr(config, "SKIP_WEEKDAYS", ()):
        # в этот день недели обед не заказываем: карточек нет, автозаказа нет
        poll["skip"] = True
        poll["confirmed"] = True
        state["poll"] = poll
        save_state()
        log.info("день недели %s в SKIP_WEEKDAYS, пропускаем", wd)
        await notify(f"📅 {poll['date'][8:]}.{poll['date'][5:7]} ({DAYS[wd]}): обед не заказываем, карточку не шлю.",
                     targets=[config.DAD_ID])
        return
    if poll["voted"]:  # папа уже голосовал руками
        poll["confirmed"] = True
        for g, idxs in groups(poll).items():
            for i in idxs:
                if i in poll["voted"]:
                    poll["sel"][g] = i
    state["poll"] = poll
    save_state()
    await send_cards(poll)


async def catch_up():
    """При старте: последний опрос в теме, вдруг пропустили."""
    ent = await user.get_entity(PeerChannel(config.CHAT_ID))
    async for m in user.iter_messages(ent, limit=15, reply_to=config.TOPIC_ID):
        if isinstance(m.media, MessageMediaPoll):
            await on_new_poll(m)
            break


@user.on(events.NewMessage(chats=CHAT_PEER))
async def user_new_message(ev):
    m = ev.message
    rt = m.reply_to
    top = getattr(rt, "reply_to_top_id", None) or getattr(rt, "reply_to_msg_id", None)
    if top != config.TOPIC_ID:
        return
    if isinstance(m.media, MessageMediaPoll):
        await on_new_poll(m)


# ---------------------------------------------------------------- кнопки бота
@bot.on(events.NewMessage(pattern=r"^/start"))
async def bot_start(ev):
    uid = ev.sender_id
    s = await ev.get_sender()
    name = " ".join(x for x in (getattr(s, "first_name", ""), getattr(s, "last_name", "")) if x)
    state["known"][str(uid)] = name
    save_state()
    if uid not in allowed_ids():
        await ev.reply("Привет. Тебя ещё не подключили, папе сказала.")
        await notify(f"👤 /start от {name} (id {uid}). Чтобы подключить, впиши KID_ID = {uid} в config.py.",
                     targets=[config.DAD_ID])
        return
    await ev.reply("Привет! Сюда будут приходить обеды на выбор.")
    poll = state.get("poll")
    if poll and not is_closed(poll):
        await send_cards(poll, targets=[uid])


@bot.on(events.CallbackQuery)
async def bot_button(ev):
    uid = ev.sender_id
    if uid not in allowed_ids():
        await ev.answer("Не для тебя", alert=True)
        return
    poll = state.get("poll")
    if not poll:
        await ev.answer("Сейчас нет открытого опроса")
        return
    if is_closed(poll):
        await ev.answer("Приём заказов закрыт", alert=True)
        await refresh_cards(poll)
        return
    data = ev.data.decode()
    try:
        if data == "skip":
            poll["skip"] = True
            if poll["voted"]:
                await retract(poll)
            poll["confirmed"] = True
            await ev.answer("Ок, обед снят")
            await notify(f"🚫 {poll['date'][8:]}.{poll['date'][5:7]}: в школу не идёт, обед снят.")
        elif data == "unskip":
            poll["skip"] = False
            poll["confirmed"] = False
            await ev.answer("Выбирай обед")
        elif data == "order":
            if not selection_complete(poll):
                await ev.answer("Ничего не отмечено. Если обед не нужен, жми «Не иду в школу»", alert=True)
                return
            poll["skip"] = False
            poll["default_used"] = False
            await vote(poll, selection_indexes(poll))
            names = " + ".join(poll["dishes"][i] for i in poll["voted"])
            await ev.answer("Заказано!")
            await notify(f"✅ Обед на {poll['date'][8:]}.{poll['date'][5:7]}: {names}")
        else:
            g, i = data.split(":")
            i = int(i)
            # все кнопки — переключатели: повторное нажатие снимает
            poll["sel"][g] = -1 if poll["sel"].get(g) == i else i
            poll["skip"] = False
            await ev.answer()
            if poll["confirmed"]:
                # уже заказано, а выбор поменяли: переголосовать сразу (пусто = снять голос)
                if selection_complete(poll):
                    await vote(poll, selection_indexes(poll))
                else:
                    await retract(poll)
        save_state()
        await refresh_cards(poll)
    except Exception as e:  # noqa: BLE001
        log.exception("кнопка %s", data)
        await ev.answer(f"Ошибка: {e}"[:190], alert=True)


# ---------------------------------------------------------------- планировщик
async def scheduler():
    """Раз в 30 с: напоминания и дефолт по расписанию (время МСК)."""
    while True:
        try:
            poll = state.get("poll")
            if poll and not poll["confirmed"] and not poll["skip"]:
                n = now_msk()
                dl = deadline(poll)
                eve = (dl - dt.timedelta(days=1)).date()
                for tag, when in (
                    ("eve", dt.datetime.combine(eve, dt.time(config.REMIND_EVE_HOUR, 0), MSK)),
                    ("morning", dt.datetime.combine(dl.date(), dt.time(config.REMIND_MORNING_HOUR, config.REMIND_MORNING_MIN), MSK)),
                ):
                    if tag not in poll["reminded"] and when <= n < dl:
                        poll["reminded"].append(tag)
                        save_state()
                        await notify("⏰ Обед на завтра ещё не выбран!" if tag == "eve"
                                     else "⏰ Обед на сегодня не выбран, до 9:00 осталось мало!")
                        await send_cards(poll)
                dflt = dt.datetime.combine(dl.date(), dt.time(config.DEFAULT_HOUR, config.DEFAULT_MIN), MSK)
                if config.DEFAULT_ORDER and "default" not in poll["reminded"] and dflt <= n < dl:
                    poll["reminded"].append("default")
                    if selection_complete(poll):
                        # что-то отметили, но «Заказать» не нажали: шлём отмеченное
                        await vote(poll, selection_indexes(poll))
                        names = " + ".join(poll["dishes"][i] for i in poll["voted"])
                        await notify(f"🤖 «Заказать» не нажали, отправила отмеченное: {names}")
                        await refresh_cards(poll)
                        raise StopIteration
                    g = groups(poll)
                    idx = []
                    # блюда из AVOID_DISHES (ребёнок не ест) в автозаказ не берём
                    avoid = [a.lower() for a in getattr(config, "AVOID_DISHES", [])]
                    soup_name = poll["dishes"][g["soup"][0]] if g["soup"] else ""
                    soup_ok = bool(g["soup"]) and not any(a in soup_name.lower() for a in avoid)
                    if config.DEFAULT_ORDER.get("soup") and soup_ok:
                        idx += g["soup"][:1]
                    idx.append(g["main"][config.DEFAULT_ORDER.get("main", 0)])
                    idx.append(g["side"][config.DEFAULT_ORDER.get("side", 0)])
                    for gname, i in zip(("soup", "main", "side"), (idx[0] if len(idx) == 3 else -1, idx[-2], idx[-1])):
                        poll["sel"][gname] = i
                    poll["default_used"] = True
                    await vote(poll, idx)
                    names = " + ".join(poll["dishes"][i] for i in idx)
                    note = "" if soup_ok else f" (суп «{soup_name}» пропустила, ребёнок такой не ест)"
                    await notify(f"🤖 Никто не выбрал, заказала по умолчанию: {names}{note}")
                    await refresh_cards(poll)
        except StopIteration:
            pass
        except Exception:  # noqa: BLE001
            log.exception("scheduler")
        await asyncio.sleep(30)


# ---------------------------------------------------------------- main
async def main():
    load_state()
    await user.start()
    me = await user.get_me()
    log.info("user: %s (%s)", me.id, me.username)
    await bot.start(bot_token=config.BOT_TOKEN)
    bme = await bot.get_me()
    log.info("bot: @%s", bme.username)
    await catch_up()
    asyncio.create_task(scheduler())
    log.info("готова, слежу за темой %s в чате %s", config.TOPIC_ID, config.CHAT_ID)
    await asyncio.gather(user.run_until_disconnected(), bot.run_until_disconnected())


if __name__ == "__main__":
    asyncio.run(main())
