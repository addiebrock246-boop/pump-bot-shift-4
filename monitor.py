#!/usr/bin/env python3
"""
Pump.fun Token Monitor via DexScreener - 5 Minute Updates
- Only monitors Pump.fun tokens on Solana
- Checks every 5 minutes
- ₹100 investment breakdown (tokens, 2x/5x/10x profit)
- Pump.fun buy link
"""

import asyncio
import aiohttp
import json
import logging
import time
from datetime import datetime
from typing import Dict, List, Optional
from telegram import Bot

# ========== CONFIGURATION ==========
TELEGRAM_BOT_TOKEN = "8870427358:AAFeiXpIQ8JnYs8ZVZ_6Vbzvcj1GTjVwMKg"
TELEGRAM_CHAT_ID = "5964851833"

# Shift configuration - CHANGE THIS PER REPOSITORY
SHIFT_NAME = "Shift 4"
SHIFT_TIMING = "6 PM - 12 AM"

INVEST_AMOUNT_INR = 100
INR_PER_USD = 83.0
CHECK_INTERVAL = 300  # 5 minutes
MIN_PROFIT_FOR_ALERT = 1000  # ₹1000+ profit wale tokens dikhao

# Track tokens we've already alerted about
alerted_tokens = set()
# Track token price history for growth calculation
token_prices = {}  # mint -> {'first_price': float, 'name': str, 'symbol': str, 'pair_address': str}

logging.basicConfig(level=logging.INFO, format='%(asctime)s - %(levelname)s - %(message)s')
logger = logging.getLogger(__name__)

# ========== HELPER FUNCTIONS ==========
def usd_to_inr(usd: float) -> float:
    return usd * INR_PER_USD

def format_number(num: float) -> str:
    if num >= 1_000_000_000:
        return f"{num/1_000_000_000:.1f}B"
    if num >= 1_000_000:
        return f"{num/1_000_000:.1f}M"
    if num >= 1_000:
        return f"{num/1_000:.1f}K"
    return f"{num:.2f}"

def calculate_tokens(price_usd: float) -> float:
    if price_usd <= 0:
        return 0
    return (INVEST_AMOUNT_INR / INR_PER_USD) / price_usd

def calculate_profit(price_usd: float, multiplier: int) -> float:
    usd_amount = INVEST_AMOUNT_INR / INR_PER_USD
    return (usd_amount * multiplier) * INR_PER_USD

def is_pump_fun_token(pair: Dict) -> bool:
    """Check if token is from Pump.fun"""
    # Check chain is Solana
    if pair.get('chainId', '').lower() != 'solana':
        return False
    
    # Check pair address ends with 'pump' (Pump.fun tokens pattern)
    pair_address = pair.get('pairAddress', '').lower()
    if pair_address.endswith('pump'):
        return True
    
    # Also check if base token symbol or name contains pump-related patterns
    base_token = pair.get('baseToken', {})
    if 'pump' in base_token.get('symbol', '').lower():
        return True
    
    return False

# ========== DEXSCREENER API ==========
async def fetch_new_pairs() -> List[Dict]:
    """Fetch latest token pairs from DexScreener"""
    async with aiohttp.ClientSession() as session:
        url = "https://api.dexscreener.com/latest/dex/search?q=solana"
        headers = {
            'User-Agent': 'Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36'
        }
        try:
            async with session.get(url, timeout=15) as resp:
                if resp.status == 200:
                    data = await resp.json()
                    pairs = data.get('pairs', [])
                    # Filter only Pump.fun tokens
                    pump_pairs = [p for p in pairs if is_pump_fun_token(p)]
                    # Sort by creation time (newest first) - DexScreener returns sorted
                    return pump_pairs[:20]  # Top 20 newest
                else:
                    logger.error(f"DexScreener API error: {resp.status}")
                    return []
        except Exception as e:
            logger.error(f"DexScreener fetch error: {e}")
            return []

async def get_token_price(mint: str) -> Optional[Dict]:
    """Get current price and details for a specific token"""
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
                            'volume_24h_usd': float(p.get('volume', {}).get('h24', 0)),
                        }
        except Exception as e:
            logger.error(f"Price fetch error for {mint}: {e}")
        return None

# ========== TELEGRAM ==========
async def send_telegram_message(text: str):
    bot = Bot(token=TELEGRAM_BOT_TOKEN)
    try:
        await bot.send_message(
            chat_id=TELEGRAM_CHAT_ID,
            text=text,
            parse_mode='Markdown',
            disable_web_page_preview=True
        )
        logger.info("Telegram message sent")
    except Exception as e:
        logger.error(f"Telegram error: {e}")

async def send_startup_message():
    """Send startup message for this shift"""
    await send_telegram_message(f"✅ {SHIFT_NAME} ({SHIFT_TIMING}): Bot started. Monitoring Pump.fun via DexScreener...")

async def send_token_alert(token_data: Dict, first_price: float, current_price: float):
    """Send detailed token alert with ₹100 breakdown"""
    tokens = calculate_tokens(first_price)
    profit_2x = calculate_profit(first_price, 2)
    profit_5x = calculate_profit(first_price, 5)
    profit_10x = calculate_profit(first_price, 10)
    growth_pct = ((current_price - first_price) / first_price) * 100 if first_price > 0 else 0
    current_value = tokens * current_price * INR_PER_USD
    
    mint = token_data.get('mint', '')
    msg = f"""
🚨 *NEW PUMP.FUN TOKEN DETECTED!*

*Token:* {token_data.get('name', 'N/A')} (${token_data.get('symbol', 'N/A')})
*Mint:* `{mint}`

📊 *Current Stats*
💰 Price: ₹{usd_to_inr(current_price):.6f} (${current_price:.8f})
📈 5m Change: {token_data.get('price_change_5m', 0):.1f}%
🏦 Market Cap: ₹{format_number(usd_to_inr(token_data.get('market_cap_usd', 0)))}
💧 Liquidity: ₹{format_number(usd_to_inr(token_data.get('liquidity_usd', 0)))}

📈 *Growth Since Detection*
• First seen: ₹{usd_to_inr(first_price):.6f}
• Now: ₹{usd_to_inr(current_price):.6f}
• Growth: {growth_pct:.1f}%

💰 *₹{INVEST_AMOUNT_INR} INVESTMENT BREAKDOWN*
• Tokens received: {tokens:,.0f}
• Value now: ₹{current_value:.2f}
• Profit: ₹{current_value - INVEST_AMOUNT_INR:.2f}

🎯 *Targets (if price reaches):*
• 2x → ₹{profit_2x:.2f}
• 5x → ₹{profit_5x:.2f}
• 10x → ₹{profit_10x:.2f}

🔗 *Buy:* [Pump.fun](https://pump.fun/{mint})
📊 *Chart:* [DexScreener](https://dexscreener.com/solana/{mint})
"""
    await send_telegram_message(msg)
    logger.info(f"Alert sent for {token_data.get('symbol')}")

# ========== CORE LOGIC ==========
async def check_tokens():
    """Main loop - checks every 5 minutes"""
    while True:
        try:
            logger.info(f"[{SHIFT_NAME}] Checking for new Pump.fun tokens...")
            pairs = await fetch_new_pairs()
            
            for pair in pairs:
                base_token = pair.get('baseToken', {})
                mint = base_token.get('address', '')
                symbol = base_token.get('symbol', 'Unknown')
                name = base_token.get('name', 'Unknown')
                
                if not mint:
                    continue
                
                # Skip if already alerted
                if mint in alerted_tokens:
                    continue
                
                # Get current price
                current_price = float(pair.get('priceUsd', 0))
                if current_price <= 0:
                    continue
                
                # Store first price
                if mint not in token_prices:
                    token_prices[mint] = {
                        'first_price': current_price,
                        'name': name,
                        'symbol': symbol,
                        'pair_address': pair.get('pairAddress', ''),
                        'detected_at': time.time()
                    }
                    
                    # Send alert for new token
                    token_data = {
                        'mint': mint,
                        'name': name,
                        'symbol': symbol,
                        'price_change_5m': float(pair.get('priceChange', {}).get('m5', 0)),
                        'market_cap_usd': float(pair.get('marketCap', 0)),
                        'liquidity_usd': float(pair.get('liquidity', {}).get('usd', 0))
                    }
                    await send_token_alert(token_data, current_price, current_price)
                    alerted_tokens.add(mint)
                    logger.info(f"New token alerted: {symbol}")
                
                # Check for significant growth (>1000% from first price)
                elif mint in token_prices:
                    first_price = token_prices[mint]['first_price']
                    growth_pct = ((current_price - first_price) / first_price) * 100 if first_price > 0 else 0
                    
                    # If token grew significantly and we already alerted, send update
                    if growth_pct >= 1000 and mint not in alerted_tokens:
                        token_data = {
                            'mint': mint,
                            'name': name,
                            'symbol': symbol,
                            'price_change_5m': float(pair.get('priceChange', {}).get('m5', 0)),
                            'market_cap_usd': float(pair.get('marketCap', 0)),
                            'liquidity_usd': float(pair.get('liquidity', {}).get('usd', 0))
                        }
                        await send_token_alert(token_data, first_price, current_price)
                        alerted_tokens.add(mint)
                        logger.info(f"Growth alert sent for {symbol}: {growth_pct:.1f}%")
            
            # Cleanup old tokens (older than 2 hours)
            now = time.time()
            to_remove = []
            for mint, data in token_prices.items():
                if now - data['detected_at'] > 7200:  # 2 hours
                    to_remove.append(mint)
            for mint in to_remove:
                del token_prices[mint]
                alerted_tokens.discard(mint)
            
        except Exception as e:
            logger.error(f"Check error: {e}")
        
        # Wait for next check
        logger.info(f"[{SHIFT_NAME}] Next check in {CHECK_INTERVAL//60} minutes...")
        await asyncio.sleep(CHECK_INTERVAL)

# ========== MAIN ==========
async def main():
    logger.info(f"Starting {SHIFT_NAME} ({SHIFT_TIMING}) Bot...")
    await send_startup_message()
    await check_tokens()

if __name__ == "__main__":
    asyncio.run(main())
