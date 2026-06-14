#!/usr/bin/env python3
import asyncio
import aiohttp
import json
import logging
import time
from datetime import datetime
from typing import Dict, List
import websockets
from telegram import Bot

# ========== CONFIGURATION ==========
TELEGRAM_BOT_TOKEN = "8870427358:AAFeiXpIQ8JnYs8ZVZ_6Vbzvcj1GTjVwMKg"
TELEGRAM_CHAT_ID = "5964851833"

# Shift 4 specific
SHIFT_NAME = "Shift 4"
SHIFT_TIMING = "6 PM - 12 AM"

INVEST_AMOUNT_INR = 100
INR_PER_USD = 83.0
MIN_PROFIT_FOR_ALERT = 1000
WS_URL = "wss://pumpportal.fun/api/data"
# ==================================

logging.basicConfig(level=logging.INFO, format='%(asctime)s - %(levelname)s - %(message)s')
logger = logging.getLogger(__name__)

pending_tokens = {}
trending_scores = {}

def usd_to_inr(usd: float) -> float:
    return usd * INR_PER_USD

def calculate_tokens(price_usd: float) -> float:
    if price_usd <= 0:
        return 0
    return (INVEST_AMOUNT_INR / INR_PER_USD) / price_usd

def calculate_profit(initial_price_usd: float, current_price_usd: float) -> dict:
    tokens = calculate_tokens(initial_price_usd)
    current_value_inr = tokens * current_price_usd * INR_PER_USD
    profit_inr = current_value_inr - INVEST_AMOUNT_INR
    growth_pct = ((current_price_usd / initial_price_usd) - 1) * 100
    return {
        'tokens': tokens,
        'current_value_inr': current_value_inr,
        'profit_inr': profit_inr,
        'growth_percent': growth_pct
    }

async def fetch_current_price(mint: str) -> float:
    async with aiohttp.ClientSession() as session:
        url = f"https://api.dexscreener.com/latest/dex/tokens/{mint}"
        try:
            async with session.get(url, timeout=10) as resp:
                if resp.status == 200:
                    data = await resp.json()
                    if data.get('pairs') and len(data['pairs']) > 0:
                        return float(data['pairs'][0].get('priceUsd', 0))
        except Exception:
            pass
        return 0.0

async def fetch_initial_buyers(mint: str) -> int:
    async with aiohttp.ClientSession() as session:
        url = f"https://public-api.birdeye.so/defi/token_security?address={mint}"
        try:
            async with session.get(url, timeout=10) as resp:
                if resp.status == 200:
                    data = await resp.json()
                    if data.get('success'):
                        return data.get('data', {}).get('unique_buyers', 0)
        except Exception:
            pass
        return 0

async def send_telegram_message(text: str):
    bot = Bot(token=TELEGRAM_BOT_TOKEN)
    try:
        await bot.send_message(chat_id=TELEGRAM_CHAT_ID, text=text, parse_mode='Markdown', disable_web_page_preview=True)
        logger.info("Telegram message sent")
    except Exception as e:
        logger.error(f"Telegram error: {e}")

async def check_and_send_15m_updates():
    while True:
        await asyncio.sleep(900)
        now = time.time()
        for mint, data in list(pending_tokens.items()):
            age = now - data['create_time']
            if age >= 3600:
                continue
            current_price = await fetch_current_price(mint)
            if current_price == 0:
                continue
            profit_data = calculate_profit(data['initial_price_usd'], current_price)
            if profit_data['profit_inr'] >= MIN_PROFIT_FOR_ALERT:
                msg = f"""
📢 *15-MINUTE UPDATE*
*Token:* {data['name']} (${data['symbol']})
*Mint:* `{mint}`

💰 *If you had invested ₹{INVEST_AMOUNT_INR}:*
• Tokens received: {profit_data['tokens']:,.0f}
• Value now: ₹{profit_data['current_value_inr']:.2f}
• Profit: ₹{profit_data['profit_inr']:.2f}

📈 *Price change:* {profit_data['growth_percent']:.1f}% (last 15 min)

🔗 [Buy on Pump.fun](https://pump.fun/{mint}) | [Chart](https://dexscreener.com/solana/{mint})
"""
                await send_telegram_message(msg)
                trending_scores[mint] = profit_data['growth_percent']

async def check_matured_tokens():
    while True:
        now = time.time()
        to_remove = []
        for mint, data in list(pending_tokens.items()):
            if now - data['create_time'] >= 3600:
                current_price = await fetch_current_price(mint)
                if current_price > 0:
                    profit_data = calculate_profit(data['initial_price_usd'], current_price)
                    if profit_data['profit_inr'] >= MIN_PROFIT_FOR_ALERT:
                        msg = f"""
⏰ *1‑HOUR FINAL REPORT*
*Token:* {data['name']} (${data['symbol']})
*Mint:* `{mint}`

💰 *₹{INVEST_AMOUNT_INR} investment result:*
• Tokens: {profit_data['tokens']:,.0f}
• Final value: ₹{profit_data['current_value_inr']:.2f}
• Net profit: ₹{profit_data['profit_inr']:.2f}
• Growth: {profit_data['growth_percent']:.1f}%

👥 *Initial unique buyers:* {data['buyers']}

🔗 [Buy now](https://pump.fun/{mint}) | [Chart](https://dexscreener.com/solana/{mint})
"""
                        await send_telegram_message(msg)
                to_remove.append(mint)
        for mint in to_remove:
            del pending_tokens[mint]
            trending_scores.pop(mint, None)

        if trending_scores:
            sorted_tokens = sorted(trending_scores.items(), key=lambda x: x[1], reverse=True)
            top_mint, top_gain = sorted_tokens[0]
            top_data = pending_tokens.get(top_mint) if top_mint in pending_tokens else None
            if top_data:
                top_msg = f"""
🏆 *CURRENT TOP PERFORMER (last 1 hour)*
*Token:* {top_data['name']} (${top_data['symbol']})
*Growth:* {top_gain:.1f}%
*Mint:* `{top_mint}`
🔗 [Buy here](https://pump.fun/{top_mint})
"""
                await send_telegram_message(top_msg)
        await asyncio.sleep(60)

async def process_new_token(mint: str, creation_data: dict):
    price = await fetch_current_price(mint)
    if price == 0:
        return
    buyers = await fetch_initial_buyers(mint)
    pending_tokens[mint] = {
        'create_time': time.time(),
        'initial_price_usd': price,
        'name': creation_data.get('name', 'Unknown'),
        'symbol': creation_data.get('symbol', 'Unknown'),
        'buyers': buyers
    }
    logger.info(f"Stored {mint} - price ${price:.8f}, buyers {buyers}")

async def listen():
    await send_telegram_message(f"✅ {SHIFT_NAME} ({SHIFT_TIMING}): Bot started. Monitoring Pump.fun...")
    while True:
        try:
            async with websockets.connect(WS_URL) as ws:
                logger.info("Connected to Pump Portal")
                await ws.send(json.dumps({"method": "subscribeNewToken"}))
                async for msg in ws:
                    data = json.loads(msg)
                    if data.get('type') == 'newToken':
                        mint = data.get('mint')
                        if mint and mint not in pending_tokens:
                            logger.info(f"New token detected: {mint}")
                            asyncio.create_task(process_new_token(mint, data))
        except Exception as e:
            logger.error(f"WebSocket error: {e}, reconnecting in 5s")
            await asyncio.sleep(5)

async def main():
    asyncio.create_task(check_matured_tokens())
    asyncio.create_task(check_and_send_15m_updates())
    await listen()

if __name__ == "__main__":
    asyncio.run(main())
