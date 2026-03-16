import os
import logging
from dotenv import load_dotenv
from telegram import Update, InlineKeyboardButton, InlineKeyboardMarkup
from telegram.ext import (
    Application,
    CommandHandler,
    CallbackQueryHandler,
    ContextTypes,
)
from search import search_all

load_dotenv()
logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s %(message)s")
logger = logging.getLogger(__name__)

MOOD_QUERIES = {
    "happy":     ("😄 Радостное",    ["комедия фильм", "веселый мультфильм"]),
    "sad":       ("😢 Грустное",     ["мелодрама фильм", "драма трогательный"]),
    "romantic":  ("💕 Романтическое",["романтика фильм", "любовная история"]),
    "scary":     ("😱 Страшное",     ["ужасы фильм", "триллер напряженный"]),
    "thoughtful":("🤔 Задумчивое",   ["документальный фильм", "философская драма"]),
    "action":    ("💥 Боевик",       ["боевик фильм", "экшн приключения"]),
    "fantasy":   ("🧙 Фантастика",   ["фантастика фильм", "фэнтези приключения"]),
    "comedy":    ("🤡 Комедия",      ["комедия сериал", "ситком смешной"]),
}

PLACE_QUERIES = {
    "kitchen": (
        "🍳 Кухня",
        ["короткий комедийный сериал", "кулинарное шоу"],
        "Что-то лёгкое и короткое — чтобы смотреть одним глазом 🍳",
    ),
    "living": (
        "🛋 Гостиная",
        ["сериал драма", "семейный фильм"],
        "Уютный вечер с сериалом или хорошим кино 🛋",
    ),
    "cinema": (
        "🎬 Кинозал",
        ["блокбастер полный фильм", "эпический фильм"],
        "Большой экран — большое кино! 🎬",
    ),
}


def _esc(text: str) -> str:
    return text.replace("&", "&amp;").replace("<", "&lt;").replace(">", "&gt;")


def format_video(video: dict, index: int) -> str:
    title = _esc(video["title"])
    desc = _esc(video["description"])
    url = video["url"]
    source = video["source"]
    dur = video["duration_min"]
    dur_str = f"⏱ {dur} мин  " if dur else ""
    desc_str = f"\n<i>{desc}</i>" if desc else ""
    return (
        f"<b>{index}. {title}</b>{desc_str}\n"
        f"{dur_str}📺 {source}\n"
        f"🔗 {url}"
    )


def main_menu_keyboard():
    return InlineKeyboardMarkup([
        [InlineKeyboardButton("🎭 По настроению", callback_data="menu:mood")],
        [InlineKeyboardButton("📍 По месту просмотра", callback_data="menu:place")],
    ])


def mood_keyboard():
    buttons = []
    row = []
    for key, (label, _) in MOOD_QUERIES.items():
        row.append(InlineKeyboardButton(label, callback_data=f"mood:{key}"))
        if len(row) == 2:
            buttons.append(row)
            row = []
    if row:
        buttons.append(row)
    buttons.append([InlineKeyboardButton("↩️ Назад", callback_data="menu:main")])
    return InlineKeyboardMarkup(buttons)


def place_keyboard():
    buttons = [
        [InlineKeyboardButton(v[0], callback_data=f"place:{k}")]
        for k, v in PLACE_QUERIES.items()
    ]
    buttons.append([InlineKeyboardButton("↩️ Назад", callback_data="menu:main")])
    return InlineKeyboardMarkup(buttons)


def more_keyboard(context_key: str):
    return InlineKeyboardMarkup([
        [InlineKeyboardButton("🔄 Ещё рекомендации", callback_data=f"more:{context_key}")],
        [InlineKeyboardButton("🏠 В главное меню", callback_data="menu:main")],
    ])


async def start(update: Update, context: ContextTypes.DEFAULT_TYPE):
    await update.message.reply_text(
        "👋 Привет! Я <b>КиноМан</b> — твой личный помощник по фильмам и сериалам.\n\n"
        "Найду что посмотреть на Rutube и VK Video — по настроению или по месту просмотра!",
        parse_mode="HTML",
        reply_markup=main_menu_keyboard(),
    )


async def button_handler(update: Update, context: ContextTypes.DEFAULT_TYPE):
    query = update.callback_query
    await query.answer()
    data = query.data

    if data == "menu:main":
        await query.edit_message_text(
            "🎬 Главное меню — выбери как искать:",
            reply_markup=main_menu_keyboard(),
        )

    elif data == "menu:mood":
        await query.edit_message_text(
            "🎭 Какое у тебя сейчас настроение?",
            reply_markup=mood_keyboard(),
        )

    elif data == "menu:place":
        await query.edit_message_text(
            "📍 Где будешь смотреть?",
            reply_markup=place_keyboard(),
        )

    elif data.startswith("mood:"):
        mood_key = data.split(":")[1]
        await handle_recommendation(query, context, "mood", mood_key, page=0)

    elif data.startswith("place:"):
        place_key = data.split(":")[1]
        await handle_recommendation(query, context, "place", place_key, page=0)

    elif data.startswith("more:"):
        parts = data.split(":")
        kind, key, page_str = parts[1], parts[2], parts[3]
        await handle_recommendation(query, context, kind, key, page=int(page_str))


async def handle_recommendation(query, context, kind: str, key: str, page: int):
    await query.edit_message_text("🔍 Ищу для тебя...")

    if kind == "mood":
        label, search_queries = MOOD_QUERIES[key]
        intro = f"Подборка для настроения <b>{label}</b>:"
    else:
        label, search_queries, intro = PLACE_QUERIES[key]

    query_str = search_queries[page % len(search_queries)]
    offset = (page // len(search_queries)) * 10

    videos = await search_all(query_str, count=10 + offset)
    videos = videos[offset:offset + 10]

    if not videos:
        await query.edit_message_text(
            "😔 Ничего не нашёл по этому запросу. Попробуй другой вариант.",
            reply_markup=main_menu_keyboard(),
        )
        return

    text = f"🎬 {intro}\n\n"
    text += "\n\n─────────────────\n\n".join(
        format_video(v, i + 1) for i, v in enumerate(videos)
    )

    next_page = page + 1
    context_key = f"{kind}:{key}:{next_page}"

    await query.edit_message_text(
        text,
        parse_mode="HTML",
        reply_markup=more_keyboard(context_key),
        disable_web_page_preview=True,
    )


def main():
    token = os.getenv("TELEGRAM_TOKEN")
    if not token:
        raise RuntimeError("TELEGRAM_TOKEN не задан в .env")

    app = Application.builder().token(token).build()
    app.add_handler(CommandHandler("start", start))
    app.add_handler(CallbackQueryHandler(button_handler))

    logger.info("Бот запущен")
    app.run_polling(drop_pending_updates=True)


if __name__ == "__main__":
    main()
