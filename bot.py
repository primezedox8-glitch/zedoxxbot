# =========================
# ZEDOX BOT - COMPLETE VERSION
# With Working Subfolders, Fast Response, Fixed Give Points
# =========================

import telebot
from telebot.types import InlineKeyboardMarkup, InlineKeyboardButton, ReplyKeyboardMarkup
import os, time, random, string, threading, hashlib, hmac
from pymongo import MongoClient
from datetime import datetime, timedelta
from functools import wraps

BOT_TOKEN = os.environ.get("BOT_TOKEN")
ADMIN_ID = int(os.environ.get("ADMIN_ID"))

if not BOT_TOKEN:
    raise ValueError("BOT_TOKEN environment variable not set!")

# =========================
# 🌐 MONGODB SETUP (OPTIMIZED)
# =========================
MONGO_URI = os.environ.get("MONGO_URI")
if not MONGO_URI:
    raise ValueError("MONGO_URI environment variable not set!")

client = MongoClient(MONGO_URI, maxPoolSize=50, minPoolSize=10, connectTimeoutMS=5000, socketTimeoutMS=5000)
db = client["zedox_complete"]

# Collections
users_col = db["users"]
folders_col = db["folders"]
codes_col = db["codes"]
config_col = db["config"]
custom_buttons_col = db["custom_buttons"]
admins_col = db["admins"]
payments_col = db["payments"]

# Create indexes for speed
users_col.create_index("points")
users_col.create_index("vip")
users_col.create_index("referrals_count")
folders_col.create_index([("cat", 1), ("parent", 1)])
folders_col.create_index("number", unique=True, sparse=True)

bot = telebot.TeleBot(BOT_TOKEN, parse_mode="Markdown")

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
            "require_screenshot": True
        }
        config_col.insert_one(cfg)
    return cfg

def set_config(key, value):
    global _config_cache
    _config_cache = None
    config_col.update_one({"_id": "config"}, {"$set": {key: value}}, upsert=True)

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
    
    if not force_channels:
        return False
    
    for ch in force_channels:
        try:
            member = bot.get_chat_member(ch, uid)
            if member.status in ["left", "kicked"]:
                kb = InlineKeyboardMarkup()
                for channel in force_channels:
                    kb.add(InlineKeyboardButton(f"📢 Join {channel}", url=f"https://t.me/{channel.replace('@','')}"))
                kb.add(InlineKeyboardButton("✅ I Joined", callback_data="recheck"))
                bot.send_message(uid, "🚫 **Access Restricted!**\n\nPlease join the following channels:", reply_markup=kb, parse_mode="Markdown")
                return True
        except:
            kb = InlineKeyboardMarkup()
            for channel in force_channels:
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

def main_menu(uid):
    kb = ReplyKeyboardMarkup(resize_keyboard=True, row_width=2)
    
    kb.add("📂 FREE METHODS", "💎 VIP METHODS")
    kb.add("📦 PREMIUM APPS", "⚡ SERVICES")
    kb.add("💰 POINTS", "⭐ BUY VIP")
    
    custom_btns = get_custom_buttons()
    if custom_btns:
        row = []
        for btn in custom_btns:
            row.append(btn["text"])
            if len(row) == 2:
                kb.add(*row)
                row = []
        if row:
            kb.add(*row)
    
    kb.add("🎁 REFERRAL", "👤 ACCOUNT")
    kb.add("📚 MY METHODS", "💎 GET POINTS")
    kb.add("🆔 CHAT ID", "🏆 REDEEM")
    
    if is_admin(uid):
        kb.add("⚙️ ADMIN PANEL")
    
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
    kb.row("📱 Upload APPS", "⚡ Upload SERVICE")
    kb.row("📁 Create Subfolder", "🗑 Delete Folder")
    kb.row("✏️ Edit Price", "✏️ Edit Name")
    kb.row("📝 Edit Content", "🔀 Move Folder")
    kb.row("👑 Add VIP", "👑 Remove VIP")
    kb.row("💰 Give Points", "🎫 Generate Codes")
    kb.row("📊 View Codes", "📦 Points Packages")
    kb.row("👥 Admin Management", "📞 Set Contacts")
    kb.row("⚙️ VIP Settings", "💳 Payment Methods")
    kb.row("🏦 Binance Settings", "📸 Screenshot")
    kb.row("➕ Add Button", "➖ Remove Button")
    kb.row("➕ Add Channel", "➖ Remove Channel")
    kb.row("⚙️ Settings", "📊 Stats")
    kb.row("📢 Broadcast", "🔔 Notify")
    kb.row("📊 Leaderboard", "❌ Exit")
    
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

@bot.message_handler(func=lambda m: m.text in ["📦 Upload FREE", "💎 Upload VIP", "📱 Upload APPS", "⚡ Upload SERVICE"] and is_admin(m.from_user.id))
def upload_handler(m):
    cats = {"📦 Upload FREE": "free", "💎 Upload VIP": "vip", "📱 Upload APPS": "apps", "⚡ Upload SERVICE": "services"}
    start_upload(m.from_user.id, cats[m.text], m.text == "⚡ Upload SERVICE")

# =========================
# 📁 CREATE SUBFOLDER
# =========================
@bot.message_handler(func=lambda m: m.text == "📁 Create Subfolder" and is_admin(m.from_user.id))
def create_subfolder(m):
    msg = bot.send_message(m.from_user.id, "📁 **Create Subfolder**\n\nSend: `category parent_name sub_name price`\nExample: `free MainFolder SubFolder 10`", parse_mode="Markdown")
    bot.register_next_step_handler(msg, create_subfolder_process)

def create_subfolder_process(m):
    try:
        parts = m.text.split(maxsplit=3)
        if len(parts) < 4:
            bot.send_message(m.from_user.id, "❌ Use: category parent name price")
            return
        
        cat, parent, name, price = parts[0].lower(), parts[1], parts[2], int(parts[3])
        
        if cat not in ["free", "vip", "apps", "services"]:
            bot.send_message(m.from_user.id, "❌ Invalid category")
            return
        
        parent_folder = fs.get_one(cat, parent)
        if not parent_folder:
            bot.send_message(m.from_user.id, f"❌ Parent '{parent}' not found!")
            return
        
        number = fs.add(cat, name, [], price, parent)
        bot.send_message(m.from_user.id, f"✅ Created!\n📌 #{number}\n📂 {parent} → {name}\n💰 {price} pts", reply_markup=admin_menu(), parse_mode="Markdown")
    except:
        bot.send_message(m.from_user.id, "❌ Error! Use: category parent name price")

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
# 🗑 DELETE FOLDER
# =========================
@bot.message_handler(func=lambda m: m.text == "🗑 Delete Folder" and is_admin(m.from_user.id))
def del_start(m):
    msg = bot.send_message(m.from_user.id, "🗑 **Delete Folder**\n\nSend: `category folder_name`\nExample: `free My Folder`", parse_mode="Markdown")
    bot.register_next_step_handler(msg, del_folder)

def del_folder(m):
    try:
        parts = m.text.split(maxsplit=1)
        if len(parts) != 2:
            bot.send_message(m.from_user.id, "❌ Use: category folder_name")
            return
        
        cat, name = parts[0].lower(), parts[1].strip()
        
        if cat not in ["free", "vip", "apps", "services"]:
            bot.send_message(m.from_user.id, "❌ Invalid category")
            return
        
        folder = fs.get_one(cat, name)
        if not folder:
            all_folders = fs.get(cat)
            for f in all_folders:
                if f["name"].lower() == name.lower():
                    folder, name = f, f["name"]
                    break
        
        if not folder:
            bot.send_message(m.from_user.id, f"❌ '{name}' not found in {cat}!")
            return
        
        kb = InlineKeyboardMarkup()
        kb.add(InlineKeyboardButton("✅ DELETE", callback_data=f"confirm_del|{cat}|{name}"), InlineKeyboardButton("❌ CANCEL", callback_data="cancel_del"))
        
        sub_count = len(fs.get(cat, name))
        bot.send_message(m.from_user.id, f"⚠️ **Confirm**\n\nDelete '{name}' from {cat.upper()}?\nSubfolders: {sub_count}\nThis cannot be undone!", reply_markup=kb, parse_mode="Markdown")
    except:
        bot.send_message(m.from_user.id, "❌ Error!")

@bot.callback_query_handler(func=lambda c: c.data.startswith("confirm_del|"))
def confirm_delete(c):
    _, cat, name = c.data.split("|")
    if fs.delete(cat, name):
        bot.edit_message_text(f"✅ Deleted: {cat} → {name}", c.from_user.id, c.message.message_id)
        bot.send_message(c.from_user.id, "✅ Deleted!", reply_markup=admin_menu())
    else:
        bot.edit_message_text("❌ Not found!", c.from_user.id, c.message.message_id)
    bot.answer_callback_query(c.id)

@bot.callback_query_handler(func=lambda c: c.data == "cancel_del")
def cancel_delete(c):
    bot.edit_message_text("❌ Cancelled", c.from_user.id, c.message.message_id)
    bot.send_message(c.from_user.id, "Returning...", reply_markup=admin_menu())
    bot.answer_callback_query(c.id)

# =========================
# ✏️ EDIT PRICE
# =========================
@bot.message_handler(func=lambda m: m.text == "✏️ Edit Price" and is_admin(m.from_user.id))
def edit_price_start(m):
    msg = bot.send_message(m.from_user.id, "✏️ **Edit Price**\n\nSend: `category folder_name new_price`\nExample: `vip Method 50`", parse_mode="Markdown")
    bot.register_next_step_handler(msg, edit_price_process)

def edit_price_process(m):
    try:
        parts = m.text.split()
        cat, price = parts[0].lower(), int(parts[-1])
        name = " ".join(parts[1:-1])
        fs.edit_price(cat, name, price)
        bot.send_message(m.from_user.id, f"✅ Price updated: {price} pts", reply_markup=admin_menu())
    except:
        bot.send_message(m.from_user.id, "❌ Use: category name price")

# =========================
# ✏️ EDIT NAME
# =========================
@bot.message_handler(func=lambda m: m.text == "✏️ Edit Name" and is_admin(m.from_user.id))
def edit_name_start(m):
    msg = bot.send_message(m.from_user.id, "✏️ **Edit Name**\n\nSend: `category old_name new_name`\nExample: `free Old New`", parse_mode="Markdown")
    bot.register_next_step_handler(msg, edit_name_process)

def edit_name_process(m):
    try:
        parts = m.text.split(maxsplit=2)
        cat, old, new = parts[0].lower(), parts[1], parts[2]
        fs.edit_name(cat, old, new)
        bot.send_message(m.from_user.id, f"✅ Renamed: {old} → {new}", reply_markup=admin_menu())
    except:
        bot.send_message(m.from_user.id, "❌ Use: category old new")

# =========================
# 📝 EDIT CONTENT (FAST)
# =========================
edit_sessions = {}

@bot.message_handler(func=lambda m: m.text == "📝 Edit Content" and is_admin(m.from_user.id))
def edit_content_start(m):
    msg = bot.send_message(m.from_user.id, "📝 **Edit Content**\n\nSend: `category folder_name`\nExample: `vip My Method`", parse_mode="Markdown")
    bot.register_next_step_handler(msg, edit_content_select)

def edit_content_select(m):
    uid = m.from_user.id
    parts = m.text.split(maxsplit=1)
    if len(parts) != 2:
        bot.send_message(uid, "❌ Use: category name")
        return
    
    cat, name = parts[0].lower(), parts[1].strip()
    if cat not in ["free", "vip", "apps", "services"]:
        bot.send_message(uid, "❌ Invalid category")
        return
    
    if not fs.get_one(cat, name):
        bot.send_message(uid, f"❌ '{name}' not found!")
        return
    
    edit_sessions[uid] = {"cat": cat, "name": name}
    kb = InlineKeyboardMarkup(row_width=2)
    kb.add(InlineKeyboardButton("📝 Text", callback_data="edit_text"), InlineKeyboardButton("📁 Files", callback_data="edit_files"), InlineKeyboardButton("❌ Cancel", callback_data="edit_cancel"))
    bot.send_message(uid, f"📝 **Edit: {name}**\n\nWhat to edit?", reply_markup=kb, parse_mode="Markdown")

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
    fs.edit_content(s["cat"], s["name"], "text", m.text)
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
        fs.edit_content(s["cat"], s["name"], "files", s["new_files"])
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
    try:
        # Get the raw text and split by space
        text = m.text.strip()
        parts = text.split()
        
        if len(parts) != 2:
            bot.send_message(m.from_user.id, 
                "❌ **Invalid format!**\n\n"
                "Please use: `user_id points`\n\n"
                "Example: `7712834912 200`\n\n"
                "Make sure there is a space between user_id and points.",
                parse_mode="Markdown")
            return
        
        user_id_str = parts[0].strip()
        points_str = parts[1].strip()
        
        # Validate user_id (should be digits only)
        if not user_id_str.isdigit():
            bot.send_message(m.from_user.id, 
                f"❌ **Invalid User ID!**\n\n"
                f"User ID must contain only numbers.\n"
                f"You sent: `{user_id_str}`\n\n"
                f"Example: `7712834912`",
                parse_mode="Markdown")
            return
        
        # Convert to integer (supports large numbers)
        try:
            user_id = int(user_id_str)
        except ValueError:
            bot.send_message(m.from_user.id, 
                f"❌ **Invalid User ID!**\n\n"
                f"Could not convert `{user_id_str}` to a valid User ID.\n\n"
                f"Example: `7712834912`",
                parse_mode="Markdown")
            return
        
        # Validate points
        try:
            points = int(points_str)
        except ValueError:
            bot.send_message(m.from_user.id, 
                f"❌ **Invalid Points!**\n\n"
                f"Points must be a number.\n"
                f"You sent: `{points_str}`\n\n"
                f"Example: `100` or `250`",
                parse_mode="Markdown")
            return
        
        if points <= 0:
            bot.send_message(m.from_user.id, 
                "❌ **Points must be greater than 0!**\n\n"
                f"You tried to add: `{points}` points",
                parse_mode="Markdown")
            return
        
        if points > 1000000:
            bot.send_message(m.from_user.id, 
                "⚠️ **Maximum 1,000,000 points per transaction!**\n\n"
                f"You tried to add: `{points:,}` points",
                parse_mode="Markdown")
            return
        
        # Check if user exists in database
        user_data = users_col.find_one({"_id": str(user_id)})
        if not user_data:
            bot.send_message(m.from_user.id, 
                f"❌ **User Not Found!**\n\n"
                f"User with ID `{user_id}` has not started the bot yet.\n\n"
                f"📌 **Solution:** Ask the user to send `/start` to the bot first, then try again.\n\n"
                f"User ID provided: `{user_id}`",
                parse_mode="Markdown")
            return
        
        # Get user object and add points
        user = User(user_id)
        old_points = user.points()
        user.add_points(points)
        new_points = user.points()
        
        # Get username for display
        username = user.username()
        if username:
            username_display = f"@{username}"
        else:
            username_display = f"ID: {user_id}"
        
        # Send success message to admin
        bot.send_message(m.from_user.id, 
            f"✅ **Points Added Successfully!** ✅\n\n"
            f"👤 **User:** {username_display}\n"
            f"🆔 **User ID:** `{user_id}`\n"
            f"💰 **Old Balance:** `{old_points:,}`\n"
            f"➕ **Points Added:** `+{points:,}`\n"
            f"💰 **New Balance:** `{new_points:,}`\n\n"
            f"✨ Points have been added successfully!",
            parse_mode="Markdown")
        
        # Send notification to the user
        try:
            bot.send_message(user_id, 
                f"🎉 **Points Received!** 🎉\n\n"
                f"✨ You received **+{points:,} points**!\n\n"
                f"💰 **Balance Update:**\n"
                f"┌ Old Balance: `{old_points:,}`\n"
                f"└ New Balance: `{new_points:,}`\n\n"
                f"💡 **What can you do with points?**\n"
                f"• Buy VIP methods from 💎 VIP METHODS\n"
                f"• Access premium content\n"
                f"• Redeem special offers\n\n"
                f"Thank you for being part of ZEDOX! 🚀",
                parse_mode="Markdown")
        except Exception as e:
            bot.send_message(m.from_user.id, 
                f"⚠️ **Notification Failed**\n\n"
                f"Points were added but couldn't notify the user.\n"
                f"User may have blocked the bot.\n\n"
                f"Error: {str(e)}",
                parse_mode="Markdown")
            
    except Exception as e:
        bot.send_message(m.from_user.id, 
            f"❌ **Error Occurred!**\n\n"
            f"Please use the correct format: `user_id points`\n\n"
            f"Example: `7712834912 200`\n\n"
            f"Error: {str(e)}",
            parse_mode="Markdown")

# =========================
# 🎫 GENERATE CODES
# =========================
@bot.message_handler(func=lambda m: m.text == "🎫 Generate Codes" and is_admin(m.from_user.id))
def gen_codes_start(m):
    kb = InlineKeyboardMarkup()
    kb.add(InlineKeyboardButton("Single", callback_data="gen_single"), InlineKeyboardButton("Multi", callback_data="gen_multi"))
    bot.send_message(m.from_user.id, "🎫 **Generate Codes**\n\nChoose type:", reply_markup=kb, parse_mode="Markdown")

@bot.callback_query_handler(func=lambda c: c.data.startswith("gen_"))
def gen_codes_type(c):
    multi = (c.data == "gen_multi")
    code_gen_session[c.from_user.id] = {"multi": multi}
    msg = bot.send_message(c.from_user.id, "💰 Points per code:", parse_mode="Markdown")
    bot.register_next_step_handler(msg, process_code_points)
    bot.answer_callback_query(c.id)

code_gen_session = {}

def process_code_points(m):
    uid = m.from_user.id
    try:
        pts = int(m.text)
        if pts <= 0 or pts > 100000:
            bot.send_message(uid, "❌ Points: 1-100,000")
            return
        code_gen_session[uid]["points"] = pts
        msg = bot.send_message(uid, "🔢 How many? (1-100):", parse_mode="Markdown")
        bot.register_next_step_handler(msg, process_code_count)
    except:
        bot.send_message(uid, "❌ Invalid number!")

def process_code_count(m):
    uid = m.from_user.id
    try:
        count = int(m.text)
        if count <= 0 or count > 100:
            bot.send_message(uid, "❌ Count: 1-100")
            return
        
        session = code_gen_session.get(uid)
        if not session:
            bot.send_message(uid, "❌ Session expired!")
            return
        
        if session["multi"]:
            msg = bot.send_message(uid, "📅 Expiry days? (0 = none):", parse_mode="Markdown")
            bot.register_next_step_handler(msg, lambda x: process_code_expiry(x, session["points"], count))
        else:
            codes = codesys.generate(session["points"], count, False)
            bot.send_message(uid, f"✅ {count} codes!\n\n<code>{chr(10).join(codes)}</code>", parse_mode="Markdown", reply_markup=admin_menu())
            code_gen_session.pop(uid, None)
    except:
        bot.send_message(uid, "❌ Invalid number!")

def process_code_expiry(m, points, count):
    uid = m.from_user.id
    try:
        days = int(m.text) if m.text != "0" else None
        codes = codesys.generate(points, count, True, days)
        expiry_msg = f"Expiry: {days} days" if days else "No expiry"
        bot.send_message(uid, f"✅ {count} multi-use codes!\n⏰ {expiry_msg}\n\n<code>{chr(10).join(codes)}</code>", parse_mode="Markdown", reply_markup=admin_menu())
        code_gen_session.pop(uid, None)
    except:
        bot.send_message(uid, "❌ Invalid days!")

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
# 🔘 CUSTOM BUTTONS
# =========================
@bot.message_handler(func=lambda m: m.text == "➕ Add Button" and is_admin(m.from_user.id))
def add_button_cmd(m):
    msg = bot.send_message(m.from_user.id, "🔘 **Add Button**\n\nSend: `type|text|data`\nTypes: link|folder\nExample: `link|Website|https://example.com`", parse_mode="Markdown")
    bot.register_next_step_handler(msg, add_button_process)

def add_button_process(m):
    try:
        parts = m.text.split("|")
        if len(parts) != 3:
            bot.send_message(m.from_user.id, "❌ Use: type|text|data")
            return
        typ, text, data = parts[0].lower(), parts[1], parts[2]
        if typ not in ["link", "folder"]:
            bot.send_message(m.from_user.id, "❌ Type: link or folder")
            return
        if typ == "folder" and not fs.get_by_number(int(data)):
            bot.send_message(m.from_user.id, f"❌ Folder #{data} not found!")
            return
        add_custom_button(text, typ, data)
        bot.send_message(m.from_user.id, f"✅ Button added: {text}", reply_markup=admin_menu())
    except:
        bot.send_message(m.from_user.id, "❌ Invalid format!")

@bot.message_handler(func=lambda m: m.text == "➖ Remove Button" and is_admin(m.from_user.id))
def remove_button_cmd(m):
    btns = get_custom_buttons()
    if not btns:
        bot.send_message(m.from_user.id, "❌ No buttons!")
        return
    kb = InlineKeyboardMarkup(row_width=1)
    for b in btns:
        kb.add(InlineKeyboardButton(f"❌ {b['text']}", callback_data=f"rmbtn_{b['text']}"))
    bot.send_message(m.from_user.id, "Select button:", reply_markup=kb)

@bot.callback_query_handler(func=lambda c: c.data.startswith("rmbtn_"))
def remove_btn_cb(c):
    text = c.data[6:]
    remove_custom_button(text)
    bot.edit_message_text(f"✅ Removed: {text}", c.from_user.id, c.message.message_id)
    bot.answer_callback_query(c.id)

# =========================
# 📢 FORCE JOIN CHANNELS
# =========================
@bot.message_handler(func=lambda m: m.text == "➕ Add Channel" and is_admin(m.from_user.id))
def add_channel_cmd(m):
    msg = bot.send_message(m.from_user.id, "➕ Send channel @username:", parse_mode="Markdown")
    bot.register_next_step_handler(msg, add_channel_process)

def add_channel_process(m):
    ch = m.text.strip()
    if not ch.startswith("@"):
        bot.send_message(m.from_user.id, "❌ Must start with @")
        return
    cfg = get_config()
    chs = cfg.get("force_channels", [])
    if ch in chs:
        bot.send_message(m.from_user.id, "❌ Already added!")
        return
    chs.append(ch)
    set_config("force_channels", chs)
    bot.send_message(m.from_user.id, f"✅ Added: {ch}", reply_markup=admin_menu())

@bot.message_handler(func=lambda m: m.text == "➖ Remove Channel" and is_admin(m.from_user.id))
def remove_channel_cmd(m):
    cfg = get_config()
    chs = cfg.get("force_channels", [])
    if not chs:
        bot.send_message(m.from_user.id, "❌ No channels!")
        return
    kb = InlineKeyboardMarkup(row_width=1)
    for ch in chs:
        kb.add(InlineKeyboardButton(f"❌ {ch}", callback_data=f"rmch_{ch}"))
    bot.send_message(m.from_user.id, "Select channel:", reply_markup=kb)

@bot.callback_query_handler(func=lambda c: c.data.startswith("rmch_"))
def remove_channel_cb(c):
    ch = c.data[5:]
    cfg = get_config()
    chs = [c for c in cfg.get("force_channels", []) if c != ch]
    set_config("force_channels", chs)
    bot.edit_message_text(f"✅ Removed: {ch}", c.from_user.id, c.message.message_id)
    bot.answer_callback_query(c.id)

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
    cfg = get_config()
    current = cfg.get("notify", True)
    set_config("notify", not current)
    bot.send_message(m.from_user.id, f"🔔 Notifications: {'ON' if not current else 'OFF'}", reply_markup=admin_menu())

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
    
    known = ["📂 FREE METHODS", "💎 VIP METHODS", "📦 PREMIUM APPS", "⚡ SERVICES", "💰 POINTS", "⭐ BUY VIP", "🎁 REFERRAL", "👤 ACCOUNT", "🆔 CHAT ID", "🏆 REDEEM", "📚 MY METHODS", "💎 GET POINTS", "⚙️ ADMIN PANEL"]
    if m.text and m.text not in known:
        bot.send_message(uid, "❌ Use menu buttons", reply_markup=main_menu(uid))

# =========================
# 🚀 RUN BOT
# =========================
def run_bot():
    while True:
        try:
            print("=" * 50)
            print("🚀 ZEDOX BOT - COMPLETE VERSION")
            print(f"✅ Bot: @{bot.get_me().username}")
            print(f"👑 Owner: {ADMIN_ID}")
            print(f"💾 MongoDB: Connected")
            print(f"📁 Subfolders: WORKING")
            print(f"📊 VIP Count: FIXED")
            print(f"🏆 Leaderboard: ADDED")
            print(f"💰 Give Points: WORKING")
            print(f"⚡ Speed: OPTIMIZED")
            print("=" * 50)
            bot.infinity_polling(timeout=60, long_polling_timeout=60)
        except Exception as e:
            print(f"❌ Error: {e}")
            time.sleep(5)

if __name__ == "__main__":
    threading.Thread(target=run_bot, daemon=True).start()
    while True:
        time.sleep(1)
