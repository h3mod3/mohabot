import asyncio
import re
import logging
import time
import os
from telethon import TelegramClient, events
from quotexapi.stable_api import Quotex

# --- Basic Configuration (Edit as needed) ---

API_ID = 24984216
API_HASH = '4b8bfd48b288ad7d5b636a3769ec9ed1'
SESSION_NAME = 'telegram_session'

PRIVATE_CHANNEL_ID = -1002521341661
CALL_STICKER_ID = 6244745318668177162
PUT_STICKER_ID = 6244635754052456590

# Logging setup
logging.basicConfig(
    format='%(asctime)s - %(name)s - %(levelname)s - %(message)s',
    level=logging.INFO
)
logger = logging.getLogger(__name__)

# Initialize API & Telegram
api = Quotex()
client = TelegramClient(SESSION_NAME, API_ID, API_HASH)

TRADE_AMOUNT = 0.0
last_signal_info = None
last_executed_trade = {}

def parse_first_signal_message(message_text):
    try:
        lines = message_text.upper().splitlines()
        if not lines:
            return None

        signal_line = lines[0].strip()
        pattern = re.compile(r'([A-Z]{3})\s?([A-Z]{3})\s+(LIVE|OTC)\s+NEXT\s+(\d+)\s+MINUTES?')
        match = pattern.search(signal_line)

        if not match:
            return None

        asset1, asset2, market_type, duration_str = match.groups()
        asset = f"{asset1}{asset2}"
        if market_type == 'OTC':
            asset += "_otc"

        duration_seconds = int(duration_str) * 60
        logger.info(f"Parsed first signal: asset={asset}, duration={duration_seconds}s")
        return {"asset": asset, "duration": duration_seconds}
    except Exception as e:
        logger.error(f"Error parsing first signal: {e}")
        return None

@client.on(events.NewMessage(chats=PRIVATE_CHANNEL_ID))
async def combined_handler(event):
    global last_signal_info, last_executed_trade

    if event.message.sticker:
        sticker_id = event.message.sticker.id
        logger.info(f"Sticker received. Sticker ID: {sticker_id}")

        direction = None
        if sticker_id == CALL_STICKER_ID:
            direction = "call"
        elif sticker_id == PUT_STICKER_ID:
            direction = "put"
        else:
            logger.warning("Unknown sticker received. Ignored.")
            last_signal_info = None
            last_executed_trade = {}
            return

        if last_signal_info:
            logger.info(f"Assembled new trade signal. Direction: {direction.upper()}")
            final_trade_info = {
                "asset": last_signal_info["asset"],
                "duration": last_signal_info["duration"],
                "direction": direction,
                "amount": TRADE_AMOUNT,
                "martingale_level": 0
            }
            last_signal_info = None
            await execute_trade(final_trade_info)
        elif last_executed_trade and last_executed_trade["direction"] == direction:
            time_since_last_trade = time.time() - last_executed_trade.get("timestamp", 0)
            if time_since_last_trade < (last_executed_trade["duration"] + 15):
                logger.info("Martingale signal detected.")
                martingale_trade_info = {
                    "asset": last_executed_trade["asset"],
                    "duration": last_executed_trade["duration"],
                    "direction": direction,
                    "amount": last_executed_trade["amount"] * 2,
                    "martingale_level": last_executed_trade["martingale_level"] + 1
                }
                await execute_trade(martingale_trade_info)
            else:
                logger.warning("Martingale signal received too late. Ignored.")
                last_executed_trade = {}
        else:
            logger.warning("Direction sticker without prior signal. Ignored.")
        return

    if event.message.text:
        parsed_data = parse_first_signal_message(event.message.text)
        if parsed_data:
            logger.info("Signal info saved. Awaiting direction sticker...")
            last_signal_info = parsed_data
            last_executed_trade = {}
        else:
            last_signal_info = None
            last_executed_trade = {}
            logger.info("Unrecognized text. Signal memory cleared.")

async def execute_trade(trade_info):
    global last_executed_trade
    logger.info(f"Attempting to execute trade: {trade_info}")

    try:
        asset_name, asset_data = await api.get_available_asset(trade_info["asset"], force_open=True)
        is_otc_signal = "_otc" in trade_info["asset"]
        is_otc_found = "_otc" in asset_name

        if (asset_data and asset_data[2]) and (is_otc_signal == is_otc_found):
            logger.info(f"Asset '{asset_name}' is open. Executing ${trade_info['amount']}...")
            status, buy_info = await api.buy(
                amount=trade_info['amount'],
                asset=asset_name,
                direction=trade_info["direction"],
                duration=trade_info["duration"],
                time_mode="TIME"
            )
            if status:
                logger.info(f"Trade opened. Trade ID: {buy_info.get('id', 'N/A')}")
                last_executed_trade = {
                    "asset": asset_name,
                    "direction": trade_info["direction"],
                    "duration": trade_info["duration"],
                    "amount": trade_info["amount"],
                    "martingale_level": trade_info["martingale_level"],
                    "timestamp": time.time()
                }
            else:
                logger.error(f"Trade failed: {buy_info}")
                last_executed_trade = {}
        else:
            logger.warning(f"Ignored signal. Asset mismatch or market closed.")
            last_executed_trade = {}
    except Exception as e:
        logger.error(f"Error during trade execution: {e}")
        last_executed_trade = {}

async def main():
    global TRADE_AMOUNT

    try:
        TRADE_AMOUNT = float(os.getenv("TRADE_AMOUNT", "1.0"))
        if TRADE_AMOUNT <= 0:
            raise ValueError("TRADE_AMOUNT must be greater than 0.")
    except Exception as e:
        logger.critical(f"❌ خطأ في قراءة TRADE_AMOUNT: {e}")
        return

    logger.info(f"✅ تم تعيين مبلغ الصفقة الأساسي: ${TRADE_AMOUNT}")
    logger.info("--- Starting the trading bot ---")

    logger.info("Attempting to connect to Quotex account...")
    check, reason = await api.connect()
    if not check:
        logger.critical(f"Failed to connect to Quotex: {reason}. Please check your credentials in settings/config.ini")
        return

    logger.info(f"Connected to Quotex successfully. Active account: {'Demo' if api.account_is_demo else 'Real'}")
    
    await client.start()
    logger.info("Connected to Telegram. Bot is now listening for messages in the channel...")
    
    await client.run_until_disconnected()
    logger.info("Telegram disconnected.")

if __name__ == '__main__':
    try:
        asyncio.run(main())
    except Exception as e:
        logger.critical(f"حدث خطأ فادح أدى إلى إيقاف البرنامج: {e}")
    finally:
        logger.info("--- تم إيقاف الروبوت ---")
