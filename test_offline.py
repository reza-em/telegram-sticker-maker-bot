"""Offline tests with a mocked Telegram API. Never touches the network or real state.json."""
import os, sys, io, json, tempfile
os.environ["TELEGRAM_BOT_TOKEN"] = "TEST:TOKEN"
import store
tmp = tempfile.mkdtemp(); store.set_path(os.path.join(tmp, "state.json"))
from PIL import Image
import bot, logic, editor, texts

SENT = []          # (method, data)
PACKS = {}         # name -> list of stickers
class Fake:
    n = 0
def fake_call(method, data=None, files=None, timeout=60):
    data = dict(data or {}); SENT.append((method, data, files))
    if method == "getFile":
        return {"file_path": "x/y.png", "file_size": 100}
    if method in ("createNewStickerSet", "addStickerToSet"):
        name = data["name"]; Fake.n += 1
        emo = json.loads(data.get("sticker") or data["stickers"])
        emo = emo["emoji_list"] if isinstance(emo, dict) else emo[0]["emoji_list"]
        w = files["stk"][1]
        assert len(w) <= 512*1024
        im = Image.open(io.BytesIO(w)); assert max(im.size) == 512, im.size
        PACKS.setdefault(name, []).append({"file_id": f"F{Fake.n}", "emoji": emo[0], "set_name": name})
        return True
    if method == "getStickerSet":
        return {"stickers": PACKS.get(data["name"], [])}
    if method == "deleteStickerFromSet":
        for l in PACKS.values():
            l[:] = [s for s in l if s["file_id"] != data["sticker"]]
        return True
    if method == "setStickerEmojiList":
        for l in PACKS.values():
            for s in l:
                if s["file_id"] == data["sticker"]: s["emoji"] = json.loads(data["emoji_list"])[0]
        return True
    return {"message_id": 1}
bot.call = fake_call
bot.BOT_USERNAME = "miusticker_bot"
def fake_dl(fid):
    b = io.BytesIO(); Image.new("RGB", (800, 600), (200, 50, 50)).save(b, "JPEG"); return b.getvalue()
bot.download_file = fake_dl
bot.time.sleep = lambda s: None

def msgs(to=None, method="sendMessage"):
    return [d for m, d, f in SENT if m == method and (to is None or d.get("chat_id") == to)]
def last_text(to):
    m = [d.get("text") or d.get("caption") for mm, d, f in SENT if d.get("chat_id") == to and (d.get("text") or d.get("caption"))]
    return m[-1] if m else None
def tg(uid, username=None, name="U", lang="fa"):
    return {"id": uid, "first_name": name, "username": username, "language_code": lang}
def say(uid, text=None, username=None, **extra):
    m = {"chat": {"id": uid}, "from": tg(uid, username), "text": text}; m.update(extra)
    m = {k: v for k, v in m.items() if v is not None}
    bot.handle_message(m, "miusticker_bot")
def photo(uid, username=None):
    say(uid, None, username, photo=[{"file_id": "P1"}])
def press(uid, data, mid=5, username=None):
    bot.handle_callback({"id": "c", "from": tg(uid, username), "data": data,
                         "message": {"chat": {"id": uid}, "message_id": mid}}, "miusticker_bot")
def buttons(d):
    mk = json.loads(d["reply_markup"])["inline_keyboard"]; return [b["callback_data"] for r in mk for b in r if "callback_data" in b]

ADMIN, U1, U2, U3 = 111, 222, 333, 444
ok = lambda c, m: (print(("PASS " if c else "FAIL ") + m), c or sys.exit(1))

# --- admin binding
say(U1, "/admin", username="example_fake")
ok(logic.is_admin(U1) is False and store.admin_id() is None, "impostor username not bound")
say(ADMIN, "/start", username="Example_Owner")
ok(store.admin_id() == ADMIN, "admin auto-bound (case-insens.)")
say(999, "/start", username="example_owner")
ok(store.admin_id() == ADMIN, "second 'example_owner' can't rebind")
say(ADMIN, "/admin", username="renamed_now")
ok("a:stats" in buttons(msgs(ADMIN)[-1]), "admin works after rename (by numeric id)")
n = len(SENT); say(U1, "/admin"); press(U1, "a:stats"); press(U1, "a:ugf:222"); press(U1, "a:bcy")
ok(all(m != "editMessageText" for m, d, f in SENT[n:]) and not logic.access_info(U1)["kind"] == "unlimited", "non-admin blocked from /admin + callbacks")

# --- quota
bot.logic.set_setting("free_quota", 2)
say(U1, "/start", username="alice")
photo(U1); photo(U1)
ok(logic.access_info(U1)["free_left"] == 0 and len(PACKS) == 1, "2 free stickers consumed")
n = len(SENT); photo(U1)
ok("addStickerToSet" not in [m for m, _, _ in SENT[n:]] and "قیمت" not in "" and "🙈" in last_text(U1), "3rd blocked with upgrade msg")
ok("<code>222</code>" in msgs(U1)[-1]["text"], "quota msg shows user numeric id")
say(U1, "/me"); print(last_text(U1))

# --- grant unlimited
press(ADMIN, "a:grant"); say(ADMIN, "@alice"); press(ADMIN, "a:ugf:222")
ok(logic.access_info(U1)["kind"] == "unlimited", "grant unlimited by @username")
photo(U1); photo(U1)
ok(len(PACKS[list(PACKS)[0]]) == 4, "unlimited bypasses quota")
press(ADMIN, "a:urc:222"); ok(logic.access_info(U1)["kind"] == "quota", "revoke")
# credits
press(ADMIN, "a:credits"); say(ADMIN, "222"); say(ADMIN, "۳")
ok(logic.access_info(U1)["credits"] == 3, "add credits (Persian digits)")
photo(U1); ok(logic.access_info(U1)["credits"] == 2, "credit consumed")
say(ADMIN, "/admin"); press(ADMIN, "a:grant"); say(ADMIN, "nobody"); ok("پیدا نکردم" in last_text(ADMIN), "unknown user handled")
press(ADMIN, "a:set"); press(ADMIN, "a:sn:free_quota"); say(ADMIN, "abc"); say(ADMIN, "7")
ok(store.settings()["free_quota"] == 7, "admin edits free quota")

# --- quota exhausted -> contact-admin message (no payments)
bot.logic.set_setting("free_quota", 0)
say(U2, "/start", username="bob"); photo(U2)
out = msgs(U2)[-1]
ok("<code>333</code>" in out["text"] and out.get("parse_mode") == "HTML", "quota msg has numeric ID in code span")
mk = json.loads(out["reply_markup"])["inline_keyboard"]
ok(any(b.get("url") == "https://t.me/example_owner" for r in mk for b in r), "URL button to admin")
bot.update_user(U2, lang="en"); photo(U2); ok("numeric ID" in msgs(U2)[-1]["text"] and "<code>333</code>" in msgs(U2)[-1]["text"], "english variant")
bot.update_user(U2, lang="fa")
say(U2, "/me"); ok("<code>333</code>" in msgs(U2)[-1]["text"] and "example_owner" in msgs(U2)[-1]["reply_markup"], "/me shows ID + contact button")
say(U2, "/buy"); ok("دستور نامعتبر" in last_text(U2), "/buy no longer exists")
ok(not any(w in json.dumps(bot.T, ensure_ascii=False).lower() for w in ("receipt", "buy access", "card number", "payment")), "no payment strings left in texts")
n = len(SENT); press(U2, "m:buy"); ok(not any(m == "sendMessage" for m, _, _ in SENT[n:]), "old buy button is inert")
# admin grants manually -> user notified
n = len(SENT); press(ADMIN, "a:credits"); say(ADMIN, "333"); say(ADMIN, "2")
ok(logic.access_info(U2)["credits"] == 2 and "💎" in last_text(U2) or "اعتبار" in last_text(U2), "admin adds credits by id; user notified")
photo(U2); ok(logic.access_info(U2)["credits"] == 1, "credit spent after grant")
press(ADMIN, "a:grant"); say(ADMIN, "@bob"); press(ADMIN, "a:ugd:333"); say(ADMIN, "30")
ok(logic.access_info(U2)["kind"] == "until" and "30" in last_text(U2), "grant N days by @username; user notified")
press(ADMIN, "a:ugf:333"); ok(logic.access_info(U2)["kind"] == "unlimited" and "🥳" in last_text(U2), "grant unlimited; user notified")
press(ADMIN, "a:urc:333"); ok(logic.access_info(U2)["kind"] == "quota" and logic.access_info(U2)["credits"] == 0 and "ℹ️" in last_text(U2), "revoke; user notified")
ok("a:pq" not in buttons(msgs(ADMIN)[-1]) if msgs(ADMIN)[-1].get("reply_markup") else True, "no payments queue")
say(ADMIN, "/admin"); home = buttons(msgs(ADMIN)[-1]); ok(not any(b.startswith(("a:pq", "a:pl", "a:st:")) for b in home), "admin home has no payment buttons")
press(ADMIN, "a:set"); ok(not any(b.startswith(("a:pl", "a:st:")) for b in buttons(msgs(ADMIN, "editMessageText")[-1])), "settings: quota/referral only")
ok(not any(k in store.snapshot() for k in ("plans", "payments")), "state has no payment data")
bot.logic.set_setting("free_quota", 10)

# --- admin-editable price line
bot.logic.set_setting("free_quota", 0); bot.update_user(U2, lang="fa")
photo(U2); ok("💰" not in msgs(U2)[-1]["text"], "price unset -> nothing about price")
say(U2, "/me"); ok("💰 قیمت" not in msgs(U2)[-1]["text"] and "💰" not in msgs(U2)[-1]["text"].replace("💎", ""), "/me: no price when unset")
press(ADMIN, "a:set"); ok("a:price" in buttons(msgs(ADMIN, "editMessageText")[-1]), "settings has Set price button")
press(ADMIN, "a:price"); ok(set(["a:pr:both", "a:pr:fa", "a:pr:en", "a:pr:clear"]) <= set(buttons(msgs(ADMIN, "editMessageText")[-1])), "price menu buttons")
n = len(SENT); press(U2, "a:price"); press(U2, "a:pr:both"); ok(not any(m == "editMessageText" for m, _, _ in SENT[n:]) and store.settings()["price_fa"] == "", "non-admin can't touch price")
say(U2, "hello <b>"); ok(store.settings()["price_fa"] == "", "non-admin text doesn't set price")
press(ADMIN, "a:pr:both"); say(ADMIN, "۵۰ استیکر = X تومان & <b>test</b>")
ok(store.settings()["price_fa"].startswith("۵۰") and store.settings()["price_en"] == store.settings()["price_fa"], "price saved for both langs")
photo(U2); txt = msgs(U2)[-1]["text"]
ok("💰 ۵۰ استیکر" in txt and "&lt;b&gt;" in txt and txt.index("💰") < txt.index("<code>333</code>"), "quota msg: price (HTML-escaped) above ID")
say(U2, "/me"); txt = msgs(U2)[-1]["text"]
ok("💰 ۵۰ استیکر" in txt and txt.index("💰") < txt.index("<code>333</code>") and txt.count("۵۰ استیکر") == 1, "/me: price above ID, shown once")
press(ADMIN, "a:pr:en"); say(ADMIN, "50 stickers = Y"); bot.update_user(U2, lang="en"); photo(U2)
ok("💰 50 stickers = Y" in msgs(U2)[-1]["text"], "en-only override used for English")
bot.update_user(U2, lang="fa"); photo(U2); ok("۵۰ استیکر" in msgs(U2)[-1]["text"], "fa text unchanged")
mk = json.loads(msgs(U2)[-1]["reply_markup"])["inline_keyboard"]; ok(any("url" in b and "example_owner" in b["url"] for r in mk for b in r), "contact button still below")
press(ADMIN, "a:pr:clear"); ok(store.settings()["price_fa"] == "" and store.settings()["price_en"] == "", "clear price")
photo(U2); ok("💰" not in msgs(U2)[-1]["text"], "after clear: nothing about price")
bot.logic.set_setting("free_quota", 10)


# --- price plans manager (display only)
bot.logic.set_setting("free_quota", 0); bot.update_user(U2, lang="fa")
say(ADMIN, "/admin"); ok("a:pp" in buttons(msgs(ADMIN)[-1]), "admin home has top-level Prices button")
ok([b["text"] for r in json.loads(msgs(ADMIN)[-1]["reply_markup"])["inline_keyboard"] for b in r if b.get("callback_data") == "a:pp"] == ["💰 قیمت‌ها / Prices"], "Prices button label bilingual")
say(U2, "/admin"); ok("a:pp" not in json.dumps(msgs(U2)[-1].get("reply_markup", "")), "non-admin gets no Prices button")
n = len(SENT); press(U2, "a:pp"); press(U2, "a:ppa"); press(U2, "a:ppcy"); press(U2, "a:ppt:1"); ok(not any(m == "editMessageText" for m, _, _ in SENT[n:]) and len(logic.pplans()) == 3, "non-admin can't manage plans")
ok([p["title"] for p in logic.pplans()] == ["پایه / Basic", "اقتصادی / Economy", "پیشرفته / Advanced"], "(d) 3 default names in order")
ok(all(not p["price"] and not p["count"] and not p["popular"] for p in logic.pplans()), "(d) placeholders have no invented price/count")
photo(U2); ok("پایه" not in msgs(U2)[-1]["text"] and "💰" not in msgs(U2)[-1]["text"], "(d) unfilled placeholders hidden in quota msg")
say(U2, "/plans"); t0 = msgs(U2)[-1]["text"]; ok("پایه" not in t0 and "🆓" in t0 and "<code>333</code>" in t0, "(c) Plans screen: placeholders hidden, free tier + ID shown")
# (c) main menu button
say(U2, "/help"); mm = [b for r in json.loads(msgs(U2)[-1]["reply_markup"])["inline_keyboard"] for b in r]
ok(any(b.get("callback_data") == "m:plans" and b["text"] == "💎 پلن‌ها / Plans" for b in mm), "(c) main menu has 💎 Plans button")
press(U2, "m:plans"); ok("🆓" in msgs(U2)[-1]["text"] and any("url" in b for r in json.loads(msgs(U2)[-1]["reply_markup"])["inline_keyboard"] for b in r), "(c) button opens plans w/ contact button")
# (a) count required
ok(logic.add_pplan("bad", 0, "x") is None and logic.add_pplan("bad", None, "x") is None, "(a) count required at logic level")
press(ADMIN, "a:pp"); ok("a:ppa" in buttons(msgs(ADMIN, "editMessageText")[-1]), "manager lists plans with Add")
press(ADMIN, "a:ppe:1"); press(ADMIN, "a:ppep:1"); say(ADMIN, "۱۰۰ تومان <x>")
ok(logic.get_pplan(1)["price"].startswith("۱۰۰"), "(a) edit price")
photo(U2); ok("پایه" not in msgs(U2)[-1]["text"], "(d) price but no count -> still hidden")
press(ADMIN, "a:ppen:1"); say(ADMIN, "abc"); say(ADMIN, "0"); ok(logic.get_pplan(1)["count"] is None, "(a) invalid/zero count rejected on edit")
say(ADMIN, "۲۰"); ok(logic.get_pplan(1)["count"] == 20, "(a) edit count (Persian digits)")
press(ADMIN, "a:ppet:1"); say(ADMIN, "پایه / Basic ⭐"); ok(logic.get_pplan(1)["title"] == "پایه / Basic ⭐", "(a) edit name")
press(ADMIN, "a:pped:1"); say(ADMIN, "ماهانه"); press(ADMIN, "a:ppef:1"); say(ADMIN, "بدون واترمارک | پشتیبانی ; <b>سریع</b>")
press(ADMIN, "a:ppt:1"); ok(logic.get_pplan(1)["popular"] is True and logic.get_pplan(1)["duration"] == "ماهانه", "(b) duration + popular toggle")
# wizard: add full plan
press(ADMIN, "a:ppa"); say(ADMIN, "Pro"); say(ADMIN, "nope"); say(ADMIN, "100"); say(ADMIN, "9 USD"); say(ADMIN, "per month"); say(ADMIN, "No watermark | Priority"); press(ADMIN, "a:ppp:1")
p4 = logic.pplans()[-1]; ok(p4["title"] == "Pro" and p4["count"] == 100 and p4["price"] == "9 USD" and p4["duration"] == "per month" and p4["features"].startswith("No watermark") and p4["popular"], "(a)(b) wizard: title->count(required)->price->duration->features->popular")
press(ADMIN, "a:ppa"); say(ADMIN, "Mini"); say(ADMIN, "5"); say(ADMIN, "1 USD"); press(ADMIN, "a:pps:dur"); press(ADMIN, "a:pps:feat"); press(ADMIN, "a:ppp:0")
p5 = logic.pplans()[-1]; ok(p5["title"] == "Mini" and p5["count"] == 5 and p5["duration"] == "" and p5["features"] == "" and not p5["popular"], "(b) duration/features optional (skip), popular off")
# user-facing cards
say(U2, "/plans"); txt = msgs(U2)[-1]["text"]
ok("⭐ محبوب‌ترین" in txt and "پایه / Basic ⭐" in txt and "🎟 20 استیکر" in txt and "⏳ ماهانه" in txt and "✔️ بدون واترمارک" in txt and "✔️ پشتیبانی" in txt, "(b) fa card: popular badge, count, duration, feature bullets")
ok("&lt;x&gt;" in txt and "&lt;b&gt;" in txt, "HTML escaped in cards")
ok("Pro" in txt and "🎟 100" in txt and "Mini" in txt and "اقتصادی" not in txt and "پیشرفته" not in txt, "ready plans shown, placeholders hidden")
ok(txt.index("🆓") < txt.index("پایه / Basic ⭐") < txt.index("Pro") < txt.index("Mini") < txt.index("<code>333</code>"), "order: free, plans, then ID footer")
ok(txt.count("⭐ محبوب‌ترین") == 2, "popular badge only on popular plans (2 of 4)")
bot.update_user(U2, lang="en"); say(U2, "/plans"); d = msgs(U2)[-1]
ok("⭐ Popular" in d["text"] and "🎟 100 stickers" in d["text"] and "⏳ per month" in d["text"] and "✔️ Priority" in d["text"] and "Free" in d["text"], "(b)(c) english cards")
ok([b["text"] for r in json.loads(d["reply_markup"])["inline_keyboard"] for b in r if "url" in b and "t.me/example_owner" in b["url"]] == ["💬 Contact admin"], "contact button under cards")
photo(U2); ok("💰 Plans:" in msgs(U2)[-1]["text"] and "100 stickers" in msgs(U2)[-1]["text"], "quota msg compact plan list (en)")
bot.update_user(U2, lang="fa"); photo(U2); txt = msgs(U2)[-1]["text"]; ok("💰 پلن‌ها" in txt and txt.index("💰 پلن‌ها") < txt.index("<code>333</code>"), "quota msg plans above ID")
say(U2, "/me"); txt = msgs(U2)[-1]["text"]; ok("💰 پلن‌ها" in txt and txt.index("💰 پلن‌ها") < txt.index("<code>333</code>"), "/me plans above ID")
# reorder
ids = [p["id"] for p in logic.pplans()]
press(ADMIN, f"a:ppd:{ids[0]}"); ok([p["id"] for p in logic.pplans()][:2] == [ids[1], ids[0]], "(a) move down")
press(ADMIN, f"a:ppu:{ids[0]}"); ok([p["id"] for p in logic.pplans()][:2] == [ids[0], ids[1]], "(a) move up")
ok(logic.move_pplan(ids[0], -1) is False and logic.move_pplan(ids[-1], 1) is False, "reorder bounds safe")
press(ADMIN, "a:pp"); mk_ = buttons(msgs(ADMIN, "editMessageText")[-1]); ok(f"a:ppd:{ids[0]}" in mk_ and f"a:ppu:{ids[-1]}" in mk_ and f"a:ppx:{ids[0]}" in mk_, "manager shows reorder + delete buttons")
# (e) grant shortcut adds exactly count
press(ADMIN, "a:u:333"); qb = buttons(msgs(ADMIN, "editMessageText")[-1]); ok("a:ugp:333:1" in qb and f"a:ugp:333:{p4['id']}" in qb and "a:ugp:333:2" not in qb, "user card: quick buttons only for plans with a count")
press(ADMIN, "a:grant"); say(ADMIN, "333"); ok("a:ugp:333:1" in buttons(msgs(ADMIN)[-1]), "Grant access by id offers plan buttons")
c0 = logic.access_info(U2)["credits"]; press(ADMIN, "a:ugp:333:1"); ok(logic.access_info(U2)["credits"] == c0 + 20, "(e) quick plan adds exactly count (20) as credits")
ok(logic.access_info(U2)["kind"] == "quota", "(e) grant is credits only, no unlimited")
press(ADMIN, f"a:ugp:333:{p4['id']}"); ok(logic.access_info(U2)["credits"] == c0 + 120, "(e) second plan adds exactly 100")
ok("100" in last_text(U2), "user notified of credits")
n = len(SENT); press(ADMIN, "a:ugp:333:2"); ok(logic.access_info(U2)["credits"] == c0 + 120, "placeholder plan (no count) grants nothing")
press(ADMIN, f"a:ppx:{p5['id']}"); ok(logic.get_pplan(p5["id"]) is None, "(a) delete plan")
# migration from old plan shape
with store.transaction() as s_: s_["price_plans"] = [{"id": 1, "title": "old", "price": "p", "kind": "credits", "amount": 3}, {"id": 2, "title": "olddays", "price": "p", "kind": "days", "amount": 30}]
op_ = logic.pplans(); ok(op_[0]["count"] == 3 and "kind" not in op_[0] and op_[1]["count"] is None and not logic.plan_ready(op_[1]), "migration: credits->count, days plans hidden")
press(ADMIN, "a:ppcy"); ok(logic.pplans() == [], "clear all")
bot.update_user(U2, lang="fa"); photo(U2); ok("💰" not in msgs(U2)[-1]["text"], "no plans -> nothing about price")
say(U2, "/plans"); ok("🌷" in msgs(U2)[-1]["text"], "no ready plans -> friendly 'coming soon'")
press(ADMIN, "a:pp"); press(ADMIN, "a:price"); ok("a:pr:both" in buttons(msgs(ADMIN, "editMessageText")[-1]), "simple price line still reachable")
bot.logic.clear_pplans(); bot.logic.set_setting("free_quota", 10)



# --- multiple admins / support contacts / owner change
EX, NEWOWN = 888, 999
say(EX, "/start", username="helper"); say(NEWOWN, "/start", username="newowner")
say(ADMIN, "/admin"); ok({"a:ad", "a:su"} <= set(buttons(msgs(ADMIN)[-1])), "owner home has Admins + Support buttons")
press(ADMIN, "a:ad"); press(ADMIN, "a:ada"); say(ADMIN, "nobody"); ok("پیدا نکردم" in last_text(ADMIN), "add admin: unknown user")
say(ADMIN, "@helper"); ok(logic.is_extra_admin(EX) and not logic.is_owner(EX), "extra admin added by @username")
ok("👮" in last_text(EX), "new admin notified")
press(ADMIN, "a:ada"); say(ADMIN, "111"); ok(logic.list_admins() == [EX], "can't add owner as extra admin")
say(EX, "/admin"); hb = buttons(msgs(EX)[-1]); ok({"a:stats", "a:ul:1", "a:grant", "a:revoke", "a:credits"} <= set(hb) and not {"a:bc", "a:ad", "a:su", "a:set", "a:pp", "a:ref"} & set(hb), "extra admin panel limited")
n = len(SENT)
for op in ("a:ad", "a:ada", "a:adx:888", "a:own", "a:ownc:888", "a:su", "a:sup:primary", "a:set", "a:pp", "a:ppcy", "a:bc", "a:bcy", "a:price", "a:sn:free_quota", "a:rt"):
    press(EX, op)
ok(store.settings()["free_quota"] == 10 and logic.list_admins() == [EX] and store.admin_id() == ADMIN and [p["title"] for p in logic.pplans()][:1] == ["پایه / Basic ⭐"] or logic.pplans() == [], "owner-only callbacks ignored for extra admin (state intact)")
ok(store.admin_id() == ADMIN and logic.is_extra_admin(EX) and store.support()["primary"] == "example_owner", "extra admin cannot change owner/admins/support")
ok(not any(m == "editMessageText" for m, _, _ in SENT[n:]), "no panel screens opened for owner-only ops")
say(EX, "x"); press(EX, "a:bc"); say(EX, "HACKED-BROADCAST"); ok(store.snapshot()["pending_broadcast"] is None, "extra admin can't broadcast")
press(EX, "a:grant"); say(EX, "222"); press(EX, "a:ugf:222"); ok(logic.access_info(U1)["kind"] == "unlimited", "extra admin can grant")
press(EX, "a:urc:222"); ok(logic.access_info(U1)["kind"] == "quota", "extra admin can revoke")
press(EX, "a:credits"); say(EX, "222"); say(EX, "4"); ok(logic.access_info(U1)["credits"] >= 4, "extra admin can add credits")
press(EX, "a:stats"); ok(msgs(EX, "editMessageText")[-1]["text"].startswith("📊"), "extra admin sees stats")
ok(logic.access_info(EX)["kind"] == "admin", "extra admin unlimited")
press(ADMIN, "a:adx:888"); ok(not logic.is_extra_admin(EX), "owner removes extra admin"); ok("ℹ️" in last_text(EX), "removed admin notified")
n = len(SENT); say(EX, "/admin"); press(EX, "a:stats"); ok(not any(m == "editMessageText" for m, _, _ in SENT[n:]), "removed admin loses access")

# support contacts
def url_btns(d): return [b for r in json.loads(d["reply_markup"])["inline_keyboard"] for b in r if "url" in b and "t.me/" in b["url"] and "share" not in b["url"]]
bot.logic.revoke(U2); bot.logic.set_setting("free_quota", 0); bot.update_user(U2, lang="fa", free_used=0); photo(U2)
ub = url_btns(msgs(U2)[-1]); ok([b["url"] for b in ub] == ["https://t.me/example_owner"], "default: single contact button to example_owner")
ok("پشتیبان دوم" not in msgs(U2)[-1]["text"], "no backup note by default")
press(ADMIN, "a:su"); press(ADMIN, "a:sup:primary"); say(ADMIN, "bad name!"); ok("نامعتبر" in last_text(ADMIN), "invalid username rejected")
say(ADMIN, "@MainSupport"); ok(store.support()["primary"] == "MainSupport", "primary support edited")
photo(U2); ok([b["url"] for b in url_btns(msgs(U2)[-1])] == ["https://t.me/MainSupport"], "button uses new primary")
press(ADMIN, "a:sup:backup"); say(ADMIN, "https://t.me/Backup_Helper")
ok(store.support()["backup"] == "Backup_Helper", "backup support set (t.me url accepted)")
photo(U2); d = msgs(U2)[-1]; ub = url_btns(d)
ok([b["url"] for b in ub] == ["https://t.me/MainSupport", "https://t.me/Backup_Helper"] and [b["text"] for b in ub] == ["💬 پشتیبان ۱", "💬 پشتیبان ۲"], "two support buttons (fa)")
ok("اگر پشتیبان اول در دسترس نبود" in d["text"], "fallback note (fa)")
bot.update_user(U2, lang="en"); photo(U2); d = msgs(U2)[-1]
ok([b["text"] for b in url_btns(d)] == ["💬 Support 1", "💬 Support 2"] and "isn't available" in d["text"], "two support buttons + note (en)")
say(U2, "/me"); ok(len(url_btns(msgs(U2)[-1])) == 2, "/me shows both support buttons")
say(U2, "/id"); ok(len(url_btns(msgs(U2)[-1])) == 2, "/id shows both")
say(U2, "/start"); say(U2, "/help"); ok(len(url_btns(msgs(U2)[-1])) == 2, "main menu shows both")
bot.update_user(U2, lang="fa")
press(ADMIN, "a:suc"); ok(store.support()["backup"] == "" and len(url_btns(msgs(U2, "sendMessage")[-1])) >= 1, "backup removed")
photo(U2); ok(len(url_btns(msgs(U2)[-1])) == 1 and "پشتیبان دوم" not in msgs(U2)[-1]["text"], "back to single button")
bot.logic.set_support("primary", "example_owner"); bot.logic.set_setting("free_quota", 10)

# change owner (with confirm)
press(ADMIN, "a:own"); say(ADMIN, "@newowner")
ok("a:ownc:999" in buttons(msgs(ADMIN)[-1]) and store.admin_id() == ADMIN, "change owner asks for confirmation; not applied yet")
press(ADMIN, "a:ad"); ok(store.admin_id() == ADMIN, "cancel path keeps owner")
press(EX, "a:ownc:999"); ok(store.admin_id() == ADMIN, "non-owner can't confirm owner change")
press(ADMIN, "a:ownc:999"); ok(store.admin_id() == NEWOWN and logic.is_owner(NEWOWN) and not logic.is_admin(ADMIN), "owner transferred; old owner demoted")
ok("👑" in last_text(NEWOWN), "new owner notified")
n = len(SENT); press(ADMIN, "a:stats"); say(ADMIN, "/admin"); ok(not any(m == "editMessageText" for m, _, _ in SENT[n:]), "old owner has no panel access")
say(ADMIN, "/start", username="example_owner"); ok(store.admin_id() == NEWOWN, "@example_owner username no longer auto-binds once owner set")
say(NEWOWN, "/admin"); ok("a:ad" in buttons(msgs(NEWOWN)[-1]), "new owner has full panel")
press(NEWOWN, "a:ownc:999"); ok(store.admin_id() == NEWOWN, "transfer to self is a no-op")
# restore for the remaining tests
with store.transaction() as s_: s_["admin_id"] = ADMIN

# --- referral
bot.logic.set_setting("free_quota", 10)
get_credits = lambda u: logic.access_info(u)["credits"]; C0 = get_credits(U1)
say(U3, "/start ref_222", username="carol")
ok(logic.access_info(U3)["credits"] == 2 and get_credits(U1) == C0 + 5, "referral: +5 referrer (2+5), +2 invitee")
ok("🎁" in last_text(U1), "referrer notified")
say(U3, "/start ref_222"); ok(get_credits(U1) == C0 + 5, "no double count")
say(555, "/start ref_555"); ok(logic.access_info(555)["credits"] == 0, "self-referral ignored")
c2 = get_credits(U2); say(U1, "/start ref_333"); ok(get_credits(U2) == c2, "existing user cannot be referred")
say(666, "hi"); say(666, "/start ref_222"); ok(get_credits(U1) == C0 + 5, "user seen before doesn't count")
press(ADMIN, "a:rt"); say(777, "/start ref_222"); ok(get_credits(U1) == C0 + 5, "toggle off disables referrals")
press(ADMIN, "a:rt"); say(U1, "/invite"); ok("ref_222" in last_text(U1) and "miusticker_bot" in last_text(U1), "invite link shown")
from urllib.parse import urlparse, parse_qs
link = "https://t.me/miusticker_bot?start=ref_222"
for l, start in (("fa", "🌸 سلام! یه ربات خیلی بامزه"), ("en", "🌸 Hey! I found the cutest bot")):
    bot.update_user(U1, lang=l); n = len(SENT); say(U1, "/invite"); new = [d for m, d, f in SENT[n:] if m == "sendMessage"]
    fwd = new[-1]; ok(fwd["text"].startswith(start) and fwd["text"].endswith("\n" + link), f"[{l}] forwardable invite text ends with link")
    if l == "fa": ok(fwd["text"] == "🌸 سلام! یه ربات خیلی بامزه پیدا کردم که هر عکسی رو توی چند ثانیه به استیکر تلگرام تبدیل می‌کنه ✨\n🖼 حجم عکس رو خودش کم می‌کنه، می‌تونی روش متن فارسی بنویسی، ایموجی بذاری و مستقیم توی پک استیکر خودت ذخیره‌ش کنی 💖\n🎁 با این لینک وارد شو تا استیکر رایگان هدیه بگیری:\n" + link, "fa text exact")
    if l == "en": ok(fwd["text"] == "🌸 Hey! I found the cutest bot that turns any photo into a Telegram sticker in seconds ✨\n🖼 It shrinks the file size for you, lets you add text and emojis, and saves it straight into your own sticker pack 💖\n🎁 Join with my link and get free stickers as a gift:\n" + link, "en text exact")
    sb = [b for r in json.loads(fwd["reply_markup"])["inline_keyboard"] for b in r if "url" in b][0]
    ok("📤" in sb["text"], f"[{l}] Share button")
    q = urlparse(sb["url"]); qs = parse_qs(q.query)
    ok(q.netloc == "t.me" and q.path == "/share/url" and qs["url"] == [link] and qs["text"] == [fwd["text"][:-len(link)-1]], f"[{l}] share URL decodes to link + text")
    ok("ref_222" in new[0]["text"], f"[{l}] info message has stats+link")
bot.update_user(U1, lang="fa")
press(ADMIN, "a:ref"); print(msgs(ADMIN)[-1].get("text"))
say(U1, "/me"); print(last_text(U1))

# --- editing
pack = store.get_user(U1)["pack"]
n0 = len(PACKS[pack])
say(U1, "/edit"); ok(any(m == "sendSticker" for m, _, _ in SENT[-3:]), "edit opens preview sticker")
press(U1, "e:rot"); press(U1, "e:flip")
say(U1, "x"); press(U1, "e:text"); say(U1, "سلام دنیا"); press(U1, "e:pos:bottom")
d = bot.get_draft(U1); im = Image.open(io.BytesIO(d["cur"])); im.convert("RGB").save("/tmp/draft_text.png")
ok(len(d["hist"]) == 3, "history has rot/flip/text")
press(U1, "e:undo"); ok(len(d["hist"]) == 2, "undo"); press(U1, "e:reset"); ok(d["cur"] == d["orig"], "reset to original")
press(U1, "e:rot"); press(U1, "e:emo"); say(U1, "🔥🎉")
ok(PACKS[pack][-1]["emoji"] == "🔥", "emoji of just-made sticker changed (setStickerEmojiList)")
press(U1, "e:upd"); ok(len(PACKS[pack]) == n0, "update replaces sticker (add+delete)")
press(U1, "m:dly"); ok(len(PACKS[pack]) == n0 - 1, "delete last sticker")
st = PACKS[pack][-1]
say(U1, "/delsticker", reply_to_message={"sticker": st}); ok(len(PACKS[pack]) == n0 - 2, "remove by reply")
st = PACKS[pack][-1]
say(U1, "❤️", reply_to_message={"sticker": st}); ok(PACKS[pack][-1]["emoji"] == "❤️", "emoji change by replying with emoji")
press(U1, "m:rm"); say(U1, None, sticker={"file_id": "ZZ", "set_name": "pack_999_1_by_miusticker_bot"})
ok("🙅" in last_text(U1), "can't remove someone else's sticker")
press(U1, "m:rm"); st = PACKS[pack][-1]; say(U1, None, sticker=st); ok(len(PACKS[pack]) == n0 - 3, "remove by sending sticker")
# preview mode
press(U1, "m:prev"); n = len(SENT); photo(U1)
ok(not any(m == "addStickerToSet" for m, _, _ in SENT[n:]) and any(m == "sendSticker" for m, _, _ in SENT[n:]), "preview mode: not added until confirm")
press(U1, "e:add"); ok(len(PACKS[pack]) == n0 - 2, "add from preview")

# --- broadcast
n = len(SENT); press(ADMIN, "a:bc"); say(ADMIN, "BROADCAST-HELLO")
ok("BROADCAST-HELLO" not in [d.get("text") for d in msgs(U1)], "not sent before confirm")
press(ADMIN, "a:bcy")
import time as _t; _t.sleep(1.5)
ok(any(d.get("text") == "BROADCAST-HELLO" for d in msgs(U1)), "broadcast sent after confirm")

# --- stats/users
press(ADMIN, "a:stats"); print(msgs(ADMIN, "editMessageText")[-1]["text"])
press(ADMIN, "a:ul:1"); press(ADMIN, "a:u:222"); print(msgs(ADMIN, "editMessageText")[-1]["text"])
# --- i18n parity + english
for l in ("fa", "en"):
    bot.update_user(U1, lang=l)
    say(U1, "/me"); press(U1, "m:tools")
ok(set(bot.T["fa"]) == set(bot.T["en"]), "fa/en key parity")
# --- corrupt state resilience
open(store.PATH, "w").write("{broken")
say(U1, "/me"); ok(True, "corrupt state file doesn't crash")
print("ALL PASSED")
