import asyncio
import re
import logging
import os
from telethon import TelegramClient, events
from quotexapi.stable_api import Quotex
import time

# --- Configuration using Environment Variables ---
API_ID = int(os.getenv("API_ID"))
API_HASH = os.getenv("API_HASH")
SESSION_NAME = 'telegram_session'

PRIVATE_CHANNEL_ID = int(os.getenv("PRIVATE_CHANNEL_ID", "-1002521341661"))
CALL_STICKER_ID = int(os.getenv("CALL_STICKER_ID", "6244745318668177162"))
PUT_STICKER_ID = int(os.getenv("PUT_STICKER_ID", "6244635754052456590"))

EMAIL = os.getenv("QUOTEX_EMAIL")
PASSWORD = os.getenv("QUOTEX_PASSWORD")
ACCOUNT_TYPE = os.getenv("ACCOUNT_TYPE", "demo")

# Logging settings
logging.basicConfig(format='%(asctime)s - %(name)s - %(levelname)s - %(message)s', level=logging.INFO)
logger = logging.getLogger(__name__)

# Initialize Quotex API
api = Quotex(email=EMAIL, password=PASSWORD, account_type=ACCOUNT_TYPE)

# Initialize Telegram client
client = TelegramClient(SESSION_NAME, API_ID, API_HASH)

# Global variables
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
                logger.warning("Martingale signal received too late. Ignored as a new trade.")
                last_executed_trade = {}
        else:
            logger.warning("Direction sticker received without prior signal or mismatched. Ignored.")
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
            logger.info("Unrecognized text message. Cleared signal memory.")

async def execute_trade(trade_info):
    global last_executed_trade
    logger.info(f"Attempting to execute trade: {trade_info}")
    try:
        asset_name, asset_data = await api.get_available_asset(trade_info["asset"], force_open=True)
        is_otc_signal = "_otc" in trade_info["asset"]
        is_otc_found = "_otc" in asset_name
        if (asset_data and asset_data[2]) and (is_otc_signal == is_otc_found):
            logger.info(f"Asset '{asset_name}' is open and in the correct market. Executing ${trade_info['amount']}...")
            status, buy_info = await api.buy(
                amount=trade_info['amount'],
                asset=asset_name,
                direction=trade_info["direction"],
                duration=trade_info["duration"],
                time_mode="TIME"
            )
            if status:
                logger.info(f"Trade opened successfully. Trade ID: {buy_info.get('id', 'N/A')}")
                last_executed_trade = {
                    "asset": asset_name,
                    "direction": trade_info["direction"],
                    "duration": trade_info["duration"],
                    "amount": trade_info["amount"],
                    "martingale_level": trade_info["martingale_level"],
                    "timestamp": time.time()
                }
            else:
                logger.error(f"Failed to open trade. Reason: {buy_info}")
                last_executed_trade = {}
        else:
            market_status = "open" if (asset_data and asset_data[2]) else "closed"
            logger.warning(f"Ignored signal. Expected asset: '{trade_info['asset']}'. Found: '{asset_name}'. Market status: {market_status}.")
            last_executed_trade = {}
    except Exception as e:
        logger.error(f"Unexpected error during trade execution: {e}")
        last_executed_trade = {}

async def main():
    global TRADE_AMOUNT
    while True:
        try:
            amount_input = input("Enter base trade amount (e.g., 1.5): ")
            TRADE_AMOUNT = float(amount_input)
            if TRADE_AMOUNT > 0:
                break
            else:
                print("Please enter a positive amount greater than zero.")
        except ValueError:
            print("Invalid input. Please enter a decimal or integer value.")
    logger.info(f"Base trade amount set: ${TRADE_AMOUNT}")
    logger.info("--- Starting the trading bot ---")
    logger.info("Attempting to connect to Quotex account...")
    check, reason = await api.connect()
    if not check:
        logger.critical(f"Failed to connect to Quotex: {reason}. Please check your credentials.")
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
        logger.critical(f"Fatal error occurred. Bot stopped: {e}")
    finally:
        logger.info("--- Bot stopped ---")
