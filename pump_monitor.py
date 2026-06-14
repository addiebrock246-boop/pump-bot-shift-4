#!/usr/bin/env python3
import asyncio
import aiohttp
import json
import logging
from datetime import datetime
from typing import Dict, Tuple
import websockets
from telegram import Bot

# ========== YOUR CREDENTIALS (as provided) ==========
TELEGRAM_BOT_TOKEN = "8870427358:AAFeiXpIQ8JnYs8ZVZ_6Vbzvcj1GTjVwMKg"  # Compromised, change later
TELEGRAM_CHAT_ID = "5964851833"
# ======================================================

# ========== SHIFT INFORMATION (Change for Shift 4) ==========
SHIFT_NAME = "Shift 4"          # ← Shift 4 ke liye change
SHIFT_TIMING = "6 PM - 12 AM"   # ← Shift 4 ke liye change
# ============================================================

WS_URL = "wss://pumpportal.fun/api/data"
ANALYSIS_WINDOW = 3600
ALERT_THRESHOLD = 7

# ... (baaki poora code same hai, maine neeche pura copy-paste kar diya hai)

logging.basicConfig(level=logging.INFO, format='%(asctime)s - %(levelname)s - %(message)s')
logger = logging.getLogger(__name__)

def usd_to_inr(usd: float) -> float:
    return usd * 83.0

def format_number(num: float) -> str:
    if num >= 1_000_000_000:
        return f"{num/1_000_000_000:.1f}B"
    if num >= 1_000_000:
        return f"{num/1_000_000:.1f}M"
    if num >= 1_000:
        return f"{num/1_000:.1f}K"
    return f"{num:.2f}"

def calculate_score(token_data: Dict) -> Tuple[int, Dict]:
    score = 0
    details = {}
    buyers = token_data.get('unique_buyers', 0)
    if buyers >= 30:
        score += 2
        details['buyers'] = f"+2 ({buyers} buyers)"
    else:
        details['buyers'] = f"+0 (only {buyers})"
    
    buy_vol = token_data.get('buy_volume_usd', 0)
    sell_vol = token_data.get('sell_volume_usd', 0)
    if buy_vol > sell_vol * 2:
        score += 2
        details['volume'] = f"+2 (Buy {format_number(buy_vol)} / Sell {format_number(sell_vol)})"
    else:
        details['volume'] = f"+0 (Ratio: {buy_vol/sell_vol:.1f}x)" if sell_vol else "+0"
    
    if not token_data.get('creator_sold', False):
        score += 1
        details['creator'] = "+1 (Creator not sold)"
    else:
        details['creator'] = "+0 (Creator sold!)"
    
    if token_data.get('age_hours', 0) >= 1:
        score += 1
        details['age'] = "+1 (Alive 1h)"
    else:
        details['age'] = "+0"
    
    liq = token_data.get('liquidity_usd', 0)
    if liq >= 5000:
        score += 1
        details['liquidity'] = f"+1 (Liq ${format_number(liq)})"
    else:
        details['liquidity'] = f"+0 (Liq ${format_number(liq)})"
    
    ch5 = token_data.get('price_change_5m', 0)
    ch15 = token_data.get('price_change_15m', 0)
    ch1 = token_data.get('price_change_1h', 0)
    if ch5 > 0 and ch15 > 0 and ch1 > 0:
        score += 1
        details['trend'] = "+1 (Positive across all)"
    else:
        details['trend'] = "+0"
    
    top10 = token_data.get('top10_holder_pct', 100)
    if top10 < 50:
        score += 1
        details['holders'] = f"+1 (Top10 {top10:.1f}%)"
    else:
        details['holders'] = f"+0 (Top10 {top10:.1f}%)"
    
    details['data'] = "+1 (Data verified)"
    score += 1
    return min(score, 10), details

def reject_token(token_data: Dict) -> Tuple[bool, str]:
    if token_data.get('creator_sold', False):
        return True, "Creator sold"
    if token_data.get('unique_buyers', 0) < 10:
        return True, f"Only {token_data.get('unique_buyers', 0)} buyers"
    if token_data.get('top_holder_pct', 0) > 40:
        return True, f"Top holder {token_data.get('top_holder_pct', 0):.1f}%"
    if token_data.get('liquidity_usd', 0) < 1000:
        return True, f"Low liquidity ${format_number(token_data.get('liquidity_usd', 0))}"
    if token_data.get('price_change_1h', 0) > 1000:
        return True, f"Pump suspect {token_data.get('price_change_1h', 0):.0f}%"
    return False, ""

async def fetch_dexscreener_data(mint: str) -> Dict:
    async with aiohttp.ClientSession() as session:
        url = f"https://api.dexscreener.com/latest/dex/tokens/{mint}"
        try:
            async with session.get(url, timeout=10) as resp:
                if resp.status == 200:
                    data = await resp.json()
                    if data.get('pairs') and len(data['pairs']) > 0:
                        p = data['pairs'][0]
                        return {
                            'price_usd': float(p.get('priceUsd', 0)),
                            'liquidity_usd': float(p.get('liquidity', {}).get('usd', 0)),
                            'price_change_5m': float(p.get('priceChange', {}).get('m5', 0)),
                            'price_change_1h': float(p.get('priceChange', {}).get('h1', 0)),
                            'market_cap_usd': float(p.get('marketCap', 0)),
                            'symbol': p.get('baseToken', {}).get('symbol', ''),
                            'name': p.get('baseToken', {}).get('name', ''),
                        }
        except Exception as e:
            logger.error(f"DexScreener error: {e}")
    return {}

async def fetch_birdeye_data(mint: str) -> Dict:
    async with aiohttp.ClientSession() as session:
        url = f"https://public-api.birdeye.so/defi/token_security?address={mint}"
        try:
            async with session.get(url, timeout=10) as resp:
                if resp.status == 200:
                    data = await resp.json()
                    if data.get('success'):
                        d = data.get('data', {})
                        return {
                            'unique_buyers': d.get('unique_buyers', 0),
                            'unique_sellers': d.get('unique_sellers', 0),
                            'buy_volume_usd': d.get('buy_volume_usd', 0),
                            'sell_volume_usd': d.get('sell_volume_usd', 0),
                            'top_holder_pct': d.get('top_holder_percent', 0),
                            'top10_holder_pct': d.get('top_10_holder_percent', 100),
                            'creator_sold': d.get('creator_sold', False),
                        }
        except Exception as e:
            logger.error(f"Birdeye error: {e}")
    return {}

async def send_alert(token: Dict, score: int, details: Dict):
    bot = Bot(token=TELEGRAM_BOT_TOKEN)
    price_inr = usd_to_inr(token.get('price_usd', 0))
    mc_inr = usd_to_inr(token.get('market_cap_usd', 0))
    liq_inr = usd_to_inr(token.get('liquidity_usd', 0))
    msg = f"""
🔥 Pump.fun Token Alert 🔥

<b>Name:</b> {token.get('name', 'N/A')}
<b>Symbol:</b> {token.get('symbol', 'N/A')}
<b>Mint:</b> <code>{token.get('mint')}</code>

📊 <b>Stats</b>
Price: ₹{price_inr:.6f}
Market Cap: ₹{format_number(mc_inr)}
Liquidity: ₹{format_number(liq_inr)}
1h change: +{token.get('price_change_1h', 0):.0f}%

👥 <b>Activity</b>
Buyers: {token.get('unique_buyers', 0)}
Sellers: {token.get('unique_sellers', 0)}
Buy/Sell ratio: {token.get('buy_volume_usd', 0)/max(1,token.get('sell_volume_usd',1)):.1f}x
Creator sold: {'Yes' if token.get('creator_sold') else 'No'}

🎯 <b>Targets (from current)</b>
2x → ₹{price_inr*2:.6f}
5x → ₹{price_inr*5:.6f}
10x → ₹{price_inr*10:.6f}

⭐ <b>Score: {score}/10</b>
📋 <b>Breakdown</b>
""" + "\n".join([f"• {v}" for v in details.values()]) + f"""

<b>Verdict:</b> {'✅ Watchlist' if score>=8 else '⚠️ Manual check'}
"""
    try:
        await bot.send_message(chat_id=TELEGRAM_CHAT_ID, text=msg, parse_mode='HTML')
        logger.info(f"Alert sent for {token.get('symbol')}")
    except Exception as e:
        logger.error(f"Telegram error: {e}")

async def process_token(mint: str, creation_data: Dict):
    logger.info(f"Waiting {ANALYSIS_WINDOW}s for token {mint}...")
    await asyncio.sleep(ANALYSIS_WINDOW)
    dex = await fetch_dexscreener_data(mint)
    birdeye = await fetch_birdeye_data(mint)
    if not dex and not birdeye:
        logger.warning(f"No data for {mint}")
        return
    token_data = {
        'mint': mint,
        'name': dex.get('name', creation_data.get('name', 'Unknown')),
        'symbol': dex.get('symbol', creation_data.get('symbol', 'Unknown')),
        'age_hours': ANALYSIS_WINDOW / 3600,
        **dex,
        **birdeye
    }
    reject, reason = reject_token(token_data)
    if reject:
        logger.info(f"Rejected {mint}: {reason}")
        return
    score, details = calculate_score(token_data)
    if score >= ALERT_THRESHOLD:
        await send_alert(token_data, score, details)
    else:
        logger.info(f"Score {score} < {ALERT_THRESHOLD} for {mint}")

async def listen():
    while True:
        try:
            async with websockets.connect(WS_URL) as ws:
                logger.info("Connected to Pump Portal")
                await ws.send(json.dumps({"method": "subscribeNewToken", "params": {}}))
                async for msg in ws:
                    data = json.loads(msg)
                    if data.get('type') == 'newToken':
                        mint = data.get('mint')
                        if mint:
                            logger.info(f"New token: {mint}")
                            asyncio.create_task(process_token(mint, data))
        except Exception as e:
            logger.error(f"WS error: {e}, reconnecting in 5s")
            await asyncio.sleep(5)

async def send_startup_message():
    """Send a Telegram message when the bot starts for this shift."""
    bot = Bot(token=TELEGRAM_BOT_TOKEN)
    msg = f"✅ {SHIFT_NAME} ({SHIFT_TIMING}): Bot started. Monitoring Pump.fun..."
    try:
        await bot.send_message(chat_id=TELEGRAM_CHAT_ID, text=msg)
        logger.info("Startup message sent")
    except Exception as e:
        logger.error(f"Failed to send startup message: {e}")

async def main():
    logger.info(f"Starting {SHIFT_NAME} ({SHIFT_TIMING}) Bot...")
    await send_startup_message()
    await listen()

if __name__ == "__main__":
    asyncio.run(main())
