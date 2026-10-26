import asyncio
import logging
import sqlite3
import os
from datetime import datetime, timedelta

from aiogram import Bot, Dispatcher, F
from aiogram.client.default import DefaultBotProperties
from aiogram.enums import ParseMode
from aiogram.filters import CommandStart, Command
from aiogram.types import Message, CallbackQuery, InlineKeyboardMarkup, InlineKeyboardButton, BotCommand
from aiogram.fsm.storage.memory import MemoryStorage

# ========== НАСТРОЙКИ (меняешь только это) ==========
BOT_TOKEN = os.getenv("BOT_TOKEN", "8908550813:AAED2wjaCbHCfabaIaJCsBRV1qzNBidp7GE")
ADMIN_ID = int(os.getenv("ADMIN_ID", "7342606169"))  # твой telegram id
LINKNI_URL = "https://telegram.me/linknibot/app?startapp=x_1ou2x"

REWARD_PER_CLAIM = 0.3       # сколько получает пользователь за 1 подписку
CLAIM_COOLDOWN_MIN = 3        # минут между заявками
DAILY_CLAIM_LIMIT = 15        # максимум заявок в сутки на пользователя
WITHDRAW_MIN = 15.0           # минимум звёзд НА БАЛАНСЕ для вывода (50 к выплате + 30% комиссии = 65)
WITHDRAWAL_COMMISSION = 0.30  # комиссия при выводе (30%) — столько теряется при конвертации звёзд

REFERRAL_REWARD = 3.0         # сколько получает пригласивший
REFERRAL_REQUIRED_CLAIMS = 5  # сколько заданий должен выполнить приглашённый, чтобы пригласивший получил награду

BOT_DISPLAY_NAME = "StarVault"
DIVIDER = "┄┄┄┄┄┄┄┄┄┄┄┄┄┄"
# =====================================================

logging.basicConfig(level=logging.INFO)
bot = Bot(token=BOT_TOKEN, default=DefaultBotProperties(parse_mode=ParseMode.HTML))
dp = Dispatcher(storage=MemoryStorage())

DB_PATH = "linkni_bot.db"


def db():
    conn = sqlite3.connect(DB_PATH)
    conn.row_factory = sqlite3.Row
    return conn


def init_db():
    conn = db()
    conn.execute("""
        CREATE TABLE IF NOT EXISTS users (
            user_id INTEGER PRIMARY KEY,
            username TEXT,
            balance REAL DEFAULT 0,
            referred_by INTEGER,
            referral_rewarded INTEGER DEFAULT 0,
            created_at TEXT
        )
    """)
    conn.execute("""
        CREATE TABLE IF NOT EXISTS claims (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            user_id INTEGER,
            status TEXT DEFAULT 'confirmed',
            created_at TEXT
        )
    """)
    conn.execute("""
        CREATE TABLE IF NOT EXISTS withdrawals (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            user_id INTEGER,
            amount REAL,
            net_amount REAL,
            status TEXT DEFAULT 'pending',
            created_at TEXT
        )
    """)
    conn.commit()
    conn.close()


def get_or_create_user(user_id: int, username: str, referred_by: int = None):
    """Возвращает (row, is_new). Если пользователь новый и указан валидный referred_by — сохраняет его."""
    conn = db()
    row = conn.execute("SELECT * FROM users WHERE user_id=?", (user_id,)).fetchone()
    is_new = False
    if not row:
        is_new = True
        valid_ref = referred_by if (referred_by and referred_by != user_id) else None
        # реферер должен реально существовать в базе
        if valid_ref:
            ref_exists = conn.execute("SELECT 1 FROM users WHERE user_id=?", (valid_ref,)).fetchone()
            if not ref_exists:
                valid_ref = None
        conn.execute(
            "INSERT INTO users (user_id, username, balance, referred_by, referral_rewarded, created_at) VALUES (?,?,0,?,0,?)",
            (user_id, username, valid_ref, datetime.utcnow().isoformat())
        )
        conn.commit()
        row = conn.execute("SELECT * FROM users WHERE user_id=?", (user_id,)).fetchone()
    conn.close()
    return row, is_new


def maybe_reward_referrer(user_id: int):
    """Проверяет, выполнил ли пользователь достаточно заданий, чтобы его реферер получил награду."""
    conn = db()
    user = conn.execute("SELECT * FROM users WHERE user_id=?", (user_id,)).fetchone()
    if not user or not user["referred_by"] or user["referral_rewarded"]:
        conn.close()
        return None

    claims_count = conn.execute(
        "SELECT COUNT(*) c FROM claims WHERE user_id=?", (user_id,)
    ).fetchone()["c"]

    if claims_count >= REFERRAL_REQUIRED_CLAIMS:
        referrer_id = user["referred_by"]
        conn.execute(
            "UPDATE users SET balance = balance + ? WHERE user_id=?",
            (REFERRAL_REWARD, referrer_id)
        )
        conn.execute(
            "UPDATE users SET referral_rewarded = 1 WHERE user_id=?", (user_id,)
        )
        conn.commit()
        conn.close()
        return referrer_id

    conn.close()
    return None


def main_menu():
    kb = InlineKeyboardMarkup(inline_keyboard=[
        [InlineKeyboardButton(text="🔗 Получить ссылку", callback_data="get_link")],
        [InlineKeyboardButton(text="💰 Баланс", callback_data="balance"),
         InlineKeyboardButton(text="📤 Вывод", callback_data="withdraw")],
        [InlineKeyboardButton(text="👥 Пригласить друга", callback_data="invite")],
        [InlineKeyboardButton(text="ℹ️ Как это работает", callback_data="help")],
    ])
    return kb


@dp.message(CommandStart())
async def start(message: Message):
    # парсим payload вида "/start 123456789"
    parts = message.text.split(maxsplit=1)
    referred_by = None
    if len(parts) == 2 and parts[1].isdigit():
        referred_by = int(parts[1])

    user, is_new = get_or_create_user(message.from_user.id, message.from_user.username or "", referred_by)

    await message.answer(
        f"✨ <b>{BOT_DISPLAY_NAME}</b> ✨\n"
        f"{DIVIDER}\n\n"
        "Зарабатывай звёзды за подписки — легко и быстро.\n\n"
        f"🔗 Жми <b>«Получить ссылку»</b>, подписывайся на канал и подтверждай — "
        f"получишь <b>{REWARD_PER_CLAIM}⭐</b> на баланс.\n\n"
        f"👥 За друга, которого пригласишь, получишь <b>{REFERRAL_REWARD}⭐</b> — "
        f"как только он выполнит {REFERRAL_REQUIRED_CLAIMS} задания.\n\n"
        f"📤 Минимум для вывода: <b>{WITHDRAW_MIN:.0f}⭐</b>\n\n"
        f"{DIVIDER}",
        reply_markup=main_menu()
    )

    if is_new and user["referred_by"] and ADMIN_ID:
        try:
            await bot.send_message(
                user["referred_by"],
                f"👀 По твоей ссылке пришёл новый человек!\n"
                f"Как только он выполнит {REFERRAL_REQUIRED_CLAIMS} задания — тебе начислится <b>{REFERRAL_REWARD}⭐</b>."
            )
        except Exception:
            pass


@dp.callback_query(F.data == "help")
async def help_menu(callback: CallbackQuery):
    kb = InlineKeyboardMarkup(inline_keyboard=[
        [InlineKeyboardButton(text="⬅️ Назад", callback_data="back")],
    ])
    await callback.message.edit_text(
        f"ℹ️ <b>Как это работает</b>\n"
        f"{DIVIDER}\n\n"
        "1️⃣ Жмёшь «Получить ссылку»\n"
        "2️⃣ Переходишь и подписываешься на канал\n"
        f"3️⃣ Возвращаешься и жмёшь «Я подписался» — получаешь <b>{REWARD_PER_CLAIM}⭐</b>\n\n"
        f"👥 Зовёшь друзей по своей ссылке — за каждого, кто выполнит "
        f"{REFERRAL_REQUIRED_CLAIMS} задания, получаешь <b>{REFERRAL_REWARD}⭐</b>\n\n"
        f"📤 Копишь до <b>{WITHDRAW_MIN:.0f}⭐</b> и выводишь\n\n"
        "⚠️ Накрутка и спам заявок ведут к бану.",
        reply_markup=kb
    )
    await callback.answer()


@dp.callback_query(F.data == "invite")
async def invite(callback: CallbackQuery):
    me = await bot.get_me()
    link = f"https://t.me/{me.username}?start={callback.from_user.id}"
    kb = InlineKeyboardMarkup(inline_keyboard=[
        [InlineKeyboardButton(text="⬅️ Назад", callback_data="back")],
    ])
    await callback.message.edit_text(
        f"👥 <b>Твоя реферальная ссылка</b>\n"
        f"{DIVIDER}\n\n"
        f"<code>{link}</code>\n\n"
        f"За каждого друга, который выполнит {REFERRAL_REQUIRED_CLAIMS} задания, "
        f"ты получишь <b>{REFERRAL_REWARD}⭐</b> мгновенно, как только он это сделает.",
        reply_markup=kb
    )
    await callback.answer()


@dp.callback_query(F.data == "get_link")
async def get_link(callback: CallbackQuery):
    kb = InlineKeyboardMarkup(inline_keyboard=[
        [InlineKeyboardButton(text="➡️ Перейти и подписаться", url=LINKNI_URL)],
        [InlineKeyboardButton(text="✅ Я подписался", callback_data="claim")],
        [InlineKeyboardButton(text="⬅️ Назад", callback_data="back")],
    ])
    await callback.message.edit_text(
        f"🔗 <b>Получи звёзды за подписку</b>\n"
        f"{DIVIDER}\n\n"
        "1️⃣ Жми кнопку ниже и подпишись на канал\n"
        "2️⃣ Вернись сюда и нажми «Я подписался»\n\n"
        "⚠️ <i>Заявки проверяются, накрутка приводит к бану.</i>",
        reply_markup=kb
    )
    await callback.answer()


@dp.callback_query(F.data == "claim")
async def claim(callback: CallbackQuery):
    user_id = callback.from_user.id
    conn = db()

    # проверка кулдауна
    last = conn.execute(
        "SELECT created_at FROM claims WHERE user_id=? ORDER BY id DESC LIMIT 1",
        (user_id,)
    ).fetchone()
    now = datetime.utcnow()
    if last:
        last_time = datetime.fromisoformat(last["created_at"])
        if now - last_time < timedelta(minutes=CLAIM_COOLDOWN_MIN):
            wait = CLAIM_COOLDOWN_MIN - int((now - last_time).total_seconds() // 60)
            await callback.answer(f"⏳ Подожди ещё ~{max(wait,1)} мин перед новой заявкой", show_alert=True)
            conn.close()
            return

    # проверка дневного лимита
    today_start = now.replace(hour=0, minute=0, second=0, microsecond=0).isoformat()
    count_today = conn.execute(
        "SELECT COUNT(*) as c FROM claims WHERE user_id=? AND created_at>=?",
        (user_id, today_start)
    ).fetchone()["c"]
    if count_today >= DAILY_CLAIM_LIMIT:
        await callback.answer("🚫 Дневной лимит заявок исчерпан. Приходи завтра.", show_alert=True)
        conn.close()
        return

    # засчитываем
    conn.execute(
        "INSERT INTO claims (user_id, status, created_at) VALUES (?, 'confirmed', ?)",
        (user_id, now.isoformat())
    )
    conn.execute(
        "UPDATE users SET balance = balance + ? WHERE user_id=?",
        (REWARD_PER_CLAIM, user_id)
    )
    conn.commit()
    row = conn.execute("SELECT balance FROM users WHERE user_id=?", (user_id,)).fetchone()
    conn.close()

    await callback.answer(f"✅ Начислено +{REWARD_PER_CLAIM}⭐\n💰 Баланс: {row['balance']:.2f}⭐", show_alert=True)

    # проверяем, не пора ли наградить того, кто пригласил этого пользователя
    referrer_id = maybe_reward_referrer(user_id)
    if referrer_id:
        try:
            await bot.send_message(
                referrer_id,
                f"🎉 Твой друг выполнил {REFERRAL_REQUIRED_CLAIMS} задания!\nТебе начислено <b>{REFERRAL_REWARD}⭐</b>."
            )
        except Exception:
            pass


@dp.callback_query(F.data == "balance")
async def balance(callback: CallbackQuery):
    conn = db()
    row = conn.execute("SELECT balance FROM users WHERE user_id=?", (callback.from_user.id,)).fetchone()
    conn.close()
    bal = row["balance"] if row else 0
    await callback.answer(f"💰 Твой баланс\n\n{bal:.2f}⭐", show_alert=True)


@dp.callback_query(F.data == "withdraw")
async def withdraw(callback: CallbackQuery):
    user_id = callback.from_user.id
    conn = db()
    row = conn.execute("SELECT balance FROM users WHERE user_id=?", (user_id,)).fetchone()

    if not row or row["balance"] < WITHDRAW_MIN:
        await callback.answer(
            f"Недостаточно средств. Минимум для вывода: {WITHDRAW_MIN:.0f}⭐ на балансе",
            show_alert=True
        )
        conn.close()
        return

    gross_amount = row["balance"]
    net_amount = gross_amount * (1 - WITHDRAWAL_COMMISSION)

    conn.execute(
        "INSERT INTO withdrawals (user_id, amount, net_amount, status, created_at) VALUES (?,?,?, 'pending', ?)",
        (user_id, gross_amount, net_amount, datetime.utcnow().isoformat())
    )
    conn.execute("UPDATE users SET balance = 0 WHERE user_id=?", (user_id,))
    conn.commit()
    conn.close()

    await callback.answer(
        "📤 Заявка на вывод отправлена! Ожидай выплату.",
        show_alert=True
    )
    if ADMIN_ID:
        await bot.send_message(
            ADMIN_ID,
            f"💸 <b>Новая заявка на вывод</b>\n"
            f"{DIVIDER}\n"
            f"👤 @{callback.from_user.username} (id {user_id})\n"
            f"📊 Списано: {gross_amount:.2f}⭐\n"
            f"💵 К выплате (после комиссии {int(WITHDRAWAL_COMMISSION*100)}%): <b>{net_amount:.2f}⭐</b>"
        )


@dp.callback_query(F.data == "back")
async def back(callback: CallbackQuery):
    await callback.message.edit_text(
        f"✨ <b>{BOT_DISPLAY_NAME}</b> — главное меню\n{DIVIDER}",
        reply_markup=main_menu()
    )
    await callback.answer()


# ========== АДМИН-КОМАНДЫ ==========

@dp.message(Command("stats"))
async def stats(message: Message):
    if message.from_user.id != ADMIN_ID:
        return
    conn = db()
    users_count = conn.execute("SELECT COUNT(*) c FROM users").fetchone()["c"]
    claims_count = conn.execute("SELECT COUNT(*) c FROM claims").fetchone()["c"]
    referrals_rewarded = conn.execute(
        "SELECT COUNT(*) c FROM users WHERE referral_rewarded=1"
    ).fetchone()["c"]
    pending_amount = conn.execute(
        "SELECT COALESCE(SUM(net_amount),0) s FROM withdrawals WHERE status='pending'"
    ).fetchone()["s"]
    conn.close()
    await message.answer(
        f"📊 <b>Статистика {BOT_DISPLAY_NAME}</b>\n"
        f"{DIVIDER}\n\n"
        f"👥 Пользователей: <b>{users_count}</b>\n"
        f"✅ Всего заявок на подписку: <b>{claims_count}</b>\n"
        f"🤝 Оплаченных рефералов: <b>{referrals_rewarded}</b>\n"
        f"💸 К выплате (после комиссии {int(WITHDRAWAL_COMMISSION*100)}%): <b>{pending_amount:.2f}⭐</b>"
    )


@dp.message(Command("payouts"))
async def payouts(message: Message):
    if message.from_user.id != ADMIN_ID:
        return
    conn = db()
    rows = conn.execute(
        "SELECT * FROM withdrawals WHERE status='pending' ORDER BY id"
    ).fetchall()
    conn.close()
    if not rows:
        await message.answer("✅ Нет заявок на выплату.")
        return
    text = f"💸 <b>Заявки на выплату</b>\n{DIVIDER}\n\n"
    for r in rows:
        text += f"#{r['id']} — user_id {r['user_id']}: списано {r['amount']:.2f}⭐, к выплате <b>{r['net_amount']:.2f}⭐</b>\n"
    text += f"\n{DIVIDER}\nОтметить выплаченным: <code>/paid ID</code>"
    await message.answer(text)


@dp.message(Command("paid"))
async def mark_paid(message: Message):
    if message.from_user.id != ADMIN_ID:
        return
    parts = message.text.split()
    if len(parts) != 2 or not parts[1].isdigit():
        await message.answer("Использование: /paid ID_заявки")
        return
    wid = int(parts[1])
    conn = db()
    conn.execute("UPDATE withdrawals SET status='paid' WHERE id=?", (wid,))
    conn.commit()
    conn.close()
    await message.answer(f"Заявка #{wid} отмечена как выплаченная.")


async def main():
    init_db()
    await bot.set_my_commands([
        BotCommand(command="start", description="🏠 Главное меню"),
    ])
    await bot.delete_webhook(drop_pending_updates=True)
    await dp.start_polling(bot)


if __name__ == "__main__":
    asyncio.run(main())
