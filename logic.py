"""Business logic on top of store.py (no Telegram calls here)."""
import os
import time, copy
import store
from store import transaction

ADMIN_USERNAME = os.environ.get("OWNER_USERNAME", "example_owner").lower()
ACTIVE_WINDOW = 7 * 86400

def now():
    return int(time.time())

def fmt_date(ts):
    return time.strftime("%Y-%m-%d %H:%M", time.localtime(ts))

# ---------- users / admin ----------
def touch_user(tg_user):
    """Register/refresh a Telegram user. Returns (is_new, user_copy). is_new = never seen before."""
    uid = str(tg_user["id"])
    with transaction() as st:
        is_new = uid not in st["users"]
        u = st["users"].setdefault(uid, {})
        u.setdefault("first_seen", now())
        u["last_seen"] = now()
        u["name"] = " ".join(x for x in [tg_user.get("first_name"), tg_user.get("last_name")] if x)[:64]
        un = (tg_user.get("username") or "").lower()
        if un:
            u["username"] = un
        else:
            u.pop("username", None)
        if tg_user.get("language_code"):
            u["language_code"] = tg_user["language_code"]
        return is_new, copy.deepcopy(u)

def bind_admin_if_needed(tg_user):
    """Auto-bind the admin numeric id the first time @example_owner writes. True if newly bound."""
    if (tg_user.get("username") or "").lower() != ADMIN_USERNAME:
        return False
    with transaction() as st:
        if st.get("admin_id"):
            return False
        st["admin_id"] = int(tg_user["id"])
        return True

def is_owner(uid):
    a = store.admin_id()
    return a is not None and int(uid) == int(a)

def is_extra_admin(uid):
    with transaction(write=False) as st:
        return int(uid) in [int(x) for x in st.get("admins", [])]

def is_admin(uid):
    """Owner or extra admin (panel access; extras are limited in bot.py)."""
    return is_owner(uid) or is_extra_admin(uid)

def list_admins():
    with transaction(write=False) as st:
        return [int(x) for x in st.get("admins", [])]

def add_admin(uid):
    with transaction() as st:
        if st.get("admin_id") is not None and int(uid) == int(st["admin_id"]):
            return False
        if int(uid) not in [int(x) for x in st["admins"]]:
            st["admins"].append(int(uid))
        return True

def remove_admin(uid):
    with transaction() as st:
        n = len(st["admins"])
        st["admins"] = [int(x) for x in st["admins"] if int(x) != int(uid)]
        return len(st["admins"]) != n

def set_owner(new_uid):
    """Move ownership to new_uid. Old owner becomes a normal user (loses all admin rights)."""
    with transaction() as st:
        st["admin_id"] = int(new_uid)
        st["admins"] = [int(x) for x in st["admins"] if int(x) != int(new_uid)]

USERNAME_RE = __import__("re").compile(r"^[A-Za-z][A-Za-z0-9_]{4,31}$")

def clean_username(s):
    s = (s or "").strip()
    for pre in ("https://t.me/", "http://t.me/", "t.me/", "@"):
        if s.lower().startswith(pre):
            s = s[len(pre):]
    return s if USERNAME_RE.match(s) else None

def set_support(slot, name):
    """slot: 'primary' | 'backup'. name '' clears backup (primary can't be empty)."""
    with transaction() as st:
        if slot == "primary" and not name:
            return False
        st["support"][slot] = name or ""
        return True

def find_user(q):
    q = (q or "").strip()
    st = store.snapshot()
    if q.lstrip("-").isdigit():
        return int(q) if q in st["users"] else None
    q = q.lstrip("@").lower()
    if not q:
        return None
    for uid, u in st["users"].items():
        if u.get("username") == q:
            return int(uid)
    return None

# ---------- access / quota ----------
def _access(st, uid):
    u = st["users"].get(str(uid), {})
    quota = int(st["settings"]["free_quota"])
    free_left = max(0, quota - int(u.get("free_used", 0)))
    credits = int(u.get("credits", 0))
    until = int(u.get("unl_until", 0) or 0)
    a = st.get("admin_id")
    if (a is not None and int(uid) == int(a)) or int(uid) in [int(x) for x in st.get("admins", [])]:
        kind = "admin"
    elif u.get("unlimited"):
        kind = "unlimited"
    elif until > now():
        kind = "until"
    else:
        kind = "quota"
    return {"kind": kind, "free_left": free_left, "free_total": quota, "credits": credits,
            "until": until, "can": kind != "quota" or free_left > 0 or credits > 0}

def access_info(uid):
    with transaction(write=False) as st:
        return _access(st, uid)

def consume(uid):
    """Record one sticker made, spending quota if needed. Returns False if not allowed."""
    with transaction() as st:
        acc = _access(st, uid)
        if not acc["can"]:
            return False
        u = st["users"].setdefault(str(uid), {})
        u["made"] = int(u.get("made", 0)) + 1
        if acc["kind"] == "quota":
            if acc["free_left"] > 0:
                u["free_used"] = int(u.get("free_used", 0)) + 1
            else:
                u["credits"] = int(u.get("credits", 0)) - 1
        return True

def add_credits(uid, n):
    with transaction() as st:
        u = st["users"].setdefault(str(uid), {})
        u["credits"] = max(0, int(u.get("credits", 0)) + int(n))
        return u["credits"]

def grant_unlimited(uid):
    with transaction() as st:
        st["users"].setdefault(str(uid), {})["unlimited"] = True

def add_days(uid, days):
    with transaction() as st:
        u = st["users"].setdefault(str(uid), {})
        base = max(now(), int(u.get("unl_until", 0) or 0))
        u["unl_until"] = base + int(days) * 86400
        return u["unl_until"]

def revoke(uid):
    with transaction() as st:
        u = st["users"].setdefault(str(uid), {})
        u.pop("unlimited", None); u.pop("unl_until", None); u["credits"] = 0

# ---------- referrals ----------
def try_referral(new_uid, referrer_uid):
    """Call only when `new_uid` was just created (never seen before). Returns bonus dict or None."""
    with transaction() as st:
        s = st["settings"]
        if not s.get("ref_enabled"):
            return None
        if int(new_uid) == int(referrer_uid):
            return None
        ref = st["users"].get(str(referrer_uid))
        me = st["users"].get(str(new_uid))
        if ref is None or me is None or me.get("referred_by") or me.get("made") or me.get("pack"):
            return None
        rb, ib = int(s["ref_bonus"]), int(s["ref_invitee_bonus"])
        me["referred_by"] = int(referrer_uid)
        me["credits"] = int(me.get("credits", 0)) + ib
        ref["credits"] = int(ref.get("credits", 0)) + rb
        ref["ref_count"] = int(ref.get("ref_count", 0)) + 1
        ref["ref_earned"] = int(ref.get("ref_earned", 0)) + rb
        return {"bonus": rb, "invitee_bonus": ib}

# ---------- settings ----------
NUM_SETTINGS = ("free_quota", "ref_bonus", "ref_invitee_bonus")
PRICE_KEYS = ("price_fa", "price_en")

def set_setting(key, value):
    with transaction() as st:
        if key in NUM_SETTINGS:
            st["settings"][key] = int(value)
        elif key in PRICE_KEYS:
            st["settings"][key] = str(value).strip()[:500]
        elif key == "ref_enabled":
            st["settings"][key] = bool(value)
        else:
            raise KeyError(key)

def toggle_ref():
    with transaction() as st:
        st["settings"]["ref_enabled"] = not st["settings"]["ref_enabled"]
        return st["settings"]["ref_enabled"]

# ---------- display-only price plans (tier cards) ----------
MAX_PPLANS = 10

def pplans():
    return store.snapshot()["price_plans"]

def get_pplan(pid):
    return next((p for p in pplans() if p["id"] == int(pid)), None)

def plan_ready(p):
    """Shown to users only when a sticker count AND a price text are set."""
    return bool(p.get("count")) and int(p["count"]) > 0 and bool((p.get("price") or "").strip())

def add_pplan(title, count, price, duration="", features="", popular=False):
    """count is REQUIRED (>0). Returns plan id, or None if invalid/full."""
    if not count or int(count) <= 0:
        return None
    with transaction() as st:
        if len(st["price_plans"]) >= MAX_PPLANS:
            return None
        pid = st["next_pplan_id"]; st["next_pplan_id"] = pid + 1
        p = store.new_plan(pid, str(title).strip()[:60])
        p.update(count=int(count), price=str(price).strip()[:120], duration=str(duration).strip()[:40],
                 features=str(features).strip()[:200], popular=bool(popular))
        st["price_plans"].append(p)
        return pid

def update_pplan(pid, **kw):
    """Fields: title, count (must be >0), price, duration, features, popular. Returns False if plan missing/invalid."""
    with transaction() as st:
        p = next((p for p in st["price_plans"] if p["id"] == int(pid)), None)
        if not p:
            return False
        if "count" in kw and (not kw["count"] or int(kw["count"]) <= 0):
            return False
        lim = {"title": 60, "price": 120, "duration": 40, "features": 200}
        for k, v in kw.items():
            if k in lim: p[k] = str(v).strip()[:lim[k]]
            elif k == "count": p["count"] = int(v)
            elif k == "popular": p["popular"] = bool(v)
        return True

def toggle_popular(pid):
    with transaction() as st:
        p = next((p for p in st["price_plans"] if p["id"] == int(pid)), None)
        if not p: return None
        p["popular"] = not p.get("popular")
        return p["popular"]

def move_pplan(pid, delta):
    with transaction() as st:
        pl = st["price_plans"]
        i = next((i for i, p in enumerate(pl) if p["id"] == int(pid)), None)
        j = None if i is None else i + int(delta)
        if i is None or j < 0 or j >= len(pl):
            return False
        pl[i], pl[j] = pl[j], pl[i]
        return True

def remove_pplan(pid):
    with transaction() as st:
        n = len(st["price_plans"])
        st["price_plans"] = [p for p in st["price_plans"] if p["id"] != int(pid)]
        return len(st["price_plans"]) != n

def clear_pplans():
    with transaction() as st:
        st["price_plans"] = []

def grant_plan(uid, pid):
    """Shortcut: add exactly the plan's sticker count as credits. Returns count or None."""
    p = get_pplan(pid)
    if not p or not p.get("count") or int(p["count"]) <= 0:
        return None
    add_credits(uid, int(p["count"]))
    return int(p["count"])

# ---------- stats ----------
def stats():
    st = store.snapshot()
    t = now()
    users = st["users"]
    return {
        "users": len(users),
        "active": sum(1 for u in users.values() if t - int(u.get("last_seen", 0)) <= ACTIVE_WINDOW),
        "made": sum(int(u.get("made", 0)) for u in users.values()),
        "unl": sum(1 for u in users.values() if u.get("unlimited") or int(u.get("unl_until", 0) or 0) > t),
        "refs": sum(int(u.get("ref_count", 0)) for u in users.values()),
    }

def top_referrers(n=5):
    st = store.snapshot()
    rows = [(int(uid), u) for uid, u in st["users"].items() if u.get("ref_count")]
    rows.sort(key=lambda r: -int(r[1]["ref_count"]))
    return rows[:n]

def users_page(page, per=8):
    st = store.snapshot()
    rows = sorted(st["users"].items(), key=lambda kv: -int(kv[1].get("last_seen", 0)))
    pages = max(1, (len(rows) + per - 1) // per)
    page = min(max(1, page), pages)
    return [(int(k), v) for k, v in rows[(page - 1) * per: page * per]], page, pages, len(rows)

def all_user_ids():
    return [int(k) for k in store.snapshot()["users"]]
