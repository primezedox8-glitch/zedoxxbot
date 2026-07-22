# =========================
# ZEDOX BOT - COMPLETE VERSION
# With Working Subfolders, Fast Response, Fixed Give Points
# =========================

import telebot
from telebot.types import InlineKeyboardMarkup, InlineKeyboardButton, ReplyKeyboardMarkup
import os, time, random, string, threading, hashlib, hmac, json, csv, io, zipfile, traceback, logging, re, unicodedata
from urllib.parse import urlsplit, urlunsplit, parse_qsl, urlencode
from pymongo import MongoClient, ReturnDocument
from pymongo.errors import PyMongoError, AutoReconnect, ConnectionFailure, ConfigurationError
from datetime import datetime, timedelta
from functools import wraps

BOT_TOKEN = os.environ.get("BOT_TOKEN", "").strip()
ADMIN_ID_RAW = os.environ.get("ADMIN_ID", "").strip()
MONGO_URI = os.environ.get("MONGO_URI", "").strip()

def _strip_invisible(value):
    return "".join(ch for ch in str(value) if unicodedata.category(ch) not in ("Cf", "Cc")).strip()

def sanitize_mongo_uri(uri):
    """Remove invisible characters and unsafe URI options."""
    try:
        uri = _strip_invisible(uri)
        parts = urlsplit(uri)
        allowed = {
            "retrywrites", "journal", "readpreference", "replicaset", "authsource",
            "tls", "ssl", "tlsallowinvalidcertificates", "connecttimeoutms",
            "sockettimeoutms", "serverselectiontimeoutms", "maxpoolsize",
            "minpoolsize", "appname", "directconnection", "compressors",
            "zlibcompressionlevel", "uuidrepresentation"
        }
        cleaned = []
        for k, v in parse_qsl(parts.query, keep_blank_values=True):
            k = _strip_invisible(k)
            v = _strip_invisible(v)
            if k.lower() in allowed:
                cleaned.append((k, v))
        return urlunsplit((parts.scheme, parts.netloc, parts.path, urlencode(cleaned), parts.fragment))
    except Exception:
        return _strip_invisible(uri)

MONGO_URI = sanitize_mongo_uri(MONGO_URI)

if not BOT_TOKEN:
    raise RuntimeError("BOT_TOKEN environment variable is missing")
if not ADMIN_ID_RAW or not ADMIN_ID_RAW.isdigit():
    raise RuntimeError("ADMIN_ID must contain digits only")
if not MONGO_URI:
    raise RuntimeError("MONGO_URI environment variable is missing")

ADMIN_ID = int(ADMIN_ID_RAW)

# =========================
# 🌐 MONGODB SETUP (RELIABLE)
# =========================
def connect_mongodb():
    last_error = None
    for attempt in range(1, 11):
        try:
            mongo_client = MongoClient(
                MONGO_URI,
                serverSelectionTimeoutMS=15000,
                connectTimeoutMS=15000,
                socketTimeoutMS=30000,
                maxPoolSize=100,
                minPoolSize=2,
                appname="zedox-bot",
                retryWrites=True,
                retryReads=True,
            )
            mongo_client.admin.command("ping")
            print("✅ MongoDB connected")
            return mongo_client
        except Exception as exc:
            last_error = exc
            print(f"⚠️ MongoDB connection attempt {attempt}/10 failed: {exc}", flush=True)
            time.sleep(min(attempt * 3, 20))
    raise RuntimeError(f"MongoDB connection failed after retries: {last_error}")

client = connect_mongodb()
db = client["zedox_complete"]

# Collections — names are unchanged so all existing data remains available.
users_col = db["users"]
folders_col = db["folders"]
codes_col = db["codes"]
config_col = db["config"]
custom_buttons_col = db["custom_buttons"]
admins_col = db["admins"]
payments_col = db["payments"]
# New collections only extend the schema; original names remain unchanged.
logs_col = db["logs"]
broadcasts_col = db["broadcasts"]
auto_posts_col = db["auto_posts"]
source_chats_col = db["source_chats"]
point_history_col = db["point_history"]
purchases_col = db["purchases"]
referrals_col = db["referrals"]
backups_col = db["backups"]

# Index creation must never prevent the bot from starting.
def ensure_indexes():
    index_jobs = [
        (users_col, "points", {}),
        (users_col, "vip", {}),
        (users_col, "referrals_count", {}),
        (folders_col, [("cat", 1), ("parent", 1)], {}),
        (folders_col, "number", {"unique": True, "sparse": True}),
        (logs_col, [("created_at", -1)], {}),
        (broadcasts_col, [("run_at", 1), ("status", 1)], {}),
        (auto_posts_col, [("next_run", 1), ("active", 1)], {}),
        (point_history_col, [("user_id", 1), ("created_at", -1)], {}),
        (payments_col, [("user_id", 1), ("created_at", -1)], {}),
    ]
    for collection, keys, options in index_jobs:
        try:
            collection.create_index(keys, **options)
        except Exception as exc:
            print(f"⚠️ Index skipped for {collection.name}: {exc}", flush=True)

ensure_indexes()

bot = telebot.TeleBot(BOT_TOKEN, parse_mode="Markdown", threaded=True, num_threads=int(os.environ.get("BOT_WORKERS", "12")))

# Cache for frequently accessed data
_config_cache = None
_config_cache_time = 0
CACHE_TTL = 30

def get_cached_config():
    global _config_cache, _config_cache_time
    now = time.time()
    if _config_cache and (now - _config_cache_time) < CACHE_TTL:
        return _config_cache
    _config_cache = get_config()
    _config_cache_time = now
    return _config_cache

# =========================
# 🔐 SECURITY
# =========================
def validate_request(message):
    if not message or not message.from_user:
        return False
    if len(message.text or "") > 4096:
        return False
    return True

def hash_user_data(uid):
    secret = os.environ.get("BOT_TOKEN", "secret_key")
    return hmac.new(secret.encode(), str(uid).encode(), hashlib.sha256).hexdigest()[:16]

# =========================
# ⚙️ CONFIG SYSTEM
# =========================
def get_config():
    cfg = config_col.find_one({"_id": "config"})
    if not cfg:
        cfg = {
            "_id": "config",
            "force_channels": [],
            "custom_buttons": [],
            "vip_msg": "💎 Buy VIP to unlock this!",
            "welcome": "🔥 Welcome to ZEDOX BOT",
            "ref_reward": 5,
            "notify": True,
            "purchase_msg": "💰 Purchase VIP to access premium features!",
            "next_folder_number": 1,
            "points_per_dollar": 100,
            "contact_username": None,
            "contact_link": None,
            "vip_contact": None,
            "vip_price": 50,
            "vip_points_price": 5000,
            "payment_methods": ["💳 Binance", "💵 USDT (TRC20)", "💰 Bank Transfer", "🪙 Bitcoin"],
            "referral_vip_count": 50,
            "referral_purchase_count": 10,
            "vip_duration_days": 30,
            "binance_coin": "USDT",
            "binance_network": "TRC20",
            "binance_address": "",
            "binance_memo": "",
            "require_screenshot": True,
            "auto_import_free_source": None,
            "auto_import_vip_source": None,
            "recent_admin_chat_id": None,
            "recent_admin_chat_title": None,
            "hidden_main_buttons": [],
            "force_groups": [],
            "join_notify_group": None,
            "join_notify_enabled": True,
            "method_notify_group": None,
            "method_notify_enabled": True,
            "auto_import_free_source": None,
            "auto_import_vip_source": None
        }
        config_col.insert_one(cfg)
    return cfg

def set_config(key, value):
    global _config_cache
    _config_cache = None
    config_col.update_one({"_id": "config"}, {"$set": {key: value}}, upsert=True)


def normalize_chat_reference(value):
    value = _strip_invisible(value or "").strip()
    if not value:
        raise ValueError("Chat/link cannot be empty")
    if re.fullmatch(r"-?\d+", value):
        return int(value)
    m = re.search(r"(?:https?://)?(?:t\.me|telegram\.me)/([A-Za-z0-9_]{5,})", value, re.I)
    if m:
        return "@" + m.group(1)
    if value.startswith("@") and re.fullmatch(r"@[A-Za-z0-9_]{5,}", value):
        return value
    if re.fullmatch(r"[A-Za-z0-9_]{5,}", value):
        return "@" + value
    raise ValueError("Send @username, username, t.me link, or numeric chat ID")

def normalize_url_or_username(value):
    value = _strip_invisible(value or "").strip()
    if value.startswith(("http://", "https://", "tg://")):
        return value
    ref = normalize_chat_reference(value)
    if isinstance(ref, int):
        raise ValueError("A numeric ID cannot be opened as a button link")
    return f"https://t.me/{ref.lstrip('@')}"

def admin_success(uid, text="Process Complete", reply_markup=None):
    bot.send_message(uid, f"✅ **{text}**", parse_mode="Markdown", reply_markup=reply_markup or admin_menu())

def admin_error(uid, exc, reply_markup=None):
    bot.send_message(uid, f"❌ **Process Failed**\n{str(exc)[:1000]}", parse_mode="Markdown", reply_markup=reply_markup or admin_menu())

def send_method_notification(action, folder):
    cfg = get_cached_config()
    if not cfg.get("method_notify_enabled", True):
        return
    target = cfg.get("method_notify_group") or cfg.get("join_notify_group")
    if not target:
        return
    try:
        cat = str(folder.get("cat", "")).upper()
        name = folder.get("name", "Unknown")
        number = folder.get("number", "?")
        price = folder.get("price", 0)
        bot.send_message(target, f"🔔 **Method {action}**\n\n📂 Category: **{cat}**\n🔢 Number: `{number}`\n📄 Name: **{name}**\n💰 Price: `{price}` points", parse_mode="Markdown")
    except Exception as exc:
        log_event("method_notification_error", details={"error": str(exc)}, level="error")

# =========================
# 👑 MULTIPLE ADMINS SYSTEM
# =========================
def init_admins():
    if not admins_col.find_one({"_id": ADMIN_ID}):
        admins_col.insert_one({
            "_id": ADMIN_ID,
            "username": None,
            "added_by": "system",
            "added_at": time.time(),
            "is_owner": True
        })

init_admins()

def is_admin(uid):
    uid = int(uid) if isinstance(uid, str) else uid
    if uid == ADMIN_ID:
        return True
    return admins_col.find_one({"_id": uid}) is not None

def add_admin(uid, username=None, added_by=None):
    uid = int(uid) if isinstance(uid, str) else uid
    if admins_col.find_one({"_id": uid}):
        return False
    admins_col.insert_one({
        "_id": uid,
        "username": username,
        "added_by": added_by,
        "added_at": time.time(),
        "is_owner": False
    })
    return True

def remove_admin(uid):
    uid = int(uid) if isinstance(uid, str) else uid
    if uid == ADMIN_ID:
        return False
    result = admins_col.delete_one({"_id": uid})
    return result.deleted_count > 0

def get_all_admins():
    return list(admins_col.find({}))

# =========================
# 👤 USER SYSTEM
# =========================
class User:
    _cache = {}
    _cache_time = {}
    
    def __init__(self, uid):
        self.uid = str(uid)
        
        if uid in self._cache and (time.time() - self._cache_time.get(uid, 0)) < 30:
            self.data = self._cache[uid]
            return
        
        data = users_col.find_one({"_id": self.uid})
        
        if not data:
            data = {
                "_id": self.uid,
                "points": 0,
                "vip": False,
                "vip_expiry": None,
                "ref": None,
                "refs": 0,
                "refs_who_bought_vip": 0,
                "purchased_methods": [],
                "used_codes": [],
                "username": None,
                "created_at": time.time(),
                "last_active": time.time(),
                "hash_id": hash_user_data(uid),
                "total_points_earned": 0,
                "total_points_spent": 0
            }
            users_col.insert_one(data)
        
        self.data = data
        self._cache[uid] = data
        self._cache_time[uid] = time.time()
    
    def save(self):
        users_col.update_one({"_id": self.uid}, {"$set": self.data})
        self._cache[self.uid] = self.data
        self._cache_time[self.uid] = time.time()
    
    def is_vip(self):
        if self.data.get("vip", False):
            expiry = self.data.get("vip_expiry")
            if expiry and expiry < time.time():
                self.data["vip"] = False
                self.data["vip_expiry"] = None
                self.save()
                return False
            return True
        return False
    
    def points(self): 
        return self.data.get("points", 0)
    
    def purchased_methods(self): 
        return self.data.get("purchased_methods", [])
    
    def used_codes(self): 
        return self.data.get("used_codes", [])
    
    def username(self): 
        return self.data.get("username", None)
    
    def update_username(self, username):
        if username != self.data.get("username"):
            self.data["username"] = username
            self.save()
    
    def add_points(self, p):
        self.data["points"] += p
        self.data["total_points_earned"] = self.data.get("total_points_earned", 0) + p
        self.save()
    
    def spend_points(self, p):
        self.data["points"] -= p
        self.data["total_points_spent"] = self.data.get("total_points_spent", 0) + p
        self.save()
    
    def make_vip(self, duration_days=None):
        self.data["vip"] = True
        if duration_days and duration_days > 0:
            self.data["vip_expiry"] = time.time() + (duration_days * 86400)
        else:
            self.data["vip_expiry"] = None
        self.save()
    
    def remove_vip(self):
        self.data["vip"] = False
        self.data["vip_expiry"] = None
        self.save()
    
    def purchase_method(self, method_name, price):
        if self.points() >= price:
            self.spend_points(price)
            if method_name not in self.purchased_methods():
                self.data["purchased_methods"].append(method_name)
                self.save()
            return True
        return False
    
    def can_access_method(self, method_name):
        return self.is_vip() or method_name in self.purchased_methods()
    
    def add_used_code(self, code):
        if code not in self.used_codes():
            self.data["used_codes"].append(code)
            self.save()
            return True
        return False
    
    def has_used_code(self, code):
        return code in self.used_codes()
    
    def add_ref(self):
        self.data["refs"] = self.data.get("refs", 0) + 1
        self.save()
        
        config = get_cached_config()
        required_refs = config.get("referral_vip_count", 50)
        
        if self.data["refs"] >= required_refs and not self.is_vip():
            self.make_vip(config.get("vip_duration_days", 30))
            return True
        return False
    
    def add_ref_bought_vip(self):
        self.data["refs_who_bought_vip"] = self.data.get("refs_who_bought_vip", 0) + 1
        self.save()
        
        config = get_cached_config()
        required_purchases = config.get("referral_purchase_count", 10)
        
        if self.data["refs_who_bought_vip"] >= required_purchases and not self.is_vip():
            self.make_vip(config.get("vip_duration_days", 30))
            return True
        return False
    
    def get_refs_count(self):
        return self.data.get("refs", 0)
    
    def get_refs_bought_vip_count(self):
        return self.data.get("refs_who_bought_vip", 0)

# =========================
# 📁 FOLDER SYSTEM (WITH WORKING SUBFOLDERS)
# =========================
class FS:
    def add(self, cat, name, files, price, parent=None, number=None, text_content=None):
        if number is None:
            config = get_config()
            number = config.get("next_folder_number", 1)
            set_config("next_folder_number", number + 1)
        
        folder_data = {
            "cat": cat,
            "name": name,
            "files": files,
            "price": price,
            "parent": parent,
            "number": number,
            "created_at": time.time()
        }
        
        if text_content:
            folder_data["text_content"] = text_content
        
        folders_col.insert_one(folder_data)
        return number
    
    def get(self, cat, parent=None):
        query = {"cat": cat}
        if parent:
            query["parent"] = parent
        else:
            query["parent"] = None
        return list(folders_col.find(query).sort("number", 1))
    
    def get_one(self, cat, name, parent=None):
        query = {"cat": cat, "name": name}
        if parent:
            query["parent"] = parent
        return folders_col.find_one(query)
    
    def get_by_number(self, number):
        return folders_col.find_one({"number": number})
    
    def update_numbers_after_delete(self, deleted_number):
        folders_col.update_many(
            {"number": {"$gt": deleted_number}},
            {"$inc": {"number": -1}}
        )
        config = get_config()
        current_next = config.get("next_folder_number", 1)
        if current_next > deleted_number:
            set_config("next_folder_number", current_next - 1)
    
    def delete_all_subfolders(self, cat, parent_name):
        subfolders = list(folders_col.find({"cat": cat, "parent": parent_name}))
        for sub in subfolders:
            self.delete_all_subfolders(cat, sub["name"])
            folders_col.delete_one({"_id": sub["_id"]})
    
    def delete(self, cat, name, parent=None):
        query = {"cat": cat, "name": name}
        if parent:
            query["parent"] = parent
        else:
            query["parent"] = None
        
        folder = folders_col.find_one(query)
        if not folder:
            return False
        
        number = folder.get("number")
        self.delete_all_subfolders(cat, name)
        folders_col.delete_one(query)
        
        if number:
            self.update_numbers_after_delete(number)
        
        return True
    
    def edit_price(self, cat, name, price, parent=None):
        query = {"cat": cat, "name": name}
        if parent:
            query["parent"] = parent
        folders_col.update_one(query, {"$set": {"price": price}})
    
    def edit_name(self, cat, old, new, parent=None):
        query = {"cat": cat, "name": old}
        if parent:
            query["parent"] = parent
        folders_col.update_one(query, {"$set": {"name": new}})
        folders_col.update_many({"cat": cat, "parent": old}, {"$set": {"parent": new}})
    
    def move_folder(self, number, new_parent):
        folders_col.update_one({"number": number}, {"$set": {"parent": new_parent}})
    
    def edit_content(self, cat, name, content_type, content, parent=None):
        query = {"cat": cat, "name": name}
        if parent:
            query["parent"] = parent
        
        if content_type == "text":
            folders_col.update_one(query, {"$set": {"text_content": content}})
        elif content_type == "files":
            folders_col.update_one(query, {"$set": {"files": content}})
        return True

fs = FS()

# =========================
# 🏆 CODES SYSTEM
# =========================
class Codes:
    def generate(self, pts, count, multi_use=False, expiry_days=None):
        res = []
        expiry = time.time() + (expiry_days * 86400) if expiry_days else None
        
        for _ in range(count):
            code = "ZEDOX" + ''.join(random.choices(string.ascii_uppercase+string.digits, k=8))
            while codes_col.find_one({"_id": code}):
                code = "ZEDOX" + ''.join(random.choices(string.ascii_uppercase+string.digits, k=8))
            
            codes_col.insert_one({
                "_id": code,
                "points": pts,
                "used": False,
                "multi_use": multi_use,
                "used_count": 0,
                "max_uses": 0 if not multi_use else 10,
                "expiry": expiry,
                "created_at": time.time(),
                "used_by_users": []
            })
            res.append(code)
        return res
    
    def redeem(self, code, user):
        code_data = codes_col.find_one({"_id": code})
        
        if not code_data:
            return False, 0, "invalid"
        
        if code_data.get("expiry") and time.time() > code_data["expiry"]:
            return False, 0, "expired"
        
        if not code_data.get("multi_use", False) and code_data.get("used", False):
            return False, 0, "already_used"
        
        if user.uid in code_data.get("used_by_users", []):
            return False, 0, "already_used_by_user"
        
        if code_data.get("multi_use", False):
            used_count = code_data.get("used_count", 0)
            max_uses = code_data.get("max_uses", 10)
            if used_count >= max_uses:
                return False, 0, "max_uses_reached"
        
        pts = code_data["points"]
        user.add_points(pts)
        
        update_data = {
            "$push": {"used_by_users": user.uid},
            "$inc": {"used_count": 1}
        }
        
        if not code_data.get("multi_use", False):
            update_data["$set"] = {"used": True}
        
        codes_col.update_one({"_id": code}, update_data)
        user.add_used_code(code)
        
        return True, pts, "success"
    
    def get_all_codes(self):
        return list(codes_col.find({}).sort("created_at", -1))
    
    def get_stats(self):
        total = codes_col.count_documents({})
        used = codes_col.count_documents({"used": True})
        unused = total - used
        multi_use = codes_col.count_documents({"multi_use": True})
        return total, used, unused, multi_use

codesys = Codes()

# =========================
# 📦 POINTS PACKAGES SYSTEM
# =========================
def get_points_packages():
    packages = config_col.find_one({"_id": "points_packages"})
    if not packages:
        default_packages = {
            "_id": "points_packages",
            "packages": [
                {"points": 100, "price": 5, "currency": "USD", "bonus": 0, "active": True},
                {"points": 250, "price": 10, "currency": "USD", "bonus": 25, "active": True},
                {"points": 550, "price": 20, "currency": "USD", "bonus": 100, "active": True},
                {"points": 1500, "price": 50, "currency": "USD", "bonus": 500, "active": True},
                {"points": 3500, "price": 100, "currency": "USD", "bonus": 1500, "active": True},
                {"points": 10000, "price": 250, "currency": "USD", "bonus": 5000, "active": True}
            ]
        }
        config_col.insert_one(default_packages)
        return default_packages["packages"]
    return packages["packages"]

def save_points_packages(packages):
    config_col.update_one(
        {"_id": "points_packages"},
        {"$set": {"packages": packages}},
        upsert=True
    )

# =========================
# 🚫 FORCE JOIN (FAST)
# =========================
_force_cache = {}
FORCE_CACHE_TTL = 10

def force_block(uid):
    global _force_cache
    now = time.time()
    
    if is_admin(uid):
        return False
    
    cfg = get_cached_config()
    force_channels = cfg.get("force_channels", [])
    force_groups = cfg.get("force_groups", [])
    force_targets = list(dict.fromkeys(force_channels + force_groups))
    
    if not force_targets:
        return False
    
    for ch in force_targets:
        try:
            member = bot.get_chat_member(ch, uid)
            if member.status in ["left", "kicked"]:
                kb = InlineKeyboardMarkup()
                for channel in force_targets:
                    kb.add(InlineKeyboardButton(f"📢 Join {channel}", url=f"https://t.me/{channel.replace('@','')}"))
                kb.add(InlineKeyboardButton("✅ I Joined", callback_data="recheck"))
                bot.send_message(uid, "🚫 **Access Restricted!**\n\nPlease join the following channels:", reply_markup=kb, parse_mode="Markdown")
                return True
        except:
            kb = InlineKeyboardMarkup()
            for channel in force_targets:
                kb.add(InlineKeyboardButton(f"📢 Join {channel}", url=f"https://t.me/{channel.replace('@','')}"))
            kb.add(InlineKeyboardButton("✅ I Joined", callback_data="recheck"))
            bot.send_message(uid, f"🚫 **Please join required channels!**", reply_markup=kb, parse_mode="Markdown")
            return True
    
    return False

def force_join_handler(func):
    @wraps(func)
    def wrapper(message):
        if force_block(message.from_user.id):
            return
        return func(message)
    return wrapper

# =========================
# 📱 MAIN MENU
# =========================
def get_custom_buttons():
    cfg = get_cached_config()
    return cfg.get("custom_buttons", [])

def add_custom_button(button_text, button_type, button_data):
    cfg = get_config()
    buttons = cfg.get("custom_buttons", [])
    buttons.append({
        "text": button_text,
        "type": button_type,
        "data": button_data
    })
    set_config("custom_buttons", buttons)

def remove_custom_button(button_text):
    cfg = get_config()
    buttons = cfg.get("custom_buttons", [])
    buttons = [b for b in buttons if b["text"] != button_text]
    set_config("custom_buttons", buttons)

def get_hidden_main_buttons():
    cfg = get_cached_config()
    return set(cfg.get("hidden_main_buttons", []))

MAIN_MENU_ROWS = [
    ("📂 FREE METHODS", "💎 VIP METHODS"),
    ("📦 PREMIUM APPS", "⚡ SERVICES"),
    ("💰 POINTS", "⭐ BUY VIP"),
    ("🎁 REFERRAL", "👤 ACCOUNT"),
    ("📚 MY METHODS", "💎 GET POINTS"),
    ("🆔 CHAT ID", "🏆 REDEEM"),
]

MAIN_MENU_BUTTONS = [button for row in MAIN_MENU_ROWS for button in row]

def main_menu(uid):
    kb = ReplyKeyboardMarkup(resize_keyboard=True, row_width=2)
    hidden = get_hidden_main_buttons()

    for row in MAIN_MENU_ROWS:
        visible = [button for button in row if button not in hidden]
        if visible:
            kb.row(*visible)

    custom_btns = get_custom_buttons()
    if custom_btns:
        row = []
        for btn in custom_btns:
            if btn["text"] in hidden:
                continue
            row.append(btn["text"])
            if len(row) == 2:
                kb.row(*row)
                row = []
        if row:
            kb.row(*row)

    if is_admin(uid):
        kb.row("⚙️ ADMIN PANEL")

    return kb

# =========================
# 🚀 START
# =========================
@bot.message_handler(commands=["start"])
def start_cmd(m):
    if not validate_request(m):
        return
    
    uid = m.from_user.id
    args = m.text.split()
    is_new_user = users_col.find_one({"_id": str(uid)}, {"_id": 1}) is None
    
    user = User(uid)
    
    if m.from_user.username:
        user.update_username(m.from_user.username)
    
    if len(args) > 1:
        ref_id = args[1]
        
        if ref_id != str(uid) and ref_id.isdigit():
            ref_user_data = users_col.find_one({"_id": ref_id})
            
            if ref_user_data and not user.data.get("ref"):
                try:
                    ref_user = User(ref_id)
                    reward = get_cached_config().get("ref_reward", 5)
                    
                    ref_user.add_points(reward)
                    got_vip = ref_user.add_ref()
                    
                    user.data["ref"] = ref_id
                    user.save()
                    
                    try:
                        vip_msg = ""
                        if got_vip:
                            vip_msg = f"\n\n🎉 **CONGRATULATIONS!** 🎉\nYou've reached {ref_user.get_refs_count()} referrals and got **FREE VIP ACCESS**!"
                        
                        bot.send_message(int(ref_id), 
                            f"👤 **New Referral Alert!**\n\n"
                            f"✨ **@{user.username or user.uid}** just joined!\n\n"
                            f"💰 You earned **+{reward} points**!\n"
                            f"📊 Total Referrals: **{ref_user.get_refs_count()}**\n"
                            f"💎 Total Points: **{ref_user.points()}**{vip_msg}",
                            parse_mode="Markdown")
                    except:
                        pass
                except:
                    pass
    
    if force_block(uid):
        return
    
    cfg = get_cached_config()
    welcome_msg = cfg.get("welcome", "Welcome to ZEDOX BOT!")
    
    bot.send_message(uid, f"{welcome_msg}\n\n💰 Your points: **{user.points()}**", reply_markup=main_menu(uid))

    if is_new_user:
        notify_group = cfg.get("join_notify_group")
        if notify_group and cfg.get("join_notify_enabled", True):
            try:
                full_name = " ".join(x for x in [m.from_user.first_name, m.from_user.last_name] if x) or "Unknown"
                username = f"@{m.from_user.username}" if m.from_user.username else "No username"
                referrer = user.data.get("ref") or "Direct join"
                bot.send_message(
                    notify_group,
                    f"🆕 **New User Joined Bot**\n\n"
                    f"👤 Name: {full_name}\n"
                    f"🔗 Username: {username}\n"
                    f"🆔 User ID: `{uid}`\n"
                    f"🌐 Language: {m.from_user.language_code or 'Unknown'}\n"
                    f"🎁 Referrer: `{referrer}`\n"
                    f"🕒 Joined: {datetime.now().strftime('%Y-%m-%d %H:%M:%S')}",
                    parse_mode="Markdown",
                )
            except Exception as exc:
                log_event("join_notification_error", uid, notify_group, {"error": str(exc)}, level="error")

# =========================
# 💰 POINTS COMMAND
# =========================
@bot.message_handler(func=lambda m: m.text == "💰 POINTS")
@force_join_handler
def points_cmd(m):
    uid = m.from_user.id
    user = User(uid)
    
    purchased_count = len(user.purchased_methods())
    ref_count = user.get_refs_count()
    ref_bought_count = user.get_refs_bought_vip_count()
    
    points_msg = f"💰 **YOUR POINTS BALANCE** 💰\n\n"
    points_msg += f"┌ **Points:** `{user.points()}`\n"
    points_msg += f"├ **VIP Status:** {'✅ Active' if user.is_vip() else '❌ Not Active'}\n"
    points_msg += f"├ **Purchased Methods:** `{purchased_count}`\n"
    points_msg += f"├ **Total Referrals:** `{ref_count}`\n"
    points_msg += f"├ **Referrals who bought VIP:** `{ref_bought_count}`\n"
    points_msg += f"├ **Total Earned:** `{user.data.get('total_points_earned', 0)}`\n"
    points_msg += f"└ **Total Spent:** `{user.data.get('total_points_spent', 0)}`\n\n"
    
    points_msg += f"✨ **Ways to Earn Points:**\n"
    points_msg += f"• 🎁 **Referral System:** Share your link\n"
    points_msg += f"• 🏆 **Redeem Codes:** Use coupon codes\n"
    points_msg += f"• 💎 **Purchase:** Click 💎 GET POINTS button\n\n"
    
    points_msg += f"🎯 **Referral Rewards:**\n"
    cfg = get_cached_config()
    points_msg += f"• Invite {cfg.get('referral_vip_count', 50)} users → **FREE VIP**\n"
    points_msg += f"• {cfg.get('referral_purchase_count', 10)} referrals buy VIP → **FREE VIP**\n\n"
    
    points_msg += f"💡 **Use points to:**\n"
    points_msg += f"• Buy individual VIP methods\n"
    points_msg += f"• Access premium content\n"
    points_msg += f"• Redeem special offers"
    
    bot.send_message(uid, points_msg, parse_mode="Markdown")

# =========================
# 💎 GET POINTS
# =========================
@bot.message_handler(func=lambda m: m.text == "💎 GET POINTS")
@force_join_handler
def get_points_button(m):
    uid = m.from_user.id
    user = User(uid)
    
    packages = get_points_packages()
    active_packages = [p for p in packages if p.get("active", True)]
    cfg = get_cached_config()
    
    contact_username = cfg.get("contact_username")
    contact_link = cfg.get("contact_link")
    
    binance_address = cfg.get("binance_address", "")
    binance_coin = cfg.get("binance_coin", "USDT")
    binance_network = cfg.get("binance_network", "TRC20")
    binance_memo = cfg.get("binance_memo", "")
    
    message = f"💰 **GET POINTS** 💰\n\n"
    message += f"✨ **Your Current Balance:** `{user.points()}` points\n\n"
    
    if active_packages:
        message += f"📦 **BUY POINTS PACKAGES:**\n\n"
        for i, pkg in enumerate(active_packages, 1):
            total_points = pkg["points"] + pkg.get("bonus", 0)
            price_display = f"${pkg['price']}"
            
            message += f"💎 **Package {i}:**\n"
            message += f"   • {pkg['points']} points for `{price_display}`\n"
            if pkg.get("bonus", 0) > 0:
                message += f"   • **BONUS:** +{pkg['bonus']} points FREE!\n"
                message += f"   • **Total:** `{total_points}` points\n"
            message += f"   • 💰 **Value:** {price_display}\n\n"
        
        if binance_address:
            message += f"💳 **Binance Payment Details:**\n"
            message += f"┌ **Coin:** {binance_coin}\n"
            message += f"├ **Network:** {binance_network}\n"
            message += f"├ **Address:** `{binance_address}`\n"
            if binance_memo:
                message += f"├ **Memo/Tag:** `{binance_memo}`\n"
            message += f"└ **Amount:** Equal to package price\n\n"
            
            if cfg.get("require_screenshot", True):
                message += f"📸 **IMPORTANT:** Send payment screenshot!\n\n"
        
        message += f"✨ **How to Purchase:**\n"
        message += f"1️⃣ Send payment to Binance address\n"
        message += f"2️⃣ Take a screenshot\n"
        message += f"3️⃣ Send screenshot here\n"
        message += f"4️⃣ Send User ID: `{uid}`\n"
        message += f"5️⃣ Mention package\n\n"
        
        message += f"💳 **Other Payment Methods:**\n"
        for method in cfg.get("payment_methods", ["💳 Binance", "💵 USDT"]):
            if "Binance" not in method:
                message += f"• {method}\n"
        message += f"\n"
        
        message += f"🎁 **Special Offers:**\n"
        message += f"• First purchase: **10% BONUS**\n"
        message += f"• Referral: Earn points\n\n"
        
        message += f"⚡ **Fast delivery!**\n\n"
    else:
        message += f"❌ No packages available.\n\n"
    
    message += f"🎁 **FREE WAYS TO EARN POINTS:**\n"
    message += f"• **Referral System:** Share your link\n"
    message += f"• **Redeem Codes:** Use coupon codes\n\n"
    
    message += f"💡 **Tip:** More points = More VIP methods!"
    
    kb = InlineKeyboardMarkup(row_width=2)
    
    if contact_link:
        kb.add(InlineKeyboardButton("📞 Contact Admin", url=contact_link))
    elif contact_username:
        kb.add(InlineKeyboardButton("📞 Contact Admin", url=f"https://t.me/{contact_username.replace('@', '')}"))
    else:
        try:
            admin_chat = bot.get_chat(ADMIN_ID)
            if admin_chat.username:
                kb.add(InlineKeyboardButton("📞 Contact Admin", url=f"https://t.me/{admin_chat.username}"))
        except:
            pass
    
    if active_packages:
        kb.add(InlineKeyboardButton("💰 Check Balance", callback_data="check_balance"))
    kb.add(InlineKeyboardButton("🎁 Referral Link", callback_data="get_referral"))
    kb.add(InlineKeyboardButton("⭐ VIP Info", callback_data="get_vip_info"))
    
    bot.send_message(uid, message, reply_markup=kb, parse_mode="Markdown")

# =========================
# 📂 SHOW FOLDERS (FAST)
# =========================
def get_folders_kb(cat, parent=None, page=0, items_per_page=15):
    data = fs.get(cat, parent)
    
    start = page * items_per_page
    end = start + items_per_page
    page_items = data[start:end]
    
    kb = InlineKeyboardMarkup(row_width=2)
    
    for item in page_items:
        name = item["name"]
        price = item.get("price", 0)
        number = item.get("number", "?")
        
        # Check if has subfolders
        subfolders = fs.get(cat, name)
        icon = "📁" if subfolders else "📄"
        
        text = f"{icon} [{number}] {name}"
        if price > 0:
            text += f" [{price} pts]"
        
        kb.add(InlineKeyboardButton(text, callback_data=f"open|{cat}|{name}|{parent or ''}"))
    
    nav_buttons = []
    if page > 0:
        nav_buttons.append(InlineKeyboardButton("⬅️", callback_data=f"page|{cat}|{page-1}|{parent or ''}"))
    if end < len(data):
        nav_buttons.append(InlineKeyboardButton("➡️", callback_data=f"page|{cat}|{page+1}|{parent or ''}"))
    
    if nav_buttons:
        kb.row(*nav_buttons)
    
    if parent:
        kb.add(InlineKeyboardButton("🔙 Back", callback_data=f"back|{cat}|{parent}"))
    
    return kb

@bot.message_handler(func=lambda m: m.text in [
    "📂 FREE METHODS",
    "💎 VIP METHODS",
    "📦 PREMIUM APPS",
    "⚡ SERVICES"
])
@force_join_handler
def show_category(m):
    uid = m.from_user.id
    
    mapping = {
        "📂 FREE METHODS": "free",
        "💎 VIP METHODS": "vip",
        "📦 PREMIUM APPS": "apps",
        "⚡ SERVICES": "services"
    }
    
    cat = mapping.get(m.text)
    
    if cat is None:
        bot.send_message(uid, "❌ Invalid category")
        return
    
    data = fs.get(cat)
    
    if not data:
        bot.send_message(uid, f"📂 {m.text}\n\nNo folders available!", parse_mode="Markdown")
        return
    
    bot.send_message(uid, f"📂 {m.text}\n\nSelect:", reply_markup=get_folders_kb(cat))

# =========================
# 📂 OPEN FOLDER (WITH WORKING SUBFOLDERS)
# =========================
@bot.callback_query_handler(func=lambda c: c.data.startswith("open|"))
def open_folder(c):
    uid = c.from_user.id
    user = User(uid)
    
    parts = c.data.split("|")
    cat = parts[1]
    name = parts[2]
    parent = parts[3] if len(parts) > 3 and parts[3] else None
    
    folder = fs.get_one(cat, name, parent if parent else None)
    
    if not folder:
        bot.answer_callback_query(c.id, "❌ Folder not found")
        return
    
    # CHECK FOR SUBFOLDERS - THIS IS THE KEY
    subfolders = fs.get(cat, name)
    
    if subfolders and len(subfolders) > 0:
        # Show subfolders
        kb = InlineKeyboardMarkup(row_width=1)
        
        for sub in subfolders:
            sub_name = sub["name"]
            sub_number = sub.get("number", "?")
            sub_price = sub.get("price", 0)
            
            # Check deeper subfolders
            deeper = fs.get(cat, sub_name)
            icon = "📁" if deeper else "📄"
            
            text = f"{icon} [{sub_number}] {sub_name}"
            if sub_price > 0:
                text += f" - {sub_price} pts"
            
            kb.add(InlineKeyboardButton(text, callback_data=f"open|{cat}|{sub_name}|{name}"))
        
        kb.add(InlineKeyboardButton("🔙 BACK", callback_data=f"back|{cat}|{name}"))
        
        bot.edit_message_text(
            f"📁 <b>{name}</b>",
            uid,
            c.message.message_id,
            reply_markup=kb,
            parse_mode="HTML"
        )
        bot.answer_callback_query(c.id)
        return
    
    # Handle text content
    text_content = folder.get("text_content")
    if text_content and not folder.get("files"):
        price = folder.get("price", 0)
        
        if cat == "vip":
            if user.is_vip() or user.can_access_method(name):
                pass
            else:
                if price > 0:
                    buy_kb = InlineKeyboardMarkup(row_width=2)
                    buy_kb.add(
                        InlineKeyboardButton(f"💰 Buy {price} pts", callback_data=f"buy|{cat}|{name}|{price}"),
                        InlineKeyboardButton("⭐ VIP", callback_data="get_vip"),
                        InlineKeyboardButton("💎 Points", callback_data="get_points")
                    )
                    buy_kb.add(InlineKeyboardButton("❌ Cancel", callback_data="cancel_buy"))
                    bot.answer_callback_query(c.id, "🔒 VIP method")
                    bot.send_message(uid, f"🔒 **{name}**\n\nPrice: {price} pts\nYour points: {user.points()}", reply_markup=buy_kb, parse_mode="Markdown")
                else:
                    buy_kb = InlineKeyboardMarkup(row_width=2)
                    buy_kb.add(
                        InlineKeyboardButton("⭐ VIP", callback_data="get_vip"),
                        InlineKeyboardButton("💎 Points", callback_data="get_points")
                    )
                    bot.answer_callback_query(c.id, "🔒 VIP only")
                    bot.send_message(uid, f"🔒 **{name}**\nVIP only!", reply_markup=buy_kb, parse_mode="Markdown")
                return
        
        if cat != "vip" and price > 0 and not user.is_vip():
            if user.points() < price:
                bot.answer_callback_query(c.id, f"❌ Need {price} pts! You have {user.points()}", True)
                return
            user.spend_points(price)
            bot.answer_callback_query(c.id, f"✅ -{price} pts")
        
        bot.send_message(uid, f"📄 **{name}**\n\n{text_content}", parse_mode="Markdown")
        
        if cat == "vip" and not user.is_vip():
            user.purchase_method(name, 0)
        
        return
    
    # Handle files
    files = folder.get("files", [])
    price = folder.get("price", 0)
    
    if cat == "vip":
        if user.is_vip() or user.can_access_method(name):
            pass
        else:
            if price > 0:
                buy_kb = InlineKeyboardMarkup(row_width=2)
                buy_kb.add(
                    InlineKeyboardButton(f"💰 Buy {price} pts", callback_data=f"buy|{cat}|{name}|{price}"),
                    InlineKeyboardButton("⭐ VIP", callback_data="get_vip"),
                    InlineKeyboardButton("💎 Points", callback_data="get_points")
                )
                buy_kb.add(InlineKeyboardButton("❌ Cancel", callback_data="cancel_buy"))
                bot.answer_callback_query(c.id, "🔒 VIP method")
                bot.send_message(uid, f"🔒 **{name}**\n\nPrice: {price} pts\nYour points: {user.points()}", reply_markup=buy_kb, parse_mode="Markdown")
            else:
                buy_kb = InlineKeyboardMarkup(row_width=2)
                buy_kb.add(
                    InlineKeyboardButton("⭐ VIP", callback_data="get_vip"),
                    InlineKeyboardButton("💎 Points", callback_data="get_points")
                )
                bot.answer_callback_query(c.id, "🔒 VIP only")
                bot.send_message(uid, f"🔒 **{name}**\nVIP only!", reply_markup=buy_kb, parse_mode="Markdown")
            return
    
    if cat != "vip" and price > 0 and not user.is_vip():
        if user.points() < price:
            bot.answer_callback_query(c.id, f"❌ Need {price} pts! You have {user.points()}", True)
            return
        user.spend_points(price)
        bot.answer_callback_query(c.id, f"✅ -{price} pts")
    
    if files:
        bot.answer_callback_query(c.id, "📤 Sending...")
        count = 0
        for f in files:
            try:
                bot.copy_message(uid, f["chat"], f["msg"])
                count += 1
                time.sleep(0.1)
            except:
                continue
        
        if get_cached_config().get("notify", True):
            if count > 0:
                bot.send_message(uid, f"✅ {count} file(s) sent!")
            else:
                bot.send_message(uid, "❌ Failed to send.")
    else:
        bot.send_message(uid, "📁 No files.")
    
    if cat == "vip" and not user.is_vip():
        user.purchase_method(name, 0)

# =========================
# 🔙 BACK BUTTON
# =========================
@bot.callback_query_handler(func=lambda c: c.data.startswith("back|"))
def back_handler(c):
    _, cat, current_parent = c.data.split("|")
    
    parent_folder = fs.get_one(cat, current_parent)
    if parent_folder:
        grand_parent = parent_folder.get("parent")
        bot.edit_message_reply_markup(
            c.from_user.id,
            c.message.message_id,
            reply_markup=get_folders_kb(cat, grand_parent)
        )
    else:
        bot.edit_message_reply_markup(
            c.from_user.id,
            c.message.message_id,
            reply_markup=get_folders_kb(cat)
        )
    bot.answer_callback_query(c.id)

# =========================
# 📄 PAGINATION
# =========================
@bot.callback_query_handler(func=lambda c: c.data.startswith("page|"))
def page_handler(c):
    _, cat, page, parent = c.data.split("|")
    parent = parent if parent != "None" else None
    
    try:
        bot.edit_message_reply_markup(
            c.from_user.id,
            c.message.message_id,
            reply_markup=get_folders_kb(cat, parent, int(page))
        )
    except:
        pass
    bot.answer_callback_query(c.id)

# =========================
# 💰 BUY METHOD
# =========================
@bot.callback_query_handler(func=lambda c: c.data.startswith("buy|"))
def buy_method(c):
    uid = c.from_user.id
    user = User(uid)
    
    try:
        _, cat, method_name, price = c.data.split("|")
        price = int(price)
    except:
        bot.answer_callback_query(c.id, "Invalid")
        return
    
    if user.is_vip():
        bot.answer_callback_query(c.id, "✅ You are VIP!", True)
        open_folder(c)
        return
    
    if user.can_access_method(method_name):
        bot.answer_callback_query(c.id, "✅ You own this!", True)
        open_folder(c)
        return
    
    if user.points() < price:
        bot.answer_callback_query(c.id, f"❌ Need {price} pts! You have {user.points()}", True)
        return
    
    if user.purchase_method(method_name, price):
        bot.answer_callback_query(c.id, f"✅ Purchased! -{price} pts", True)
        bot.edit_message_text(
            f"✅ **Purchased!**\n\nYou now own: {method_name}\nRemaining: {user.points()} pts",
            uid,
            c.message.message_id,
            parse_mode="Markdown"
        )
    else:
        bot.answer_callback_query(c.id, "❌ Failed!", True)

# =========================
# CALLBACK HANDLERS
# =========================
@bot.callback_query_handler(func=lambda c: c.data == "get_vip")
def get_vip_callback(c):
    uid = c.from_user.id
    user = User(uid)
    cfg = get_cached_config()
    
    if user.is_vip():
        bot.answer_callback_query(c.id, "✅ Already VIP!", True)
        return
    
    vip_msg = cfg.get("vip_msg", "💎 Buy VIP!")
    vip_price_usd = cfg.get("vip_price", 50)
    vip_price_points = cfg.get("vip_points_price", 5000)
    vip_contact = cfg.get("vip_contact")
    
    binance_address = cfg.get("binance_address", "")
    binance_coin = cfg.get("binance_coin", "USDT")
    binance_network = cfg.get("binance_network", "TRC20")
    binance_memo = cfg.get("binance_memo", "")
    
    message = f"💎 **VIP**\n\n{vip_msg}\n\n💰 Price:\n• ${vip_price_usd} USD\n• {vip_price_points} points\n\n"
    
    if binance_address:
        message += f"💳 **Binance:**\nCoin: {binance_coin}\nNetwork: {binance_network}\nAddress: `{binance_address}`\n"
        if binance_memo:
            message += f"Memo: `{binance_memo}`\n"
        message += f"Amount: ${vip_price_usd}\n\n"
    
    message += f"✨ Benefits:\n• All VIP methods\n• Priority support\n• No points needed\n\n"
    
    if vip_contact:
        message += f"📞 Contact: {vip_contact}\n"
    
    message += f"\n🆔 ID: `{uid}`\n💰 Points: {user.points()}"
    
    kb = InlineKeyboardMarkup()
    if user.points() >= vip_price_points:
        kb.add(InlineKeyboardButton(f"⭐ Buy with {vip_price_points} pts", callback_data="buy_vip_points"))
    if vip_contact:
        if vip_contact.startswith("http"):
            kb.add(InlineKeyboardButton("📞 Contact", url=vip_contact))
        elif vip_contact.startswith("@"):
            kb.add(InlineKeyboardButton("📞 Contact", url=f"https://t.me/{vip_contact.replace('@', '')}"))
    
    bot.edit_message_text(message, uid, c.message.message_id, reply_markup=kb if kb.keyboard else None, parse_mode="Markdown")
    bot.answer_callback_query(c.id)

@bot.callback_query_handler(func=lambda c: c.data == "buy_vip_points")
def buy_vip_points_callback(c):
    uid = c.from_user.id
    user = User(uid)
    cfg = get_cached_config()
    vip_price_points = cfg.get("vip_points_price", 5000)
    
    if user.is_vip():
        bot.answer_callback_query(c.id, "✅ Already VIP!", True)
        return
    
    if user.points() >= vip_price_points:
        user.spend_points(vip_price_points)
        user.make_vip(cfg.get("vip_duration_days", 30))
        bot.answer_callback_query(c.id, f"✅ VIP Purchased! -{vip_price_points} pts", True)
        bot.edit_message_text(
            f"🎉 **CONGRATULATIONS!** 🎉\n\nYou are now VIP!\n\n💰 Points: {user.points()}",
            uid,
            c.message.message_id,
            parse_mode="Markdown"
        )
    else:
        bot.answer_callback_query(c.id, f"❌ Need {vip_price_points} pts! You have {user.points()}", True)

@bot.callback_query_handler(func=lambda c: c.data == "get_points")
def get_points_callback(c):
    get_points_button(c.message)
    bot.answer_callback_query(c.id)

@bot.callback_query_handler(func=lambda c: c.data == "cancel_buy")
def cancel_buy(c):
    bot.edit_message_text("❌ Cancelled", c.from_user.id, c.message.message_id)
    bot.answer_callback_query(c.id)

@bot.callback_query_handler(func=lambda c: c.data == "check_balance")
def check_balance_callback(c):
    uid = c.from_user.id
    user = User(uid)
    
    bot.answer_callback_query(c.id, f"💰 Balance: {user.points()} pts", True)
    bot.edit_message_text(
        f"💰 **Balance**\n\nPoints: {user.points()}\nVIP: {'✅' if user.is_vip() else '❌'}\nReferrals: {user.get_refs_count()}",
        uid,
        c.message.message_id,
        parse_mode="Markdown"
    )

@bot.callback_query_handler(func=lambda c: c.data == "get_referral")
def get_referral_callback(c):
    uid = c.from_user.id
    cfg = get_cached_config()
    link = f"https://t.me/{bot.get_me().username}?start={uid}"
    
    bot.edit_message_text(
        f"🎁 **Referral Link**\n\n`{link}`\n\n✨ Rewards:\n• +{cfg.get('ref_reward', 5)} pts per referral\n• {cfg.get('referral_vip_count', 50)} referrals → FREE VIP\n• {cfg.get('referral_purchase_count', 10)} referral purchases → FREE VIP",
        uid,
        c.message.message_id,
        parse_mode="Markdown"
    )

@bot.callback_query_handler(func=lambda c: c.data == "get_vip_info")
def get_vip_info_callback(c):
    uid = c.from_user.id
    cfg = get_cached_config()
    vip_contact = cfg.get("vip_contact")
    vip_price_usd = cfg.get("vip_price", 50)
    vip_price_points = cfg.get("vip_points_price", 5000)
    
    message = f"⭐ **VIP Benefits** ⭐\n\n✨ Why become VIP?\n• ALL VIP methods\n• No points needed\n• Priority support\n• Exclusive content\n\n💰 Price: ${vip_price_usd} or {vip_price_points} pts\n\n🎁 FREE VIP:\n• Invite {cfg.get('referral_vip_count', 50)} users\n• Get {cfg.get('referral_purchase_count', 10)} referrals to buy VIP\n\n"
    
    if vip_contact:
        message += f"📞 Contact: {vip_contact}"
    
    kb = InlineKeyboardMarkup()
    if vip_contact:
        if vip_contact.startswith("http"):
            kb.add(InlineKeyboardButton("📞 Contact", url=vip_contact))
        elif vip_contact.startswith("@"):
            kb.add(InlineKeyboardButton("📞 Contact", url=f"https://t.me/{vip_contact.replace('@', '')}"))
    
    bot.edit_message_text(message, uid, c.message.message_id, reply_markup=kb if kb.keyboard else None, parse_mode="Markdown")

@bot.callback_query_handler(func=lambda c: c.data == "recheck")
def recheck(c):
    uid = c.from_user.id
    user = User(uid)
    
    if not force_block(uid):
        try:
            bot.edit_message_text("✅ **Access Granted!**", uid, c.message.message_id, parse_mode="Markdown")
        except:
            pass
        bot.send_message(uid, f"🎉 Welcome!\n\n💰 Points: {user.points()}", reply_markup=main_menu(uid))
    else:
        bot.answer_callback_query(c.id, "❌ Join channels first!", True)

# =========================
# 📚 MY METHODS
# =========================
@bot.message_handler(func=lambda m: m.text == "📚 MY METHODS")
@force_join_handler
def show_purchased_methods(m):
    uid = m.from_user.id
    user = User(uid)
    
    purchased = user.purchased_methods()
    
    if user.is_vip():
        bot.send_message(uid, "💎 **VIP Member**\n\nAccess to ALL VIP methods!", parse_mode="Markdown")
        return
    
    if not purchased:
        bot.send_message(uid, f"📚 **Your Methods**\n\nNo purchased methods yet.\n\n💰 Points: {user.points()}", parse_mode="Markdown")
        return
    
    all_vip_methods = {item["name"]: item.get("number", "?") for item in fs.get("vip")}
    
    kb = InlineKeyboardMarkup(row_width=2)
    for method in purchased:
        number = all_vip_methods.get(method, "?")
        kb.add(InlineKeyboardButton(f"[{number}] {method}", callback_data=f"open|vip|{method}|"))
    
    bot.send_message(uid, f"📚 **Your Methods** ({len(purchased)})\n\n💰 Points: {user.points()}", reply_markup=kb, parse_mode="Markdown")

# =========================
# 👤 ACCOUNT
# =========================
@bot.message_handler(func=lambda m: m.text == "👤 ACCOUNT")
@force_join_handler
def account_cmd(m):
    uid = m.from_user.id
    user = User(uid)
    
    status = "💎 VIP" if user.is_vip() else "🆓 Free"
    purchased_count = len(user.purchased_methods())
    ref_count = user.get_refs_count()
    ref_bought_count = user.get_refs_bought_vip_count()
    
    account_text = f"**👤 Account**\n\n"
    account_text += f"┌ Status: {status}\n"
    account_text += f"├ Points: {user.points()}\n"
    account_text += f"├ Referrals: {ref_count}\n"
    account_text += f"├ Referral Purchases: {ref_bought_count}\n"
    account_text += f"├ Purchased: {purchased_count} methods\n"
    account_text += f"├ Earned: {user.data.get('total_points_earned', 0)}\n"
    account_text += f"└ Spent: {user.data.get('total_points_spent', 0)}\n\n"
    
    if not user.is_vip():
        cfg = get_cached_config()
        account_text += f"💡 **FREE VIP:**\n• Invite {cfg.get('referral_vip_count', 50)} users\n• Get {cfg.get('referral_purchase_count', 10)} referrals to buy VIP\n"
    
    account_text += f"\n🆔 ID: `{uid}`"
    
    bot.send_message(uid, account_text, parse_mode="Markdown")

# =========================
# 🎁 REFERRAL
# =========================
@bot.message_handler(func=lambda m: m.text == "🎁 REFERRAL")
@force_join_handler
def referral_cmd(m):
    uid = m.from_user.id
    user = User(uid)
    
    link = f"https://t.me/{bot.get_me().username}?start={uid}"
    ref_count = user.get_refs_count()
    ref_reward = get_cached_config().get("ref_reward", 5)
    cfg = get_cached_config()
    
    bot.send_message(uid, 
        f"🎁 **Referral System**\n\n"
        f"🔗 `{link}`\n\n"
        f"📊 Your Stats:\n"
        f"┌ Referrals: {ref_count}\n"
        f"├ Points earned: {ref_count * ref_reward}\n"
        f"└ Progress: {ref_count}/{cfg.get('referral_vip_count', 50)}\n\n"
        f"✨ Rewards:\n"
        f"• +{ref_reward} pts per referral\n"
        f"• {cfg.get('referral_vip_count', 50)} referrals → FREE VIP\n"
        f"• {cfg.get('referral_purchase_count', 10)} referral purchases → FREE VIP\n\n"
        f"💰 Points: {user.points()}",
        parse_mode="Markdown")

# =========================
# 🏆 REDEEM CODE
# =========================
@bot.message_handler(func=lambda m: m.text == "🏆 REDEEM")
@force_join_handler
def redeem_cmd(m):
    msg = bot.send_message(m.from_user.id, "🎫 **Enter code:**", parse_mode="Markdown")
    bot.register_next_step_handler(msg, redeem_code)

def redeem_code(m):
    uid = m.from_user.id
    user = User(uid)
    code = m.text.strip().upper()
    
    success, pts, reason = codesys.redeem(code, user)
    
    if success:
        bot.send_message(uid, f"✅ **Redeemed!**\n\n+{pts} points\n💰 Balance: {user.points()}", parse_mode="Markdown")
    else:
        messages = {
            "invalid": "❌ Invalid code!",
            "already_used": "❌ Code already used!",
            "already_used_by_user": "❌ You already used this code!",
            "expired": "❌ Code expired!",
            "max_uses_reached": "❌ Max uses reached!"
        }
        bot.send_message(uid, messages.get(reason, "❌ Invalid code!"), parse_mode="Markdown")

# =========================
# 🆔 CHAT ID
# =========================
@bot.message_handler(func=lambda m: m.text == "🆔 CHAT ID")
@force_join_handler
def chatid_cmd(m):
    uid = m.from_user.id
    user = User(uid)
    
    bot.send_message(uid, f"🆔 **Your ID:** `{uid}`\n\n💰 Points: {user.points()}\n⭐ VIP: {'✅' if user.is_vip() else '❌'}\n👥 Referrals: {user.get_refs_count()}", parse_mode="Markdown")

# =========================
# ⭐ BUY VIP
# =========================
@bot.message_handler(func=lambda m: m.text == "⭐ BUY VIP")
@force_join_handler
def buy_vip_button(m):
    uid = m.from_user.id
    user = User(uid)
    cfg = get_cached_config()
    
    if user.is_vip():
        bot.send_message(uid, "✅ **You are VIP!**\n\n💰 Points: {}".format(user.points()), parse_mode="Markdown")
        return
    
    vip_msg = cfg.get("vip_msg", "💎 Buy VIP!")
    vip_price_usd = cfg.get("vip_price", 50)
    vip_price_points = cfg.get("vip_points_price", 5000)
    vip_contact = cfg.get("vip_contact")
    
    binance_address = cfg.get("binance_address", "")
    binance_coin = cfg.get("binance_coin", "USDT")
    binance_network = cfg.get("binance_network", "TRC20")
    binance_memo = cfg.get("binance_memo", "")
    
    message = f"💎 **VIP**\n\n{vip_msg}\n\n💰 Price:\n• ${vip_price_usd} USD\n• {vip_price_points} points\n\n"
    
    if binance_address:
        message += f"💳 **Binance:**\nCoin: {binance_coin}\nNetwork: {binance_network}\nAddress: `{binance_address}`\n"
        if binance_memo:
            message += f"Memo: `{binance_memo}`\n"
        message += f"Amount: ${vip_price_usd}\n\n"
    
    message += f"✨ Benefits:\n• All VIP methods\n• Priority support\n• No points needed\n\n"
    
    if vip_contact:
        message += f"📞 Contact: {vip_contact}\n"
    
    message += f"\n🆔 ID: `{uid}`\n💰 Points: {user.points()}"
    
    kb = InlineKeyboardMarkup()
    if user.points() >= vip_price_points:
        kb.add(InlineKeyboardButton(f"⭐ Buy with {vip_price_points} pts", callback_data="buy_vip_points"))
    if vip_contact:
        if vip_contact.startswith("http"):
            kb.add(InlineKeyboardButton("📞 Contact", url=vip_contact))
        elif vip_contact.startswith("@"):
            kb.add(InlineKeyboardButton("📞 Contact", url=f"https://t.me/{vip_contact.replace('@', '')}"))
    
    bot.send_message(uid, message, reply_markup=kb if kb.keyboard else None, parse_mode="Markdown")

# =========================
# ⚙️ ADMIN PANEL (SHORTENED FOR SPEED)
# =========================
def admin_menu():
    kb = ReplyKeyboardMarkup(resize_keyboard=True)

    kb.row("📦 Upload FREE", "💎 Upload VIP")
    kb.row("🗑 Delete Folder", "✏️ Edit Price")
    kb.row("✏️ Edit Name", "📝 Edit Content")
    kb.row("🔀 Move Folder", "👑 Add VIP")
    kb.row("👑 Remove VIP", "💰 Give Points")
    kb.row("🎫 Generate Codes", "📊 View Codes")
    kb.row("📦 Points Packages", "👥 Admin Management")
    kb.row("📞 Set Contacts", "⚙️ VIP Settings")
    kb.row("💳 Payment Methods", "🏦 Binance Settings")
    kb.row("📸 Screenshot", "🔘 Button Manager")
    kb.row("🙈 Hide Button", "👁 Show Button")
    kb.row("📢 Force Join", "👥 Join Notifications")
    kb.row("⚙️ Settings")
    kb.row("📊 Stats", "📢 Broadcast")
    kb.row("🔔 Notify", "📊 Leaderboard")
    kb.row("🔎 Search", "📣 Auto Posts")
    kb.row("📥 Auto Import", "🧾 Logs")
    kb.row("💾 Backup/Export")
    kb.row("❌ Exit")

    return kb

@bot.message_handler(func=lambda m: m.text == "⚙️ ADMIN PANEL" and is_admin(m.from_user.id))
def open_admin(m):
    bot.send_message(m.from_user.id, "⚙️ **Admin Panel**", reply_markup=admin_menu(), parse_mode="Markdown")

@bot.message_handler(func=lambda m: m.text == "❌ Exit" and is_admin(m.from_user.id))
def exit_admin(m):
    bot.send_message(m.from_user.id, "Exited", reply_markup=main_menu(m.from_user.id))

# =========================
# 📊 LEADERBOARD (NEW FEATURE)
# =========================
@bot.message_handler(func=lambda m: m.text == "📊 Leaderboard" and is_admin(m.from_user.id))
def leaderboard_menu(m):
    kb = InlineKeyboardMarkup(row_width=2)
    kb.add(
        InlineKeyboardButton("🏆 Top Referrals", callback_data="top_referrals"),
        InlineKeyboardButton("💰 Top Points", callback_data="top_points"),
        InlineKeyboardButton("⭐ Top Earners", callback_data="top_earned")
    )
    bot.send_message(m.from_user.id, "📊 **Leaderboard**\n\nSelect leaderboard type:", reply_markup=kb, parse_mode="Markdown")

@bot.callback_query_handler(func=lambda c: c.data == "top_referrals")
def top_referrals_cb(c):
    users = list(users_col.find({}).sort("refs", -1).limit(30))
    text = "🏆 **TOP 30 USERS BY REFERRALS** 🏆\n\n"
    
    for i, user in enumerate(users, 1):
        username = user.get("username") or f"User_{user['_id'][:6]}"
        refs = user.get("refs", 0)
        is_vip = "👑" if user.get("vip", False) else "📌"
        text += f"{i}. {is_vip} <code>{username}</code> → {refs} referrals\n"
    
    if not users:
        text += "No users found!"
    
    bot.edit_message_text(text, c.from_user.id, c.message.message_id, parse_mode="HTML")
    bot.answer_callback_query(c.id)

@bot.callback_query_handler(func=lambda c: c.data == "top_points")
def top_points_cb(c):
    users = list(users_col.find({}).sort("points", -1).limit(30))
    text = "💰 **TOP 30 USERS BY POINTS** 💰\n\n"
    
    for i, user in enumerate(users, 1):
        username = user.get("username") or f"User_{user['_id'][:6]}"
        points = user.get("points", 0)
        is_vip = "👑" if user.get("vip", False) else "📌"
        text += f"{i}. {is_vip} <code>{username}</code> → {points:,} pts\n"
    
    if not users:
        text += "No users found!"
    
    bot.edit_message_text(text, c.from_user.id, c.message.message_id, parse_mode="HTML")
    bot.answer_callback_query(c.id)

@bot.callback_query_handler(func=lambda c: c.data == "top_earned")
def top_earned_cb(c):
    users = list(users_col.find({}).sort("total_points_earned", -1).limit(30))
    text = "⭐ **TOP 30 USERS BY POINTS EARNED** ⭐\n\n"
    
    for i, user in enumerate(users, 1):
        username = user.get("username") or f"User_{user['_id'][:6]}"
        earned = user.get("total_points_earned", 0)
        is_vip = "👑" if user.get("vip", False) else "📌"
        text += f"{i}. {is_vip} <code>{username}</code> → {earned:,} pts earned\n"
    
    if not users:
        text += "No users found!"
    
    bot.edit_message_text(text, c.from_user.id, c.message.message_id, parse_mode="HTML")
    bot.answer_callback_query(c.id)

# =========================
# 📤 UPLOAD SYSTEM (FAST)
# =========================
upload_sessions = {}

def start_upload(uid, cat, is_service=False):
    upload_sessions[uid] = {"cat": cat, "service": is_service, "files": [], "step": "name"}
    kb = ReplyKeyboardMarkup(resize_keyboard=True)
    kb.row("📄 Text", "📁 Files")
    kb.row("/cancel")
    msg = bot.send_message(uid, f"📤 **Upload to {cat.upper()}**\n\nChoose:", reply_markup=kb, parse_mode="Markdown")
    bot.register_next_step_handler(msg, lambda m: upload_type_choice(m, cat, is_service))

def upload_type_choice(m, cat, is_service):
    if m.text == "/cancel":
        upload_sessions.pop(m.from_user.id, None)
        bot.send_message(m.from_user.id, "❌ Cancelled", reply_markup=admin_menu())
        return
    
    if m.text == "📄 Text":
        msg = bot.send_message(m.from_user.id, "📝 **Folder name:**", parse_mode="Markdown")
        bot.register_next_step_handler(msg, lambda x: upload_text_name(x, cat, is_service))
    elif m.text == "📁 Files":
        kb = ReplyKeyboardMarkup(resize_keyboard=True)
        kb.row("/done", "/cancel")
        msg = bot.send_message(m.from_user.id, f"📤 **Upload files**\n\nSend files, /done when finished:", reply_markup=kb, parse_mode="Markdown")
        bot.register_next_step_handler(msg, lambda x: upload_file_step(x, cat, m.from_user.id, [], is_service))
    else:
        bot.send_message(m.from_user.id, "❌ Invalid", reply_markup=admin_menu())

def upload_text_name(m, cat, is_service):
    name = m.text
    msg = bot.send_message(m.from_user.id, "💰 **Price (0 = free):**", parse_mode="Markdown")
    bot.register_next_step_handler(msg, lambda x: upload_text_price(x, cat, name, is_service))

def upload_text_price(m, cat, name, is_service):
    try:
        price = int(m.text)
        msg = bot.send_message(m.from_user.id, "📝 **Content:**", parse_mode="Markdown")
        bot.register_next_step_handler(msg, lambda x: upload_text_save(x, cat, name, price, is_service))
    except:
        bot.send_message(m.from_user.id, "❌ Invalid price!")

def upload_text_save(m, cat, name, price, is_service):
    text_content = m.text
    number = fs.add(cat, name, [], price, text_content=text_content)
    send_method_notification("uploaded", fs.get_by_number(number) or {"cat":cat,"name":name,"number":number,"price":price})
    
    if is_service:
        folder = fs.get_one(cat, name)
        if folder:
            folders_col.update_one({"_id": folder["_id"]}, {"$set": {"service_msg": text_content}})
    
    bot.send_message(m.from_user.id, f"✅ Added!\n📌 #{number}\n📂 {name}\n💰 {price} pts", reply_markup=admin_menu(), parse_mode="Markdown")
    upload_sessions.pop(m.from_user.id, None)

def upload_file_step(m, cat, uid, files, is_service):
    if m.text == "/cancel":
        upload_sessions.pop(uid, None)
        bot.send_message(uid, "❌ Cancelled", reply_markup=admin_menu())
        return
    
    if m.text == "/done":
        if not files:
            bot.send_message(uid, "❌ No files!")
            return
        msg = bot.send_message(uid, "📝 **Folder name:**", parse_mode="Markdown")
        bot.register_next_step_handler(msg, lambda x: upload_file_name(x, cat, files, is_service))
        return
    
    if m.content_type in ["document", "photo", "video"]:
        files.append({"chat": m.chat.id, "msg": m.message_id, "type": m.content_type})
        bot.send_message(uid, f"✅ Saved ({len(files)} files)")
    
    bot.register_next_step_handler(m, lambda x: upload_file_step(x, cat, uid, files, is_service))

def upload_file_name(m, cat, files, is_service):
    name = m.text
    msg = bot.send_message(m.from_user.id, "💰 **Price (0 = free):**", parse_mode="Markdown")
    bot.register_next_step_handler(msg, lambda x: upload_file_save(x, cat, name, files, is_service))

def upload_file_save(m, cat, name, files, is_service):
    try:
        price = int(m.text)
        number = fs.add(cat, name, files, price)
        send_method_notification("uploaded", fs.get_by_number(number) or {"cat":cat,"name":name,"number":number,"price":price})
        
        if is_service:
            msg = bot.send_message(m.from_user.id, "📝 **Service message:**", parse_mode="Markdown")
            bot.register_next_step_handler(msg, lambda x: service_msg_save(x, cat, name, number, price, files))
        else:
            bot.send_message(m.from_user.id, f"✅ Uploaded!\n📌 #{number}\n📂 {name}\n💰 {price} pts\n📁 {len(files)} files", reply_markup=admin_menu(), parse_mode="Markdown")
            upload_sessions.pop(m.from_user.id, None)
    except:
        bot.send_message(m.from_user.id, "❌ Invalid price!")

def service_msg_save(m, cat, name, number, price, files):
    service_msg = m.text
    folder = fs.get_one(cat, name)
    if folder:
        folders_col.update_one({"_id": folder["_id"]}, {"$set": {"service_msg": service_msg}})
    
    bot.send_message(m.from_user.id, f"✅ Service added!\n📌 #{number}\n📂 {name}\n💰 {price} pts\n📁 {len(files)} files", reply_markup=admin_menu(), parse_mode="Markdown")
    upload_sessions.pop(m.from_user.id, None)

@bot.message_handler(func=lambda m: m.text in ["📦 Upload FREE", "💎 Upload VIP"] and is_admin(m.from_user.id))
def upload_handler(m):
    cats = {"📦 Upload FREE": "free", "💎 Upload VIP": "vip"}
    start_upload(m.from_user.id, cats[m.text], False)


# =========================
# 🔀 MOVE FOLDER
# =========================
@bot.message_handler(func=lambda m: m.text == "🔀 Move Folder" and is_admin(m.from_user.id))
def move_folder_start(m):
    msg = bot.send_message(m.from_user.id, "🔀 **Move Folder**\n\nSend: `number new_parent`\nUse 'root' for main level", parse_mode="Markdown")
    bot.register_next_step_handler(msg, move_folder_process)

def move_folder_process(m):
    try:
        parts = m.text.split()
        number, new_parent = int(parts[0]), parts[1] if parts[1] != "root" else None
        if not fs.get_by_number(number):
            bot.send_message(m.from_user.id, "❌ Folder not found!")
            return
        fs.move_folder(number, new_parent)
        bot.send_message(m.from_user.id, f"✅ Folder #{number} moved!", reply_markup=admin_menu())
    except:
        bot.send_message(m.from_user.id, "❌ Use: number parent")

# =========================
# 🗂 FOLDER ACTION PICKER
# =========================
_folder_admin_state = {}

def folder_action_keyboard(action, page=0, per_page=20):
    rows = list(folders_col.find({}, {"number":1,"name":1,"cat":1,"parent":1,"price":1}).sort([("cat",1),("number",1)]))
    kb = InlineKeyboardMarkup(row_width=1)
    start = page * per_page
    for f in rows[start:start+per_page]:
        label = f"[{f.get('number','?')}] {str(f.get('cat','')).upper()} • {f.get('name')}"
        if f.get('parent'): label += f" / {f.get('parent')}"
        kb.add(InlineKeyboardButton(label[:60], callback_data=f"folderact|{action}|{f.get('number')}"))
    nav=[]
    if page>0: nav.append(InlineKeyboardButton("⬅️", callback_data=f"folderpage|{action}|{page-1}"))
    if start+per_page<len(rows): nav.append(InlineKeyboardButton("➡️", callback_data=f"folderpage|{action}|{page+1}"))
    if nav: kb.row(*nav)
    return kb

def show_folder_action(uid, action, title):
    kb=folder_action_keyboard(action)
    if not kb.keyboard:
        return bot.send_message(uid,"❌ No methods/folders found.",reply_markup=admin_menu())
    bot.send_message(uid,title,reply_markup=kb,parse_mode="Markdown")

@bot.message_handler(func=lambda m: m.text == "🗑 Delete Folder" and is_admin(m.from_user.id))
def del_start(m): show_folder_action(m.from_user.id,"delete","🗑 **Select a method/folder to delete:**")

@bot.message_handler(func=lambda m: m.text == "✏️ Edit Price" and is_admin(m.from_user.id))
def edit_price_start(m): show_folder_action(m.from_user.id,"price","✏️ **Select a method/folder to edit price:**")

@bot.message_handler(func=lambda m: m.text == "✏️ Edit Name" and is_admin(m.from_user.id))
def edit_name_start(m): show_folder_action(m.from_user.id,"name","✏️ **Select a method/folder to rename:**")

@bot.message_handler(func=lambda m: m.text == "📝 Edit Content" and is_admin(m.from_user.id))
def edit_content_start(m): show_folder_action(m.from_user.id,"content","📝 **Select a method/folder to edit content:**")

@bot.callback_query_handler(func=lambda c:c.data.startswith("folderpage|"))
def folder_page_cb(c):
    if not is_admin(c.from_user.id): return bot.answer_callback_query(c.id,"Admin only",True)
    _,action,page=c.data.split("|")
    bot.edit_message_reply_markup(c.from_user.id,c.message.message_id,reply_markup=folder_action_keyboard(action,int(page)))
    bot.answer_callback_query(c.id)

@bot.callback_query_handler(func=lambda c:c.data.startswith("folderact|"))
def folder_action_cb(c):
    if not is_admin(c.from_user.id): return bot.answer_callback_query(c.id,"Admin only",True)
    try:
        _,action,num=c.data.split("|"); folder=fs.get_by_number(int(num))
        if not folder: raise ValueError("Folder not found")
        _folder_admin_state[c.from_user.id]={"action":action,"number":int(num)}
        if action=="delete":
            kb=InlineKeyboardMarkup(row_width=2)
            kb.add(InlineKeyboardButton("✅ Confirm Delete",callback_data=f"folderconfirm|delete|{num}"),InlineKeyboardButton("❌ Cancel",callback_data="folderconfirm|cancel|0"))
            bot.send_message(c.from_user.id,f"⚠️ Delete **[{num}] {folder['name']}** from **{folder['cat'].upper()}**?\nThis also deletes its subfolders.",reply_markup=kb,parse_mode="Markdown")
        elif action=="price":
            msg=bot.send_message(c.from_user.id,f"Current price: `{folder.get('price',0)}`\nSend the new price:",parse_mode="Markdown");bot.register_next_step_handler(msg,folder_price_step)
        elif action=="name":
            msg=bot.send_message(c.from_user.id,f"Current name: **{folder['name']}**\nSend the new name:",parse_mode="Markdown");bot.register_next_step_handler(msg,folder_name_step)
        else:
            edit_sessions[c.from_user.id]={"cat":folder['cat'],"name":folder['name'],"parent":folder.get('parent'),"number":int(num)}
            kb=InlineKeyboardMarkup(row_width=2);kb.add(InlineKeyboardButton("📝 Text",callback_data="edit_text"),InlineKeyboardButton("📁 Files",callback_data="edit_files"),InlineKeyboardButton("❌ Cancel",callback_data="edit_cancel"))
            bot.send_message(c.from_user.id,f"📝 **Edit [{num}] {folder['name']}**\nWhat do you want to update?",reply_markup=kb,parse_mode="Markdown")
        bot.answer_callback_query(c.id)
    except Exception as exc: bot.answer_callback_query(c.id,str(exc),True)

@bot.callback_query_handler(func=lambda c:c.data.startswith("folderconfirm|"))
def folder_confirm_cb(c):
    if not is_admin(c.from_user.id): return
    try:
        _,action,num=c.data.split("|")
        if action=="cancel":
            bot.edit_message_text("❌ Cancelled",c.from_user.id,c.message.message_id);return bot.answer_callback_query(c.id)
        folder=fs.get_by_number(int(num))
        if not folder: raise ValueError("Folder no longer exists")
        ok=fs.delete(folder['cat'],folder['name'],folder.get('parent'))
        if not ok: raise ValueError("Delete failed")
        bot.edit_message_text(f"✅ Process Complete\nDeleted: [{num}] {folder['name']}",c.from_user.id,c.message.message_id)
        bot.answer_callback_query(c.id,"Deleted")
    except Exception as exc: admin_error(c.from_user.id,exc);bot.answer_callback_query(c.id,"Failed",True)

def folder_price_step(m):
    try:
        st=_folder_admin_state.pop(m.from_user.id,None); folder=fs.get_by_number(st['number']) if st else None
        if not folder: raise ValueError("Session expired or folder missing")
        price=int((m.text or '').strip())
        if price<0: raise ValueError("Price cannot be negative")
        folders_col.update_one({"_id":folder["_id"]},{"$set":{"price":price}})
        folder['price']=price;send_method_notification("updated",folder);admin_success(m.from_user.id,f"Price updated to {price} points")
    except Exception as exc: admin_error(m.from_user.id,exc)

def folder_name_step(m):
    try:
        st=_folder_admin_state.pop(m.from_user.id,None); folder=fs.get_by_number(st['number']) if st else None
        if not folder: raise ValueError("Session expired or folder missing")
        new=(m.text or '').strip()
        if not new or len(new)>100: raise ValueError("Name must be 1-100 characters")
        old=folder['name']; folders_col.update_one({"_id":folder["_id"]},{"$set":{"name":new}});folders_col.update_many({"cat":folder['cat'],"parent":old},{"$set":{"parent":new}})
        folder['name']=new;send_method_notification("updated",folder);admin_success(m.from_user.id,f"Renamed to {new}")
    except Exception as exc: admin_error(m.from_user.id,exc)

edit_sessions = {}

@bot.callback_query_handler(func=lambda c: c.data == "edit_text")
def edit_text_cb(c):
    uid = c.from_user.id
    if uid not in edit_sessions:
        bot.answer_callback_query(c.id, "Session expired!")
        return
    
    s = edit_sessions[uid]
    folder = fs.get_one(s["cat"], s["name"])
    current = folder.get("text_content", "No content")[:200]
    msg = bot.send_message(uid, f"📝 **Current:**\n{current}\n\nSend NEW text:", parse_mode="Markdown")
    bot.register_next_step_handler(msg, save_edit_text)
    bot.answer_callback_query(c.id)

def save_edit_text(m):
    uid = m.from_user.id
    if uid not in edit_sessions:
        bot.send_message(uid, "Session expired!", reply_markup=admin_menu())
        return
    
    s = edit_sessions[uid]
    fs.edit_content(s["cat"], s["name"], "text", m.text, s.get("parent"))
    folder=fs.get_by_number(s.get("number")) or fs.get_one(s["cat"],s["name"],s.get("parent")); send_method_notification("updated",folder or s)
    bot.send_message(uid, f"✅ Text updated!", reply_markup=admin_menu())
    edit_sessions.pop(uid, None)

@bot.callback_query_handler(func=lambda c: c.data == "edit_files")
def edit_files_cb(c):
    uid = c.from_user.id
    if uid not in edit_sessions:
        bot.answer_callback_query(c.id, "Session expired!")
        return
    
    edit_sessions[uid]["new_files"] = []
    kb = ReplyKeyboardMarkup(resize_keyboard=True)
    kb.add("/done", "/cancel")
    msg = bot.send_message(uid, "📁 Send NEW files\n/done when finished:", reply_markup=kb, parse_mode="Markdown")
    bot.register_next_step_handler(msg, process_edit_files)
    bot.answer_callback_query(c.id)

def process_edit_files(m):
    uid = m.from_user.id
    if m.text == "/cancel":
        edit_sessions.pop(uid, None)
        bot.send_message(uid, "❌ Cancelled", reply_markup=admin_menu())
        return
    
    if m.text == "/done":
        if uid not in edit_sessions:
            bot.send_message(uid, "Session expired!")
            return
        s = edit_sessions[uid]
        if not s.get("new_files"):
            bot.send_message(uid, "❌ No files!")
            return
        fs.edit_content(s["cat"], s["name"], "files", s["new_files"], s.get("parent"))
        folder=fs.get_by_number(s.get("number")) or fs.get_one(s["cat"],s["name"],s.get("parent")); send_method_notification("updated",folder or s)
        bot.send_message(uid, f"✅ {len(s['new_files'])} file(s) updated!", reply_markup=admin_menu())
        edit_sessions.pop(uid, None)
        return
    
    if m.content_type in ["document", "photo", "video"]:
        edit_sessions[uid]["new_files"].append({"chat": m.chat.id, "msg": m.message_id, "type": m.content_type})
        bot.send_message(uid, f"✅ Saved ({len(edit_sessions[uid]['new_files'])} files)")
    else:
        bot.send_message(uid, "❌ Send documents, photos, or videos!")
    bot.register_next_step_handler(m, process_edit_files)

@bot.callback_query_handler(func=lambda c: c.data == "edit_cancel")
def edit_cancel_cb(c):
    edit_sessions.pop(c.from_user.id, None)
    bot.edit_message_text("❌ Cancelled", c.from_user.id, c.message.message_id)
    bot.send_message(c.from_user.id, "Returning...", reply_markup=admin_menu())
    bot.answer_callback_query(c.id)

# =========================
# 👑 ADD VIP
# =========================
@bot.message_handler(func=lambda m: m.text == "👑 Add VIP" and is_admin(m.from_user.id))
def add_vip_start(m):
    msg = bot.send_message(m.from_user.id, "👑 **Add VIP**\n\nSend user ID or @username:", parse_mode="Markdown")
    bot.register_next_step_handler(msg, add_vip_process)

def add_vip_process(m):
    inp = m.text.strip()
    if inp.startswith("@"):
        try:
            target = bot.get_chat(inp).id
        except:
            bot.send_message(m.from_user.id, "❌ User not found!")
            return
    else:
        try:
            target = int(inp)
        except:
            bot.send_message(m.from_user.id, "❌ Invalid ID!")
            return
    
    u = User(target)
    if u.is_vip():
        bot.send_message(m.from_user.id, "⚠️ Already VIP!")
        return
    
    u.make_vip(get_config().get("vip_duration_days", 30))
    bot.send_message(m.from_user.id, f"✅ User {target} is now VIP!")
    try:
        bot.send_message(target, "🎉 **You are now VIP!** 🎉\n\nAccess all VIP methods!", parse_mode="Markdown")
    except:
        pass

# =========================
# 👑 REMOVE VIP
# =========================
@bot.message_handler(func=lambda m: m.text == "👑 Remove VIP" and is_admin(m.from_user.id))
def remove_vip_start(m):
    msg = bot.send_message(m.from_user.id, "👑 **Remove VIP**\n\nSend user ID or @username:", parse_mode="Markdown")
    bot.register_next_step_handler(msg, remove_vip_process)

def remove_vip_process(m):
    inp = m.text.strip()
    if inp.startswith("@"):
        try:
            target = bot.get_chat(inp).id
        except:
            bot.send_message(m.from_user.id, "❌ User not found!")
            return
    else:
        try:
            target = int(inp)
        except:
            bot.send_message(m.from_user.id, "❌ Invalid ID!")
            return
    
    u = User(target)
    if not u.is_vip():
        bot.send_message(m.from_user.id, "⚠️ Not VIP!")
        return
    
    u.remove_vip()
    bot.send_message(m.from_user.id, f"✅ VIP removed from {target}!")
    try:
        bot.send_message(target, "⚠️ VIP status removed.", parse_mode="Markdown")
    except:
        pass

# =========================
# 💰 GIVE POINTS (FIXED - FULLY WORKING)
# =========================
@bot.message_handler(func=lambda m: m.text == "💰 Give Points" and is_admin(m.from_user.id))
def give_points_start(m):
    msg = bot.send_message(m.from_user.id, 
        "💰 **Give Points**\n\n"
        "Send: `user_id points`\n\n"
        "Example: `7712834912 200`\n\n"
        "*User must have started the bot first*",
        parse_mode="Markdown")
    bot.register_next_step_handler(msg, give_points_process)

def give_points_process(m):
    admin_id = m.from_user.id
    try:
        parts = (m.text or "").strip().split()
        if len(parts) != 2:
            raise ValueError("Use: user_id points")

        user_id_text, points_text = parts
        if not user_id_text.isdigit():
            raise ValueError("User ID must contain digits only")

        user_id = int(user_id_text)
        points = int(points_text)
        if points < 1 or points > 1_000_000:
            raise ValueError("Points must be between 1 and 1,000,000")

        updated = users_col.find_one_and_update(
            {"_id": str(user_id)},
            {
                "$inc": {"points": points, "total_points_earned": points},
                "$set": {"last_active": time.time()},
            },
            return_document=ReturnDocument.AFTER,
        )
        if not updated:
            raise ValueError("User not found. The user must send /start first")

        User._cache.pop(user_id, None)
        User._cache.pop(str(user_id), None)
        User._cache_time.pop(user_id, None)
        User._cache_time.pop(str(user_id), None)

        old_points = int(updated.get("points", 0)) - points
        new_points = int(updated.get("points", 0))
        username = updated.get("username")
        display = f"@{username}" if username else f"ID: {user_id}"

        try:
            point_history_col.insert_one({
                "user_id": str(user_id),
                "amount": points,
                "reason": "manual_give_points",
                "admin_id": str(admin_id),
                "created_at": time.time(),
            })
        except Exception:
            pass

        bot.send_message(
            admin_id,
            f"✅ **Points Added Successfully**\n\n"
            f"👤 User: {display}\n"
            f"🆔 ID: `{user_id}`\n"
            f"💰 Old: `{old_points:,}`\n"
            f"➕ Added: `+{points:,}`\n"
            f"💰 New: `{new_points:,}`",
            parse_mode="Markdown",
            reply_markup=admin_menu(),
        )

        try:
            bot.send_message(
                user_id,
                f"🎉✨ **CONGRATULATIONS!** ✨🎉\n\n"
                f"💫 You have received **{points:,} points**!\n\n"
                f"💰 Previous balance: `{old_points:,}`\n"
                f"🏆 New balance: `{new_points:,}`\n\n"
                f"🥳 Enjoy your points and unlock more methods! 🚀",
                parse_mode="Markdown",
                reply_markup=main_menu(user_id),
            )
        except Exception:
            bot.send_message(admin_id, "⚠️ Points were added, but the user could not be notified.")

    except Exception as exc:
        bot.send_message(
            admin_id,
            f"❌ {exc}\n\nExample: `7712834912 200`",
            parse_mode="Markdown",
            reply_markup=admin_menu(),
        )

# =========================
# 🎫 GENERATE CODES (FIXED)
# =========================
@bot.message_handler(func=lambda m: m.text == "🎫 Generate Codes" and is_admin(m.from_user.id))
def gen_codes_start(m):
    msg = bot.send_message(
        m.from_user.id,
        "🎫 **Generate Codes**\n\n"
        "Send: `points count type expiry_days`\n\n"
        "Type: `single` or `multi`\n"
        "Expiry: `0` for no expiry\n\n"
        "Examples:\n"
        "`100 5 single 0`\n"
        "`250 10 multi 7`",
        parse_mode="Markdown",
    )
    bot.register_next_step_handler(msg, generate_codes_process)

def generate_codes_process(m):
    uid = m.from_user.id
    if not is_admin(uid):
        return
    try:
        parts = (m.text or "").strip().lower().split()
        if len(parts) != 4:
            raise ValueError("Use: points count type expiry_days")
        points = int(parts[0])
        count = int(parts[1])
        code_type = parts[2]
        expiry_days_raw = int(parts[3])
        if not 1 <= points <= 1_000_000:
            raise ValueError("Points must be between 1 and 1,000,000")
        if not 1 <= count <= 100:
            raise ValueError("Count must be between 1 and 100")
        if code_type not in ("single", "multi"):
            raise ValueError("Type must be single or multi")
        if not 0 <= expiry_days_raw <= 3650:
            raise ValueError("Expiry must be between 0 and 3650 days")
        expiry_days = expiry_days_raw or None
        generated = codesys.generate(points, count, code_type == "multi", expiry_days)
        if not generated:
            raise RuntimeError("No codes were generated")
        body = "\n".join(generated)
        bot.send_message(
            uid,
            f"✅ <b>{len(generated)} codes generated</b>\n"
            f"💰 Points: <b>{points:,}</b>\n"
            f"🔁 Type: <b>{code_type}</b>\n"
            f"⏰ Expiry: <b>{expiry_days_raw or 'None'}</b>\n\n"
            f"<code>{body}</code>",
            parse_mode="HTML",
            reply_markup=admin_menu(),
        )
    except Exception as exc:
        bot.send_message(uid, f"❌ {exc}\n\nExample: `100 5 single 0`", parse_mode="Markdown", reply_markup=admin_menu())

# =========================
# 📊 VIEW CODES
# =========================
@bot.message_handler(func=lambda m: m.text == "📊 View Codes" and is_admin(m.from_user.id))
def view_codes(m):
    codes = codesys.get_all_codes()
    if not codes:
        bot.send_message(m.from_user.id, "📊 No codes!")
        return
    
    total, used, unused, multi = codesys.get_stats()
    text = f"📊 **Codes**\n\nTotal: {total}\nUsed: {used}\nUnused: {unused}\nMulti: {multi}\n\n"
    
    unused_codes = [c for c in codes if not c.get("used", False)][:5]
    if unused_codes:
        text += "**Recent:**\n"
        for c in unused_codes:
            text += f"• `{c['_id']}` - {c['points']} pts\n"
    
    bot.send_message(m.from_user.id, text, parse_mode="Markdown")

# =========================
# 📦 POINTS PACKAGES
# =========================
@bot.message_handler(func=lambda m: m.text == "📦 Points Packages" and is_admin(m.from_user.id))
def packages_cmd(m):
    pkgs = get_points_packages()
    text = "📦 **Points Packages**\n\n"
    for i, p in enumerate(pkgs, 1):
        status = "✅" if p.get("active", True) else "❌"
        text += f"{i}. {status} {p['points']} pts - ${p['price']}"
        if p.get("bonus", 0) > 0:
            text += f" (+{p['bonus']})"
        text += "\n"
    text += "\n/addpackage pts price bonus\n/editpackage num pts price bonus\n/togglepackage num\n/delpackage num"
    bot.send_message(m.from_user.id, text, parse_mode="Markdown")

@bot.message_handler(commands=["addpackage", "editpackage", "togglepackage", "delpackage"])
def pkg_commands(m):
    if not is_admin(m.from_user.id):
        return
    
    cmd = m.text.split()[0][1:]
    pkgs = get_points_packages()
    
    try:
        if cmd == "addpackage":
            _, pts, price, bonus = m.text.split()
            pkgs.append({"points": int(pts), "price": int(price), "bonus": int(bonus), "active": True})
            save_points_packages(pkgs)
            bot.send_message(m.from_user.id, f"✅ Added: {pts} pts for ${price}")
        elif cmd == "editpackage":
            _, num, pts, price, bonus = m.text.split()
            num = int(num) - 1
            if 0 <= num < len(pkgs):
                pkgs[num].update({"points": int(pts), "price": int(price), "bonus": int(bonus)})
                save_points_packages(pkgs)
                bot.send_message(m.from_user.id, f"✅ Package {num+1} updated!")
            else:
                bot.send_message(m.from_user.id, "❌ Invalid number!")
        elif cmd == "togglepackage":
            _, num = m.text.split()
            num = int(num) - 1
            if 0 <= num < len(pkgs):
                pkgs[num]["active"] = not pkgs[num].get("active", True)
                save_points_packages(pkgs)
                status = "activated" if pkgs[num]["active"] else "deactivated"
                bot.send_message(m.from_user.id, f"✅ Package {num+1} {status}!")
            else:
                bot.send_message(m.from_user.id, "❌ Invalid number!")
        elif cmd == "delpackage":
            _, num = m.text.split()
            num = int(num) - 1
            if 0 <= num < len(pkgs):
                removed = pkgs.pop(num)
                save_points_packages(pkgs)
                bot.send_message(m.from_user.id, f"✅ Removed: {removed['points']} pts")
            else:
                bot.send_message(m.from_user.id, "❌ Invalid number!")
    except:
        bot.send_message(m.from_user.id, f"❌ Use: /{cmd} ...")

# =========================
# 👥 ADMIN MANAGEMENT
# =========================
@bot.message_handler(func=lambda m: m.text == "👥 Admin Management" and is_admin(m.from_user.id))
def admin_management_cmd(m):
    if m.from_user.id != ADMIN_ID:
        bot.send_message(m.from_user.id, "❌ Owner only!")
        return
    
    admins = get_all_admins()
    text = "👥 **Admins**\n\n"
    for a in admins:
        owner = " 👑" if a["_id"] == ADMIN_ID else ""
        text += f"• `{a['_id']}`{owner}\n"
    text += "\n/addadmin id\n/removeadmin id\n/listadmins"
    bot.send_message(m.from_user.id, text, parse_mode="Markdown")

@bot.message_handler(commands=["addadmin", "removeadmin", "listadmins"])
def admin_commands(m):
    if m.from_user.id != ADMIN_ID:
        return
    
    cmd = m.text.split()[0][1:]
    
    if cmd == "listadmins":
        admins = get_all_admins()
        text = "👥 Admins:\n"
        for a in admins:
            text += f"• `{a['_id']}`\n"
        bot.send_message(m.from_user.id, text, parse_mode="Markdown")
        return
    
    try:
        _, uid = m.text.split()
        uid = int(uid)
        
        if cmd == "addadmin":
            if admins_col.find_one({"_id": uid}):
                bot.send_message(m.from_user.id, "❌ Already admin!")
                return
            admins_col.insert_one({"_id": uid, "added_at": time.time()})
            bot.send_message(m.from_user.id, f"✅ Admin {uid} added!")
            try:
                bot.send_message(uid, "🎉 You are now an admin!")
            except:
                pass
        else:
            if uid == ADMIN_ID:
                bot.send_message(m.from_user.id, "❌ Cannot remove owner!")
                return
            result = admins_col.delete_one({"_id": uid})
            if result.deleted_count > 0:
                bot.send_message(m.from_user.id, f"✅ Admin {uid} removed!")
            else:
                bot.send_message(m.from_user.id, "❌ Not an admin!")
    except:
        bot.send_message(m.from_user.id, f"❌ Use: /{cmd} user_id")

# =========================
# 📞 SET CONTACTS
# =========================
@bot.message_handler(func=lambda m: m.text == "📞 Set Contacts" and is_admin(m.from_user.id))
def set_contacts_menu(m):
    kb = InlineKeyboardMarkup(row_width=1)
    kb.add(InlineKeyboardButton("💰 Points Contact", callback_data="set_points"), InlineKeyboardButton("⭐ VIP Contact", callback_data="set_vip"), InlineKeyboardButton("📋 View", callback_data="view_contacts"))
    bot.send_message(m.from_user.id, "📞 **Contacts**", reply_markup=kb)

@bot.callback_query_handler(func=lambda c: c.data == "set_points")
def set_points_contact(c):
    msg = bot.send_message(c.from_user.id, "💰 Send @username or link:\nSend 'none' to remove", parse_mode="Markdown")
    bot.register_next_step_handler(msg, save_points_contact)
    bot.answer_callback_query(c.id)

def save_points_contact(m):
    if m.text.lower() == "none":
        set_config("contact_username", None)
        set_config("contact_link", None)
    elif m.text.startswith("http"):
        set_config("contact_link", m.text)
        set_config("contact_username", None)
    elif m.text.startswith("@"):
        set_config("contact_username", m.text)
        set_config("contact_link", None)
    else:
        bot.send_message(m.from_user.id, "❌ Invalid!")
        return
    bot.send_message(m.from_user.id, "✅ Updated!", reply_markup=admin_menu())

@bot.callback_query_handler(func=lambda c: c.data == "set_vip")
def set_vip_contact(c):
    msg = bot.send_message(c.from_user.id, "⭐ Send @username or link:\nSend 'none' to remove", parse_mode="Markdown")
    bot.register_next_step_handler(msg, save_vip_contact)
    bot.answer_callback_query(c.id)

def save_vip_contact(m):
    if m.text.lower() == "none":
        set_config("vip_contact", None)
    elif m.text.startswith("http") or m.text.startswith("@"):
        set_config("vip_contact", m.text)
    else:
        bot.send_message(m.from_user.id, "❌ Invalid!")
        return
    bot.send_message(m.from_user.id, "✅ Updated!", reply_markup=admin_menu())

@bot.callback_query_handler(func=lambda c: c.data == "view_contacts")
def view_contacts_cb(c):
    cfg = get_config()
    points = cfg.get("contact_username") or cfg.get("contact_link") or "Not set"
    vip = cfg.get("vip_contact") or "Not set"
    bot.edit_message_text(f"📞 Points: {points}\n⭐ VIP: {vip}", c.from_user.id, c.message.message_id, parse_mode="Markdown")
    bot.answer_callback_query(c.id)

# =========================
# 🔘 BUTTON MANAGER (BUTTON-BASED)
# =========================
_button_wizard = {}

def button_manager_keyboard():
    kb = InlineKeyboardMarkup(row_width=2)
    kb.add(
        InlineKeyboardButton("➕ Add Link Button", callback_data="btnmgr|add|link"),
        InlineKeyboardButton("📁 Add Folder Button", callback_data="btnmgr|add|folder"),
        InlineKeyboardButton("➖ Remove Button", callback_data="btnmgr|remove"),
        InlineKeyboardButton("📋 View Buttons", callback_data="btnmgr|view"),
    )
    kb.add(InlineKeyboardButton("❌ Close", callback_data="btnmgr|close"))
    return kb

@bot.message_handler(func=lambda m: m.text == "🔘 Button Manager" and is_admin(m.from_user.id))
def button_manager_cmd(m):
    bot.send_message(m.from_user.id, "🔘 **Button Manager**\n\nChoose an action:", reply_markup=button_manager_keyboard(), parse_mode="Markdown")

@bot.callback_query_handler(func=lambda c: c.data.startswith("btnmgr|"))
def button_manager_callback(c):
    if not is_admin(c.from_user.id):
        return bot.answer_callback_query(c.id, "Admin only", True)
    parts = c.data.split("|")
    action = parts[1]
    try:
        if action == "close":
            bot.delete_message(c.from_user.id, c.message.message_id)
            return bot.answer_callback_query(c.id)
        if action == "view":
            buttons = get_custom_buttons()
            text = "🔘 **Custom Buttons**\n\n" + ("\n".join(f"{i+1}. {b.get('text')} — {b.get('type')}" for i,b in enumerate(buttons)) if buttons else "No custom buttons.")
            bot.send_message(c.from_user.id, text, parse_mode="Markdown")
            return bot.answer_callback_query(c.id, "List opened")
        if action == "remove":
            buttons = get_custom_buttons()
            if not buttons:
                return bot.answer_callback_query(c.id, "No custom buttons", True)
            kb = InlineKeyboardMarkup(row_width=1)
            for i,b in enumerate(buttons):
                kb.add(InlineKeyboardButton(f"❌ {b.get('text')}", callback_data=f"btnmgr|delete|{i}"))
            bot.send_message(c.from_user.id, "Select a button to remove:", reply_markup=kb)
            return bot.answer_callback_query(c.id)
        if action == "delete":
            idx = int(parts[2]); buttons = get_custom_buttons()
            if idx < 0 or idx >= len(buttons): raise ValueError("Button no longer exists")
            removed = buttons.pop(idx); set_config("custom_buttons", buttons)
            bot.edit_message_text(f"✅ Process Complete\nRemoved: {removed.get('text')}", c.from_user.id, c.message.message_id)
            return bot.answer_callback_query(c.id, "Removed")
        if action == "add":
            typ = parts[2]
            _button_wizard[c.from_user.id] = {"type": typ}
            msg = bot.send_message(c.from_user.id, "Send the button name/text:")
            bot.register_next_step_handler(msg, button_name_step)
            return bot.answer_callback_query(c.id, "Continue in chat")
    except Exception as exc:
        bot.answer_callback_query(c.id, f"Error: {exc}", True)
        bot.send_message(c.from_user.id, f"❌ Process Failed\n{exc}", reply_markup=admin_menu())

def button_name_step(m):
    try:
        state = _button_wizard.get(m.from_user.id)
        if not state: raise ValueError("Session expired")
        text = (m.text or "").strip()
        if not text or len(text) > 50: raise ValueError("Button name must be 1-50 characters")
        state["text"] = text
        prompt = "Send link, @username, username, or t.me link:" if state["type"] == "link" else "Send the folder number:"
        msg = bot.send_message(m.from_user.id, prompt)
        bot.register_next_step_handler(msg, button_data_step)
    except Exception as exc:
        bot.send_message(m.from_user.id, f"❌ Process Failed\n{exc}", reply_markup=admin_menu())

def button_data_step(m):
    try:
        state = _button_wizard.pop(m.from_user.id, None)
        if not state: raise ValueError("Session expired")
        data = (m.text or "").strip()
        if state["type"] == "link":
            data = normalize_url_or_username(data)
        else:
            if not data.isdigit() or not fs.get_by_number(int(data)): raise ValueError("Folder number not found")
        add_custom_button(state["text"], state["type"], data)
        bot.send_message(m.from_user.id, f"✅ Process Complete\nButton added: {state['text']}", reply_markup=admin_menu())
    except Exception as exc:
        bot.send_message(m.from_user.id, f"❌ Process Failed\n{exc}", reply_markup=admin_menu())

# =========================
# 📢 FORCE JOIN: CHANNELS + GROUPS
# =========================
def force_join_menu_keyboard():
    kb = InlineKeyboardMarkup(row_width=2)
    kb.add(
        InlineKeyboardButton("➕ Add Channel", callback_data="force|add|channel"),
        InlineKeyboardButton("➕ Add Group", callback_data="force|add|group"),
        InlineKeyboardButton("➖ Remove Channel", callback_data="force|remove|channel"),
        InlineKeyboardButton("➖ Remove Group", callback_data="force|remove|group"),
        InlineKeyboardButton("📋 View Required Chats", callback_data="force|view"),
    )
    kb.add(InlineKeyboardButton("❌ Close", callback_data="force|close"))
    return kb

@bot.message_handler(func=lambda m: m.text == "📢 Force Join" and is_admin(m.from_user.id))
def force_join_menu(m):
    bot.send_message(m.from_user.id, "📢 **Force Join Manager**\n\nFor private groups, use the numeric chat ID (`-100...`). The bot must be an admin.", reply_markup=force_join_menu_keyboard(), parse_mode="Markdown")

@bot.callback_query_handler(func=lambda c: c.data.startswith("force|"))
def force_join_callback(c):
    if not is_admin(c.from_user.id): return bot.answer_callback_query(c.id, "Admin only", True)
    _, action, *rest = c.data.split("|")
    try:
        if action == "close":
            bot.delete_message(c.from_user.id, c.message.message_id); return bot.answer_callback_query(c.id)
        if action == "view":
            cfg=get_config(); channels=cfg.get("force_channels",[]); groups=cfg.get("force_groups",[])
            text="📢 **Required Channels**\n"+("\n".join(channels) if channels else "None")+"\n\n👥 **Required Groups**\n"+("\n".join(map(str,groups)) if groups else "None")
            bot.send_message(c.from_user.id,text,parse_mode="Markdown"); return bot.answer_callback_query(c.id,"Opened")
        typ=rest[0]
        key="force_channels" if typ=="channel" else "force_groups"
        if action=="add":
            _button_wizard[c.from_user.id]={"force_key":key,"force_type":typ}
            msg=bot.send_message(c.from_user.id, "Send @username or numeric chat ID (`-100...`):", parse_mode="Markdown")
            bot.register_next_step_handler(msg, force_add_step); return bot.answer_callback_query(c.id,"Continue in chat")
        if action=="remove":
            items=get_config().get(key,[])
            if not items:return bot.answer_callback_query(c.id,"Nothing to remove",True)
            kb=InlineKeyboardMarkup(row_width=1)
            for i,item in enumerate(items):kb.add(InlineKeyboardButton(f"❌ {item}",callback_data=f"force|delete|{typ}|{i}"))
            bot.send_message(c.from_user.id,"Select an item to remove:",reply_markup=kb); return bot.answer_callback_query(c.id)
        if action=="delete":
            typ,index=rest[0],int(rest[1]); key="force_channels" if typ=="channel" else "force_groups"; items=get_config().get(key,[])
            if index<0 or index>=len(items):raise ValueError("Item no longer exists")
            removed=items.pop(index);set_config(key,items);bot.edit_message_text(f"✅ Process Complete\nRemoved: {removed}",c.from_user.id,c.message.message_id);return bot.answer_callback_query(c.id,"Removed")
    except Exception as exc:
        bot.answer_callback_query(c.id,f"Error: {exc}",True);bot.send_message(c.from_user.id,f"❌ Process Failed\n{exc}",reply_markup=admin_menu())

def force_add_step(m):
    try:
        state=_button_wizard.pop(m.from_user.id,None)
        if not state or "force_key" not in state:raise ValueError("Session expired")
        value=(m.text or "").strip()
        if not value.startswith("@"):
            try:value=str(int(value))
            except:raise ValueError("Use @username or numeric chat ID")
        # Verify bot can access the chat.
        chat=bot.get_chat(value)
        bot_member=bot.get_chat_member(chat.id,bot.get_me().id)
        if bot_member.status not in ("administrator","creator"):raise ValueError("Make the bot admin in that channel/group first")
        items=get_config().get(state["force_key"],[])
        normalized=str(chat.id) if value.lstrip("-").isdigit() else value
        if normalized in items:raise ValueError("Already added")
        items.append(normalized);set_config(state["force_key"],items)
        bot.send_message(m.from_user.id,f"✅ Process Complete\nAdded: {normalized}",reply_markup=admin_menu())
    except Exception as exc:
        bot.send_message(m.from_user.id,f"❌ Process Failed\n{exc}",reply_markup=admin_menu())

# =========================
# 👥 NEW USER JOIN NOTIFICATIONS
# =========================
def join_notification_keyboard():
    cfg=get_cached_config(); kb=InlineKeyboardMarkup(row_width=2)
    kb.add(InlineKeyboardButton("➕ Set Join Group",callback_data="joinnotify|set"),InlineKeyboardButton("🗑 Remove Join Group",callback_data="joinnotify|remove"))
    kb.add(InlineKeyboardButton(f"👤 Join Alerts: {'ON' if cfg.get('join_notify_enabled',True) else 'OFF'}",callback_data="joinnotify|togglejoin"))
    kb.add(InlineKeyboardButton("➕ Set Method Group",callback_data="joinnotify|setmethod"),InlineKeyboardButton("🗑 Remove Method Group",callback_data="joinnotify|removemethod"))
    kb.add(InlineKeyboardButton(f"🔔 Method Alerts: {'ON' if cfg.get('method_notify_enabled',True) else 'OFF'}",callback_data="joinnotify|togglemethod"))
    kb.add(InlineKeyboardButton("📋 View Settings",callback_data="joinnotify|view"))
    return kb

@bot.message_handler(func=lambda m:m.text=="👥 Join Notifications" and is_admin(m.from_user.id))
def join_notification_menu(m):
    bot.send_message(m.from_user.id,"👥 **Notification Settings**\n\nAccepts @username, username, t.me link, or numeric ID. Bot must be admin.",reply_markup=join_notification_keyboard(),parse_mode="Markdown")

@bot.callback_query_handler(func=lambda c:c.data.startswith("joinnotify|"))
def join_notification_cb(c):
    if not is_admin(c.from_user.id):return bot.answer_callback_query(c.id,"Admin only",True)
    action=c.data.split("|")[1]
    try:
        cfg=get_config()
        if action=="view":
            text=f"👤 Join group: `{cfg.get('join_notify_group') or 'Not set'}`\nJoin alerts: **{'ON' if cfg.get('join_notify_enabled',True) else 'OFF'}**\n\n🔔 Method group: `{cfg.get('method_notify_group') or cfg.get('join_notify_group') or 'Not set'}`\nMethod alerts: **{'ON' if cfg.get('method_notify_enabled',True) else 'OFF'}**"
            bot.send_message(c.from_user.id,text,parse_mode="Markdown",reply_markup=join_notification_keyboard());return bot.answer_callback_query(c.id)
        if action=="remove": set_config("join_notify_group",None);admin_success(c.from_user.id,"Join notification group removed");return bot.answer_callback_query(c.id,"Removed")
        if action=="removemethod": set_config("method_notify_group",None);admin_success(c.from_user.id,"Method notification group removed");return bot.answer_callback_query(c.id,"Removed")
        if action=="togglejoin": set_config("join_notify_enabled",not cfg.get("join_notify_enabled",True));bot.edit_message_reply_markup(c.from_user.id,c.message.message_id,reply_markup=join_notification_keyboard());return bot.answer_callback_query(c.id,"Updated")
        if action=="togglemethod": set_config("method_notify_enabled",not cfg.get("method_notify_enabled",True));bot.edit_message_reply_markup(c.from_user.id,c.message.message_id,reply_markup=join_notification_keyboard());return bot.answer_callback_query(c.id,"Updated")
        _join_notify_pending[c.from_user.id]="method" if action=="setmethod" else "join"
        msg=bot.send_message(c.from_user.id,"Send group @username, username, t.me link, or numeric ID:");bot.register_next_step_handler(msg,save_join_notification_group);bot.answer_callback_query(c.id,"Continue in chat")
    except Exception as exc: admin_error(c.from_user.id,exc)

_join_notify_pending={}
def save_join_notification_group(m):
    try:
        value=normalize_chat_reference(m.text); chat=bot.get_chat(value); member=bot.get_chat_member(chat.id,bot.get_me().id)
        if member.status not in ("administrator","creator"):raise ValueError("Bot must be admin in the group")
        kind=_join_notify_pending.pop(m.from_user.id,"join")
        key="method_notify_group" if kind=="method" else "join_notify_group"
        set_config(key,chat.id)
        admin_success(m.from_user.id,f"{'Method' if kind=='method' else 'Join'} notification group set: {chat.id}")
        bot.send_message(chat.id,f"✅ This group will receive {'method upload/update' if kind=='method' else 'new-user join'} notifications.")
    except Exception as exc: admin_error(m.from_user.id,exc)

# =========================
# ⚙️ SETTINGS
# =========================
@bot.message_handler(func=lambda m: m.text == "⚙️ Settings" and is_admin(m.from_user.id))
def settings_cmd(m):
    kb = InlineKeyboardMarkup(row_width=2)
    kb.add(InlineKeyboardButton("⭐ VIP Msg", callback_data="set_vip_msg"), InlineKeyboardButton("🏠 Welcome", callback_data="set_welcome"), InlineKeyboardButton("💰 Ref Reward", callback_data="set_reward"), InlineKeyboardButton("💵 Points/$", callback_data="set_ppd"))
    bot.send_message(m.from_user.id, "⚙️ **Settings**", reply_markup=kb)

@bot.callback_query_handler(func=lambda c: c.data == "set_vip_msg")
def set_vip_msg_cb(c):
    msg = bot.send_message(c.from_user.id, "Send new VIP message:", parse_mode="Markdown")
    bot.register_next_step_handler(msg, lambda x: update_config("vip_msg", x.text) or bot.send_message(x.from_user.id, "✅ Updated!", reply_markup=admin_kb()))
    bot.answer_callback_query(c.id)

@bot.callback_query_handler(func=lambda c: c.data == "set_welcome")
def set_welcome_cb(c):
    msg = bot.send_message(c.from_user.id, "Send new welcome message:", parse_mode="Markdown")
    bot.register_next_step_handler(msg, lambda x: update_config("welcome", x.text) or bot.send_message(x.from_user.id, "✅ Updated!", reply_markup=admin_kb()))
    bot.answer_callback_query(c.id)

@bot.callback_query_handler(func=lambda c: c.data == "set_reward")
def set_reward_cb(c):
    current = get_config().get("ref_reward", 5)
    msg = bot.send_message(c.from_user.id, f"Current: {current}\nSend new amount:", parse_mode="Markdown")
    bot.register_next_step_handler(msg, lambda x: update_config("ref_reward", int(x.text)) or bot.send_message(x.from_user.id, f"✅ Set to {x.text} points!", reply_markup=admin_kb()) if x.text.isdigit() else bot.send_message(x.from_user.id, "❌ Invalid!"))
    bot.answer_callback_query(c.id)

@bot.callback_query_handler(func=lambda c: c.data == "set_ppd")
def set_ppd_cb(c):
    current = get_config().get("points_per_dollar", 100)
    msg = bot.send_message(c.from_user.id, f"Current: {current} pts = $1\nSend new value:", parse_mode="Markdown")
    bot.register_next_step_handler(msg, lambda x: update_config("points_per_dollar", int(x.text)) or bot.send_message(x.from_user.id, f"✅ Set to {x.text} pts = $1!", reply_markup=admin_kb()) if x.text.isdigit() else bot.send_message(x.from_user.id, "❌ Invalid!"))
    bot.answer_callback_query(c.id)

# =========================
# 📊 STATS (FIXED VIP COUNT)
# =========================
@bot.message_handler(func=lambda m: m.text == "📊 Stats" and is_admin(m.from_user.id))
def stats_cmd(m):
    total = users_col.count_documents({})
    vip = users_col.count_documents({"vip": True})
    free = total - vip
    
    all_u = list(users_col.find({}))
    points = sum(u.get("points", 0) for u in all_u)
    earned = sum(u.get("total_points_earned", 0) for u in all_u)
    spent = sum(u.get("total_points_spent", 0) for u in all_u)
    refs = sum(u.get("refs", 0) for u in all_u)
    purchases = sum(len(u.get("purchased_methods", [])) for u in all_u)
    
    free_f = folders_col.count_documents({"cat": "free"})
    vip_f = folders_col.count_documents({"cat": "vip"})
    apps_f = folders_col.count_documents({"cat": "apps"})
    svc_f = folders_col.count_documents({"cat": "services"})
    
    total_c, used_c, _, _ = codesys.get_stats()
    
    text = f"📊 **ZEDOX STATISTICS**\n\n"
    text += f"👥 **USERS:**\n"
    text += f"┌ Total Users: `{total}`\n"
    text += f"├ VIP Users: `{vip}`\n"
    text += f"└ Free Users: `{free}`\n\n"
    
    text += f"💰 **POINTS:**\n"
    text += f"┌ Current Total: `{points:,}`\n"
    text += f"├ Total Earned: `{earned:,}`\n"
    text += f"├ Total Spent: `{spent:,}`\n"
    text += f"└ Avg per User: `{points//total if total > 0 else 0}`\n\n"
    
    text += f"📚 **CONTENT:**\n"
    text += f"┌ FREE METHODS: `{free_f}`\n"
    text += f"├ VIP METHODS: `{vip_f}`\n"
    text += f"├ PREMIUM APPS: `{apps_f}`\n"
    text += f"└ SERVICES: `{svc_f}`\n\n"
    
    text += f"📈 **ACTIVITY:**\n"
    text += f"┌ Total Referrals: `{refs}`\n"
    text += f"├ Total Purchases: `{purchases}`\n"
    text += f"├ Total Codes: `{total_c}`\n"
    text += f"├ Used Codes: `{used_c}`\n"
    text += f"└ Unused Codes: `{total_c - used_c}`"
    
    bot.send_message(m.from_user.id, text, parse_mode="Markdown")

# =========================
# 📢 BROADCAST
# =========================
@bot.message_handler(func=lambda m: m.text == "📢 Broadcast" and is_admin(m.from_user.id))
def broadcast_cmd(m):
    kb = InlineKeyboardMarkup(row_width=2)
    kb.add(InlineKeyboardButton("All", callback_data="bc_all"), InlineKeyboardButton("VIP", callback_data="bc_vip"), InlineKeyboardButton("Free", callback_data="bc_free"))
    bot.send_message(m.from_user.id, "📢 Broadcast to:", reply_markup=kb)

@bot.callback_query_handler(func=lambda c: c.data.startswith("bc_"))
def broadcast_target_cb(c):
    target = c.data[3:]
    msg = bot.send_message(c.from_user.id, f"Send message to {target.upper()} users:", parse_mode="Markdown")
    bot.register_next_step_handler(msg, lambda x: send_broadcast(x, target))
    bot.answer_callback_query(c.id)

def send_broadcast(m, target):
    query = {}
    if target == "vip":
        query = {"vip": True}
    elif target == "free":
        query = {"vip": False}
    
    users = list(users_col.find(query))
    if not users:
        bot.send_message(m.from_user.id, "❌ No users!")
        return
    
    status = bot.send_message(m.from_user.id, f"📤 Broadcasting to {len(users)} users...")
    sent, failed = 0, 0
    
    for u in users:
        try:
            uid = int(u["_id"])
            if m.content_type == "text":
                bot.send_message(uid, m.text, parse_mode="HTML")
            elif m.content_type == "photo":
                bot.send_photo(uid, m.photo[-1].file_id, caption=m.caption, parse_mode="HTML")
            elif m.content_type == "video":
                bot.send_video(uid, m.video.file_id, caption=m.caption, parse_mode="HTML")
            elif m.content_type == "document":
                bot.send_document(uid, m.document.file_id, caption=m.caption, parse_mode="HTML")
            sent += 1
            if sent % 20 == 0:
                time.sleep(0.3)
        except:
            failed += 1
    
    bot.edit_message_text(f"✅ Done!\n📤 Sent: {sent}\n❌ Failed: {failed}", m.from_user.id, status.message_id)

# =========================
# 🔔 NOTIFY
# =========================
@bot.message_handler(func=lambda m: m.text == "🔔 Notify" and is_admin(m.from_user.id))
def toggle_notify_cmd(m):
    cfg=get_config(); new=not cfg.get("method_notify_enabled",True);set_config("method_notify_enabled",new)
    bot.send_message(m.from_user.id,f"🔔 Method upload/update notifications: {'ON' if new else 'OFF'}",reply_markup=admin_menu())

# =========================
# 🏦 BINANCE SETTINGS
# =========================
@bot.message_handler(func=lambda m: m.text == "🏦 Binance Settings" and is_admin(m.from_user.id))
def binance_settings_menu(m):
    kb = InlineKeyboardMarkup(row_width=1)
    kb.add(InlineKeyboardButton("💰 Coin", callback_data="set_binance_coin"), InlineKeyboardButton("🌐 Network", callback_data="set_binance_network"), InlineKeyboardButton("📍 Address", callback_data="set_binance_address"), InlineKeyboardButton("📝 Memo", callback_data="set_binance_memo"), InlineKeyboardButton("📋 View", callback_data="view_binance_settings"))
    bot.send_message(m.from_user.id, "🏦 **Binance**", reply_markup=kb)

@bot.callback_query_handler(func=lambda c: c.data == "set_binance_coin")
def set_binance_coin_cb(c):
    msg = bot.send_message(c.from_user.id, f"Coin (USDT, BUSD, BTC):\nCurrent: {get_config().get('binance_coin', 'USDT')}", parse_mode="Markdown")
    bot.register_next_step_handler(msg, lambda x: set_config("binance_coin", x.text.upper()) or bot.send_message(x.from_user.id, f"✅ Set to {x.text.upper()}", reply_markup=admin_menu()))
    bot.answer_callback_query(c.id)

@bot.callback_query_handler(func=lambda c: c.data == "set_binance_network")
def set_binance_network_cb(c):
    msg = bot.send_message(c.from_user.id, f"Network (TRC20, BEP20, ERC20):\nCurrent: {get_config().get('binance_network', 'TRC20')}", parse_mode="Markdown")
    bot.register_next_step_handler(msg, lambda x: set_config("binance_network", x.text.upper()) or bot.send_message(x.from_user.id, f"✅ Set to {x.text.upper()}", reply_markup=admin_menu()))
    bot.answer_callback_query(c.id)

@bot.callback_query_handler(func=lambda c: c.data == "set_binance_address")
def set_binance_address_cb(c):
    msg = bot.send_message(c.from_user.id, f"Address:\nCurrent: {get_config().get('binance_address', 'Not set')}", parse_mode="Markdown")
    bot.register_next_step_handler(msg, lambda x: set_config("binance_address", x.text) or bot.send_message(x.from_user.id, f"✅ Address saved!", reply_markup=admin_menu()))
    bot.answer_callback_query(c.id)

@bot.callback_query_handler(func=lambda c: c.data == "set_binance_memo")
def set_binance_memo_cb(c):
    msg = bot.send_message(c.from_user.id, f"Memo/Tag (send 'none' to clear):\nCurrent: {get_config().get('binance_memo', 'None')}", parse_mode="Markdown")
    bot.register_next_step_handler(msg, lambda x: set_config("binance_memo", "" if x.text.lower() == "none" else x.text) or bot.send_message(x.from_user.id, f"✅ Memo saved!", reply_markup=admin_menu()))
    bot.answer_callback_query(c.id)

@bot.callback_query_handler(func=lambda c: c.data == "view_binance_settings")
def view_binance_settings_cb(c):
    cfg = get_config()
    text = f"🏦 **Binance**\n\n💰 Coin: {cfg.get('binance_coin', 'USDT')}\n🌐 Network: {cfg.get('binance_network', 'TRC20')}\n📍 Address: `{cfg.get('binance_address', 'Not set')}`\n📝 Memo: `{cfg.get('binance_memo', 'None') or 'None'}`\n📸 Screenshot: {'Yes' if cfg.get('require_screenshot', True) else 'No'}"
    bot.edit_message_text(text, c.from_user.id, c.message.message_id, parse_mode="Markdown")
    bot.answer_callback_query(c.id)

# =========================
# 📸 SCREENSHOT
# =========================
@bot.message_handler(func=lambda m: m.text == "📸 Screenshot" and is_admin(m.from_user.id))
def screenshot_setting_menu(m):
    cfg = get_config()
    current = cfg.get("require_screenshot", True)
    status = "✅ ENABLED" if current else "❌ DISABLED"
    kb = InlineKeyboardMarkup()
    kb.add(InlineKeyboardButton("🔘 Toggle", callback_data="toggle_screenshot"))
    bot.send_message(m.from_user.id, f"📸 **Screenshot**\n\n{status}\n\nRequire screenshot for payments.", reply_markup=kb)

@bot.callback_query_handler(func=lambda c: c.data == "toggle_screenshot")
def toggle_screenshot_cb(c):
    cfg = get_config()
    current = cfg.get("require_screenshot", True)
    set_config("require_screenshot", not current)
    new_status = "ENABLED" if not current else "DISABLED"
    bot.answer_callback_query(c.id, f"Screenshot {new_status}!")
    bot.edit_message_text(f"✅ Screenshot {new_status}!", c.from_user.id, c.message.message_id)
    bot.send_message(c.from_user.id, "Returning...", reply_markup=admin_menu())

# =========================
# 💳 PAYMENT METHODS
# =========================
@bot.message_handler(func=lambda m: m.text == "💳 Payment Methods" and is_admin(m.from_user.id))
def payment_methods_menu(m):
    methods = get_config().get("payment_methods", ["💳 Binance", "💵 USDT"])
    text = "💳 **Payment Methods**\n\n"
    for i, mtd in enumerate(methods, 1):
        text += f"{i}. {mtd}\n"
    text += "\n/addmethod name\n/removemethod number\n/listmethods"
    bot.send_message(m.from_user.id, text, parse_mode="Markdown")

@bot.message_handler(commands=["addmethod", "removemethod", "listmethods"])
def payment_commands(m):
    if not is_admin(m.from_user.id):
        return
    
    cmd = m.text.split()[0][1:]
    methods = get_config().get("payment_methods", ["💳 Binance", "💵 USDT"])
    
    if cmd == "listmethods":
        text = "💳 **Methods**\n\n"
        for i, mtd in enumerate(methods, 1):
            text += f"{i}. {mtd}\n"
        bot.send_message(m.from_user.id, text, parse_mode="Markdown")
        return
    
    try:
        if cmd == "addmethod":
            method = m.text.replace("/addmethod", "").strip()
            if not method:
                bot.send_message(m.from_user.id, "❌ Usage: /addmethod name")
                return
            methods.append(method)
            set_config("payment_methods", methods)
            bot.send_message(m.from_user.id, f"✅ Added: {method}")
        elif cmd == "removemethod":
            _, num = m.text.split()
            num = int(num) - 1
            if 0 <= num < len(methods):
                removed = methods.pop(num)
                set_config("payment_methods", methods)
                bot.send_message(m.from_user.id, f"✅ Removed: {removed}")
            else:
                bot.send_message(m.from_user.id, "❌ Invalid number!")
    except:
        bot.send_message(m.from_user.id, f"❌ Use: /{cmd} ...")

# =========================
# ⚙️ VIP SETTINGS
# =========================
@bot.message_handler(func=lambda m: m.text == "⚙️ VIP Settings" and is_admin(m.from_user.id))
def vip_settings_menu(m):
    kb = InlineKeyboardMarkup(row_width=1)
    kb.add(InlineKeyboardButton("💰 USD Price", callback_data="set_vip_price_usd"), InlineKeyboardButton("💎 Points Price", callback_data="set_vip_price_points"), InlineKeyboardButton("👥 Referral VIP", callback_data="set_ref_vip_count"), InlineKeyboardButton("🛒 Purchase VIP", callback_data="set_ref_purchase_count"), InlineKeyboardButton("📅 Duration", callback_data="set_vip_duration"), InlineKeyboardButton("📋 View", callback_data="view_vip_settings"))
    bot.send_message(m.from_user.id, "⚙️ **VIP Settings**", reply_markup=kb)

@bot.callback_query_handler(func=lambda c: c.data == "set_vip_price_usd")
def set_vip_price_usd_cb(c):
    msg = bot.send_message(c.from_user.id, f"USD Price:\nCurrent: ${get_config().get('vip_price', 50)}", parse_mode="Markdown")
    bot.register_next_step_handler(msg, lambda x: set_config("vip_price", int(x.text)) or bot.send_message(x.from_user.id, f"✅ Set to ${x.text}", reply_markup=admin_menu()) if x.text.isdigit() else bot.send_message(x.from_user.id, "❌ Invalid!"))
    bot.answer_callback_query(c.id)

@bot.callback_query_handler(func=lambda c: c.data == "set_vip_price_points")
def set_vip_price_points_cb(c):
    msg = bot.send_message(c.from_user.id, f"Points Price:\nCurrent: {get_config().get('vip_points_price', 5000)}", parse_mode="Markdown")
    bot.register_next_step_handler(msg, lambda x: set_config("vip_points_price", int(x.text)) or bot.send_message(x.from_user.id, f"✅ Set to {x.text} points", reply_markup=admin_menu()) if x.text.isdigit() else bot.send_message(x.from_user.id, "❌ Invalid!"))
    bot.answer_callback_query(c.id)

@bot.callback_query_handler(func=lambda c: c.data == "set_ref_vip_count")
def set_ref_vip_count_cb(c):
    msg = bot.send_message(c.from_user.id, f"Referrals for VIP:\nCurrent: {get_config().get('referral_vip_count', 50)}", parse_mode="Markdown")
    bot.register_next_step_handler(msg, lambda x: set_config("referral_vip_count", int(x.text)) or bot.send_message(x.from_user.id, f"✅ Set to {x.text} referrals", reply_markup=admin_menu()) if x.text.isdigit() else bot.send_message(x.from_user.id, "❌ Invalid!"))
    bot.answer_callback_query(c.id)

@bot.callback_query_handler(func=lambda c: c.data == "set_ref_purchase_count")
def set_ref_purchase_count_cb(c):
    msg = bot.send_message(c.from_user.id, f"Referral Purchases for VIP:\nCurrent: {get_config().get('referral_purchase_count', 10)}", parse_mode="Markdown")
    bot.register_next_step_handler(msg, lambda x: set_config("referral_purchase_count", int(x.text)) or bot.send_message(x.from_user.id, f"✅ Set to {x.text} purchases", reply_markup=admin_menu()) if x.text.isdigit() else bot.send_message(x.from_user.id, "❌ Invalid!"))
    bot.answer_callback_query(c.id)

@bot.callback_query_handler(func=lambda c: c.data == "set_vip_duration")
def set_vip_duration_cb(c):
    msg = bot.send_message(c.from_user.id, f"VIP Duration (days, 0 = permanent):\nCurrent: {get_config().get('vip_duration_days', 30)}", parse_mode="Markdown")
    bot.register_next_step_handler(msg, lambda x: set_config("vip_duration_days", int(x.text)) or bot.send_message(x.from_user.id, f"✅ Set to {x.text} days" + (" (permanent)" if int(x.text) == 0 else ""), reply_markup=admin_menu()) if x.text.isdigit() else bot.send_message(x.from_user.id, "❌ Invalid!"))
    bot.answer_callback_query(c.id)

@bot.callback_query_handler(func=lambda c: c.data == "view_vip_settings")
def view_vip_settings_cb(c):
    cfg = get_config()
    text = f"📋 **VIP Settings**\n\n💰 USD: ${cfg.get('vip_price', 50)}\n💎 Points: {cfg.get('vip_points_price', 5000)}\n👥 Referrals: {cfg.get('referral_vip_count', 50)}\n🛒 Purchases: {cfg.get('referral_purchase_count', 10)}\n📅 Duration: {cfg.get('vip_duration_days', 30)} days" + (" (permanent)" if cfg.get('vip_duration_days', 30) == 0 else "")
    bot.edit_message_text(text, c.from_user.id, c.message.message_id, parse_mode="Markdown")
    bot.answer_callback_query(c.id)

# =========================
# 🔗 ADD CUSTOM LINK
# =========================
@bot.message_handler(func=lambda m: m.text == "🔗 Add Custom Link" and is_admin(m.from_user.id))
def add_custom_link_cmd(m):
    msg = bot.send_message(m.from_user.id, "🔗 **Add Link**\n\nSend: `text|url`\nExample: `Website|https://example.com`", parse_mode="Markdown")
    bot.register_next_step_handler(msg, add_custom_link_process)

def add_custom_link_process(m):
    try:
        parts = m.text.split("|")
        if len(parts) != 2:
            bot.send_message(m.from_user.id, "❌ Use: text|url")
            return
        text, url = parts[0].strip(), parts[1].strip()
        if not url.startswith(("http://", "https://")):
            url = "https://" + url
        add_custom_button(text, "link", url)
        bot.send_message(m.from_user.id, f"✅ Added: {text}", reply_markup=admin_menu())
    except:
        bot.send_message(m.from_user.id, "❌ Invalid format!")

# =========================
# 📋 VIEW LINKS
# =========================
@bot.message_handler(func=lambda m: m.text == "📋 View Links" and is_admin(m.from_user.id))
def view_links_cmd(m):
    btns = get_custom_buttons()
    if not btns:
        bot.send_message(m.from_user.id, "📋 No buttons!")
        return
    text = "📋 **Buttons**\n\n"
    for i, b in enumerate(btns, 1):
        text += f"{i}. {b['text']} ({b['type']})\n"
    bot.send_message(m.from_user.id, text, parse_mode="Markdown")


# =========================
# 🧩 COMPLETE UPDATE EXTENSIONS
# =========================
TZ_OFFSET_SECONDS = 5 * 3600  # Asia/Karachi
_scheduler_stop = threading.Event()

def now_ts():
    return time.time()

def log_event(action, actor=None, target=None, details=None, level="info"):
    try:
        logs_col.insert_one({"action": action, "actor": str(actor) if actor is not None else None,
                             "target": str(target) if target is not None else None,
                             "details": details or {}, "level": level, "created_at": now_ts()})
    except Exception:
        pass

def done(uid, extra=""):
    bot.send_message(uid, "✅ Done Successfully" + (f"\n{extra}" if extra else ""), reply_markup=admin_menu())

def safe_admin(handler):
    @wraps(handler)
    def wrapped(m, *a, **kw):
        if not is_admin(m.from_user.id): return
        try: return handler(m, *a, **kw)
        except Exception as exc:
            log_event("admin_error", m.from_user.id, details={"error": str(exc), "trace": traceback.format_exc()}, level="error")
            bot.send_message(m.from_user.id, f"❌ {type(exc).__name__}: {exc}")
    return wrapped

def add_point_history(uid, amount, reason, admin_id=None, note=None):
    point_history_col.insert_one({"user_id": str(uid), "amount": int(amount), "reason": reason,
                                  "admin_id": str(admin_id) if admin_id else None, "note": note,
                                  "created_at": now_ts()})
    log_event("points_adjusted", admin_id, uid, {"amount": amount, "reason": reason, "note": note})

def atomic_adjust_points(uid, amount, reason="manual", admin_id=None, note=None):
    uid = str(uid)
    if amount < 0:
        doc = users_col.find_one_and_update({"_id": uid, "points": {"$gte": abs(amount)}},
            {"$inc": {"points": amount, "total_points_spent": abs(amount)}, "$set": {"last_active": now_ts()}},
            return_document=ReturnDocument.AFTER)
    else:
        doc = users_col.find_one_and_update({"_id": uid},
            {"$inc": {"points": amount, "total_points_earned": amount}, "$set": {"last_active": now_ts()}},
            return_document=ReturnDocument.AFTER)
    if doc:
        User._cache.pop(uid, None); User._cache_time.pop(uid, None)
        add_point_history(uid, amount, reason, admin_id, note)
    return doc



@bot.message_handler(func=lambda m: m.text == "💾 Backup/Export" and is_admin(m.from_user.id))
def backup_export_menu(m):
    bot.send_message(m.from_user.id,"💾 **Backup / Export**\n\n`/backup`\n`/export users`\n`/export vip`\n`/export referrals`\n`/export purchases`\n`/export payments`",parse_mode="Markdown")

def send_json_document(uid, name, data):
    raw=json.dumps(data,default=str,ensure_ascii=False,indent=2).encode(); f=io.BytesIO(raw); f.name=name; bot.send_document(uid,f)

@bot.message_handler(commands=["backup","export"])
def backup_export_commands(m):
    if not is_admin(m.from_user.id): return
    try:
        if m.text.startswith('/backup'):
            payload={n:list(db[n].find({})) for n in db.list_collection_names()}; raw=json.dumps(payload,default=str,ensure_ascii=False).encode()
            z=io.BytesIO();
            with zipfile.ZipFile(z,'w',zipfile.ZIP_DEFLATED) as zz: zz.writestr('zedox_backup.json',raw)
            z.seek(0); z.name=f"zedox_backup_{datetime.now().strftime('%Y%m%d_%H%M')}.zip"; bot.send_document(m.from_user.id,z); log_event('backup',m.from_user.id)
        else:
            kind=(m.text.split(maxsplit=1)[1] if len(m.text.split(maxsplit=1))>1 else 'users').lower()
            mapping={'users':(users_col,{}),'vip':(users_col,{'vip':True}),'referrals':(users_col,{'ref':{'$ne':None}}),'purchases':(purchases_col,{}),'payments':(payments_col,{})}
            col,q=mapping.get(kind,mapping['users']); send_json_document(m.from_user.id,f'{kind}.json',list(col.find(q)))
    except Exception as exc: bot.send_message(m.from_user.id,f"❌ Export failed: {exc}")



@bot.message_handler(func=lambda m: m.text == "📣 Auto Posts" and is_admin(m.from_user.id))
def auto_posts_menu(m):
    bot.send_message(m.from_user.id,"📣 **Auto Posts Manager**\n\nChoose an action:",reply_markup=auto_posts_keyboard(),parse_mode="Markdown")

def auto_posts_keyboard():
    kb=InlineKeyboardMarkup(row_width=2)
    kb.add(InlineKeyboardButton("⏱ Every Hours",callback_data="autoui|create|hours"),InlineKeyboardButton("🗓 Daily Time",callback_data="autoui|create|daily"),InlineKeyboardButton("📋 List Posts",callback_data="autoui|list"),InlineKeyboardButton("⏸ Pause",callback_data="autoui|manage|pause"),InlineKeyboardButton("▶️ Resume",callback_data="autoui|manage|resume"),InlineKeyboardButton("🗑 Delete",callback_data="autoui|manage|delete"))
    return kb

_pending_auto={}

def auto_post_item_keyboard(action):
    rows=list(auto_posts_col.find({}).sort("created_at",-1).limit(30));kb=InlineKeyboardMarkup(row_width=1)
    for x in rows:
        label=f"{x.get('channel')} | {x.get('schedule')} {x.get('value')} | {'ON' if x.get('active') else 'OFF'}"
        kb.add(InlineKeyboardButton(label,callback_data=f"autoui|do|{action}|{x['_id']}"))
    return kb

@bot.callback_query_handler(func=lambda c:c.data.startswith("autoui|"))
def auto_ui_callback(c):
    if not is_admin(c.from_user.id):return bot.answer_callback_query(c.id,"Admin only",True)
    parts=c.data.split("|");action=parts[1]
    try:
        if action=="list":
            rows=list(auto_posts_col.find({}).sort("created_at",-1).limit(30));text="📋 **Auto Posts**\n\n"+("\n".join(f"`{x['_id']}`\n{x.get('channel')} — {x.get('schedule')} {x.get('value')} — {'ON' if x.get('active') else 'OFF'}" for x in rows) if rows else "No auto posts.")
            bot.send_message(c.from_user.id,text,parse_mode="Markdown");return bot.answer_callback_query(c.id)
        if action=="manage":
            manage=parts[2];kb=auto_post_item_keyboard(manage)
            if not kb.keyboard:return bot.answer_callback_query(c.id,"No auto posts",True)
            bot.send_message(c.from_user.id,f"Select auto post to {manage}:",reply_markup=kb);return bot.answer_callback_query(c.id)
        if action=="do":
            from bson import ObjectId
            manage,oid=parts[2],ObjectId(parts[3])
            if manage in ("pause","resume"):
                result=auto_posts_col.update_one({"_id":oid},{"$set":{"active":manage=="resume"}})
            else:result=auto_posts_col.delete_one({"_id":oid})
            if not result.modified_count and not getattr(result,"deleted_count",0):raise ValueError("Auto post not found or unchanged")
            bot.edit_message_text(f"✅ Process Complete\nAuto post {manage}d.",c.from_user.id,c.message.message_id);return bot.answer_callback_query(c.id,"Done")
        if action=="create":
            mode=parts[2];_pending_auto[c.from_user.id]={"schedule":"every_hours" if mode=="hours" else "daily"}
            msg=bot.send_message(c.from_user.id,"Send target channel/group @username or numeric ID:");bot.register_next_step_handler(msg,auto_target_step);return bot.answer_callback_query(c.id,"Continue in chat")
    except Exception as exc:bot.answer_callback_query(c.id,f"Error: {exc}",True);bot.send_message(c.from_user.id,f"❌ Process Failed\n{exc}",reply_markup=admin_menu())

def auto_target_step(m):
    try:
        state=_pending_auto.get(m.from_user.id);state["channel"]=normalize_chat_reference(m.text);bot.get_chat(state["channel"])
        prompt="Send interval in hours, for example `2`:" if state["schedule"]=="every_hours" else "Send daily time as `HH:MM`:"
        msg=bot.send_message(m.from_user.id,prompt,parse_mode="Markdown");bot.register_next_step_handler(msg,auto_schedule_step)
    except Exception as exc:_pending_auto.pop(m.from_user.id,None);bot.send_message(m.from_user.id,f"❌ Process Failed\n{exc}",reply_markup=admin_menu())

def auto_schedule_step(m):
    try:
        state=_pending_auto.get(m.from_user.id);value=(m.text or "").strip()
        if state["schedule"]=="every_hours":
            if float(value)<=0:raise ValueError("Hours must be greater than 0")
        else:
            hh,mm=map(int,value.split(":"));
            if not(0<=hh<=23 and 0<=mm<=59):raise ValueError("Time must be HH:MM")
        state["value"]=value;msg=bot.send_message(m.from_user.id,"Now send or forward the post content:");bot.register_next_step_handler(msg,save_auto_post)
    except Exception as exc:_pending_auto.pop(m.from_user.id,None);bot.send_message(m.from_user.id,f"❌ Process Failed\n{exc}",reply_markup=admin_menu())

def _message_payload(m):
    payload={"content_type":m.content_type}
    if m.content_type=="text": payload["text"]=m.text or ""
    elif m.content_type=="photo": payload.update({"file_id":m.photo[-1].file_id,"caption":m.caption or ""})
    elif m.content_type=="video": payload.update({"file_id":m.video.file_id,"caption":m.caption or ""})
    elif m.content_type=="document": payload.update({"file_id":m.document.file_id,"caption":m.caption or ""})
    elif m.content_type=="animation": payload.update({"file_id":m.animation.file_id,"caption":m.caption or ""})
    else: payload.update({"source_chat":m.chat.id,"source_message":m.message_id})
    return payload

def _send_payload(target,payload):
    typ=payload.get("content_type")
    if typ=="text":
        text=payload.get("text","")
        for i in range(0,len(text),4096): bot.send_message(target,text[i:i+4096])
    elif typ=="photo": bot.send_photo(target,payload["file_id"],caption=payload.get("caption") or None)
    elif typ=="video": bot.send_video(target,payload["file_id"],caption=payload.get("caption") or None)
    elif typ=="document": bot.send_document(target,payload["file_id"],caption=payload.get("caption") or None)
    elif typ=="animation": bot.send_animation(target,payload["file_id"],caption=payload.get("caption") or None)
    else: bot.copy_message(target,payload["source_chat"],payload["source_message"])

def save_auto_post(m):
    try:
        x=_pending_auto.pop(m.from_user.id,None)
        if not x:raise ValueError("Session expired. Open Auto Posts and start again")
        now=now_ts()
        if x['schedule']=='every_hours':next_run=now+float(x['value'])*3600
        else:
            hh,mm=map(int,x['value'].split(':'));local=datetime.utcfromtimestamp(now+TZ_OFFSET_SECONDS);nxt=local.replace(hour=hh,minute=mm,second=0,microsecond=0)
            if nxt<=local:nxt+=timedelta(days=1)
            next_run=nxt.timestamp()-TZ_OFFSET_SECONDS
        payload=_message_payload(m)
        _send_payload(x['channel'],payload)  # test immediately
        auto_posts_col.insert_one({**x,'payload':payload,'next_run':next_run,'active':True,'created_at':now})
        admin_success(m.from_user.id,"Auto post created and test message sent")
    except Exception as exc: admin_error(m.from_user.id,exc)

@bot.message_handler(func=lambda m: m.text == "📥 Auto Import" and is_admin(m.from_user.id))
def auto_import_menu(m):
    bot.send_message(m.from_user.id,"📥 **Auto Import / Upload**\n\nChoose an action:",reply_markup=auto_import_keyboard(),parse_mode="Markdown")

def auto_import_keyboard():
    kb=InlineKeyboardMarkup(row_width=2)
    kb.add(
        InlineKeyboardButton("➕ Add Source", callback_data="importui|sourceadd"),
        InlineKeyboardButton("➖ Remove Source", callback_data="importui|sourceremove"),
        InlineKeyboardButton("📋 View Sources", callback_data="importui|sourcelist"),
        InlineKeyboardButton("📤 Import/Upload Method", callback_data="importui|method"),
        InlineKeyboardButton("🆓 Set FREE by Link/ID", callback_data="importui|setfree"),
        InlineKeyboardButton("💎 Set VIP by Link/ID", callback_data="importui|setvip"),
        InlineKeyboardButton("🆓 Use Recent Chat as FREE", callback_data="importui|recentfree"),
        InlineKeyboardButton("💎 Use Recent Chat as VIP", callback_data="importui|recentvip"),
        InlineKeyboardButton("📋 View Auto Channels", callback_data="importui|viewauto"),
    )
    return kb

_import_state={}
@bot.callback_query_handler(func=lambda c:c.data.startswith("importui|"))
def import_ui_callback(c):
    if not is_admin(c.from_user.id):return bot.answer_callback_query(c.id,"Admin only",True)
    action=c.data.split("|")[1]
    try:
        if action=="sourcelist":
            rows=list(source_chats_col.find({}));bot.send_message(c.from_user.id,"📋 **Sources**\n\n"+("\n".join(str(x['_id']) for x in rows) if rows else "No sources."),parse_mode="Markdown");return bot.answer_callback_query(c.id)
        if action=="sourceremove":
            rows=list(source_chats_col.find({}));
            if not rows:return bot.answer_callback_query(c.id,"No sources",True)
            kb=InlineKeyboardMarkup(row_width=1)
            for i,x in enumerate(rows):kb.add(InlineKeyboardButton(f"❌ {x['_id']}",callback_data=f"importui|deletesource|{i}"))
            _import_state[c.from_user.id]={"sources":[x['_id'] for x in rows]};bot.send_message(c.from_user.id,"Select source to remove:",reply_markup=kb);return bot.answer_callback_query(c.id)
        if action=="deletesource":
            state=_import_state.get(c.from_user.id,{});idx=int(c.data.split("|")[2]);src=state.get("sources",[])[idx];source_chats_col.delete_one({"_id":src});bot.edit_message_text(f"✅ Process Complete\nRemoved source: {src}",c.from_user.id,c.message.message_id);return bot.answer_callback_query(c.id,"Removed")
        if action=="viewauto":
            cfg=get_config();bot.send_message(c.from_user.id,f"🆓 FREE source: `{cfg.get('auto_import_free_source') or 'Not set'}`\n💎 VIP source: `{cfg.get('auto_import_vip_source') or 'Not set'}`",parse_mode="Markdown");return bot.answer_callback_query(c.id)
        if action in ("recentfree", "recentvip"):
            cfg = get_config()
            chat_id = cfg.get("recent_admin_chat_id")
            title = cfg.get("recent_admin_chat_title") or str(chat_id or "")
            if not chat_id:
                raise ValueError("No recent private chat detected. Add the bot as administrator in the group/channel first, then reopen this menu.")
            category = "free" if action == "recentfree" else "vip"
            key = "auto_import_free_source" if category == "free" else "auto_import_vip_source"
            set_config(key, int(chat_id))
            source_chats_col.update_one(
                {"_id": int(chat_id)},
                {"$set": {"active": True, "category": category, "title": title, "added_at": now_ts()}},
                upsert=True,
            )
            admin_success(c.from_user.id, f"{category.upper()} private source set: {title} (`{chat_id}`)")
            return bot.answer_callback_query(c.id, "Source saved")
        if action in ("setfree","setvip"):
            _import_state[c.from_user.id]={"set_source_category":"free" if action=="setfree" else "vip"}
            msg=bot.send_message(c.from_user.id,"Send channel @username, username, t.me link, or numeric ID. Make bot admin:");bot.register_next_step_handler(msg,import_set_category_source);return bot.answer_callback_query(c.id,"Continue in chat")
        if action=="sourceadd":
            msg=bot.send_message(c.from_user.id,"Send source chat @username, username, t.me link, or numeric ID:");bot.register_next_step_handler(msg,import_add_source_step);return bot.answer_callback_query(c.id,"Continue in chat")
        if action=="method":
            _import_state[c.from_user.id]={"step":"category"};kb=InlineKeyboardMarkup(row_width=2)
            for cat,label in [("free","FREE"),("vip","VIP"),("apps","APPS"),("services","SERVICES")]:kb.add(InlineKeyboardButton(label,callback_data=f"importcat|{cat}"))
            bot.send_message(c.from_user.id,"Choose destination category:",reply_markup=kb);return bot.answer_callback_query(c.id)
    except Exception as exc:bot.send_message(c.from_user.id,f"❌ Process Failed\n{exc}",reply_markup=admin_menu())

def import_add_source_step(m):
    try:
        src=normalize_chat_reference(m.text);bot.get_chat(src);source_chats_col.update_one({'_id':src},{'$set':{'active':True,'added_at':now_ts()}},upsert=True);bot.send_message(m.from_user.id,f"✅ Process Complete\nSource added: {src}",reply_markup=admin_menu())
    except Exception as exc:bot.send_message(m.from_user.id,f"❌ Process Failed\n{exc}",reply_markup=admin_menu())

def import_set_category_source(m):
    try:
        st=_import_state.pop(m.from_user.id,None)
        if not st: raise ValueError("Session expired")
        ref=normalize_chat_reference(m.text);chat=bot.get_chat(ref);member=bot.get_chat_member(chat.id,bot.get_me().id)
        if member.status not in ("administrator","creator"): raise ValueError("Make the bot admin in that channel")
        key="auto_import_free_source" if st["set_source_category"]=="free" else "auto_import_vip_source"
        set_config(key,chat.id);source_chats_col.update_one({"_id":chat.id},{"$set":{"active":True,"category":st["set_source_category"],"added_at":now_ts()}},upsert=True)
        admin_success(m.from_user.id,f"{st['set_source_category'].upper()} auto-import channel set: {chat.id}")
    except Exception as exc: admin_error(m.from_user.id,exc)

@bot.callback_query_handler(func=lambda c:c.data.startswith("importcat|"))
def import_category_cb(c):
    if not is_admin(c.from_user.id):return
    _import_state[c.from_user.id]={"category":c.data.split("|",1)[1]};msg=bot.send_message(c.from_user.id,"Send price in points (0 for free):");bot.register_next_step_handler(msg,import_price_step);bot.answer_callback_query(c.id)

def import_price_step(m):
    try:
        state=_import_state.get(m.from_user.id);price=int((m.text or "").strip());
        if price<0:raise ValueError("Price cannot be negative")
        state["price"]=price;msg=bot.send_message(m.from_user.id,"Now send or forward the method file/message:");bot.register_next_step_handler(msg,import_method_step)
    except Exception as exc:_import_state.pop(m.from_user.id,None);bot.send_message(m.from_user.id,f"❌ Process Failed\n{exc}",reply_markup=admin_menu())

def import_method_step(m):
    try:
        state=_import_state.pop(m.from_user.id,None)
        if not state:raise ValueError("Session expired")
        name=((m.text or m.caption or 'Imported Method').strip().splitlines()[0][:100]);files=[{'chat':m.chat.id,'msg':m.message_id,'type':m.content_type}];number=fs.add(state['category'],name,files,state['price']);send_method_notification('uploaded',fs.get_by_number(number) or {'cat':state['category'],'name':name,'number':number,'price':state['price']});log_event('method_imported',m.from_user.id,number,{'name':name});bot.send_message(m.from_user.id,f"✅ Process Complete\nImported #{number}: {name}",reply_markup=admin_menu())
    except Exception as exc:bot.send_message(m.from_user.id,f"❌ Process Failed\n{exc}",reply_markup=admin_menu())

@bot.my_chat_member_handler()
def remember_admin_chat(update):
    """Remember private groups/channels when the bot is promoted to administrator."""
    try:
        chat = update.chat
        new_status = update.new_chat_member.status
        if new_status not in ("administrator", "creator"):
            return
        if chat.type not in ("group", "supergroup", "channel"):
            return
        title = getattr(chat, "title", None) or str(chat.id)
        set_config("recent_admin_chat_id", int(chat.id))
        set_config("recent_admin_chat_title", title)
        source_chats_col.update_one(
            {"_id": int(chat.id)},
            {"$set": {"active": True, "title": title, "detected_at": now_ts(), "detected_by_admin_event": True}},
            upsert=True,
        )
        # Automatically classify obvious names; otherwise admin can select Recent Chat in Auto Import.
        upper_title = title.upper()
        category = None
        if "VIP" in upper_title or "PREMIUM" in upper_title:
            category = "vip"
        elif "FREE" in upper_title:
            category = "free"
        if category:
            key = "auto_import_vip_source" if category == "vip" else "auto_import_free_source"
            set_config(key, int(chat.id))
            source_chats_col.update_one({"_id": int(chat.id)}, {"$set": {"category": category}})
        try:
            detected = f"\n✅ Automatically selected as **{category.upper()}** source." if category else "\nOpen Auto Import and tap **Use Recent Chat as FREE/VIP**."
            bot.send_message(
                ADMIN_ID,
                f"🤖 **Private Chat Detected**\n\n📌 {title}\n🆔 `{chat.id}`\nType: {chat.type}{detected}",
                parse_mode="Markdown",
            )
        except Exception:
            pass
    except Exception as exc:
        log_event("remember_admin_chat_error", details={"error": str(exc)}, level="error")


def _set_import_source_from_chat(message, category):
    try:
        chat = message.chat
        if chat.type not in ("group", "supergroup", "channel"):
            raise ValueError("Send this command inside the source group/channel")
        member = bot.get_chat_member(chat.id, bot.get_me().id)
        if member.status not in ("administrator", "creator"):
            raise ValueError("Make the bot administrator first")
        key = "auto_import_vip_source" if category == "vip" else "auto_import_free_source"
        set_config(key, int(chat.id))
        set_config("recent_admin_chat_id", int(chat.id))
        set_config("recent_admin_chat_title", getattr(chat, "title", None) or str(chat.id))
        source_chats_col.update_one(
            {"_id": int(chat.id)},
            {"$set": {"active": True, "category": category, "title": getattr(chat, "title", None), "added_at": now_ts()}},
            upsert=True,
        )
        bot.send_message(chat.id, f"✅ This private chat is now the {category.upper()} auto-import source.")
        try:
            admin_success(ADMIN_ID, f"{category.upper()} private source connected: {getattr(chat, 'title', chat.id)} (`{chat.id}`)")
        except Exception:
            pass
    except Exception as exc:
        try:
            bot.send_message(message.chat.id, f"❌ Process Failed\n{exc}")
        except Exception:
            pass


@bot.message_handler(commands=["setvipimport"])
def set_vip_import_here(m):
    _set_import_source_from_chat(m, "vip")


@bot.message_handler(commands=["setfreeimport"])
def set_free_import_here(m):
    _set_import_source_from_chat(m, "free")


@bot.channel_post_handler(content_types=['text','photo','video','document','audio','animation','voice'])
def auto_import_channel_post(m):
    try:
        raw_command = (m.text or m.caption or "").strip().lower()
        if raw_command.startswith("/setvipimport") or raw_command == "#setvipimport":
            return _set_import_source_from_chat(m, "vip")
        if raw_command.startswith("/setfreeimport") or raw_command == "#setfreeimport":
            return _set_import_source_from_chat(m, "free")
        cfg=get_cached_config(); source_map={cfg.get('auto_import_free_source'):'free',cfg.get('auto_import_vip_source'):'vip'}
        cat=source_map.get(m.chat.id)
        if not cat: return
        raw=(m.text or m.caption or '').strip(); first=(raw.splitlines()[0].strip() if raw else '')
        match=re.fullmatch(r"#method(\d+)(?:\.(\d+))?",first,re.I)
        if not match: return
        key=int(match.group(1)); part=int(match.group(2) or 0)
        file_item={'chat':m.chat.id,'msg':m.message_id,'type':m.content_type}
        if part:
            folder=folders_col.find_one({'cat':cat,'auto_method_key':key})
            if not folder:
                bot.send_message(m.chat.id,f"❌ #method{key} does not exist yet.")
                return
            folders_col.update_one({'_id':folder['_id']},{'$push':{'files':file_item},'$set':{'updated_at':now_ts()}})
            folder['files']=folder.get('files',[])+[file_item];send_method_notification('updated',folder)
            bot.send_message(m.chat.id,f"✅ Added file {part} to [{folder.get('number')}] {folder.get('name')}")
            return
        lines=raw.splitlines();name=(lines[1].strip() if len(lines)>1 and lines[1].strip() else f"Method {key}")[:100]
        existing=folders_col.find_one({'cat':cat,'auto_method_key':key})
        if existing:
            folders_col.update_one({'_id':existing['_id']},{'$set':{'name':name,'files':[file_item],'updated_at':now_ts()}})
            existing.update({'name':name,'files':[file_item]});send_method_notification('updated',existing)
            bot.send_message(m.chat.id,f"✅ Updated [{existing.get('number')}] {name}")
        else:
            number=fs.add(cat,name,[file_item],0);folders_col.update_one({'number':number},{'$set':{'auto_method_key':key,'source_chat':m.chat.id}})
            folder=fs.get_by_number(number);send_method_notification('uploaded',folder)
            bot.send_message(m.chat.id,f"✅ Imported [{number}] {name}")
    except Exception as exc:
        try: bot.send_message(m.chat.id,f"❌ Auto import failed: {str(exc)[:500]}")
        except Exception: pass
        log_event('auto_import_error',target=getattr(m.chat,'id',None),details={'error':str(exc)},level='error')

def scheduler_loop():
    while not _scheduler_stop.wait(20):
        try:
            now=now_ts()
            for x in broadcasts_col.find({'status':'scheduled','run_at':{'$lte':now}}).limit(10):
                q={} if x['target']=='all' else {'vip':x['target']=='vip'}; sent=failed=0
                for u in users_col.find(q,{'_id':1}):
                    try: bot.copy_message(int(u['_id']),x['source_chat'],x['source_message']); sent+=1
                    except Exception: failed+=1
                broadcasts_col.update_one({'_id':x['_id']},{'$set':{'status':'sent','sent':sent,'failed':failed,'sent_at':now}}); log_event('broadcast_sent',x.get('created_by'),details={'sent':sent,'failed':failed})
            for x in auto_posts_col.find({'active':True,'next_run':{'$lte':now}}).limit(20):
                try:
                    if x.get('payload'): _send_payload(x['channel'],x['payload'])
                    else: bot.copy_message(x['channel'],x['source_chat'],x['source_message'])
                    log_event('auto_post_sent',target=x['channel'])
                except Exception as exc: log_event('auto_post_error',target=x['channel'],details={'error':str(exc)},level='error')
                if x['schedule'] == 'every_hours':
                    nxt = now + max(float(x['value']), 0.01) * 3600
                else:
                    hh, mm = map(int, str(x['value']).split(':'))
                    local_now = datetime.utcfromtimestamp(now + TZ_OFFSET_SECONDS)
                    local_next = local_now.replace(hour=hh, minute=mm, second=0, microsecond=0)
                    if local_next <= local_now:
                        local_next += timedelta(days=1)
                    nxt = local_next.timestamp() - TZ_OFFSET_SECONDS
                auto_posts_col.update_one({'_id':x['_id']},{'$set':{'next_run':nxt, 'last_run':now}})
        except Exception as exc: log_event('scheduler_error',details={'error':str(exc)},level='error')

threading.Thread(target=scheduler_loop,name='zedox-scheduler',daemon=True).start()

# =========================
# 🧠 FALLBACK
# =========================
@bot.message_handler(func=lambda m: True)
def fallback(m):
    if not validate_request(m):
        return
    
    uid = m.from_user.id
    
    if force_block(uid):
        return
    
    # Check custom buttons
    for btn in get_custom_buttons():
        if m.text == btn["text"]:
            if btn["type"] == "link":
                kb = InlineKeyboardMarkup()
                kb.add(InlineKeyboardButton("🔗 Open", url=btn["data"]))
                bot.send_message(uid, f"🔗 {btn['text']}", reply_markup=kb)
            elif btn["type"] == "folder":
                f = fs.get_by_number(int(btn["data"]))
                if f:
                    fake = type('obj', (object,), {'from_user': m.from_user, 'id': m.message_id, 'data': f"open|{f['cat']}|{f['name']}|"})
                    open_folder(fake)
            return
    
    known = MAIN_MENU_BUTTONS + [
        "⚙️ ADMIN PANEL", "🔎 Search", "📣 Auto Posts", "📥 Auto Import",
        "🧾 Logs", "💾 Backup/Export", "🙈 Hide Button", "👁 Show Button"
    ]
    if m.text and m.text not in known:
        bot.send_message(uid, "❌ Use menu buttons", reply_markup=main_menu(uid))

# =========================
# 🚀 RUN BOT
# =========================
def run_bot():
    print("=" * 50, flush=True)
    print("🚀 ZEDOX BOT - RAILWAY READY", flush=True)
    print(f"👑 Owner ID: {ADMIN_ID}", flush=True)
    print("💾 Existing MongoDB data: preserved", flush=True)
    print("=" * 50, flush=True)

    # Remove any old webhook before long polling.
    bot.remove_webhook()
    time.sleep(1)

    me = bot.get_me()
    print(f"✅ Logged in as @{me.username}", flush=True)

    while True:
        try:
            bot.infinity_polling(
                timeout=30,
                long_polling_timeout=30,
                skip_pending=True,
                allowed_updates=["message", "callback_query", "channel_post", "my_chat_member", "chat_member"],
                restart_on_change=False,
            )
        except KeyboardInterrupt:
            raise
        except Exception as exc:
            log_event("polling_restart", details={"error": str(exc), "trace": traceback.format_exc()}, level="error")
            print(f"⚠️ Polling error; restarting: {exc}", flush=True)
            time.sleep(5)

if __name__ == "__main__":
    run_bot()
