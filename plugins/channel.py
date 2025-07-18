import re
import logging
from plugins.Dreamxfutures.Imdbposter import get_movie_details, fetch_image
from database.users_chats_db import db
from pyrogram import Client, filters, enums
from info import CHANNELS, MOVIE_UPDATE_CHANNEL, LINK_PREVIEW, ABOVE_PREVIEW
from Script import script
from database.ia_filterdb import save_file
from pyrogram.types import InlineKeyboardMarkup, InlineKeyboardButton
from utils import temp
from pymongo.errors import PyMongoError

logger = logging.getLogger(__name__)

# Constants
CAPTION_LANGUAGES = {
    "hin": "Hindi", "hindi": "Hindi",
    "tam": "Tamil", "tamil": "Tamil",
    "kan": "Kannada", "kannada": "Kannada",
    "tel": "Telugu", "telugu": "Telugu",
    "mal": "Malayalam", "malayalam": "Malayalam",
    "eng": "English", "english": "English",
    "pun": "Punjabi", "punjabi": "Punjabi",
    "ben": "Bengali", "bengali": "Bengali",
    "mar": "Marathi", "marathi": "Marathi",
    "guj": "Gujarati", "gujarati": "Gujarati",
    "urd": "Urdu", "urdu": "Urdu"
}

OTT_PLATFORMS = {
    "nf": "Netflix", "netflix": "Netflix",
    "sonyliv": "SonyLiv", "sony": "SonyLiv", "sliv": "SonyLiv",
    "amzn": "Amazon Prime Video", "prime": "Amazon Prime Video", "primevideo": "Amazon Prime Video",
    "hotstar": "Disney+ Hotstar", "zee5": "Zee5",
    "jio": "JioHotstar", "jhs": "JioHotstar",
    "aha": "Aha", "hbo": "HBO Max", "paramount": "Paramount+",
    "apple": "Apple TV+", "hoichoi": "Hoichoi", "sunnxt": "Sun NXT", "viki": "Viki"
}

STANDARD_GENRES = {
    'Action', 'Adventure', 'Animation', 'Biography', 'Comedy', 'Crime', 'Documentary',
    'Drama', 'Family', 'Fantasy', 'Film-Noir', 'History', 'Horror', 'Music',
    'Musical', 'Mystery', 'Romance', 'Sci-Fi', 'Sport', 'Thriller', 'War', 'Western'
}

QUALITIES = [
    "HDCam", "HDTC", "CamRip", "TS", "TC", "TeleSync",
    "DVDScr", "DVDRip", "PreDVD",
    "WEBRip", "WEB-DL", "TVRip", "HDTV",
    "BluRay", "BRRip", "BDRip",
    "360p", "480p", "720p", "1080p", "2160p", "4K",
    "HEVC", "HDRip"
]

media_filter = filters.document | filters.video | filters.audio

@Client.on_message(filters.chat(CHANNELS) & media_filter)
async def media(bot, message):
    """Media Handler"""
    for file_type in ("document", "video", "audio"):
        media = getattr(message, file_type, None)
        if media is not None:
            break
    else:
        return

    media.file_type = file_type
    media.caption = message.caption


    success, _ = await save_file(media)
    if not success:
        logger.info("save_file returned False, skipping update for %s", media.file_name)
        return

    try:
        if await db.movie_update_status(bot.me.id):
            await send_msg(bot, filename=media.file_name, caption=media.caption or "")
        else:
            logger.debug("Movie-update status disabled for bot %s", bot.me.id)
    except Exception:
        logger.exception("Error In Movie Update for file %s", media.file_name)

async def send_msg(bot, filename, caption):
    """Parse and send the enriched movie info."""

    # 1) Clean and normalize
    filename = clean_mentions_links(filename).title()
    caption_clean = clean_mentions_links(caption or "").lower()
    filename_lower = filename.lower()
    # 2) Extract year & season
    year_match = re.search(r"\b(19|20)\d{2}\b", caption_clean)
    year = year_match.group(0) if year_match else None
    season_match = (re.search(r"(?i)(?:s|season)0*(\d{1,2})", caption_clean)
                    or re.search(r"(?i)(?:s|season)0*(\d{1,2})", filename_lower))
    season = season_match.group(1) if season_match else None

    if year and year in filename:
        filename = filename[:filename.find(year) + 4]
    elif season and season in filename.lower():
        filename = filename[:filename.lower().find(season) + len(season)]

    # 3) Quality & language & tags
    quality = await get_qualities(caption_clean, QUALITIES) or await get_qualities(filename_lower, QUALITIES) or "N/A"
    language_set = ({CAPTION_LANGUAGES[key] for key in CAPTION_LANGUAGES if key.lower() in caption_clean}
                    or {CAPTION_LANGUAGES[key] for key in CAPTION_LANGUAGES if key.lower() in filename_lower}
    )
    language = ", ".join(language_set) if language_set else "N/A"
    tag = "#SERIES" if season else "#MOVIE"
    ott_platform = extract_ott_platform(f"{filename} {caption_clean}")

    # 4) Clean filename spacing
    filename = re.sub(r"[()\[\]{}:;'\-!,.?_]", " ", filename)
    filename = re.sub(r"\s+", " ", filename).strip()

    # 5) Attempt to mark as “seen” in the DB (upsert)
    try:
        result = await db.filename_col.update_one(
            {"_id": filename},
            {"$setOnInsert": {"_id": filename}},
            upsert=True
        )
        is_new = result.upserted_id is not None
    except PyMongoError as db_err:
        logger.error("DB insert error for '%s': %s", filename, db_err, exc_info=True)
        return

    if not is_new:
        logger.info("'%s' already processed; skipping", filename)
        return
    
    resized_poster = None
    genres = None
    poster_url = None
    rating = None
    year = None
    imdb_url = None
    try:
        details = await get_movie_details(filename)
        print (f"IMDB details for {filename}: {details}")
        if details:
            language = language or details.get("language") or "N/A"
            year = year or details.get("year")
            raw_genres = details.get("genres", [])
            if isinstance(raw_genres, str):
                raw_genres = [g.strip() for g in raw_genres.split(",")]
            genres_filtered = [g for g in raw_genres if g in STANDARD_GENRES]
            genres = ", ".join(genres_filtered) if genres_filtered else "N/A"
            rating = details.get("rating", "N/A")
            poster_url = details.get("poster_url", None)
            imdb_url = details.get("url", None)

    except Exception as imdb_err:
        logger.warning("IMDB fetch error for '%s': %s", filename, imdb_err, exc_info=True)

    # 7) Download/rescale poster
    
    
    if poster_url:
        try:
            resized_poster = await fetch_image(poster_url)
        except Exception as img_err:
            logger.warning("Image fetch error for '%s': %s", poster_url, img_err, exc_info=True)

    # 8) Build the message
    text = script.MOVIE_UPDATE_NOTIFY_TXT.format(
               poster_url=poster_url,
               imdb_url=imdb_url,
               filename=filename,
               tag=tag,
               genres=genres,
               ott=ott_platform,
               quality=quality,
               language=language,
               rating=rating,
               search_link=temp.B_LINK
        )



    buttons = InlineKeyboardMarkup([[
        InlineKeyboardButton(
            'ɢᴇᴛ ғɪʟᴇs',
            url=f"https://t.me/{temp.U_NAME}?start=getfile-{filename.replace(' ', '-')}"
        )
    ]])

    # 9) Send
    try:
        if resized_poster and not LINK_PREVIEW:
            await bot.send_photo(
                chat_id=MOVIE_UPDATE_CHANNEL,
                photo=resized_poster,
                caption=text,
                reply_markup=buttons,
                parse_mode=enums.ParseMode.HTML
            )
            
        elif LINK_PREVIEW:
            await bot.send_message(
                chat_id=MOVIE_UPDATE_CHANNEL,
                text=text,
                reply_markup=buttons,
                invert_media=True if ABOVE_PREVIEW else False,
                parse_mode=enums.ParseMode.HTML
                
            )
            
        else:
            await bot.send_message(
                chat_id=MOVIE_UPDATE_CHANNEL,
                text=text,
                reply_markup=buttons,
                parse_mode=enums.ParseMode.HTML
            )

    except Exception as send_err:
        logger.exception("Failed to send message for '%s': %s", filename, send_err)

def clean_mentions_links(text: str) -> str:
    return re.sub(
        r'@[^ \n\r\t.,:;!?()\[\]{}<>\\/"\'=_%]+|\bwww\.[^\s\]\)]+|\([\@^]+\)|\[[\@^]+\]',
        '',
        text or ""
    ).strip()

async def get_qualities(text: str, qualities: list) -> str:
    matches = [q for q in qualities if q.lower() in text]
    return ", ".join(matches) if matches else ""

def extract_ott_platform(text: str) -> str:
    text = text.lower()
    found = {plat for key, plat in OTT_PLATFORMS.items() if key in text}
    return " | ".join(found) if found else "N/A"

def media_tag(filename: str, caption: str) -> str:
    if re.search(r'(?:s|season)[\s\-_]*\d+', f"{filename} {caption}", flags=re.IGNORECASE):
        return '#TV_SERIES'
    return '#MOVIE'
