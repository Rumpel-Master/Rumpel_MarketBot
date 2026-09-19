import asyncio
import html
import logging
import os
import sqlite3
from datetime import datetime

from aiogram import Bot, Dispatcher, F, Router
from aiogram.client.default import DefaultBotProperties
from aiogram.enums import ParseMode
from aiogram.filters import Command, CommandStart
from aiogram.fsm.context import FSMContext
from aiogram.fsm.state import State, StatesGroup
from aiogram.types import CallbackQuery, InlineKeyboardButton, InlineKeyboardMarkup, LabeledPrice, Message
from aiogram.utils.keyboard import InlineKeyboardBuilder
from dotenv import load_dotenv

load_dotenv()
BOT_TOKEN = os.getenv("BOT_TOKEN", "")
ADMIN_IDS = {int(x) for x in os.getenv("ADMIN_IDS", "").split(",") if x.strip().isdigit()}
DB_PATH = os.getenv("DB_PATH", "market.db")
WEB_HOST = os.getenv("WEB_HOST", "127.0.0.1")
WEB_PORT = int(os.getenv("WEB_PORT", "8080"))

logging.basicConfig(level=logging.INFO)
router = Router()


def db():
    con = sqlite3.connect(DB_PATH)
    con.row_factory = sqlite3.Row
    return con


def now():
    return datetime.utcnow().isoformat(timespec="seconds")


def log_action(user_id: int, action: str, target_type: str = "", target_id: int | None = None, details: str = ""):
    con = db()
    con.execute(
        "INSERT INTO user_actions(user_id,action,target_type,target_id,details,created_at) VALUES(?,?,?,?,?,?)",
        (user_id, action, target_type, target_id, details[:1000], now()),
    )
    con.execute("UPDATE users SET last_seen_at=? WHERE tg_id=?", (now(), user_id))
    con.commit()
    con.close()


def init_db():
    con = db()
    con.executescript("""
    CREATE TABLE IF NOT EXISTS users (
        tg_id INTEGER PRIMARY KEY,
        anon_name TEXT NOT NULL,
        username TEXT,
        balance_rub INTEGER NOT NULL DEFAULT 0,
        reputation REAL NOT NULL DEFAULT 5.0,
        reputation_votes INTEGER NOT NULL DEFAULT 0,
        is_seller INTEGER NOT NULL DEFAULT 0,
        seller_name TEXT,
        seller_bio TEXT,
        created_at TEXT NOT NULL,
        last_seen_at TEXT
    );
    CREATE TABLE IF NOT EXISTS listings (
        id INTEGER PRIMARY KEY AUTOINCREMENT,
        seller_id INTEGER NOT NULL,
        title TEXT NOT NULL,
        description TEXT NOT NULL,
        price_stars INTEGER NOT NULL,
        price_rub INTEGER NOT NULL DEFAULT 0,
        category TEXT NOT NULL,
        file_id TEXT,
        file_name TEXT,
        verified INTEGER NOT NULL DEFAULT 0,
        status TEXT NOT NULL DEFAULT 'active',
        created_at TEXT NOT NULL
    );
    CREATE TABLE IF NOT EXISTS purchases (
        id INTEGER PRIMARY KEY AUTOINCREMENT,
        buyer_id INTEGER NOT NULL,
        listing_id INTEGER NOT NULL,
        stars INTEGER NOT NULL,
        telegram_charge_id TEXT,
        created_at TEXT NOT NULL
    );
    CREATE TABLE IF NOT EXISTS reputation_votes (
        id INTEGER PRIMARY KEY AUTOINCREMENT,
        voter_id INTEGER NOT NULL,
        seller_id INTEGER NOT NULL,
        listing_id INTEGER,
        stars INTEGER NOT NULL CHECK(stars BETWEEN 1 AND 5),
        created_at TEXT NOT NULL,
        UNIQUE(voter_id, seller_id, listing_id)
    );
    CREATE TABLE IF NOT EXISTS balance_topups (
        id INTEGER PRIMARY KEY AUTOINCREMENT,
        user_id INTEGER NOT NULL,
        rub_amount INTEGER NOT NULL,
        stars_amount INTEGER NOT NULL,
        telegram_charge_id TEXT,
        created_at TEXT NOT NULL
    );
    CREATE TABLE IF NOT EXISTS tickets (
        id INTEGER PRIMARY KEY AUTOINCREMENT,
        user_id INTEGER NOT NULL,
        text TEXT NOT NULL,
        status TEXT NOT NULL DEFAULT 'open',
        created_at TEXT NOT NULL,
        closed_at TEXT
    );
    CREATE TABLE IF NOT EXISTS ticket_messages (
        id INTEGER PRIMARY KEY AUTOINCREMENT,
        ticket_id INTEGER NOT NULL,
        sender_type TEXT NOT NULL,
        sender_id INTEGER,
        text TEXT NOT NULL,
        created_at TEXT NOT NULL
    );
    CREATE TABLE IF NOT EXISTS product_views (
        id INTEGER PRIMARY KEY AUTOINCREMENT,
        user_id INTEGER NOT NULL,
        listing_id INTEGER NOT NULL,
        created_at TEXT NOT NULL
    );
    CREATE TABLE IF NOT EXISTS user_actions (
        id INTEGER PRIMARY KEY AUTOINCREMENT,
        user_id INTEGER NOT NULL,
        action TEXT NOT NULL,
        target_type TEXT,
        target_id INTEGER,
        details TEXT,
        created_at TEXT NOT NULL
    );
    """)
    cols = {r[1] for r in con.execute("PRAGMA table_info(users)").fetchall()}
    if "last_seen_at" not in cols:
        con.execute("ALTER TABLE users ADD COLUMN last_seen_at TEXT")
    tcols = {r[1] for r in con.execute("PRAGMA table_info(tickets)").fetchall()}
    if "closed_at" not in tcols:
        con.execute("ALTER TABLE tickets ADD COLUMN closed_at TEXT")
    con.commit()
    con.close()


def ensure_user(tg_id: int, username: str | None):
    con = db()
    row = con.execute("SELECT tg_id FROM users WHERE tg_id=?", (tg_id,)).fetchone()
    if row:
        con.execute("UPDATE users SET username=?,last_seen_at=? WHERE tg_id=?", (username, now(), tg_id))
    else:
        anon = f"Участник #{tg_id % 10000:04d}"
        con.execute(
            "INSERT INTO users(tg_id,anon_name,username,created_at,last_seen_at) VALUES(?,?,?,?,?)",
            (tg_id, anon, username, now(), now()),
        )
    con.commit()
    con.close()


def get_user(tg_id: int):
    con = db(); row = con.execute("SELECT * FROM users WHERE tg_id=?", (tg_id,)).fetchone(); con.close(); return row


def seed():
    con = db()
    if con.execute("SELECT COUNT(*) FROM listings").fetchone()[0] == 0:
        demos = [
            ("Карта таверны «Старый Дракон»", "Демо-карта для VTT: таверна, несколько зон и версии для игры.", 50, 150, "Карты TaleSpire"),
            ("Набор NPC: Городская стража", "12 готовых NPC, заметки мастеру и заготовки сцен.", 80, 240, "Материалы для кампаний"),
            ("TaleSpire — GM Seat", "Пример позиции администрации: 150 ₽ за место.", 50, 150, "TaleSpire"),
        ]
        for title, desc, stars, rub, category in demos:
            con.execute(
                "INSERT INTO listings(seller_id,title,description,price_stars,price_rub,category,verified,status,created_at) VALUES(?,?,?,?,?,?,1,'active',?)",
                (0, title, desc, stars, rub, category, now()),
            )
    con.commit(); con.close()


def main_kb():
    kb = InlineKeyboardBuilder()
    kb.button(text="🛒 Маркет", callback_data="market")
    kb.button(text="📋 Объявления", callback_data="listings")
    kb.button(text="👤 Профиль", callback_data="profile")
    kb.button(text="💰 Пополнить баланс", callback_data="topup")
    kb.button(text="🏪 Стать продавцом", callback_data="seller")
    kb.button(text="👥 Участники", callback_data="members")
    kb.button(text="🆘 Поддержка", callback_data="support")
    kb.adjust(2, 2, 2, 1)
    return kb.as_markup()


def back_kb(cb="home"):
    return InlineKeyboardMarkup(inline_keyboard=[[InlineKeyboardButton(text="⬅️ Назад", callback_data=cb)]])


class SellerForm(StatesGroup):
    name = State(); bio = State()


class ListingForm(StatesGroup):
    title = State(); description = State(); price = State(); category = State(); file = State()


class SupportForm(StatesGroup):
    message = State()


@router.message(CommandStart())
async def start(message: Message):
    ensure_user(message.from_user.id, message.from_user.username)
    log_action(message.from_user.id, "start")
    await message.answer(
        "🐉 <b>D&D Market</b>\n\nДобро пожаловать в маркет материалов для D&D и TaleSpire!\n\n"
        "Здесь можно покупать карты, материалы для кампаний и места TaleSpire, а продавцам — размещать свои товары.",
        reply_markup=main_kb(),
    )


@router.callback_query(F.data == "home")
async def home(call: CallbackQuery, state: FSMContext):
    await state.clear(); ensure_user(call.from_user.id, call.from_user.username); log_action(call.from_user.id, "home")
    await call.message.edit_text("🐉 <b>D&D Market</b>\n\nВыберите раздел:", reply_markup=main_kb()); await call.answer()


@router.callback_query(F.data == "profile")
async def profile(call: CallbackQuery):
    ensure_user(call.from_user.id, call.from_user.username); log_action(call.from_user.id, "profile_view")
    u = get_user(call.from_user.id)
    seller = html.escape(u["seller_name"]) if u["is_seller"] else "нет"
    await call.message.edit_text(
        f"👤 <b>Профиль</b>\n\nПубличное имя: <code>{html.escape(u['anon_name'])}</code>\n"
        f"Баланс: <b>{u['balance_rub']} ₽</b>\n⭐ Репутация: <b>{u['reputation']:.1f}/5.0</b> ({u['reputation_votes']} оценок)\n"
        f"Продавец: <b>{seller}</b>",
        reply_markup=InlineKeyboardMarkup(inline_keyboard=[
            [InlineKeyboardButton(text="💰 Пополнить баланс", callback_data="topup")],
            [InlineKeyboardButton(text="⬅️ Назад", callback_data="home")],
        ]),
    ); await call.answer()


@router.callback_query(F.data == "topup")
async def topup(call: CallbackQuery):
    log_action(call.from_user.id, "topup_open")
    await call.message.edit_text(
        "💰 <b>Пополнение баланса</b>\n\nВыберите сумму.\n\n"
        "Сейчас доступно пополнение через Telegram Stars. Демо-курс: 1 ⭐ = 2 ₽.",
        reply_markup=InlineKeyboardMarkup(inline_keyboard=[
            [InlineKeyboardButton(text="100 ₽", callback_data="topup:100"), InlineKeyboardButton(text="300 ₽", callback_data="topup:300")],
            [InlineKeyboardButton(text="500 ₽", callback_data="topup:500"), InlineKeyboardButton(text="1000 ₽", callback_data="topup:1000")],
            [InlineKeyboardButton(text="⬅️ В профиль", callback_data="profile")],
        ]),
    ); await call.answer()


@router.callback_query(F.data.startswith("topup:"))
async def topup_invoice(call: CallbackQuery):
    rub = int(call.data.split(":")[1]); stars = (rub + 1) // 2
    log_action(call.from_user.id, "topup_invoice", "topup", rub, f"stars={stars}")
    await call.message.answer_invoice(
        title=f"Пополнение баланса на {rub} ₽", description=f"Зачисление {rub} ₽ на внутренний баланс D&D Market.",
        payload=f"topup:{rub}", currency="XTR", prices=[LabeledPrice(label=f"Баланс {rub} ₽", amount=stars)], provider_token="",
    ); await call.answer()


@router.pre_checkout_query()
async def pre_checkout(query):
    await query.answer(ok=query.invoice_payload.startswith(("topup:", "listing:")))


@router.message(F.successful_payment)
async def successful_payment(message: Message):
    payment = message.successful_payment; payload = payment.invoice_payload
    if payload.startswith("topup:"):
        rub = int(payload.split(":")[1]); con = db()
        con.execute("UPDATE users SET balance_rub=balance_rub+? WHERE tg_id=?", (rub, message.from_user.id))
        con.execute("INSERT INTO balance_topups(user_id,rub_amount,stars_amount,telegram_charge_id,created_at) VALUES(?,?,?,?,?)", (message.from_user.id, rub, payment.total_amount, payment.telegram_payment_charge_id, now()))
        con.commit(); balance = con.execute("SELECT balance_rub FROM users WHERE tg_id=?", (message.from_user.id,)).fetchone()[0]; con.close()
        log_action(message.from_user.id, "topup_success", "topup", rub, f"stars={payment.total_amount}")
        await message.answer(f"✅ Баланс пополнен на <b>{rub} ₽</b>.\nТекущий баланс: <b>{balance} ₽</b>."); return
    if payload.startswith("listing:"):
        listing_id = int(payload.split(":")[1]); con = db(); r = con.execute("SELECT * FROM listings WHERE id=? AND status='active'", (listing_id,)).fetchone()
        if not r or not r["file_id"]:
            con.close(); await message.answer("Оплата прошла, но выдача не настроена. Напишите в поддержку."); return
        con.execute("INSERT INTO purchases(buyer_id,listing_id,stars,telegram_charge_id,created_at) VALUES(?,?,?,?,?)", (message.from_user.id, listing_id, payment.total_amount, payment.telegram_payment_charge_id, now())); con.commit(); con.close()
        log_action(message.from_user.id, "purchase_success", "listing", listing_id, f"stars={payment.total_amount}")
        await message.answer("✅ <b>Оплата получена.</b>\n\nВаш цифровой товар:")
        await message.answer_document(document=r["file_id"], caption=f"📦 {html.escape(r['title'])}")


async def render_market(call: CallbackQuery, title="🛒 <b>Маркет</b>"):
    con = db(); rows = con.execute("SELECT l.*,COALESCE(u.seller_name,'Администрация') seller FROM listings l LEFT JOIN users u ON u.tg_id=l.seller_id WHERE l.status='active' ORDER BY l.id DESC LIMIT 30").fetchall(); con.close()
    text = title + "\n\n" + ("\n\n".join(
        f"#{r['id']} <b>{html.escape(r['title'])}</b>{' ✅' if r['verified'] else ''}\n{html.escape(r['category'])} · {r['price_rub']} ₽ / {r['price_stars']} ⭐ · {html.escape(r['seller'])}"
        for r in rows
    ) if rows else "Пока товаров нет.")
    kb = InlineKeyboardBuilder()
    for r in rows:
        label = f"#{r['id']} {r['title'][:25]}" + (" ✅" if r["verified"] else "")
        kb.button(text=label, callback_data=f"item:{r['id']}")
    kb.button(text="⬅️ Назад", callback_data="home"); kb.adjust(1)
    await call.message.edit_text(text[:4000], reply_markup=kb.as_markup())


@router.callback_query(F.data == "market")
async def market(call: CallbackQuery):
    log_action(call.from_user.id, "market_view"); await render_market(call); await call.answer()


@router.callback_query(F.data == "listings")
async def listings(call: CallbackQuery):
    # Раньше на эту кнопку не было обработчика — из-за этого Telegram-клиент мог показывать бесконечный spinner.
    log_action(call.from_user.id, "listings_view"); await render_market(call, "📋 <b>Объявления</b>"); await call.answer()


@router.callback_query(F.data.startswith("item:"))
async def item(call: CallbackQuery):
    listing_id = int(call.data.split(":")[1]); con = db()
    r = con.execute("SELECT l.*,COALESCE(u.seller_name,'Администрация') seller,COALESCE(u.reputation,5.0) seller_rep,COALESCE(u.reputation_votes,0) seller_votes FROM listings l LEFT JOIN users u ON u.tg_id=l.seller_id WHERE l.id=? AND l.status='active'", (listing_id,)).fetchone()
    if not r:
        con.close(); await call.answer("Товар не найден", show_alert=True); return
    con.execute("INSERT INTO product_views(user_id,listing_id,created_at) VALUES(?,?,?)", (call.from_user.id, listing_id, now())); con.commit(); con.close()
    log_action(call.from_user.id, "product_view", "listing", listing_id, r["title"])
    verified = "✅ <b>Проверено администрацией</b>\n" if r["verified"] else ""
    kb = InlineKeyboardBuilder(); kb.button(text="⭐ Оплатить Stars", callback_data=f"buy:{listing_id}"); kb.button(text="💳 СБП / Карта", callback_data=f"cardpay:{listing_id}")
    if r["seller_id"] != 0: kb.button(text="⭐ Оценить продавца", callback_data=f"rate:{r['seller_id']}:{listing_id}")
    kb.button(text="⬅️ В маркет", callback_data="market"); kb.adjust(1)
    await call.message.edit_text(
        f"🧙 <b>{html.escape(r['title'])}</b>\n\n{verified}{html.escape(r['description'])}\n\n"
        f"Категория: <b>{html.escape(r['category'])}</b>\nПродавец: <b>{html.escape(r['seller'])}</b>\n"
        f"Репутация: <b>⭐ {r['seller_rep']:.1f}/5.0</b> ({r['seller_votes']} оценок)\n"
        f"Цена: <b>{r['price_rub']} ₽</b> или <b>{r['price_stars']} ⭐</b>", reply_markup=kb.as_markup())
    await call.answer()


@router.callback_query(F.data.startswith("cardpay:"))
async def cardpay(call: CallbackQuery):
    listing_id = int(call.data.split(":")[1]); log_action(call.from_user.id, "card_payment_attempt", "listing", listing_id)
    await call.answer("Оплата СБП / картой пока не подключена. Мы работаем над этим — ожидайте обновления.", show_alert=True)


@router.callback_query(F.data.startswith("buy:"))
async def buy(call: CallbackQuery):
    listing_id = int(call.data.split(":")[1]); con = db(); r = con.execute("SELECT * FROM listings WHERE id=? AND status='active'", (listing_id,)).fetchone(); con.close()
    if not r: await call.answer("Товар недоступен", show_alert=True); return
    if not r["file_id"]:
        await call.answer("Для этой позиции пока не настроена выдача файла. Напишите в поддержку.", show_alert=True); return
    log_action(call.from_user.id, "purchase_start", "listing", listing_id)
    await call.message.answer_invoice(title=r["title"], description=r["description"][:255], payload=f"listing:{listing_id}", currency="XTR", prices=[LabeledPrice(label=r["title"][:64], amount=r["price_stars"])], provider_token="")
    await call.answer()


@router.callback_query(F.data.startswith("rate:"))
async def rate(call: CallbackQuery):
    _, seller_id, listing_id = call.data.split(":"); seller_id=int(seller_id); listing_id=int(listing_id)
    kb=InlineKeyboardMarkup(inline_keyboard=[[InlineKeyboardButton(text=f"⭐ {i}",callback_data=f"vote:{seller_id}:{listing_id}:{i}") for i in (1,2,3)], [InlineKeyboardButton(text=f"⭐ {i}",callback_data=f"vote:{seller_id}:{listing_id}:{i}") for i in (4,5)], [InlineKeyboardButton(text="⬅️ Назад",callback_data=f"item:{listing_id}")]])
    await call.message.edit_text("⭐ <b>Оценка продавца</b>\n\nВыберите оценку от 1 до 5:", reply_markup=kb); await call.answer()


@router.callback_query(F.data.startswith("vote:"))
async def vote(call: CallbackQuery):
    _, seller_id, listing_id, value = call.data.split(":"); seller_id=int(seller_id); listing_id=int(listing_id); value=int(value)
    con=db()
    try: con.execute("INSERT INTO reputation_votes(voter_id,seller_id,listing_id,stars,created_at) VALUES(?,?,?,?,?)",(call.from_user.id,seller_id,listing_id,value,now()))
    except sqlite3.IntegrityError: con.close(); await call.answer("Вы уже оценивали этого продавца за этот товар.",show_alert=True); return
    row=con.execute("SELECT AVG(stars) avg,COUNT(*) cnt FROM reputation_votes WHERE seller_id=?",(seller_id,)).fetchone(); avg=round(float(row["avg"]),1); cnt=int(row["cnt"])
    con.execute("UPDATE users SET reputation=?,reputation_votes=? WHERE tg_id=?",(avg,cnt,seller_id)); con.commit(); con.close(); log_action(call.from_user.id,"reputation_vote","user",seller_id,f"stars={value}")
    await call.message.edit_text(f"✅ Оценка сохранена.\n\nРепутация продавца: <b>⭐ {avg:.1f}/5.0</b> ({cnt} оценок)",reply_markup=InlineKeyboardMarkup(inline_keyboard=[[InlineKeyboardButton(text="⬅️ К товару",callback_data=f"item:{listing_id}")]])); await call.answer()


@router.callback_query(F.data == "members")
async def members(call: CallbackQuery):
    con=db(); users=con.execute("SELECT anon_name,is_seller,seller_name,reputation,reputation_votes FROM users ORDER BY created_at DESC LIMIT 50").fetchall(); con.close()
    lines=["👥 <b>Участники</b>\n"]
    for u in users:
        lines.append(f"🏪 <b>{html.escape(u['seller_name'])}</b> — ⭐ {u['reputation']:.1f}/5.0 ({u['reputation_votes']})" if u["is_seller"] else f"👤 {html.escape(u['anon_name'])}")
    await call.message.edit_text("\n".join(lines)+"\n\nОбычные участники отображаются анонимно.",reply_markup=back_kb()); await call.answer()


@router.callback_query(F.data == "seller")
async def seller(call: CallbackQuery):
    u=get_user(call.from_user.id)
    if u["is_seller"]:
        await call.message.edit_text(f"🏪 <b>Кабинет продавца</b>\n\nМагазин: <b>{html.escape(u['seller_name'])}</b>\n⭐ {u['reputation']:.1f}/5.0 ({u['reputation_votes']} оценок)\n{html.escape(u['seller_bio'] or '')}",reply_markup=InlineKeyboardMarkup(inline_keyboard=[[InlineKeyboardButton(text="➕ Новое объявление",callback_data="new_listing")],[InlineKeyboardButton(text="📦 Мои объявления",callback_data="my_listings")],[InlineKeyboardButton(text="⬅️ Назад",callback_data="home")]]))
    else:
        await call.message.edit_text("🏪 <b>Стать продавцом</b>\n\nПосле регистрации вы сможете публиковать товары в общем маркете.",reply_markup=InlineKeyboardMarkup(inline_keyboard=[[InlineKeyboardButton(text="📝 Зарегистрироваться",callback_data="seller_reg")],[InlineKeyboardButton(text="⬅️ Назад",callback_data="home")]]))
    await call.answer()


@router.callback_query(F.data == "seller_reg")
async def seller_reg(call: CallbackQuery,state:FSMContext): await state.set_state(SellerForm.name); await call.message.answer("Введите название магазина:"); await call.answer()

@router.message(SellerForm.name)
async def seller_name(message:Message,state:FSMContext):
    name=(message.text or "").strip()[:50]
    if len(name)<2: await message.answer("Название слишком короткое."); return
    await state.update_data(name=name); await state.set_state(SellerForm.bio); await message.answer("Опишите магазин (до 300 символов):")

@router.message(SellerForm.bio)
async def seller_bio(message:Message,state:FSMContext):
    data=await state.get_data(); con=db(); con.execute("UPDATE users SET is_seller=1,seller_name=?,seller_bio=? WHERE tg_id=?",(data["name"],(message.text or "").strip()[:300],message.from_user.id)); con.commit(); con.close(); await state.clear(); log_action(message.from_user.id,"seller_registered"); await message.answer("✅ Вы зарегистрированы как продавец.",reply_markup=main_kb())


@router.callback_query(F.data == "new_listing")
async def new_listing(call:CallbackQuery,state:FSMContext): await state.set_state(ListingForm.title); await call.message.answer("Введите название товара:"); await call.answer()
@router.message(ListingForm.title)
async def listing_title(message:Message,state:FSMContext): await state.update_data(title=(message.text or "").strip()[:80]); await state.set_state(ListingForm.description); await message.answer("Введите описание:")
@router.message(ListingForm.description)
async def listing_desc(message:Message,state:FSMContext): await state.update_data(description=(message.text or "").strip()[:1000]); await state.set_state(ListingForm.price); await message.answer("Введите цену в Stars, например 150:")
@router.message(ListingForm.price)
async def listing_price(message:Message,state:FSMContext):
    try: stars=int((message.text or "").strip()); assert stars>0
    except Exception: await message.answer("Введите положительное целое число."); return
    await state.update_data(price_stars=stars); await state.set_state(ListingForm.category); await message.answer("Введите категорию:")
@router.message(ListingForm.category)
async def listing_cat(message:Message,state:FSMContext): await state.update_data(category=(message.text or "").strip()[:50]); await state.set_state(ListingForm.file); await message.answer("Отправьте файл товара документом:")
@router.message(ListingForm.file,F.document)
async def listing_file(message:Message,state:FSMContext):
    data=await state.get_data(); con=db(); con.execute("INSERT INTO listings(seller_id,title,description,price_stars,category,file_id,file_name,verified,status,created_at) VALUES(?,?,?,?,?,?,?,?,?,?)",(message.from_user.id,data["title"],data["description"],data["price_stars"],data["category"],message.document.file_id,message.document.file_name,0,"active",now())); con.commit(); con.close(); await state.clear(); log_action(message.from_user.id,"listing_created","listing",None,data["title"]); await message.answer("✅ Объявление опубликовано. Оно отображается как непроверенное.",reply_markup=main_kb())
@router.message(ListingForm.file)
async def listing_file_wrong(message:Message): await message.answer("Отправьте товар именно как документ.")
@router.callback_query(F.data == "my_listings")
async def my_listings(call:CallbackQuery):
    con=db(); rows=con.execute("SELECT * FROM listings WHERE seller_id=? ORDER BY id DESC",(call.from_user.id,)).fetchall(); con.close()
    text="📦 <b>Мои объявления</b>\n\n"+ ("\n\n".join(f"#{r['id']} — <b>{html.escape(r['title'])}</b> · {r['price_stars']} ⭐ · {'✅ проверено' if r['verified'] else '⏳ не проверено'}" for r in rows) if rows else "Нет объявлений.")
    await call.message.edit_text(text,reply_markup=back_kb("seller")); await call.answer()


@router.callback_query(F.data == "support")
async def support(call:CallbackQuery,state:FSMContext): await state.set_state(SupportForm.message); await call.message.edit_text("🆘 <b>Поддержка</b>\n\nОпишите проблему одним сообщением.",reply_markup=back_kb()); await call.answer()

@router.message(SupportForm.message)
async def support_message(message:Message,state:FSMContext):
    text=(message.text or "").strip()[:3000]; con=db(); cur=con.execute("INSERT INTO tickets(user_id,text,created_at) VALUES(?,?,?)",(message.from_user.id,text,now())); tid=cur.lastrowid; con.execute("INSERT INTO ticket_messages(ticket_id,sender_type,sender_id,text,created_at) VALUES(?,?,?,?,?)",(tid,"user",message.from_user.id,text,now())); con.commit(); con.close(); await state.clear(); log_action(message.from_user.id,"ticket_created","ticket",tid)
    for admin_id in ADMIN_IDS:
        try: await message.bot.send_message(admin_id,f"🆘 <b>Тикет #{tid}</b>\nОт: <code>{message.from_user.id}</code>\n\n{html.escape(text)}")
        except Exception: logging.exception("Cannot notify admin")
    await message.answer(f"✅ Тикет #{tid} создан. Ответ поддержки придёт сюда.",reply_markup=main_kb())


@router.message(Command("terms"))
async def terms(message:Message):
    await message.answer("<b>Правила</b>\n\nРазмещайте только контент, права на распространение которого у вас есть. Платные цифровые товары внутри Telegram оплачиваются Stars. Галочка ✅ означает проверку администрацией магазина.")

@router.message(Command("verify"))
async def verify(message:Message):
    if message.from_user.id not in ADMIN_IDS:return
    parts=(message.text or "").split();
    if len(parts)!=2 or not parts[1].isdigit(): await message.answer("/verify LISTING_ID"); return
    con=db(); cur=con.execute("UPDATE listings SET verified=1 WHERE id=?",(int(parts[1]),)); con.commit(); con.close(); await message.answer("✅ Проверено." if cur.rowcount else "Не найдено.")

@router.message(Command("unverify"))
async def unverify(message:Message):
    if message.from_user.id not in ADMIN_IDS:return
    parts=(message.text or "").split();
    if len(parts)!=2 or not parts[1].isdigit(): await message.answer("/unverify LISTING_ID"); return
    con=db(); cur=con.execute("UPDATE listings SET verified=0 WHERE id=?",(int(parts[1]),)); con.commit(); con.close(); await message.answer("Проверка снята." if cur.rowcount else "Не найдено.")


async def run():
    if not BOT_TOKEN: raise RuntimeError("Не задан BOT_TOKEN")
    init_db(); seed()
    bot=Bot(BOT_TOKEN,default=DefaultBotProperties(parse_mode=ParseMode.HTML)); dp=Dispatcher(); dp.include_router(router)
    await bot.delete_webhook(drop_pending_updates=True)
    await dp.start_polling(bot)


if __name__ == "__main__": asyncio.run(run())
