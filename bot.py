import asyncio
import os
import sqlite3
from datetime import datetime, timezone

from aiogram import Bot, Dispatcher, F
from aiogram.filters import Command, CommandStart
from aiogram.types import Message, KeyboardButton, ReplyKeyboardMarkup, ReplyKeyboardRemove

TOKEN = os.getenv("TELEGRAM_BOT_TOKEN", "").strip()
MAIN_ADMIN_ID = int(os.getenv("ADMIN_ID", "8620613020"))

if not TOKEN:
    raise RuntimeError(
        "TELEGRAM_BOT_TOKEN environment variable is missing. "
        "Set it in your hosting panel; do not put the token in GitHub."
    )

bot = Bot(token=TOKEN)
dp = Dispatcher()

# SQLite database file. Use persistent storage/volume on your hosting provider.
db = sqlite3.connect("bot_data.db")
db.row_factory = sqlite3.Row
db.execute("""
CREATE TABLE IF NOT EXISTS users (
    id INTEGER PRIMARY KEY,
    username TEXT,
    first_name TEXT,
    last_name TEXT,
    phone TEXT,
    latitude REAL,
    longitude REAL,
    registered_at TEXT
)
""")
db.execute("""
CREATE TABLE IF NOT EXISTS admins (
    user_id INTEGER PRIMARY KEY,
    added_by INTEGER NOT NULL,
    added_at TEXT NOT NULL
)
""")
db.execute(
    "INSERT OR IGNORE INTO admins (user_id, added_by, added_at) VALUES (?, ?, ?)",
    (MAIN_ADMIN_ID, MAIN_ADMIN_ID, datetime.now(timezone.utc).isoformat())
)
db.commit()


def now_utc() -> str:
    return datetime.now(timezone.utc).isoformat(timespec="seconds")


def is_admin(user_id: int) -> bool:
    row = db.execute("SELECT 1 FROM admins WHERE user_id = ?", (user_id,)).fetchone()
    return row is not None


def save_user_profile(message: Message) -> None:
    user = message.from_user
    if user is None:
        return
    db.execute(
        """
        INSERT INTO users (id, username, first_name, last_name, registered_at)
        VALUES (?, ?, ?, ?, ?)
        ON CONFLICT(id) DO UPDATE SET
            username=excluded.username,
            first_name=excluded.first_name,
            last_name=excluded.last_name
        """,
        (user.id, user.username, user.first_name, user.last_name, now_utc())
    )
    db.commit()


def phone_button() -> ReplyKeyboardMarkup:
    return ReplyKeyboardMarkup(
        keyboard=[[KeyboardButton(text="Share my phone number", request_contact=True)]],
        resize_keyboard=True,
        one_time_keyboard=True
    )


def location_button() -> ReplyKeyboardMarkup:
    return ReplyKeyboardMarkup(
        keyboard=[[KeyboardButton(text="Share my location", request_location=True)]],
        resize_keyboard=True,
        one_time_keyboard=True
    )


def main_admin_only(user_id: int) -> bool:
    return user_id == MAIN_ADMIN_ID


@dp.message(CommandStart())
async def start(message: Message):
    save_user_profile(message)
    await message.answer(
        "Welcome!\n\n"
        "This bot can optionally store your Telegram profile details, phone number, "
        "and location in its database for the stated purpose of this service.\n\n"
        "Your phone number and location are optional. Share them only if you agree. "
        "Use /privacy to see what is stored and /cancel to stop.",
        reply_markup=ReplyKeyboardRemove()
    )
    await message.answer(
        "If you want to share your phone number, tap the button below. "
        "You can skip this step.",
        reply_markup=phone_button()
    )


@dp.message(Command("privacy"))
async def privacy(message: Message):
    await message.answer(
        "Privacy information:\n"
        "• The bot stores your Telegram numeric ID and public profile fields when you use /start.\n"
        "• Your phone number is stored only if you choose to share your own contact.\n"
        "• Your location is stored only if you choose to share it.\n"
        "• Data is stored in the bot operator's SQLite database.\n"
        "• Send /delete_me to request deletion of your stored record."
    )


@dp.message(Command("cancel"))
async def cancel(message: Message):
    await message.answer("Cancelled. You can continue without sharing contact or location.",
                         reply_markup=ReplyKeyboardRemove())


@dp.message(Command("delete_me"))
async def delete_me(message: Message):
    if message.from_user is None:
        return
    db.execute("DELETE FROM users WHERE id = ?", (message.from_user.id,))
    db.commit()
    await message.answer("Your stored record has been deleted.", reply_markup=ReplyKeyboardRemove())


@dp.message(F.contact)
async def contact(message: Message):
    if message.from_user is None or message.contact is None:
        return
    # Accept only the sender's own contact, not a contact card for another person.
    if message.contact.user_id != message.from_user.id:
        await message.answer(
            "For privacy, please use the button to share your own phone number. "
            "Contacts belonging to other people are not stored.",
            reply_markup=ReplyKeyboardRemove()
        )
        return

    save_user_profile(message)
    db.execute(
        "UPDATE users SET phone = ? WHERE id = ?",
        (message.contact.phone_number, message.from_user.id)
    )
    db.commit()
    await message.answer(
        "Your phone number was saved. Location sharing is optional. "
        "Only continue if you want to share it.",
        reply_markup=location_button()
    )


@dp.message(F.location)
async def location(message: Message):
    if message.from_user is None or message.location is None:
        return
    save_user_profile(message)
    db.execute(
        "UPDATE users SET latitude = ?, longitude = ? WHERE id = ?",
        (message.location.latitude, message.location.longitude, message.from_user.id)
    )
    db.commit()
    await message.answer("Thank you. Your location was saved.", reply_markup=ReplyKeyboardRemove())


@dp.message(Command("adminhelp"))
async def adminhelp(message: Message):
    if message.from_user is None or not is_admin(message.from_user.id):
        await message.answer("This command is for admins only.")
        return
    if main_admin_only(message.from_user.id):
        await message.answer(
            "Main admin commands:\n"
            "/addadmin <telegram_id> — add an admin\n"
            "/removeadmin <telegram_id> — remove an admin (not the main admin)\n"
            "/admins — list admin IDs\n"
            "/stats — show counts\n"
            "/user <telegram_id> — view a stored record\n"
            "/delete_user <telegram_id> — delete a stored record"
        )
    else:
        await message.answer(
            "Admin commands:\n"
            "/stats — show counts\n"
            "/user <telegram_id> — view a stored record"
        )


@dp.message(Command("addadmin"))
async def add_admin(message: Message):
    if message.from_user is None or not main_admin_only(message.from_user.id):
        await message.answer("Only the main admin can add admins.")
        return
    parts = (message.text or "").split()
    if len(parts) != 2 or not parts[1].isdigit():
        await message.answer("Usage: /addadmin <telegram_id>")
        return
    target_id = int(parts[1])
    db.execute(
        "INSERT OR IGNORE INTO admins (user_id, added_by, added_at) VALUES (?, ?, ?)",
        (target_id, message.from_user.id, now_utc())
    )
    db.commit()
    await message.answer(f"Admin {target_id} is now authorized (if not already added).")


@dp.message(Command("removeadmin"))
async def remove_admin(message: Message):
    if message.from_user is None or not main_admin_only(message.from_user.id):
        await message.answer("Only the main admin can remove admins.")
        return
    parts = (message.text or "").split()
    if len(parts) != 2 or not parts[1].isdigit():
        await message.answer("Usage: /removeadmin <telegram_id>")
        return
    target_id = int(parts[1])
    if target_id == MAIN_ADMIN_ID:
        await message.answer("The main admin cannot be removed.")
        return
    db.execute("DELETE FROM admins WHERE user_id = ?", (target_id,))
    db.commit()
    await message.answer(f"Admin {target_id} has been removed if they existed.")


@dp.message(Command("admins"))
async def list_admins(message: Message):
    if message.from_user is None or not main_admin_only(message.from_user.id):
        await message.answer("Only the main admin can list admin IDs.")
        return
    rows = db.execute("SELECT user_id FROM admins ORDER BY user_id").fetchall()
    await message.answer("Authorized admin IDs:\n" + "\n".join(str(row["user_id"]) for row in rows))


@dp.message(Command("stats"))
async def stats(message: Message):
    if message.from_user is None or not is_admin(message.from_user.id):
        await message.answer("This command is for admins only.")
        return
    users_count = db.execute("SELECT COUNT(*) FROM users").fetchone()[0]
    admins_count = db.execute("SELECT COUNT(*) FROM admins").fetchone()[0]
    await message.answer(f"Stored user records: {users_count}\nAdmins: {admins_count}")


@dp.message(Command("user"))
async def get_user(message: Message):
    if message.from_user is None or not is_admin(message.from_user.id):
        await message.answer("This command is for admins only.")
        return
    parts = (message.text or "").split()
    if len(parts) != 2 or not parts[1].isdigit():
        await message.answer("Usage: /user <telegram_id>")
        return
    row = db.execute(
        """SELECT id, username, first_name, last_name, phone, latitude, longitude, registered_at
           FROM users WHERE id = ?""",
        (int(parts[1]),)
    ).fetchone()
    if row is None:
        await message.answer("No record found for that ID.")
        return
    await message.answer(
        f"ID: {row['id']}\n"
        f"Username: @{row['username'] or 'none'}\n"
        f"First name: {row['first_name'] or 'none'}\n"
        f"Last name: {row['last_name'] or 'none'}\n"
        f"Phone: {row['phone'] or 'not shared'}\n"
        f"Latitude: {row['latitude'] if row['latitude'] is not None else 'not shared'}\n"
        f"Longitude: {row['longitude'] if row['longitude'] is not None else 'not shared'}\n"
        f"Registered at (UTC): {row['registered_at']}"
    )


@dp.message(Command("delete_user"))
async def delete_user(message: Message):
    if message.from_user is None or not main_admin_only(message.from_user.id):
        await message.answer("Only the main admin can delete user records.")
        return
    parts = (message.text or "").split()
    if len(parts) != 2 or not parts[1].isdigit():
        await message.answer("Usage: /delete_user <telegram_id>")
        return
    db.execute("DELETE FROM users WHERE id = ?", (int(parts[1]),))
    db.commit()
    await message.answer("The record was deleted if it existed.")


async def main():
    print("Telegram bot is starting...")
    try:
        await dp.start_polling(bot)
    finally:
        await bot.session.close()
        db.close()


if __name__ == "__main__":
    asyncio.run(main())
