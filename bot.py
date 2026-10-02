#!/usr/bin/env python3
import os, sys, json, time, logging, threading, re, io
import requests
from PIL import Image
from compress import compress_to_sticker, prepare_image, encode_webp
import store, logic, editor, texts

TOKEN = os.environ.get("TELEGRAM_BOT_TOKEN", "")
if not TOKEN:
    print("TELEGRAM_BOT_TOKEN not set", file=sys.stderr); sys.exit(1)
API = f"https://api.telegram.org/bot{TOKEN}/"
FILE_API = f"https://api.telegram.org/file/bot{TOKEN}/"
BASE = os.path.dirname(os.path.abspath(__file__))
STATE_FILE = store.PATH
DEFAULT_EMOJI = "😀"
MAX_DOWNLOAD = 20 * 1024 * 1024
BOT_USERNAME = ""
MAX_DRAFTS = 20
MAX_HISTORY = 8

class RedactFilter(logging.Filter):
    def filter(self, record):
        msg = record.getMessage()
        if TOKEN in msg:
            record.msg = msg.replace(TOKEN, "<TOKEN>"); record.args = ()
        return True

logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s %(message)s")
log = logging.getLogger("bot")
for h in logging.getLogger().handlers:
    h.addFilter(RedactFilter())
logging.getLogger("urllib3").setLevel(logging.WARNING)

def safe(e):
    return str(e).replace(TOKEN, "<TOKEN>")

sess = requests.Session()

# ---------------- state (thin wrappers over store.py: atomic writes + file lock) ----------------
get_user = store.get_user
update_user = store.update_user

def inc_count(uid):
    with store.transaction() as st:
        u = st["users"].setdefault(str(uid), {})
        u["count"] = u.get("count", 0) + 1

def set_await(uid, kind, data=None):
    update_user(uid, awaiting=kind, await_data=data)

def user_lang(uid, tg_user=None):
    lang = get_user(uid).get("lang")
    if lang in ("fa", "en"):
        return lang
    code = ((tg_user or {}).get("language_code") or "").lower()
    return "en" if code.startswith("en") else "fa"

# ---------------- i18n ----------------
T = {
 "fa": {
  "pick_lang": "🌐 زبان را انتخاب کن / Choose your language:",
  "lang_set": "✅ زبان روی فارسی تنظیم شد.",
  "help": (
    "سلام! 👋 به ربات استیکرساز خوش اومدی.\n\n"
    "یک عکس (یا فایل تصویری) برام بفرست تا آن را به استیکر تبدیل کنم:\n"
    "• اندازه‌ی تصویر طوری تنظیم می‌شود که یک ضلع دقیقاً ۵۱۲ پیکسل باشد.\n"
    "• خروجی WebP با حجم کم است و مستقیم به پک استیکر اضافه می‌شود.\n"
    "• اگر کپشن عکس فقط یک ایموجی باشد، همان برای استیکر استفاده می‌شود (پیش‌فرض 😀).\n\n"
    "دستورها:\n"
    "/newpack <عنوان> — ساخت پک جدید (با اولین عکس بعدی ساخته می‌شود)\n"
    "/pack یا /done — لینک پک فعلی\n"
    "/lang — تغییر زبان\n"
    "/start — منوی اصلی\n\n"
    "اگر پکی نداشته باشی، خودم با اولین عکس یک پک می‌سازم."),
  "menu_title": "یکی از گزینه‌ها را انتخاب کن 👇",
  "b_make": "🖼 ساخت استیکر", "b_new": "📦 پک جدید", "b_link": "🔗 لینک پک من",
  "b_lang": "🌐 زبان", "b_help": "ℹ️ راهنما", "b_add": "➕ افزودن بعدی",
  "b_open": "🔗 باز کردن پک", "b_menu": "🏠 منو",
  "send_image": "🖼 عکس یا فایل تصویری‌ات را بفرست تا به استیکر تبدیل کنم. (کپشن ایموجی = ایموجی استیکر)",
  "ask_title": "📦 عنوان پک جدید را بنویس و بفرست:\n(برای انصراف /cancel)",
  "need_title": "لطفاً عنوان را هم بنویس، مثلاً:\n/newpack استیکرهای من",
  "pending": "👌 پک جدید با عنوان «{title}» با اولین عکسی که بفرستی ساخته می‌شود.",
  "cancelled": "لغو شد.",
  "pack_cur": "📦 پک فعلی تو ({title}، {count} استیکر):\n{url}",
  "pack_pending": "هنوز پکی ساخته نشده؛ با فرستادن اولین عکس ساخته می‌شود.",
  "pack_none": "هنوز پکی نداری. یک عکس بفرست تا پک بسازم.",
  "unknown_cmd": "دستور نامعتبر است. /start را بزن تا منو را ببینی.",
  "dl_fail": "❌ دانلود تصویر ناموفق بود. لطفاً دوباره امتحان کن.",
  "conv_fail": "❌ نتوانستم این فایل را به عنوان تصویر بخوانم.",
  "info": "📐 اندازه: {w}×{h}\n📦 حجم اصلی: {orig}\n🗜 حجم نهایی: {final} (کیفیت {q})",
  "added": "✅ استیکر به پک اضافه شد {emoji}\n{info}\n\n🔗 {url}",
  "created": "✅ پک جدید ساخته شد و استیکر اضافه شد {emoji}\n{info}\n\n🔗 {url}",
  "add_fail": "⚠️ افزودن به پک ناموفق بود.\n{err}\n\nفایل WebP فشرده را برایت می‌فرستم.\n{info}",
  "doc_caption": "فایل استیکر (WebP)",
  "doc_fail": "❌ ارسال فایل هم ناموفق بود: {err}",
  "not_image": "این فایل تصویر نیست. لطفاً عکس یا فایل تصویری (PNG/JPG/WebP) بفرست.",
  "send_photo": "لطفاً یک عکس بفرست 🖼 یا /start را بزن.",
  "unexpected": "❌ خطای غیرمنتظره رخ داد. دوباره امتحان کن.",
  "sizeKB": "{n:.1f} کیلوبایت", "sizeMB": "{n:.2f} مگابایت",
  "err_generic": "خطای تلگرام: {d}",
  "errs": {
    "STICKERS_TOO_MUCH": "پک پر شده است (حداکثر ۱۲۰ استیکر). با /newpack یک پک جدید بساز.",
    "STICKERSET_INVALID": "این پک دیگر وجود ندارد. با /newpack یک پک جدید بساز.",
    "PEER_ID_INVALID": "اول باید در چت خصوصی با ربات /start را بزنی.",
    "user not found": "اول باید در چت خصوصی با ربات /start را بزنی.",
    "bot was blocked": "ربات مسدود شده است.",
    "already occupied": "این نام پک قبلاً استفاده شده است.",
    "USER_IS_BOT": "شناسه‌ی مالک نامعتبر است.",
    "Too Many Requests": "تعداد درخواست‌ها زیاد است؛ کمی صبر کن و دوباره امتحان کن.",
  },
 },
 "en": {
  "pick_lang": "🌐 زبان را انتخاب کن / Choose your language:",
  "lang_set": "✅ Language set to English.",
  "help": (
    "Hi! 👋 Welcome to the sticker maker bot.\n\n"
    "Send me a photo (or an image file) and I'll turn it into a sticker:\n"
    "• One side is resized to exactly 512 px.\n"
    "• Output is a small WebP, added straight to your sticker pack.\n"
    "• If the caption is a single emoji, it's used for the sticker (default 😀).\n\n"
    "Commands:\n"
    "/newpack <title> — create a new pack (made with the next image)\n"
    "/pack or /done — link to your current pack\n"
    "/lang — change language\n"
    "/start — main menu\n\n"
    "If you have no pack, I'll create one with your first image."),
  "menu_title": "Choose an option 👇",
  "b_make": "🖼 Make sticker", "b_new": "📦 New pack", "b_link": "🔗 My pack link",
  "b_lang": "🌐 Language", "b_help": "ℹ️ Help", "b_add": "➕ Add another",
  "b_open": "🔗 Open pack", "b_menu": "🏠 Menu",
  "send_image": "🖼 Send me a photo or image file and I'll convert it to a sticker. (Emoji caption = sticker emoji)",
  "ask_title": "📦 Send the title for the new pack:\n(/cancel to abort)",
  "need_title": "Please include a title, e.g.:\n/newpack My stickers",
  "pending": "👌 A new pack titled “{title}” will be created with the next image you send.",
  "cancelled": "Cancelled.",
  "pack_cur": "📦 Your current pack ({title}, {count} stickers):\n{url}",
  "pack_pending": "No pack created yet; it will be created with your next image.",
  "pack_none": "You don't have a pack yet. Send an image and I'll create one.",
  "unknown_cmd": "Unknown command. Send /start to see the menu.",
  "dl_fail": "❌ Couldn't download the image. Please try again.",
  "conv_fail": "❌ I couldn't read this file as an image.",
  "info": "📐 Size: {w}×{h}\n📦 Original: {orig}\n🗜 Final: {final} (quality {q})",
  "added": "✅ Sticker added to the pack {emoji}\n{info}\n\n🔗 {url}",
  "created": "✅ New pack created and sticker added {emoji}\n{info}\n\n🔗 {url}",
  "add_fail": "⚠️ Couldn't add it to the pack.\n{err}\n\nSending you the compressed WebP file instead.\n{info}",
  "doc_caption": "Sticker file (WebP)",
  "doc_fail": "❌ Sending the file failed too: {err}",
  "not_image": "That file isn't an image. Please send a photo or an image file (PNG/JPG/WebP).",
  "send_photo": "Please send an image 🖼 or press /start.",
  "unexpected": "❌ Unexpected error. Please try again.",
  "sizeKB": "{n:.1f} KB", "sizeMB": "{n:.2f} MB",
  "err_generic": "Telegram error: {d}",
  "errs": {
    "STICKERS_TOO_MUCH": "The pack is full (max 120 stickers). Create a new one with /newpack.",
    "STICKERSET_INVALID": "This pack no longer exists. Create a new one with /newpack.",
    "PEER_ID_INVALID": "You need to press /start in a private chat with the bot first.",
    "user not found": "You need to press /start in a private chat with the bot first.",
    "bot was blocked": "The bot is blocked.",
    "already occupied": "That pack name is already taken.",
    "USER_IS_BOT": "Invalid owner id.",
    "Too Many Requests": "Too many requests; wait a bit and try again.",
  },
 },
}

def tr(lang, key, **kw):
    s = T[lang][key]
    return s.format(**kw) if kw else s

def fmt_size(lang, n):
    return tr(lang, "sizeKB", n=n/1024) if n < 1024*1024 else tr(lang, "sizeMB", n=n/1024/1024)

def explain_error(lang, desc):
    d = desc or ""
    for k, v in T[lang]["errs"].items():
        if k.lower() in d.lower():
            return v
    return tr(lang, "err_generic", d=d)

# ---- merge new strings ----
for _l in ("fa", "en"):
    T[_l].update(texts.NEW[_l])
    T[_l]["help"] = T[_l]["help"] + texts.NEW[_l]["help_extra"]

# ---------------- keyboards ----------------
def kb(rows):
    return json.dumps({"inline_keyboard": rows})

def btn(text, data=None, url=None):
    b = {"text": text}
    if url: b["url"] = url
    else: b["callback_data"] = data
    return b

def contact_btns(lang):
    """URL buttons for support: one 'Contact admin' button, or Support 1 + Support 2 if a backup is set."""
    sp = store.support()
    if sp.get("backup"):
        return [btn(tr(lang, "b_support1"), url="https://t.me/" + sp["primary"]),
                btn(tr(lang, "b_support2"), url="https://t.me/" + sp["backup"])]
    return [btn(tr(lang, "b_contact"), url="https://t.me/" + sp["primary"])]

def support_note(lang):
    return tr(lang, "support_note") if store.support().get("backup") else ""

def main_menu(lang, admin=False):
    cb = contact_btns(lang)
    rows = [
        [btn(tr(lang, "b_make"), "m:make"), btn(tr(lang, "b_new"), "m:new")],
        [btn(tr(lang, "b_edit"), "m:edit"), btn(tr(lang, "b_tools"), "m:tools")],
        [btn("💎 پلن‌ها / Plans", "m:plans"), btn(tr(lang, "b_me"), "m:me")],
        [btn(tr(lang, "b_invite"), "m:invite")],
        [btn(tr(lang, "b_link"), "m:link")] + (cb if len(cb) == 1 else []),
        [btn(tr(lang, "b_lang"), "m:lang"), btn(tr(lang, "b_help"), "m:help")],
    ]
    if len(cb) > 1:
        rows.append(cb)
    if admin:
        rows.append([btn(tr(lang, "b_admin"), "a:home")])
    return kb(rows)

def lang_menu():
    return kb([[btn("🇮🇷 فارسی", "l:fa"), btn("🇬🇧 English", "l:en")]])

def after_sticker_menu(lang, url):
    return kb([
        [btn(tr(lang, "b_add"), "m:make"), btn(tr(lang, "b_open"), url=url)],
        [btn(tr(lang, "b_edit"), "m:edit"), btn(tr(lang, "b_menu"), "m:menu")],
    ])

def tools_menu(lang, uid):
    s = tr(lang, "on" if get_user(uid).get("preview") else "off")
    return kb([
        [btn(tr(lang, "b_emo"), "m:emoji"), btn(tr(lang, "b_rm"), "m:rm")],
        [btn(tr(lang, "e_dellast"), "m:dl")],
        [btn(tr(lang, "b_mode", s=s), "m:prev")],
        [btn(tr(lang, "b_menu"), "m:menu")],
    ])

def draft_menu(lang, d):
    main = (btn(tr(lang, "e_upd"), "e:upd") if d.get("sid") else btn(tr(lang, "e_add"), "e:add"))
    return kb([
        [btn(tr(lang, "e_text"), "e:text"), btn(tr(lang, "e_rot"), "e:rot"), btn(tr(lang, "e_flip"), "e:flip")],
        [btn(tr(lang, "b_emo"), "e:emo"), btn(tr(lang, "e_undo"), "e:undo"), btn(tr(lang, "e_reset"), "e:reset")],
        [main],
        [btn(tr(lang, "e_dellast"), "m:dl"), btn(tr(lang, "b_menu"), "m:menu")],
    ])

# ---------------- telegram api ----------------
class ApiError(Exception):
    pass

def call(method, data=None, files=None, timeout=60):
    try:
        r = sess.post(API + method, data=data, files=files, timeout=timeout)
    except requests.RequestException as e:
        raise ApiError("network: " + type(e).__name__)
    try:
        j = r.json()
    except Exception:
        raise ApiError(f"bad response HTTP {r.status_code}")
    if not j.get("ok"):
        raise ApiError(j.get("description", "unknown error"))
    return j["result"]

def send(chat_id, text, markup=None, html=False):
    data = {"chat_id": chat_id, "text": text, "disable_web_page_preview": True}
    if markup: data["reply_markup"] = markup
    if html: data["parse_mode"] = "HTML"
    try:
        return call("sendMessage", data)
    except ApiError as e:
        log.warning("sendMessage failed: %s", safe(e))

def show(chat_id, msg_id, text, markup=None):
    """Edit a panel message in place if possible, else send a new one."""
    if msg_id:
        data = {"chat_id": chat_id, "message_id": msg_id, "text": text, "disable_web_page_preview": True}
        if markup: data["reply_markup"] = markup
        try:
            return call("editMessageText", data)
        except ApiError as e:
            if "not modified" in str(e).lower():
                return None
    return send(chat_id, text, markup)

def clear_kb(chat_id, msg_id):
    if not msg_id:
        return
    try:
        call("editMessageReplyMarkup", {"chat_id": chat_id, "message_id": msg_id,
                                        "reply_markup": json.dumps({"inline_keyboard": []})})
    except ApiError:
        pass

def is_emoji(s):
    s = (s or "").strip()
    if not s or len(s) > 16:
        return False
    for ch in s:
        if ch.isalnum():
            return False
        if ord(ch) < 0x2000 and ch not in "\u00a9\u00ae":
            return False
    return True

_EMO_SINGLE = {0x203C, 0x2049, 0x2122, 0x2139, 0xA9, 0xAE}

def split_emojis(s):
    """Split text into emoji clusters. Returns list, or None if it contains non-emoji characters."""
    out, cur = [], ""
    chars = [c for c in (s or "") if not c.isspace()]
    def is_mod(o):
        return o in (0xFE0F, 0x20E3) or 0x1F3FB <= o <= 0x1F3FF or 0xE0020 <= o <= 0xE007F
    def is_ri(o):
        return 0x1F1E6 <= o <= 0x1F1FF
    for i, ch in enumerate(chars):
        o = ord(ch)
        if cur and o == 0x200D:
            cur += ch; continue
        if cur and cur.endswith("\u200d"):
            cur += ch; continue
        if cur and is_mod(o):
            cur += ch; continue
        if cur and is_ri(o) and len(cur) == 1 and is_ri(ord(cur)):
            cur += ch; continue
        if ch in "0123456789#*":
            if i + 1 < len(chars) and ord(chars[i + 1]) in (0xFE0F, 0x20E3):
                if cur: out.append(cur)
                cur = ch; continue
            return None
        if o >= 0x2190 or o in _EMO_SINGLE:
            if cur: out.append(cur)
            cur = ch; continue
        return None
    if cur:
        out.append(cur)
    return out

def new_pack_name(uid, bot_username):
    return f"pack_{uid}_{int(time.time())}_by_{bot_username.lower()}"

def pack_url(name):
    return f"https://t.me/addstickers/{name}"

def owns_sticker(uid, st):
    n = (st or {}).get("set_name") or ""
    return bool(BOT_USERNAME) and n.startswith(f"pack_{uid}_") and n.endswith("_by_" + BOT_USERNAME.lower())

def download_file(file_id):
    info = call("getFile", {"file_id": file_id})
    if info.get("file_size", 0) > MAX_DOWNLOAD:
        raise ApiError("file too large")
    r = sess.get(FILE_API + info["file_path"], timeout=60)
    r.raise_for_status()
    return r.content

def input_sticker(emoji):
    emojis = emoji if isinstance(emoji, list) else [emoji]
    return {"sticker": "attach://stk", "format": "static", "emoji_list": emojis}

# ---------------- drafts (in-memory editing sessions) ----------------
DRAFTS = {}

def png_bytes(im):
    b = io.BytesIO(); im.save(b, "PNG", compress_level=1); return b.getvalue()

def img_of(b):
    return Image.open(io.BytesIO(b)).convert("RGBA")

def new_draft(uid, im, emojis, pack=None, sid=None):
    im = im if im.mode in ("RGB", "RGBA") else im.convert("RGBA")
    p = png_bytes(im)
    if len(DRAFTS) >= MAX_DRAFTS and str(uid) not in DRAFTS:
        oldest = min(DRAFTS, key=lambda k: DRAFTS[k]["ts"]); DRAFTS.pop(oldest, None)
    d = {"orig": p, "cur": p, "hist": [], "emoji": list(emojis) or [DEFAULT_EMOJI],
         "pack": pack, "sid": sid, "ts": time.time()}
    DRAFTS[str(uid)] = d
    return d

def get_draft(uid):
    d = DRAFTS.get(str(uid))
    if d: d["ts"] = time.time()
    return d

def draft_push(d, new_png):
    d["hist"].append(d["cur"])
    del d["hist"][:-MAX_HISTORY]
    d["cur"] = new_png

def last_sticker_of(pack):
    s = call("getStickerSet", {"name": pack})
    sts = s.get("stickers") or []
    return (sts[-1] if sts else None), len(sts)

def do_add(uid, bot_username, webp, emojis):
    """Add webp to the user's current pack (creating it if needed). Returns (pack_name, created)."""
    u = get_user(uid)
    pack = u.get("pack"); title = u.get("pending_title")
    if pack:
        call("addStickerToSet", {"user_id": uid, "name": pack,
                                 "sticker": json.dumps(input_sticker(emojis))},
             files={"stk": ("sticker.webp", webp, "image/webp")})
        inc_count(uid)
        return pack, False
    name = new_pack_name(uid, bot_username)
    ptitle = (title or "My Stickers")[:64]
    call("createNewStickerSet", {"user_id": uid, "name": name, "title": ptitle,
                                 "sticker_format": "static",
                                 "stickers": json.dumps([input_sticker(emojis)])},
         files={"stk": ("sticker.webp", webp, "image/webp")})
    update_user(uid, pack=name, title=ptitle, count=1, pending_title=None)
    return name, True

# ---------------- handlers ----------------
def is_admin_uid(uid):
    return logic.is_admin(uid)

# ops an extra (non-owner) admin may use: stats, users, grant/revoke/add credits (+ plan quick-grants)
EXTRA_OPS = {"home", "stats", "ul", "u", "grant", "revoke", "credits", "ugf", "ugd", "uc", "ur", "urc", "ugp"}
EXTRA_AWAIT = {"a_target", "a_num"}

def show_menu(chat_id, lang, uid=None):
    send(chat_id, tr(lang, "menu_title"), main_menu(lang, bool(uid) and is_admin_uid(uid)))

def show_pack_link(chat_id, uid, lang):
    u = get_user(uid)
    if u.get("pack"):
        send(chat_id, tr(lang, "pack_cur", title=u.get("title", "My Stickers"),
                         count=u.get("count", "?"), url=pack_url(u["pack"])), main_menu(lang))
    elif u.get("pending_title"):
        send(chat_id, tr(lang, "pack_pending"), main_menu(lang))
    else:
        send(chat_id, tr(lang, "pack_none"), main_menu(lang))

# ---- money / account ----
def esc(t):
    return (t or "").replace("&", "&amp;").replace("<", "&lt;").replace(">", "&gt;")

def price_text(lang):
    """Admin-set free-text price line for this language ('' if unset)."""
    return (store.settings().get("price_fa" if lang == "fa" else "price_en") or "").strip()

def plan_filled(p):
    return logic.plan_ready(p)

def plan_what(lang, p):
    return tr(lang, "pp_what_credits", n=p["count"])

def feature_lines(features):
    parts = [x.strip(" •-\t") for x in re.split(r"[|;\n]+", features or "") if x.strip(" •-\t")]
    return parts[:8]

def plan_card(lang, p):
    dur = ("\n⏳ " + esc(p["duration"])) if p.get("duration") else ""
    fl = feature_lines(p.get("features"))
    feats = ("\n" + "\n".join("✔️ " + esc(x) for x in fl)) if fl else ""
    card = tr(lang, "plans_card", title=esc(p["title"]), count=p["count"], price=esc(p["price"]), dur=dur, feats=feats)
    if p.get("popular"):
        card = tr(lang, "pp_popular") + "\n" + card
    return card

def plans_screen_text(lang, uid):
    ready = [p for p in logic.pplans() if logic.plan_ready(p)]
    parts = [tr(lang, "plans_title"), tr(lang, "plans_free", n=store.settings()["free_quota"])]
    parts += [plan_card(lang, p) for p in ready]
    if not ready:
        parts.append(tr(lang, "plans_none"))
    parts.append(tr(lang, "plans_footer", uid=uid, note=support_note(lang)))
    sep = "\n\n━━━━━━━━━━\n\n"
    return sep.join(parts)

def show_plans(chat_id, uid, lang):
    rows = [contact_btns(lang)]
    if store.settings()["ref_enabled"]:
        rows.append([btn(tr(lang, "b_invite"), "m:invite")])
    rows.append([btn(tr(lang, "b_menu"), "m:menu")])
    send(chat_id, plans_screen_text(lang, uid), kb(rows), html=True)

def price_block(lang):
    """Display-only plans (only filled ones) + free tier + optional simple price line. '' if nothing set."""
    parts = []
    plans = [p for p in logic.pplans() if plan_filled(p)]
    if plans:
        lines = [tr(lang, "pp_head_user"), tr(lang, "pp_free_line", n=store.settings()["free_quota"])]
        lines += [tr(lang, "pp_user_line2", star="⭐ " if p.get("popular") else "", title=esc(p["title"]),
                     what=plan_what(lang, p), price=esc(p["price"]) + ((" / " + esc(p["duration"])) if p.get("duration") else "")) for p in plans]
        parts.append("\n".join(lines))
    p = price_text(lang)
    if p:
        parts.append(tr(lang, "price_line", text=esc(p)))
    return ("\n\n".join(parts) + "\n\n") if parts else ""

def show_upgrade(chat_id, uid, lang):
    rows = [contact_btns(lang)]
    if store.settings()["ref_enabled"]:
        rows.append([btn(tr(lang, "b_invite"), "m:invite")])
    send(chat_id, tr(lang, "quota_out", uid=uid, price=price_block(lang), note=support_note(lang)), kb(rows), html=True)

def show_me(chat_id, uid, lang):
    a = logic.access_info(uid); u = get_user(uid)
    lines = [tr(lang, "me_head")]
    if a["kind"] == "admin":
        lines.append(tr(lang, "me_admin"))
    elif a["kind"] == "unlimited":
        lines.append(tr(lang, "me_unl"))
    elif a["kind"] == "until":
        lines.append(tr(lang, "me_until", date=logic.fmt_date(a["until"])))
    lines.append(tr(lang, "me_free", n=a["free_left"], total=a["free_total"]))
    lines.append(tr(lang, "me_credits", n=a["credits"]))
    lines.append(tr(lang, "me_made", n=u.get("made", 0)))
    lines.append(tr(lang, "me_ref", n=u.get("ref_count", 0), b=u.get("ref_earned", 0)))
    pb = price_block(lang).strip()
    if pb:
        lines.append(pb)
    lines.append(tr(lang, "me_id", uid=uid))
    cb = contact_btns(lang)
    rows = [[btn(tr(lang, "b_invite"), "m:invite")] + (cb if len(cb) == 1 else [])] + ([cb] if len(cb) > 1 else []) + [[btn(tr(lang, "b_menu"), "m:menu")]]
    if a["kind"] == "quota" and not a["can"]:
        lines.append("\n" + tr(lang, "quota_out", uid=uid, price="", note=support_note(lang)))
    send(chat_id, "\n".join(lines), kb(rows), html=True)

def invite_share_url(link, body):
    from urllib.parse import quote
    return "https://t.me/share/url?url=" + quote(link, safe="") + "&text=" + quote(body, safe="")

def show_invite(chat_id, uid, lang):
    s = store.settings()
    if not s["ref_enabled"]:
        send(chat_id, tr(lang, "invite_off"), main_menu(lang)); return
    u = get_user(uid)
    link = f"https://t.me/{BOT_USERNAME}?start=ref_{uid}"
    inv = tr(lang, "invite_invitee_line", b=s["ref_invitee_bonus"]) if s["ref_invitee_bonus"] > 0 else ""
    send(chat_id, tr(lang, "invite_text", link=link, bonus=s["ref_bonus"], invitee_line=inv,
                     n=u.get("ref_count", 0), earned=u.get("ref_earned", 0)) + "\n\n" + tr(lang, "invite_forward_hint"))
    body = tr(lang, "invite_share_text")
    send(chat_id, body + "\n" + link,
         kb([[btn(tr(lang, "b_share"), url=invite_share_url(link, body))], [btn(tr(lang, "b_menu"), "m:menu")]]))

# ---- editing ----
def show_draft(chat_id, uid, lang, intro=False):
    d = get_draft(uid)
    if not d:
        send(chat_id, tr(lang, "edit_none"), main_menu(lang)); return
    webp, (w, h), q = encode_webp(img_of(d["cur"]))
    if intro:
        send(chat_id, tr(lang, "edit_intro", final=tr(lang, "e_upd" if d.get("sid") else "e_add")[2:].strip()))
    try:
        call("sendSticker", {"chat_id": chat_id, "reply_markup": draft_menu(lang, d)},
             files={"sticker": ("sticker.webp", webp, "image/webp")})
    except ApiError as e:
        log.warning("sendSticker failed: %s", safe(e))
        try:
            call("sendDocument", {"chat_id": chat_id, "reply_markup": draft_menu(lang, d),
                                  "caption": tr(lang, "draft_info", w=w, h=h, final=fmt_size(lang, len(webp)))},
                 files={"document": ("sticker.webp", webp, "image/webp")})
        except ApiError as e2:
            send(chat_id, tr(lang, "edit_fail", err=explain_error(lang, str(e2))))

def start_edit_last(chat_id, uid, lang):
    """Open the editor on the last made sticker (memory draft, else download from pack)."""
    if get_draft(uid):
        show_draft(chat_id, uid, lang, intro=True); return
    u = get_user(uid)
    if not u.get("pack"):
        send(chat_id, tr(lang, "edit_none"), main_menu(lang)); return
    try:
        st, _n = last_sticker_of(u["pack"])
        if not st or st.get("is_animated") or st.get("is_video"):
            send(chat_id, tr(lang, "edit_none"), main_menu(lang)); return
        im = fit_512(Image.open(io.BytesIO(download_file(st["file_id"]))).convert("RGBA"))
    except Exception as e:
        log.warning("edit start failed: %s", safe(e))
        send(chat_id, tr(lang, "edit_fail", err=explain_error(lang, str(e)))); return
    new_draft(uid, im, st.get("emoji") and [st["emoji"]] or [DEFAULT_EMOJI], pack=u["pack"], sid=st["file_id"])
    show_draft(chat_id, uid, lang, intro=True)

def fit_512(im):
    from compress import fit_512 as f
    return f(im)

def draft_commit(chat_id, uid, lang, bot_username, update):
    d = get_draft(uid)
    if not d:
        send(chat_id, tr(lang, "edit_none"), main_menu(lang)); return
    if not update and not logic.access_info(uid)["can"]:
        show_upgrade(chat_id, uid, lang); return
    webp, _, _ = encode_webp(img_of(d["cur"]))
    try:
        if update and d.get("sid") and d.get("pack"):
            call("addStickerToSet", {"user_id": uid, "name": d["pack"],
                                     "sticker": json.dumps(input_sticker(d["emoji"]))},
                 files={"stk": ("sticker.webp", webp, "image/webp")})
            try:
                call("deleteStickerFromSet", {"sticker": d["sid"]})
            except ApiError as e:
                log.warning("delete old sticker failed: %s", safe(e))
            pack = d["pack"]
        else:
            pack, _created = do_add(uid, bot_username, webp, d["emoji"])
            logic.consume(uid)
        try:
            st, n = last_sticker_of(pack)
            d["sid"] = st["file_id"] if st else None
            if pack == get_user(uid).get("pack"):
                update_user(uid, count=n)
        except ApiError:
            d["sid"] = None
        d["pack"] = pack
    except ApiError as e:
        log.warning("commit failed: %s", safe(e))
        send(chat_id, tr(lang, "edit_fail", err=explain_error(lang, str(e)))); return
    send(chat_id, tr(lang, "edit_updated" if update else "edit_added", emoji="".join(d["emoji"]), url=pack_url(pack)),
         after_sticker_menu(lang, pack_url(pack)))

def apply_emoji(chat_id, uid, lang, sid, emojis, draft=None):
    """setStickerEmojiList for a pack sticker (or store into a draft not yet in a pack)."""
    if draft is not None and not draft.get("sid"):
        draft["emoji"] = emojis
        send(chat_id, tr(lang, "emoji_draft", emoji="".join(emojis)))
        return True
    try:
        call("setStickerEmojiList", {"sticker": sid, "emoji_list": json.dumps(emojis)})
    except ApiError as e:
        send(chat_id, tr(lang, "emoji_fail", err=explain_error(lang, str(e)))); return False
    if draft is not None:
        draft["emoji"] = emojis
    send(chat_id, tr(lang, "emoji_set", emoji="".join(emojis)), main_menu(lang))
    return True

def remove_sticker(chat_id, uid, lang, st):
    if not owns_sticker(uid, st):
        send(chat_id, tr(lang, "rm_not_yours"), main_menu(lang)); return
    try:
        call("deleteStickerFromSet", {"sticker": st["file_id"]})
    except ApiError as e:
        send(chat_id, tr(lang, "rm_fail", err=explain_error(lang, str(e)))); return
    after_delete(chat_id, uid, lang, st.get("set_name"))

def after_delete(chat_id, uid, lang, set_name):
    cur = get_user(uid).get("pack")
    d = get_draft(uid)
    if d and d.get("pack") == set_name:
        d["sid"] = None; d["pack"] = None   # the draft is no longer tied to a pack sticker
    n = None
    try:
        n = len(call("getStickerSet", {"name": set_name}).get("stickers") or [])
    except ApiError:
        n = 0
    if set_name == cur:
        if n == 0:
            update_user(uid, pack=None, title=None, count=None)
        else:
            update_user(uid, count=n)
    send(chat_id, tr(lang, "rm_done_empty" if n == 0 else "rm_done", count=n), main_menu(lang))

def delete_last(chat_id, uid, lang):
    pack = get_user(uid).get("pack")
    if not pack:
        send(chat_id, tr(lang, "dellast_none"), main_menu(lang)); return
    try:
        st, _n = last_sticker_of(pack)
    except ApiError as e:
        send(chat_id, tr(lang, "rm_fail", err=explain_error(lang, str(e)))); return
    if not st:
        send(chat_id, tr(lang, "dellast_none"), main_menu(lang)); return
    try:
        call("deleteStickerFromSet", {"sticker": st["file_id"]})
    except ApiError as e:
        send(chat_id, tr(lang, "rm_fail", err=explain_error(lang, str(e)))); return
    after_delete(chat_id, uid, lang, pack)

# ---- main image flow ----
def handle_image(msg, bot_username, file_id, lang):
    chat_id = msg["chat"]["id"]; uid = msg["from"]["id"]
    if not logic.access_info(uid)["can"]:
        show_upgrade(chat_id, uid, lang); return
    caption = (msg.get("caption") or "").strip()
    emoji = caption if is_emoji(caption) else DEFAULT_EMOJI
    try:
        raw = download_file(file_id)
    except Exception as e:
        log.warning("download failed: %s", safe(e))
        send(chat_id, tr(lang, "dl_fail")); return
    try:
        img = prepare_image(raw)
        webp, (w, h), q = encode_webp(img)
    except Exception as e:
        log.warning("convert failed: %s", safe(e))
        send(chat_id, tr(lang, "conv_fail")); return
    info = tr(lang, "info", w=w, h=h, orig=fmt_size(lang, len(raw)), final=fmt_size(lang, len(webp)), q=q)

    if get_user(uid).get("preview"):
        new_draft(uid, img, [emoji])
        show_draft(chat_id, uid, lang, intro=True)
        return

    try:
        name, created = do_add(uid, bot_username, webp, [emoji])
        logic.consume(uid)
        d = new_draft(uid, img, [emoji], pack=name)
        try:
            st, _n = last_sticker_of(name)
            d["sid"] = st["file_id"] if st else None
        except ApiError:
            pass
        send(chat_id, tr(lang, "created" if created else "added", emoji=emoji, info=info, url=pack_url(name)),
             after_sticker_menu(lang, pack_url(name)))
    except ApiError as e:
        log.warning("sticker API failed: %s", safe(e))
        send(chat_id, tr(lang, "add_fail", err=explain_error(lang, str(e)), info=info), main_menu(lang))
        try:
            call("sendDocument", {"chat_id": chat_id, "caption": tr(lang, "doc_caption")},
                 files={"document": ("sticker.webp", webp, "image/webp")})
            logic.consume(uid)
        except ApiError as e2:
            log.warning("sendDocument failed: %s", safe(e2))
            send(chat_id, tr(lang, "doc_fail", err=explain_error(lang, str(e2))))

# ---------------- admin panel ----------------
_DIG = str.maketrans("۰۱۲۳۴۵۶۷۸۹٠١٢٣٤٥٦٧٨٩", "01234567890123456789")

def parse_int(s, lo=0, hi=100000):
    s = (s or "").strip().translate(_DIG)
    if not re.fullmatch(r"\d{1,9}", s):
        return None
    n = int(s)
    return n if lo <= n <= hi else None

def uname_of(u):
    return ("@" + u["username"]) if u.get("username") else ""

def who_label(uid):
    u = get_user(uid)
    return f"{u.get('name') or uid} {uname_of(u)} ({uid})".replace("  ", " ")

def admin_home(chat_id, mid, lang, uid=None):
    if uid is not None and not logic.is_owner(uid):
        rows = [
            [btn(tr(lang, "a_b_stats"), "a:stats"), btn(tr(lang, "a_b_users"), "a:ul:1")],
            [btn(tr(lang, "a_b_grant"), "a:grant"), btn(tr(lang, "a_b_revoke"), "a:revoke")],
            [btn(tr(lang, "a_b_credits"), "a:credits")],
            [btn(tr(lang, "b_menu"), "m:menu")],
        ]
        show(chat_id, mid, tr(lang, "a_title"), kb(rows)); return
    rows = [
        [btn(tr(lang, "a_b_stats"), "a:stats"), btn(tr(lang, "a_b_users"), "a:ul:1")],
        [btn(tr(lang, "a_b_grant"), "a:grant"), btn(tr(lang, "a_b_revoke"), "a:revoke")],
        [btn("💰 قیمت‌ها / Prices", "a:pp")],
        [btn(tr(lang, "a_b_credits"), "a:credits"), btn(tr(lang, "a_b_settings"), "a:set")],
        [btn(tr(lang, "a_b_bc"), "a:bc"), btn(tr(lang, "a_b_ref"), "a:ref")],
        [btn(tr(lang, "a_b_admins"), "a:ad"), btn(tr(lang, "a_b_support"), "a:su")],
        [btn(tr(lang, "b_menu"), "m:menu")],
    ]
    show(chat_id, mid, tr(lang, "a_title"), kb(rows))

def back_row(lang, to="a:home"):
    return [btn(tr(lang, "a_back"), to)]

def admin_stats(chat_id, mid, lang):
    s = logic.stats()
    show(chat_id, mid, tr(lang, "a_stats", **s), kb([back_row(lang)]))

def admin_users(chat_id, mid, lang, page):
    rows_, page, pages, total = logic.users_page(page)
    if not rows_:
        show(chat_id, mid, tr(lang, "a_users_none"), kb([back_row(lang)])); return
    rows = []
    for uid, u in rows_:
        label = f"{u.get('name') or uid} {uname_of(u)}".strip()[:40]
        rows.append([btn(f"{label} · {uid}"[:60], f"a:u:{uid}")])
    nav = []
    if page > 1: nav.append(btn(tr(lang, "a_prev"), f"a:ul:{page-1}"))
    if page < pages: nav.append(btn(tr(lang, "a_next"), f"a:ul:{page+1}"))
    if nav: rows.append(nav)
    rows.append(back_row(lang))
    show(chat_id, mid, tr(lang, "a_users_head", p=page, pages=pages, n=total), kb(rows))

def user_status_line(lang, uid):
    a = logic.access_info(uid)
    if a["kind"] == "admin": return tr(lang, "a_status_admin")
    if a["kind"] == "unlimited": return tr(lang, "a_status_unl")
    if a["kind"] == "until": return tr(lang, "a_status_until", date=logic.fmt_date(a["until"]), c=a["credits"])
    return tr(lang, "a_status_quota", f=a["free_left"], c=a["credits"])

def quick_plan_rows(lang, uid):
    rows = []
    for p in logic.pplans():
        if p.get("count"):
            rows.append([btn(("⚡ " + tr(lang, "pp_quick_credits", title=p["title"], n=p["count"]))[:60], f"a:ugp:{uid}:{p['id']}")])
    return rows

def admin_user(chat_id, mid, lang, uid):
    u = get_user(uid)
    if not u:
        show(chat_id, mid, tr(lang, "a_not_found"), kb([back_row(lang)])); return
    txt = tr(lang, "a_user", name=u.get("name") or "-", uname=uname_of(u), uid=uid,
             ulang=u.get("lang") or (u.get("language_code") or "?"), first=logic.fmt_date(u.get("first_seen", 0)),
             made=u.get("made", 0), status=user_status_line(lang, uid), refs=u.get("ref_count", 0))
    rows = [
        [btn(tr(lang, "a_op_grant"), f"a:ugf:{uid}"), btn(tr(lang, "a_op_days"), f"a:ugd:{uid}")],
        [btn(tr(lang, "a_op_credits"), f"a:uc:{uid}"), btn(tr(lang, "a_op_revoke"), f"a:ur:{uid}")],
    ] + quick_plan_rows(lang, uid) + [
        [btn(tr(lang, "a_back"), "a:ul:1")],
    ]
    show(chat_id, mid, txt, kb(rows))

def admin_grant_choice(chat_id, mid, lang, uid):
    show(chat_id, mid, tr(lang, "a_choose_grant", who=who_label(uid)),
         kb([[btn(tr(lang, "a_op_grant"), f"a:ugf:{uid}"), btn(tr(lang, "a_op_days"), f"a:ugd:{uid}")],
             [btn(tr(lang, "a_op_credits"), f"a:uc:{uid}")]] + quick_plan_rows(lang, uid) + [back_row(lang)]))

def tell_user(uid, key, **kw):
    l = user_lang(uid)
    send(uid, tr(l, key, **kw), main_menu(l))

def admin_settings(chat_id, mid, lang):
    s = store.settings()
    txt = tr(lang, "a_settings", quota=s["free_quota"], ref=tr(lang, "on" if s["ref_enabled"] else "off"),
             rb=s["ref_bonus"], ib=s["ref_invitee_bonus"],
             price=(s.get("price_fa" if lang == "fa" else "price_en") or tr(lang, "a_price_unset"))[:200])
    rows = [
        [btn(tr(lang, "a_s_quota"), "a:sn:free_quota"), btn(tr(lang, "a_s_price"), "a:price")],
        [btn(tr(lang, "a_s_rb"), "a:sn:ref_bonus"), btn(tr(lang, "a_s_ib"), "a:sn:ref_invitee_bonus")],
        [btn(tr(lang, "a_s_reftoggle", s=tr(lang, "on" if s["ref_enabled"] else "off")), "a:rt")],
        back_row(lang),
    ]
    show(chat_id, mid, txt, kb(rows))

def admin_price(chat_id, mid, lang):
    s = store.settings()
    ns = tr(lang, "a_price_unset")
    txt = tr(lang, "a_price_menu", fa=(s.get("price_fa") or ns)[:300], en=(s.get("price_en") or ns)[:300])
    rows = [[btn(tr(lang, "a_price_both"), "a:pr:both")],
            [btn(tr(lang, "a_price_fa"), "a:pr:fa"), btn(tr(lang, "a_price_en"), "a:pr:en")],
            [btn(tr(lang, "a_price_clear"), "a:pr:clear")],
            back_row(lang, "a:pp")]
    show(chat_id, mid, txt, kb(rows))

def admin_admins(chat_id, mid, lang):
    owner = store.admin_id()
    lines = "\n".join("• " + who_label(a) for a in logic.list_admins()) or tr(lang, "ad_none")
    rows = [[btn(tr(lang, "ad_add"), "a:ada")]]
    for a in logic.list_admins():
        rows.append([btn(f"🗑 {get_user(a).get('name') or a} · {a}"[:55], f"a:adx:{a}")])
    rows.append([btn(tr(lang, "ad_owner"), "a:own")])
    rows.append(back_row(lang))
    show(chat_id, mid, tr(lang, "ad_menu", owner=who_label(owner) if owner else "-", lines=lines), kb(rows))

def admin_support(chat_id, mid, lang):
    sp = store.support()
    txt = tr(lang, "su_menu", p=sp["primary"], b=("@" + sp["backup"]) if sp["backup"] else tr(lang, "su_none"))
    rows = [[btn(tr(lang, "su_b_primary"), "a:sup:primary"), btn(tr(lang, "su_b_backup"), "a:sup:backup")]]
    if sp["backup"]:
        rows.append([btn(tr(lang, "su_b_clear"), "a:suc")])
    rows.append(back_row(lang))
    show(chat_id, mid, txt, kb(rows))

def plan_mgr_line(lang, pos, p):
    return tr(lang, "pp_mgr_line", pos=pos, star="⭐ " if p.get("popular") else "", title=p["title"],
              count=p["count"] if p.get("count") else "❓", price=p["price"] or "—",
              dur=(" / " + p["duration"]) if p.get("duration") else "",
              hid="" if logic.plan_ready(p) else tr(lang, "pp_hidden"))

def admin_pplans(chat_id, mid, lang):
    plans = logic.pplans()
    lines = "\n".join(plan_mgr_line(lang, i + 1, p) for i, p in enumerate(plans)) or tr(lang, "pp_none")
    rows = []
    for i, p in enumerate(plans):
        r = [btn(f"✏️ {p['title']}"[:30], f"a:ppe:{p['id']}")]
        if i > 0: r.append(btn(tr(lang, "pp_up"), f"a:ppu:{p['id']}"))
        if i < len(plans) - 1: r.append(btn(tr(lang, "pp_down"), f"a:ppd:{p['id']}"))
        r.append(btn("🗑", f"a:ppx:{p['id']}"))
        rows.append(r)
    rows.append([btn(tr(lang, "pp_add"), "a:ppa"), btn(tr(lang, "pp_clear"), "a:ppc")])
    rows.append([btn(tr(lang, "pp_note"), "a:price")])
    rows.append(back_row(lang))
    show(chat_id, mid, tr(lang, "pp_mgr", lines=lines), kb(rows))

def admin_plan_edit(chat_id, mid, lang, pid):
    p = logic.get_pplan(pid)
    if not p:
        send(chat_id, tr(lang, "pp_gone")); return
    card = plan_mgr_line(lang, "•", p)
    if p.get("features"): card += "\n🌟 " + p["features"]
    pop = tr(lang, "on" if p.get("popular") else "off")
    show(chat_id, mid, tr(lang, "pp_edit_menu", id=p["id"], card=card),
         kb([[btn(tr(lang, "pp_e_title"), f"a:ppet:{pid}"), btn(tr(lang, "pp_e_count"), f"a:ppen:{pid}")],
             [btn(tr(lang, "pp_e_price"), f"a:ppep:{pid}"), btn(tr(lang, "pp_e_dur"), f"a:pped:{pid}")],
             [btn(tr(lang, "pp_e_feat"), f"a:ppef:{pid}"), btn(tr(lang, "pp_e_pop", s=pop), f"a:ppt:{pid}")],
             back_row(lang, "a:pp")]))

def pop_kb(lang):
    return kb([[btn(tr(lang, "yes"), "a:ppp:1"), btn(tr(lang, "no"), "a:ppp:0")]])

def skip_kb(lang, data):
    return kb([[btn(tr(lang, "pp_skip"), data)]])

def admin_ref(chat_id, mid, lang):
    s = store.settings()
    top = logic.top_referrers(5)
    lines = "\n".join(f"{i+1}. {u.get('name') or uid} {uname_of(u)} — {u['ref_count']}" for i, (uid, u) in enumerate(top)) or tr(lang, "a_ref_none")
    txt = tr(lang, "a_ref", s=tr(lang, "on" if s["ref_enabled"] else "off"), total=logic.stats()["refs"],
             rb=s["ref_bonus"], ib=s["ref_invitee_bonus"], top=lines)
    rows = [[btn(tr(lang, "a_s_reftoggle", s=tr(lang, "on" if s["ref_enabled"] else "off")), "a:rt2")],
            [btn(tr(lang, "a_s_rb"), "a:sn:ref_bonus"), btn(tr(lang, "a_s_ib"), "a:sn:ref_invitee_bonus")],
            back_row(lang)]
    show(chat_id, mid, txt, kb(rows))

def start_broadcast_job(admin_chat, lang, text):
    ids = [i for i in logic.all_user_ids()]
    def job():
        ok = fail = 0
        for i in ids:
            r = send(i, text)
            if r: ok += 1
            else: fail += 1
            time.sleep(0.06)
        send(admin_chat, tr(lang, "a_bc_done", ok=ok, fail=fail))
    threading.Thread(target=job, daemon=True).start()

def admin_callback(data, chat_id, mid, uid, lang):
    """data starts with 'a:'; caller verified admin."""
    p = data.split(":")
    op = p[1] if len(p) > 1 else ""
    arg = p[2] if len(p) > 2 else ""
    arg2 = p[3] if len(p) > 3 else ""
    prev_data = get_user(uid).get("await_data") or {}
    if not logic.is_owner(uid) and op not in EXTRA_OPS:
        send(chat_id, tr(lang, "a_denied")); return
    set_await(uid, None)
    if op == "home": admin_home(chat_id, mid, lang, uid)
    elif op == "stats": admin_stats(chat_id, mid, lang)
    elif op == "ul": admin_users(chat_id, mid, lang, parse_int(arg, 1, 10**6) or 1)
    elif op == "u": admin_user(chat_id, mid, lang, int(arg))
    elif op in ("grant", "revoke", "credits"):
        set_await(uid, "a_target", {"op": op}); send(chat_id, tr(lang, "a_ask_target"))
    elif op == "ugf":
        t = int(arg); logic.grant_unlimited(t)
        show(chat_id, mid, tr(lang, "a_done_grant", who=who_label(t)), kb([back_row(lang)]))
        tell_user(t, "u_granted")
    elif op == "ugd":
        set_await(uid, "a_num", {"op": "days", "target": int(arg)}); send(chat_id, tr(lang, "a_ask_num", what=tr(lang, "a_what_days")))
    elif op == "uc":
        set_await(uid, "a_num", {"op": "credits", "target": int(arg)}); send(chat_id, tr(lang, "a_ask_num", what=tr(lang, "a_what_credits")))
    elif op == "ur":
        show(chat_id, mid, tr(lang, "a_confirm_revoke", who=who_label(int(arg))),
             kb([[btn(tr(lang, "yes"), f"a:urc:{arg}"), btn(tr(lang, "no"), f"a:u:{arg}")]]))
    elif op == "urc":
        t = int(arg); logic.revoke(t)
        show(chat_id, mid, tr(lang, "a_done_revoke", who=who_label(t)), kb([back_row(lang)]))
        tell_user(t, "u_revoked")
    elif op == "set": admin_settings(chat_id, mid, lang)
    elif op == "sn":
        if arg not in logic.NUM_SETTINGS: return
        set_await(uid, "a_setnum", {"key": arg}); send(chat_id, tr(lang, "a_s_ask_num"))
    elif op == "ad": admin_admins(chat_id, mid, lang)
    elif op == "ada":
        set_await(uid, "a_ad_add"); send(chat_id, tr(lang, "ad_ask_add"))
    elif op == "adx":
        t = int(arg)
        if logic.remove_admin(t):
            send(chat_id, tr(lang, "ad_removed", who=who_label(t))); tell_user(t, "ad_notify_removed")
        admin_admins(chat_id, mid, lang)
    elif op == "own":
        set_await(uid, "a_owner"); send(chat_id, tr(lang, "ad_owner_ask"))
    elif op == "ownc":
        t = int(arg); old = uid
        if not logic.is_owner(uid) or t == old:
            return
        logic.set_owner(t)
        send(chat_id, tr(lang, "ad_owner_done", who=who_label(t)), main_menu(lang, False))
        tell_user(t, "ad_owner_notify_new")
    elif op == "su": admin_support(chat_id, mid, lang)
    elif op == "sup":
        if arg in ("primary", "backup"):
            set_await(uid, "a_su", {"slot": arg}); send(chat_id, tr(lang, "su_ask"))
    elif op == "suc":
        logic.set_support("backup", ""); send(chat_id, tr(lang, "su_cleared")); admin_support(chat_id, None, lang)
    elif op == "pp": admin_pplans(chat_id, mid, lang)
    elif op == "ppa":
        if len(logic.pplans()) >= logic.MAX_PPLANS:
            send(chat_id, tr(lang, "pp_full", n=logic.MAX_PPLANS)); return
        set_await(uid, "a_pp_title", {}); send(chat_id, tr(lang, "pp_ask_title"))
    elif op == "ppx":
        logic.remove_pplan(int(arg)); send(chat_id, tr(lang, "pp_deleted")); admin_pplans(chat_id, mid, lang)
    elif op in ("ppu", "ppd"):
        logic.move_pplan(int(arg), -1 if op == "ppu" else 1); admin_pplans(chat_id, mid, lang)
    elif op == "ppc":
        show(chat_id, mid, tr(lang, "pp_clear_confirm"), kb([[btn(tr(lang, "yes"), "a:ppcy"), btn(tr(lang, "no"), "a:pp")]]))
    elif op == "ppcy":
        logic.clear_pplans(); send(chat_id, tr(lang, "pp_cleared")); admin_pplans(chat_id, mid, lang)
    elif op == "ppe": admin_plan_edit(chat_id, mid, lang, int(arg))
    elif op == "ppt":
        logic.toggle_popular(int(arg)); admin_plan_edit(chat_id, mid, lang, int(arg))
    elif op in ("ppet", "ppen", "ppep", "pped", "ppef"):
        key = {"ppet": "title", "ppen": "count", "ppep": "price", "pped": "duration", "ppef": "features"}[op]
        set_await(uid, "a_pp_edit", {"id": int(arg), "field": key})
        send(chat_id, tr(lang, {"title": "pp_ask_title", "count": "pp_ask_count", "price": "pp_ask_price",
                                "duration": "pp_ask_dur", "features": "pp_ask_feat"}[key]))
    elif op == "pps":   # skip optional step in the add-plan wizard
        d = dict(prev_data)
        if arg == "dur":
            d["duration"] = ""; set_await(uid, "a_pp_feat", d)
            send(chat_id, tr(lang, "pp_ask_feat"), skip_kb(lang, "a:pps:feat"))
        elif arg == "feat":
            d["features"] = ""; set_await(uid, "a_pp_pop", d); send(chat_id, tr(lang, "pp_ask_pop"), pop_kb(lang))
    elif op == "ppp":   # popular yes/no in wizard -> create
        d = dict(prev_data)
        if "title" not in d or "count" not in d or "price" not in d: return
        pid = logic.add_pplan(d["title"], d["count"], d["price"], d.get("duration", ""), d.get("features", ""), arg == "1")
        send(chat_id, tr(lang, "pp_added" if pid else "pp_full", n=logic.MAX_PPLANS)); admin_pplans(chat_id, None, lang)
    elif op == "ugp":
        t = int(arg); n = logic.grant_plan(t, int(arg2))
        if not n:
            send(chat_id, tr(lang, "pp_gone")); return
        show(chat_id, mid, tr(lang, "a_done_credits", n=n, who=who_label(t)), kb([back_row(lang)]))
        tell_user(t, "u_credits", n=n)
    elif op == "price": admin_price(chat_id, mid, lang)
    elif op == "pr":
        if arg == "clear":
            logic.set_setting("price_fa", ""); logic.set_setting("price_en", "")
            send(chat_id, tr(lang, "a_price_cleared")); admin_price(chat_id, None, lang)
        elif arg in ("both", "fa", "en"):
            set_await(uid, "a_price", {"which": arg})
            send(chat_id, tr(lang, "a_price_ask", which=tr(lang, "a_price_which_" + arg)))
    elif op == "rt":
        logic.toggle_ref(); admin_settings(chat_id, mid, lang)
    elif op == "rt2":
        logic.toggle_ref(); admin_ref(chat_id, mid, lang)
    elif op == "ref": admin_ref(chat_id, mid, lang)
    elif op == "bc":
        set_await(uid, "a_bc", {}); send(chat_id, tr(lang, "a_bc_ask"))
    elif op == "bcy":
        with store.transaction() as st:
            pb = st.get("pending_broadcast"); st["pending_broadcast"] = None
        if not pb:
            send(chat_id, tr(lang, "a_bc_none")); return
        show(chat_id, mid, tr(lang, "a_bc_sending"))
        start_broadcast_job(chat_id, lang, pb["text"])
    elif op == "bcn":
        with store.transaction() as st:
            st["pending_broadcast"] = None
        show(chat_id, mid, tr(lang, "a_bc_cancel"), kb([back_row(lang)]))

def admin_text(msg, uid, lang, aw, data):
    """Handle a plain text reply while an admin awaiting-state is active. Returns True if consumed."""
    chat_id = msg["chat"]["id"]; text = (msg.get("text") or "").strip()
    if not logic.is_owner(uid) and aw not in EXTRA_AWAIT:
        set_await(uid, None); return False
    if aw == "a_target":
        t = logic.find_user(text)
        if t is None:
            send(chat_id, tr(lang, "a_not_found")); return True
        op = data.get("op")
        set_await(uid, None)
        if op == "grant": admin_grant_choice(chat_id, None, lang, t)
        elif op == "revoke":
            send(chat_id, tr(lang, "a_confirm_revoke", who=who_label(t)),
                 kb([[btn(tr(lang, "yes"), f"a:urc:{t}"), btn(tr(lang, "no"), "a:home")]]))
        else:
            set_await(uid, "a_num", {"op": "credits", "target": t}); send(chat_id, tr(lang, "a_ask_num", what=tr(lang, "a_what_credits")))
        return True
    if aw == "a_num":
        n = parse_int(text, 1, 100000)
        if n is None:
            send(chat_id, tr(lang, "a_bad_num")); return True
        t = data["target"]; set_await(uid, None)
        if data["op"] == "days":
            until = logic.add_days(t, n); d = logic.fmt_date(until)
            send(chat_id, tr(lang, "a_done_days", n=n, who=who_label(t), date=d), kb([back_row(lang)]))
            tell_user(t, "u_days", n=n, date=d)
        else:
            logic.add_credits(t, n)
            send(chat_id, tr(lang, "a_done_credits", n=n, who=who_label(t)), kb([back_row(lang)]))
            tell_user(t, "u_credits", n=n)
        return True
    if aw == "a_setnum":
        n = parse_int(text, 0, 100000)
        if n is None:
            send(chat_id, tr(lang, "a_bad_num")); return True
        logic.set_setting(data["key"], n); set_await(uid, None)
        send(chat_id, tr(lang, "a_s_saved")); admin_settings(chat_id, None, lang); return True
    if aw == "a_ad_add":
        t = logic.find_user(text)
        if t is None:
            send(chat_id, tr(lang, "a_not_found")); return True
        set_await(uid, None)
        if not logic.add_admin(t):
            send(chat_id, tr(lang, "ad_is_owner")); return True
        send(chat_id, tr(lang, "ad_added", who=who_label(t))); tell_user(t, "ad_notify_new")
        admin_admins(chat_id, None, lang); return True
    if aw == "a_owner":
        t = logic.find_user(text)
        if t is None:
            send(chat_id, tr(lang, "a_not_found")); return True
        set_await(uid, None)
        if logic.is_owner(t):
            send(chat_id, tr(lang, "ad_owner_same")); return True
        send(chat_id, tr(lang, "ad_owner_confirm", who=who_label(t)),
             kb([[btn(tr(lang, "yes"), f"a:ownc:{t}"), btn(tr(lang, "no"), "a:ad")]])); return True
    if aw == "a_su":
        name = logic.clean_username(text)
        if not name:
            send(chat_id, tr(lang, "su_bad")); return True
        logic.set_support(data["slot"], name); set_await(uid, None)
        send(chat_id, tr(lang, "su_saved")); admin_support(chat_id, None, lang); return True
    if aw == "a_pp_title":
        if not text: return True
        data["title"] = text[:60]; set_await(uid, "a_pp_count", data); send(chat_id, tr(lang, "pp_ask_count")); return True
    if aw == "a_pp_count":
        n = parse_int(text, 1, 10**6)
        if n is None:
            send(chat_id, tr(lang, "a_bad_num")); return True
        data["count"] = n; set_await(uid, "a_pp_price", data); send(chat_id, tr(lang, "pp_ask_price")); return True
    if aw == "a_pp_price":
        if not text: return True
        data["price"] = text[:120]; set_await(uid, "a_pp_dur", data)
        send(chat_id, tr(lang, "pp_ask_dur"), skip_kb(lang, "a:pps:dur")); return True
    if aw == "a_pp_dur":
        if not text: return True
        data["duration"] = text[:40]; set_await(uid, "a_pp_feat", data)
        send(chat_id, tr(lang, "pp_ask_feat"), skip_kb(lang, "a:pps:feat")); return True
    if aw == "a_pp_feat":
        if not text: return True
        data["features"] = text[:200]; set_await(uid, "a_pp_pop", data); send(chat_id, tr(lang, "pp_ask_pop"), pop_kb(lang)); return True
    if aw == "a_pp_pop":
        send(chat_id, tr(lang, "pp_ask_pop"), pop_kb(lang)); return True
    if aw == "a_pp_edit":
        if not text: return True
        f = data["field"]
        if f == "count":
            n = parse_int(text, 1, 10**6)
            if n is None:
                send(chat_id, tr(lang, "a_bad_num")); return True
            val = n
        elif f in ("duration", "features"):
            val = "" if text == "-" else text
        else:
            val = text
        ok_ = logic.update_pplan(data["id"], **{f: val})
        send(chat_id, tr(lang, "pp_saved" if ok_ else "pp_gone")); set_await(uid, None)
        admin_plan_edit(chat_id, None, lang, data["id"]) if ok_ else admin_pplans(chat_id, None, lang); return True
    if aw == "a_price":
        raw = (msg.get("text") or "").strip()
        if not raw:
            return True
        which = data.get("which", "both")
        if which in ("both", "fa"): logic.set_setting("price_fa", raw)
        if which in ("both", "en"): logic.set_setting("price_en", raw)
        set_await(uid, None)
        send(chat_id, tr(lang, "a_price_saved", text=raw[:500]))
        admin_price(chat_id, None, lang); return True
    if aw == "a_bc":
        if not text:
            return True
        with store.transaction() as st:
            st["pending_broadcast"] = {"text": text[:3500], "by": uid}
        set_await(uid, None)
        send(chat_id, tr(lang, "a_bc_confirm", text=text[:3500], n=len(logic.all_user_ids())),
             kb([[btn(tr(lang, "yes"), "a:bcy"), btn(tr(lang, "no"), "a:bcn")]]))
        return True
    return False

# ---------------- message / callback routing ----------------
def sticker_of(msg):
    st = msg.get("sticker")
    return st if st and not st.get("is_animated") and not st.get("is_video") else None

def ask_emoji_for(chat_id, uid, lang, sid, draft=False):
    set_await(uid, "emoji_new", {"sid": sid, "draft": draft})
    send(chat_id, tr(lang, "ask_emoji_cur" if draft else "ask_emoji_new"))

def set_emoji_for_sticker(chat_id, uid, lang, st_obj, emojis):
    if not owns_sticker(uid, st_obj):
        send(chat_id, tr(lang, "rm_not_yours"), main_menu(lang)); return
    d = get_draft(uid)
    apply_emoji(chat_id, uid, lang, st_obj["file_id"], emojis,
                draft=d if d and d.get("sid") == st_obj["file_id"] else None)

def handle_message(msg, bot_username):
    chat_id = msg["chat"]["id"]
    if "from" not in msg or msg["from"].get("is_bot"):
        return
    tg = msg["from"]
    uid = tg["id"]
    is_new, _ = logic.touch_user(tg)
    if logic.bind_admin_if_needed(tg):
        log.info("admin bound to numeric id %s", uid)
        send(chat_id, tr(user_lang(uid, tg), "a_bound", uid=uid))
    lang = user_lang(uid, tg)
    admin = is_admin_uid(uid)
    text = (msg.get("text") or "").strip()
    aw = u_awaiting(uid)

    if text.startswith("/"):
        parts = text.split(None, 1)
        cmd = parts[0].split("@")[0].lower()
        arg = parts[1].strip() if len(parts) > 1 else ""
        if aw and cmd != "/newpack":
            set_await(uid, None)
            if cmd == "/cancel":
                send(chat_id, tr(lang, "cancelled"), main_menu(lang, admin)); return
        if cmd == "/start":
            if is_new and arg.startswith("ref_") and arg[4:].isdigit():
                r = logic.try_referral(uid, int(arg[4:]))
                if r:
                    log.info("referral: %s invited %s", arg[4:], uid)
                    rl = user_lang(int(arg[4:]))
                    send(int(arg[4:]), tr(rl, "ref_joined_referrer", bonus=r["bonus"]))
                    if r["invitee_bonus"] > 0:
                        send(chat_id, tr(lang, "ref_joined_invitee", bonus=r["invitee_bonus"]))
            send(chat_id, tr(lang, "pick_lang"), lang_menu())
        elif cmd == "/lang":
            send(chat_id, tr(lang, "pick_lang"), lang_menu())
        elif cmd == "/help":
            send(chat_id, tr(lang, "help"), main_menu(lang, admin))
        elif cmd == "/cancel":
            send(chat_id, tr(lang, "cancelled"), main_menu(lang, admin))
        elif cmd == "/newpack":
            if not arg:
                send(chat_id, tr(lang, "need_title")); return
            update_user(uid, pending_title=arg[:64], pack=None, title=None, count=None, awaiting=None, await_data=None)
            send(chat_id, tr(lang, "pending", title=arg[:64]))
        elif cmd in ("/done", "/pack"):
            show_pack_link(chat_id, uid, lang)
        elif cmd == "/plans":
            show_plans(chat_id, uid, lang)
        elif cmd == "/me":
            show_me(chat_id, uid, lang)
        elif cmd == "/id":
            send(chat_id, tr(lang, "me_id", uid=uid), kb([contact_btns(lang)]), html=True)
        elif cmd == "/invite":
            show_invite(chat_id, uid, lang)
        elif cmd == "/edit":
            start_edit_last(chat_id, uid, lang)
        elif cmd in ("/emoji", "/delsticker"):
            rst = (msg.get("reply_to_message") or {}).get("sticker")
            if not rst:
                if cmd == "/emoji":
                    set_await(uid, "emoji_pick"); send(chat_id, tr(lang, "ask_emoji_pick"))
                else:
                    set_await(uid, "rm_pick"); send(chat_id, tr(lang, "rm_ask"))
            elif cmd == "/delsticker":
                remove_sticker(chat_id, uid, lang, rst)
            else:
                ems = split_emojis(arg) if arg else None
                if ems and 1 <= len(ems) <= 20:
                    set_emoji_for_sticker(chat_id, uid, lang, rst, ems)
                elif not owns_sticker(uid, rst):
                    send(chat_id, tr(lang, "rm_not_yours"), main_menu(lang))
                else:
                    ask_emoji_for(chat_id, uid, lang, rst["file_id"])
        elif cmd == "/admin":
            if admin:
                admin_home(chat_id, None, lang, uid)
            else:
                send(chat_id, tr(lang, "unknown_cmd"), main_menu(lang))
        else:
            send(chat_id, tr(lang, "unknown_cmd"), main_menu(lang, admin))
        return

    # replying to a sticker with emojis only -> change that sticker's emoji
    rst = (msg.get("reply_to_message") or {}).get("sticker")
    if text and rst and not aw:
        ems = split_emojis(text)
        if ems and 1 <= len(ems) <= 20:
            set_emoji_for_sticker(chat_id, uid, lang, rst, ems); return

    # awaiting-states (plain input)
    if aw:
        data = get_user(uid).get("await_data") or {}
        if aw == "title" and text:
            update_user(uid, pending_title=text[:64], pack=None, title=None, count=None, awaiting=None, await_data=None)
            send(chat_id, tr(lang, "pending", title=text[:64])); return
        if aw.startswith("a_"):
            if not admin:
                set_await(uid, None)
            elif text and admin_text(msg, uid, lang, aw, data):
                return
        elif aw == "e_text" and text:
            t = editor.clean_text(text)
            if not t:
                send(chat_id, tr(lang, "text_bad")); return
            set_await(uid, "e_pos", {"text": t})
            send(chat_id, tr(lang, "ask_pos"), kb([[btn(tr(lang, "e_top"), "e:pos:top"), btn(tr(lang, "e_center"), "e:pos:center"),
                                                     btn(tr(lang, "e_bottom"), "e:pos:bottom")]]))
            return
        elif aw == "emoji_new" and text:
            ems = split_emojis(text)
            if not ems or not (1 <= len(ems) <= 20):
                send(chat_id, tr(lang, "emoji_bad")); return
            set_await(uid, None)
            d = get_draft(uid)
            if data.get("draft"):
                if d: apply_emoji(chat_id, uid, lang, d.get("sid"), ems, draft=d)
                else: send(chat_id, tr(lang, "edit_none"), main_menu(lang))
            else:
                apply_emoji(chat_id, uid, lang, data["sid"], ems,
                            draft=d if d and d.get("sid") == data["sid"] else None)
            return
        elif aw in ("emoji_pick", "rm_pick") and sticker_of(msg):
            st = msg["sticker"]
            if not owns_sticker(uid, st):
                send(chat_id, tr(lang, "rm_not_yours"), main_menu(lang)); set_await(uid, None); return
            if aw == "rm_pick":
                set_await(uid, None); remove_sticker(chat_id, uid, lang, st)
            else:
                ask_emoji_for(chat_id, uid, lang, st["file_id"])
            return
        elif aw == "e_pos" and text:
            send(chat_id, tr(lang, "ask_pos")); return
        elif text and aw in ("emoji_pick", "rm_pick", "e_text", "emoji_new"):
            send(chat_id, tr(lang, "emoji_bad" if aw == "emoji_new" else "send_photo")); return

    file_id = None
    if msg.get("photo"):
        file_id = msg["photo"][-1]["file_id"]
    elif msg.get("document") and (msg["document"].get("mime_type") or "").startswith("image/"):
        file_id = msg["document"]["file_id"]
    elif sticker_of(msg):
        file_id = msg["sticker"]["file_id"]
    if file_id:
        set_await(uid, None)
        handle_image(msg, bot_username, file_id, lang)
    elif msg.get("document"):
        send(chat_id, tr(lang, "not_image"), main_menu(lang, admin))
    else:
        send(chat_id, tr(lang, "send_photo"), main_menu(lang, admin))

def u_awaiting(uid):
    return get_user(uid).get("awaiting")

def edit_callback(data, chat_id, mid, uid, lang, bot_username):
    op = data.split(":")[1] if ":" in data else ""
    arg = data.split(":")[2] if data.count(":") >= 2 else ""
    d = get_draft(uid)
    if not d:
        send(chat_id, tr(lang, "edit_none"), main_menu(lang)); return
    if op == "add" or op == "upd":
        clear_kb(chat_id, mid)
        draft_commit(chat_id, uid, lang, bot_username, update=(op == "upd")); return
    if op == "text":
        set_await(uid, "e_text"); send(chat_id, tr(lang, "ask_text")); return
    if op == "emo":
        ask_emoji_for(chat_id, uid, lang, d.get("sid"), draft=True); return
    if op == "pos":
        t = (get_user(uid).get("await_data") or {}).get("text")
        if arg not in ("top", "center", "bottom") or not t:
            return
        set_await(uid, None)
        draft_push(d, png_bytes(editor.add_text(img_of(d["cur"]), t, arg)))
    elif op == "rot":
        draft_push(d, png_bytes(editor.rotate90(img_of(d["cur"]))))
    elif op == "flip":
        draft_push(d, png_bytes(editor.flip_h(img_of(d["cur"]))))
    elif op == "undo":
        if not d["hist"]:
            send(chat_id, tr(lang, "undo_nothing")); return
        d["cur"] = d["hist"].pop()
    elif op == "reset":
        if d["cur"] != d["orig"]:
            draft_push(d, d["orig"])
        d["cur"] = d["orig"]
        send(chat_id, tr(lang, "reset_done"))
    else:
        return
    clear_kb(chat_id, mid)
    show_draft(chat_id, uid, lang)

def handle_callback(cq, bot_username):
    cid = cq["id"]
    try:
        call("answerCallbackQuery", {"callback_query_id": cid})
    except ApiError as e:
        log.warning("answerCallbackQuery: %s", safe(e))
    data = cq.get("data") or ""
    m = cq.get("message")
    if not m:
        return
    chat_id = m["chat"]["id"]
    mid = m.get("message_id")
    uid = cq["from"]["id"]
    logic.touch_user(cq["from"])
    if logic.bind_admin_if_needed(cq["from"]):
        send(chat_id, tr(user_lang(uid, cq["from"]), "a_bound", uid=uid))
    if data.startswith("l:"):
        code = data[2:]
        if code not in ("fa", "en"):
            return
        update_user(uid, lang=code)
        send(chat_id, tr(code, "lang_set"))
        send(chat_id, tr(code, "help"), main_menu(code, is_admin_uid(uid)))
        return
    lang = user_lang(uid, cq["from"])
    admin = is_admin_uid(uid)
    if data.startswith("a:"):
        if not admin:
            log.warning("non-admin %s tried admin callback", uid); return
        admin_callback(data, chat_id, mid, uid, lang); return
    if data.startswith("e:"):
        edit_callback(data, chat_id, mid, uid, lang, bot_username); return
    if data == "m:make":
        update_user(uid, awaiting=None)
        send(chat_id, tr(lang, "send_image"))
    elif data == "m:new":
        set_await(uid, "title")
        send(chat_id, tr(lang, "ask_title"))
    elif data == "m:link":
        show_pack_link(chat_id, uid, lang)
    elif data == "m:lang":
        send(chat_id, tr(lang, "pick_lang"), lang_menu())
    elif data == "m:help":
        send(chat_id, tr(lang, "help"), main_menu(lang, admin))
    elif data == "m:menu":
        set_await(uid, None)
        show_menu(chat_id, lang, uid)
    elif data == "m:plans":
        show_plans(chat_id, uid, lang)
    elif data == "m:me":
        show_me(chat_id, uid, lang)
    elif data == "m:invite":
        show_invite(chat_id, uid, lang)
    elif data == "m:edit":
        start_edit_last(chat_id, uid, lang)
    elif data == "m:tools":
        send(chat_id, tr(lang, "tools_title"), tools_menu(lang, uid))
    elif data == "m:emoji":
        set_await(uid, "emoji_pick"); send(chat_id, tr(lang, "ask_emoji_pick"))
    elif data == "m:rm":
        set_await(uid, "rm_pick"); send(chat_id, tr(lang, "rm_ask"))
    elif data == "m:dl":
        send(chat_id, tr(lang, "dellast_confirm"), kb([[btn(tr(lang, "yes"), "m:dly"), btn(tr(lang, "no"), "m:menu")]]))
    elif data == "m:dly":
        clear_kb(chat_id, mid); delete_last(chat_id, uid, lang)
    elif data == "m:prev":
        on = not get_user(uid).get("preview")
        update_user(uid, preview=True if on else None)
        s = tr(lang, "on" if on else "off")
        send(chat_id, tr(lang, "mode_changed", s=s), tools_menu(lang, uid))

def main():
    global BOT_USERNAME
    me = call("getMe")
    BOT_USERNAME = me["username"]
    bot_username = BOT_USERNAME
    log.info("Bot started: @%s", bot_username)
    try:
        wh = call("getWebhookInfo")
        if wh.get("url"):
            log.info("Webhook was set; deleting to use long polling")
            call("deleteWebhook")
    except ApiError as e:
        log.warning("webhook check: %s", safe(e))
    offset = None
    while True:
        try:
            params = {"timeout": 30, "allowed_updates": json.dumps(["message", "callback_query"])}
            if offset:
                params["offset"] = offset
            updates = call("getUpdates", params, timeout=45)
        except ApiError as e:
            log.warning("getUpdates: %s", safe(e)); time.sleep(5); continue
        for up in updates:
            offset = up["update_id"] + 1
            try:
                if up.get("message"):
                    handle_message(up["message"], bot_username)
                elif up.get("callback_query"):
                    handle_callback(up["callback_query"], bot_username)
            except Exception as e:
                log.exception("handler error: %s", safe(e))
                try:
                    chat = (up.get("message") or up.get("callback_query", {}).get("message") or {}).get("chat", {})
                    if chat.get("id"):
                        send(chat["id"], T["fa"]["unexpected"] + "\n" + T["en"]["unexpected"])
                except Exception:
                    pass

if __name__ == "__main__":
    main()
