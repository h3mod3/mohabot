import asyncio
import re
import logging
from telethon import TelegramClient, events
from quotexapi.stable_api import Quotex
import time

# --- إعدادات أساسية (يجب تعديلها) ---

# 1. بيانات Telegram API
API_ID = 24984216  # استبدل بالـ API ID الخاص بك
API_HASH = '4b8bfd48b288ad7d5b636a3769ec9ed1'  # استبدل بالـ API Hash الخاص بك
SESSION_NAME = 'telegram_session'

# 2. معرف القناة الخاصة
PRIVATE_CHANNEL_ID = -1002521341661  # استبدل بالمعرف الصحيح للقناة

# 3. معرفات الملصقات (Sticker IDs)
CALL_STICKER_ID = 6244745318668177162  # استبدل بالمعرف الصحيح لـ UP/CALL
PUT_STICKER_ID = 6244635754052456590   # استبدل بالمعرف الصحيح لـ DOWN/PUT

# --- نهاية الإعدادات ---

# إعدادات التسجيل
logging.basicConfig(format='%(asctime)s - %(name)s - %(levelname)s - %(message)s', level=logging.INFO)
logger = logging.getLogger(__name__)

# تهيئة Quotex API
api = Quotex()

# تهيئة عميل التلغرام
client = TelegramClient(SESSION_NAME, API_ID, API_HASH)

# --- متغيرات عالمية جديدة ---
TRADE_AMOUNT = 0.0
last_signal_info = None  # لتخزين الإشارة الأولية (الأصل والمدة)
last_executed_trade = {} # لتخزين تفاصيل آخر صفقة تم تنفيذها للمضاعفة

def parse_first_signal_message(message_text):
    """
    يحلل الرسالة الأولى التي تحتوي على الأصل والمدة.
    """
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
        
        logger.info(f"تم تحليل الإشارة الأولية: الأصل={asset}, المدة={duration_seconds}s")
        return {"asset": asset, "duration": duration_seconds}

    except Exception as e:
        logger.error(f"خطأ في تحليل الرسالة الأولية: {e}")
        return None

@client.on(events.NewMessage(chats=PRIVATE_CHANNEL_ID))
async def combined_handler(event):
    """
    يتعامل مع الرسائل النصية والملصقات لتجميع الإشارة وتنفيذ المضاعفات.
    """
    global last_signal_info, last_executed_trade
    
    # 1. التعامل مع الملصقات (Stickers)
    if event.message.sticker:
        sticker_id = event.message.sticker.id
        logger.info(f"تم استلام ملصق. Sticker ID: {sticker_id}")

        direction = None
        if sticker_id == CALL_STICKER_ID:
            direction = "call"
        elif sticker_id == PUT_STICKER_ID:
            direction = "put"
        else:
            logger.warning("ملصق غير معروف، تم تجاهله.")
            # مسح الذاكرة لمنع أي عمليات غير متوقعة
            last_signal_info = None
            last_executed_trade = {}
            return

        # الحالة أ: صفقة جديدة (إشارة أولية + ملصق اتجاه)
        if last_signal_info:
            logger.info(f"تجميع إشارة جديدة. الاتجاه: {direction.upper()}")
            final_trade_info = {
                "asset": last_signal_info["asset"],
                "duration": last_signal_info["duration"],
                "direction": direction,
                "amount": TRADE_AMOUNT,
                "martingale_level": 0 # صفقة أساسية
            }
            last_signal_info = None # استهلاك الإشارة الأولية
            await execute_trade(final_trade_info)

        # الحالة ب: مضاعفة (Martingale)
        elif last_executed_trade and last_executed_trade["direction"] == direction:
            # التحقق من الوقت منذ آخر صفقة لتجنب المضاعفات الخاطئة
            time_since_last_trade = time.time() - last_executed_trade.get("timestamp", 0)
            if time_since_last_trade < (last_executed_trade["duration"] + 15): # نافذة 15 ثانية للمضاعفة
                logger.info("تم اكتشاف إشارة مضاعفة (Martingale).")
                
                martingale_trade_info = {
                    "asset": last_executed_trade["asset"],
                    "duration": last_executed_trade["duration"],
                    "direction": direction,
                    "amount": last_executed_trade["amount"] * 2, # مضاعفة المبلغ
                    "martingale_level": last_executed_trade["martingale_level"] + 1
                }
                await execute_trade(martingale_trade_info)
            else:
                logger.warning("تم استلام ملصق مضاعفة بعد فترة طويلة. تم تجاهله كصفقة جديدة.")
                last_executed_trade = {} # إعادة تعيين الذاكرة
        else:
            logger.warning("تم استلام ملصق اتجاه بدون إشارة سابقة أو كإشارة مضاعفة غير متطابقة. تم تجاهله.")

        return

    # 2. التعامل مع الرسائل النصية
    if event.message.text:
        parsed_data = parse_first_signal_message(event.message.text)
        if parsed_data:
            logger.info("تم حفظ معلومات الإشارة الأولية. في انتظار ملصق الاتجاه...")
            last_signal_info = parsed_data
            last_executed_trade = {} # مسح ذاكرة المضاعفة عند استلام إشارة جديدة
        else:
            # إذا لم تكن رسالة إشارة، فمن الأفضل مسح الذاكرة
            last_signal_info = None
            last_executed_trade = {}
            logger.info("رسالة نصية غير معروفة، تم مسح ذاكرة الإشارات.")

async def execute_trade(trade_info):
    """
    ينفذ صفقة التداول ويقوم بتحديث ذاكرة آخر صفقة للمضاعفات.
    """
    global last_executed_trade
    
    logger.info(f"محاولة تنفيذ الصفقة: {trade_info}")
    
    try:
        asset_name, asset_data = await api.get_available_asset(trade_info["asset"], force_open=True)
        
        is_otc_signal = "_otc" in trade_info["asset"]
        is_otc_found = "_otc" in asset_name
        
        if (asset_data and asset_data[2]) and (is_otc_signal == is_otc_found):
            logger.info(f"الأصل '{asset_name}' مفتوح وفي السوق الصحيح. تنفيذ بمبلغ ${trade_info['amount']}...")
            
            status, buy_info = await api.buy(
                amount=trade_info['amount'],
                asset=asset_name,
                direction=trade_info["direction"],
                duration=trade_info["duration"],
                time_mode="TIME"
            )
            
            if status:
                logger.info(f"تم فتح الصفقة بنجاح. معرف الصفقة: {buy_info.get('id', 'N/A')}")
                # تحديث ذاكرة آخر صفقة تم تنفيذها
                last_executed_trade = {
                    "asset": asset_name,
                    "direction": trade_info["direction"],
                    "duration": trade_info["duration"],
                    "amount": trade_info["amount"],
                    "martingale_level": trade_info["martingale_level"],
                    "timestamp": time.time()
                }
            else:
                logger.error(f"فشل في فتح الصفقة. السبب: {buy_info}")
                last_executed_trade = {} # مسح الذاكرة عند الفشل
        else:
            market_status = "مفتوح" if (asset_data and asset_data[2]) else "مغلق"
            logger.warning(f"تم تجاهل الإشارة. الأصل المطلوب: '{trade_info['asset']}'. الأصل المتاح: '{asset_name}'. حالة السوق: {market_status}.")
            last_executed_trade = {} # مسح الذاكرة عند الفشل
            
    except Exception as e:
        logger.error(f"حدث خطأ غير متوقع أثناء تنفيذ الصفقة: {e}")
        last_executed_trade = {} # مسح الذاكرة عند الفشل

async def main():
    global TRADE_AMOUNT
    
    while True:
        try:
            amount_input = input("الرجاء إدخال مبلغ الصفقة **الأساسي** (مثال: 1.5): ")
            TRADE_AMOUNT = float(amount_input)
            if TRADE_AMOUNT > 0:
                break
            else:
                print("الرجاء إدخال مبلغ موجب أكبر من صفر.")
        except ValueError:
            print("مدخل غير صالح. الرجاء إدخال رقم عشري أو صحيح.")
    
    logger.info(f"تم تحديد مبلغ الصفقة الأساسي: ${TRADE_AMOUNT}")
    logger.info("--- بدء تشغيل روبوت التداول ---")

    logger.info("جاري محاولة الاتصال بحساب Quotex...")
    check, reason = await api.connect()
    if not check:
        logger.critical(f"فشل الاتصال بـ Quotex: {reason}. تأكد من صحة بياناتك في settings/config.ini")
        return

    logger.info(f"تم الاتصال بـ Quotex بنجاح. الحساب الفعال: {'تجريبي' if api.account_is_demo else 'حقيقي'}")
    
    await client.start()
    logger.info("تم الاتصال بالتلغرام. الروبوت الآن يستمع للرسائل في القناة...")
    
    await client.run_until_disconnected()
    logger.info("تم فصل الاتصال بالتلغرام.")

if __name__ == '__main__':
    try:
        asyncio.run(main())
    except Exception as e:
        logger.critical(f"حدث خطأ فادح أدى إلى إيقاف البرنامج: {e}")
    finally:
        logger.info("--- تم إيقاف الروبوت ---")
