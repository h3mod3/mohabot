import json
import time
from quotexapi.ws.channels.base import Base
from quotexapi.expiration import get_expiration_time_quotex


class Buy(Base):
    """Class for Quotex buy websocket channel."""

    name = "buy"

    def __call__(self, price, asset, direction, duration, request_id, is_fast_option):
        """
        Handles the buy operation.

        If is_fast_option is True, it places a trade with a fixed duration (e.g., 60 seconds).
        If False, it places a trade that expires at a specific candle close time.
        """
        # --- بداية التعديل ---

        if is_fast_option:
            # هذا هو وضع "الوقت" الذي تريده (صفقة بمدة ثابتة)
            option_type = 100  # النوع 100 يستخدم للصفقات السريعة (Turbo)
            expiration = duration  # نستخدم المدة بالثواني مباشرةً
            end_time_for_settings = time.time() + duration # وقت تقريبي للانتهاء من أجل إعدادات الواجهة
        else:
            # هذا هو السلوك القديم (وضع المؤقت/انتهاء الشمعة)
            option_type = 1
            expiration_time = get_expiration_time_quotex(
                int(time.time()),
                duration
            )
            expiration = expiration_time
            end_time_for_settings = expiration_time

        # --- نهاية التعديل ---


        # تحديث إعدادات الواجهة (مهم لتجنب الأخطاء من السيرفر)
        self.api.settings_apply(
            asset,
            expiration,
            is_fast_option=is_fast_option,
            end_time=end_time_for_settings,
        )

        # إعداد بيانات الطلب لإرسالها
        payload = {
            "asset": asset,
            "amount": price,
            "time": expiration,  # هذا هو الحقل الأهم، سيحتوي على مدة الصفقة بالثواني
            "action": direction,
            "isDemo": self.api.account_type,
            "tournamentId": 0,
            "requestId": request_id,
            "optionType": option_type # النوع 100 للصفقات السريعة
        }

        # إرسال الطلبات إلى السيرفر
        data = f'42["tick"]'
        self.send_websocket_request(data)

        data = f'42["orders/open",{json.dumps(payload)}]'
        self.send_websocket_request(data)