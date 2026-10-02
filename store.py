"""Persistent state: one JSON file, atomic writes, inter-process file lock."""
import os, json, time, fcntl, threading, copy, logging
from contextlib import contextmanager

log = logging.getLogger("store")
BASE = os.path.dirname(os.path.abspath(__file__))
PATH = os.path.join(BASE, "state.json")

DEFAULT_SETTINGS = {
    "free_quota": 10,
    "ref_enabled": True,
    "ref_bonus": 5,
    "ref_invitee_bonus": 2,
    "price_fa": "",
    "price_en": "",
}

DEFAULT_PLAN_TITLES = ("پایه / Basic", "اقتصادی / Economy", "پیشرفته / Advanced")

_tl = threading.RLock()
_cur = None

def set_path(p):
    global PATH
    PATH = p

def new_plan(pid, title):
    return {"id": pid, "title": title, "count": None, "price": "", "duration": "", "features": "", "popular": False}

def _migrate_plan(p):
    """Old shape had kind/amount (credits|days). Credits -> count; days plans become hidden (no count)."""
    if "count" not in p:
        kind, amount = p.pop("kind", None), p.pop("amount", None)
        p["count"] = int(amount) if kind == "credits" and amount else None
        p["duration"] = f"{amount} days" if kind == "days" and amount else ""
    p.pop("kind", None); p.pop("amount", None)
    p.setdefault("duration", ""); p.setdefault("features", ""); p.setdefault("popular", False)
    p.setdefault("price", "")
    return p

def _blank():
    return {"_v": 2, "admin_id": None, "settings": dict(DEFAULT_SETTINGS), "users": {},
            "pending_broadcast": None, "admins": [], "support": {"primary": "example_owner", "backup": ""},
            "pplans_seeded": True, "next_pplan_id": 4,
            # empty placeholders: no prices/counts invented; hidden from users until the admin fills them in
            "price_plans": [new_plan(i + 1, t) for i, t in enumerate(DEFAULT_PLAN_TITLES)]}

def _normalize(st):
    b = _blank()
    for k, v in b.items():
        if k not in st:
            st[k] = v
    for k, v in DEFAULT_SETTINGS.items():
        st["settings"].setdefault(k, v)
    if not isinstance(st.get("support"), dict):
        st["support"] = {"primary": "example_owner", "backup": ""}
    st["support"].setdefault("primary", "example_owner"); st["support"].setdefault("backup", "")
    st["price_plans"] = [_migrate_plan(p) for p in st.get("price_plans", [])]
    # drop leftovers of the removed payment feature
    for k in ("plans", "payments", "next_plan_id", "next_pay_id"):
        st.pop(k, None)
    for k in ("price_text", "pay_text"):
        st["settings"].pop(k, None)
    return st

def _load():
    try:
        with open(PATH) as f:
            raw = json.load(f)
    except FileNotFoundError:
        return _blank()
    except Exception as e:
        bak = f"{PATH}.corrupt-{int(time.time())}"
        try:
            os.replace(PATH, bak)
        except OSError:
            pass
        log.error("state file unreadable (%s); moved to %s", type(e).__name__, bak)
        return _blank()
    if not isinstance(raw, dict):
        return _blank()
    if raw.get("_v") != 2:
        # legacy layout: {"<uid>": {...user...}}
        st = _blank()
        st["users"] = {k: v for k, v in raw.items() if isinstance(v, dict)}
        return st
    return _normalize(raw)

def _save(st):
    tmp = PATH + ".tmp"
    with open(tmp, "w") as f:
        json.dump(st, f, ensure_ascii=False, indent=1)
        f.flush()
        os.fsync(f.fileno())
    os.replace(tmp, PATH)

@contextmanager
def transaction(write=True):
    """Exclusive read-modify-write. Nested use reuses the outer state."""
    global _cur
    with _tl:
        if _cur is not None:
            yield _cur
            return
        fd = open(PATH + ".lock", "a+")
        try:
            fcntl.flock(fd, fcntl.LOCK_EX)
            st = _load()
            _cur = st
            try:
                yield st
                if write:
                    _save(st)
            finally:
                _cur = None
        finally:
            try:
                fcntl.flock(fd, fcntl.LOCK_UN)
            finally:
                fd.close()

def snapshot():
    with transaction(write=False) as st:
        return copy.deepcopy(st)

def get_user(uid):
    with transaction(write=False) as st:
        return copy.deepcopy(st["users"].get(str(uid), {}))

def update_user(uid, **kw):
    """Set keys (value None deletes the key). Returns updated user dict."""
    with transaction() as st:
        u = st["users"].setdefault(str(uid), {})
        for k, v in kw.items():
            if v is None:
                u.pop(k, None)
            else:
                u[k] = v
        return copy.deepcopy(u)

def settings():
    with transaction(write=False) as st:
        return dict(st["settings"])

def admin_id():
    with transaction(write=False) as st:
        return st.get("admin_id")

def support():
    with transaction(write=False) as st:
        return dict(st["support"])
