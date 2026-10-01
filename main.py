import os
import sqlite3
import threading
from functools import wraps
from flask import Flask, jsonify, request
import discord
from discord import app_commands
from discord.ext import commands

DB_PATH = os.getenv("DATABASE_PATH", "whitelist.db")
API_KEY = os.getenv("API_KEY", "")
GUILD_ID = os.getenv("DISCORD_GUILD_ID", "1516413516498862221")
ADMIN_ROLE_ID = os.getenv("1516413666306691082", "1516413666306691082")
PORT = int(os.getenv("PORT", "8080"))


def db_connection():
    conn = sqlite3.connect(DB_PATH)
    conn.row_factory = sqlite3.Row
    conn.execute("PRAGMA foreign_keys = ON")
    return conn


def setup_db():
    with db_connection() as db:
        db.executescript("""
        CREATE TABLE IF NOT EXISTS maps (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            name TEXT NOT NULL UNIQUE,
            place_id TEXT NOT NULL UNIQUE
        );
        CREATE TABLE IF NOT EXISTS users (
            roblox_user_id TEXT PRIMARY KEY,
            display_name TEXT
        );
        CREATE TABLE IF NOT EXISTS permissions (
            roblox_user_id TEXT NOT NULL,
            map_id INTEGER NOT NULL,
            PRIMARY KEY (roblox_user_id, map_id),
            FOREIGN KEY (roblox_user_id) REFERENCES users(roblox_user_id) ON DELETE CASCADE,
            FOREIGN KEY (map_id) REFERENCES maps(id) ON DELETE CASCADE
        );
        """)


def valid_id(value):
    return str(value).isdigit() and 1 <= len(str(value)) <= 20


def admin_only(interaction):
    if interaction.user.guild_permissions.manage_guild:
        return True
    return bool(ADMIN_ROLE_ID and any(str(role.id) == ADMIN_ROLE_ID for role in interaction.user.roles))


def admin_command(func):
    @wraps(func)
    async def wrapper(interaction, *args, **kwargs):
        if not admin_only(interaction):
            await interaction.response.send_message("คำสั่งนี้ใช้ได้เฉพาะแอดมินเท่านั้น", ephemeral=True)
            return
        try:
            await func(interaction, *args, **kwargs)
        except ValueError as exc:
            await interaction.response.send_message(f"ทำรายการไม่สำเร็จ: {exc}", ephemeral=True)
        except Exception as exc:
            print(f"command error: {exc}")
            if not interaction.response.is_done():
                await interaction.response.send_message("เกิดข้อผิดพลาดในระบบ", ephemeral=True)
    return wrapper


intents = discord.Intents.none()
bot = commands.Bot(command_prefix="!", intents=intents)

@bot.event
async def on_ready():
    if GUILD_ID and GUILD_ID.isdigit():
        guild = discord.Object(id=int(GUILD_ID))
        bot.tree.copy_global_to(guild=guild)
        await bot.tree.sync(guild=guild)
    else:
        await bot.tree.sync()
    print(f"Discord online: {bot.user}")


@bot.tree.command(name="map-add", description="เพิ่มหรือแก้ไขแมพ")
@app_commands.describe(name="ชื่อแมพ", place_id="Roblox Place ID")
@app_commands.default_permissions(manage_guild=True)
@admin_command
async def map_add(interaction, name: str, place_id: str):
    if not name.strip() or len(name) > 80 or not valid_id(place_id):
        raise ValueError("ชื่อแมพหรือ Place ID ไม่ถูกต้อง")
    with db_connection() as db:
        db.execute("INSERT INTO maps(name, place_id) VALUES(?, ?) ON CONFLICT(name) DO UPDATE SET place_id=excluded.place_id", (name.strip(), place_id))
    await interaction.response.send_message(f"บันทึกแมพ **{name.strip()}** แล้ว")


@bot.tree.command(name="map-list", description="แสดงรายการแมพ")
@app_commands.default_permissions(manage_guild=True)
@admin_command
async def map_list(interaction):
    with db_connection() as db:
        rows = db.execute("SELECT name, place_id FROM maps ORDER BY name").fetchall()
    text = "\n".join(f"• **{row['name']}** — {row['place_id']}" for row in rows) or "ยังไม่มีแมพ"
    await interaction.response.send_message(text)


@bot.tree.command(name="allow", description="อนุญาต Roblox User ID เข้าแมพ")
@app_commands.describe(roblox_user_id="Roblox User ID", map="ชื่อแมพ", display_name="ชื่อผู้เล่น (ถ้ามี)")
@app_commands.default_permissions(manage_guild=True)
@admin_command
async def allow(interaction, roblox_user_id: str, map: str, display_name: str = None):
    if not valid_id(roblox_user_id):
        raise ValueError("Roblox User ID ไม่ถูกต้อง")
    with db_connection() as db:
        target = db.execute("SELECT id FROM maps WHERE name=?", (map,)).fetchone()
        if not target:
            raise ValueError(f"ไม่พบแมพ {map}")
        db.execute("INSERT INTO users(roblox_user_id, display_name) VALUES(?, ?) ON CONFLICT(roblox_user_id) DO UPDATE SET display_name=COALESCE(excluded.display_name, users.display_name)", (roblox_user_id, display_name))
        db.execute("INSERT OR IGNORE INTO permissions(roblox_user_id, map_id) VALUES(?, ?)", (roblox_user_id, target["id"]))
    await interaction.response.send_message(f"เพิ่ม **{roblox_user_id}** เข้าแมพ **{map}** แล้ว")


@bot.tree.command(name="deny", description="ยกเลิกสิทธิ์ User ID ในแมพ")
@app_commands.describe(roblox_user_id="Roblox User ID", map="ชื่อแมพ")
@app_commands.default_permissions(manage_guild=True)
@admin_command
async def deny(interaction, roblox_user_id: str, map: str):
    with db_connection() as db:
        target = db.execute("SELECT id FROM maps WHERE name=?", (map,)).fetchone()
        if not target:
            raise ValueError(f"ไม่พบแมพ {map}")
        db.execute("DELETE FROM permissions WHERE roblox_user_id=? AND map_id=?", (roblox_user_id, target["id"]))
    await interaction.response.send_message("ยกเลิกสิทธิ์แล้ว")


@bot.tree.command(name="user-remove", description="ลบ User ID จากทุกแมพ")
@app_commands.describe(roblox_user_id="Roblox User ID")
@app_commands.default_permissions(manage_guild=True)
@admin_command
async def user_remove(interaction, roblox_user_id: str):
    with db_connection() as db:
        db.execute("DELETE FROM users WHERE roblox_user_id=?", (roblox_user_id,))
    await interaction.response.send_message("ลบ User ID จากทุกแมพแล้ว")


@bot.tree.command(name="whitelist-list", description="แสดงรายการ whitelist")
@app_commands.default_permissions(manage_guild=True)
@admin_command
async def whitelist_list(interaction):
    with db_connection() as db:
        rows = db.execute("SELECT u.roblox_user_id, u.display_name, m.name FROM users u JOIN permissions p ON p.roblox_user_id=u.roblox_user_id JOIN maps m ON m.id=p.map_id ORDER BY u.roblox_user_id, m.name").fetchall()
    text = "\n".join(f"• {r['roblox_user_id']} → **{r['name']}**" for r in rows) or "ยังไม่มี whitelist"
    await interaction.response.send_message(text[:1900])


app = Flask(__name__)

def require_api_key(func):
    @wraps(func)
    def wrapper(*args, **kwargs):
        if not API_KEY or request.headers.get("x-api-key") != API_KEY:
            return jsonify(allowed=False, error="unauthorized"), 401
        return func(*args, **kwargs)
    return wrapper

@app.get("/health")
def health():
    return jsonify(ok=True)

@app.post("/api/check")
@require_api_key
def check_access():
    body = request.get_json(silent=True) or {}
    user_id, place_id = str(body.get("robloxUserId", "")), str(body.get("placeId", ""))
    if not valid_id(user_id) or not valid_id(place_id):
        return jsonify(allowed=False, error="invalid_id"), 400
    with db_connection() as db:
        row = db.execute("SELECT m.name FROM permissions p JOIN maps m ON m.id=p.map_id WHERE p.roblox_user_id=? AND m.place_id=?", (user_id, place_id)).fetchone()
    return jsonify(allowed=bool(row), map=row["name"] if row else None)


def run_api():
    app.run(host="0.0.0.0", port=PORT, threaded=True)


if __name__ == "__main__":
    setup_db()
    threading.Thread(target=run_api, daemon=True).start()
    if not os.getenv("DISCORD_TOKEN"):
        raise RuntimeError("ต้องตั้งค่า DISCORD_TOKEN ใน Railway Variables")
    bot.run(os.environ["DISCORD_TOKEN"])

