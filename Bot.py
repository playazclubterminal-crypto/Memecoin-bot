import asyncio
from datetime import datetime
import os
import re
import sqlite3
import time
from aiohttp import web
import requests
from telegram import Bot

# ================= CONFIGURATION =================
# Pulls credentials securely from Render Environment Variables
BOT_TOKEN = os.environ.get("BOT_TOKEN", "YOUR_BOT_TOKEN_HERE")
MY_CHAT_ID = os.environ.get("MY_CHAT_ID", "YOUR_CHAT_ID_HERE")
# =================================================

DB_FILE = "memecoin_bot.db"


def init_db():
  conn = sqlite3.connect(DB_FILE)
  cursor = conn.cursor()
  cursor.execute("""
        CREATE TABLE IF NOT EXISTS calls (
            ca TEXT PRIMARY KEY,
            name TEXT,
            entry_price REAL,
            ath_multiplier REAL,
            status TEXT,
            notified_milestones TEXT,
            timestamp TEXT
        )
    """)
  conn.commit()
  conn.close()


init_db()


def extract_solana_ca(text):
  match = re.search(r"\b[1-9A-HJ-NP-Za-km-z]{32,44}\b", text)
  return match.group(0) if match else None


def fetch_token_data(ca):
  try:
    url = f"https://api.dexscreener.com/latest/dex/tokens/{ca}"
    res = requests.get(url, timeout=10).json()
    pairs = res.get("pairs", [])
    if not pairs:
      return None
    p = pairs[0]
    created_at = p.get("pairCreatedAt", time.time() * 1000)
    age_hours = (time.time() * 1000 - created_at) / (1000 * 3600)
    return {
        "name": p.get("baseToken", {}).get("symbol", "UNKNOWN"),
        "price": float(p.get("priceUsd", 0)),
        "liquidity": p.get("liquidity", {}).get("usd", 0),
        "pair_age_hours": age_hours,
        "dex": p.get("dexId", "unknown"),
    }
  except Exception:
    return None


async def send_telegram_message(text):
  if not BOT_TOKEN or BOT_TOKEN == "YOUR_BOT_TOKEN_HERE":
    print(f"Skipping Telegram send (Token not set): {text}")
    return
  bot = Bot(token=BOT_TOKEN)
  await bot.send_message(
      chat_id=MY_CHAT_ID,
      text=text,
      parse_mode="Markdown",
      disable_web_page_preview=True,
  )


async def price_monitor_loop():
  while True:
    try:
      conn = sqlite3.connect(DB_FILE)
      cursor = conn.cursor()
      cursor.execute(
          "SELECT ca, name, entry_price, notified_milestones FROM calls WHERE"
          " status='ACTIVE'"
      )
      active_calls = cursor.fetchall()
      conn.close()

      for ca, name, entry_price, notified in active_calls:
        if not entry_price or entry_price <= 0:
          continue
        data = fetch_token_data(ca)
        if not data:
          continue

        current_price = data["price"]
        mult = current_price / entry_price

        milestones = [2.0, 3.0, 4.0, 5.0, 10.0]
        notified_list = [float(x) for x in notified.split(",") if x]
        new_notified = notified_list.copy()

        for m in milestones:
          if mult >= m and m not in notified_list:
            msg = (
                f"🚀 **MILESTONE REACHED!** 🚀\n\n"
                f"🪙 Token: `${name}`\n"
                f"📈 Hit **{m}X** multiplier! (`+{int((m - 1) * 100)}%`)\n"
                f"📍 CA: `{ca}`\n\n"
                f"🔗 [GMGN Terminal](https://gmgn.ai/sol/token/{ca})"
            )
            await send_telegram_message(msg)
            new_notified.append(m)

        conn = sqlite3.connect(DB_FILE)
        cursor = conn.cursor()
        cursor.execute(
            "UPDATE calls SET ath_multiplier = MAX(ath_multiplier, ?),"
            " notified_milestones = ? WHERE ca = ?",
            (mult, ",".join(map(str, new_notified)), ca),
        )
        conn.commit()
        conn.close()
        await asyncio.sleep(2)
    except Exception as e:
      print(f"Error in price monitor: {e}")
    await asyncio.sleep(30)


async def midnight_recap_loop():
  while True:
    now = datetime.now()
    target = (
        now.replace(hour=0, minute=0, second=0, microsecond=0)).timestamp() + 86400
    await asyncio.sleep(max(target - time.time(), 60))

    try:
      conn = sqlite3.connect(DB_FILE)
      cursor = conn.cursor()
      cursor.execute(
          "SELECT name, ath_multiplier, ca FROM calls ORDER BY ath_multiplier"
          " DESC LIMIT 10"
      )
      top_calls = cursor.fetchall()
      conn.close()

      if top_calls:
        recap = "🌙 **DAILY TOP 10 MEMECOIN RECAP** 📊\n\n"
        for i, (name, ath, ca) in enumerate(top_calls, 1):
          recap += (
              f"{i}. `${name}` — Peak: 📈 **{ath:.1f}X** |"
              f" [GMGN](https://gmgn.ai/sol/token/{ca})\n"
          )
        await send_telegram_message(recap)
    except Exception as e:
      print(f"Error sending recap: {e}")


# Simple HTTP web server to satisfy Render free-tier web service requirements
async def handle_ping(request):
  return web.Response(text="Bot is running!")


async def run_web_server():
  app = web.Application()
  app.router.add_get("/", handle_ping)
  runner = web.AppRunner(app)
  await runner.setup()
  port = int(os.environ.get("PORT", 10000))
  site = web.TCPSite(runner, "0.0.0.0", port)
  await site.start()
  print(f"Web server started on port {port}")


async def main():
  print("🤖 Memecoin Engine Booting...")
  await run_web_server()
  asyncio.create_task(price_monitor_loop())
  asyncio.create_task(midnight_recap_loop())
  while True:
    await asyncio.sleep(3600)


if __name__ == "__main__":
  asyncio.run(main())
  
