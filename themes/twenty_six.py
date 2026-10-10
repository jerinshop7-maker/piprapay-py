"""
Twenty-six theme — port of pp-content/pp-modules/pp-themes/twenty-six/class.php.
Fields, 5 supported languages (en/bn/hi/ur/ar) and full lang_text dict are
kept identical so checkout/invoice/payment-link copy matches PHP exactly.
"""
from __future__ import annotations


class ThemeBase:
    slug = ""

    def info(self): return {"title": self.slug, "logo": "assets/logo.jpg"}
    def fields(self): return []
    def supported_languages(self): return {"en": "English"}
    def lang_text(self): return {}
    def resolve_language(self, brand_language: str) -> str:
        supported = self.supported_languages()
        if brand_language in supported:
            return brand_language
        return "en" if "en" in supported else next(iter(supported), "en")

    def build_lang(self, language: str | None = None) -> dict:
        lang = language or "en"
        return {k: v.get(lang, v.get("en", "")) for k, v in self.lang_text().items()}


class TwentySixTheme(ThemeBase):
    slug = "twenty-six"

    def info(self):
        return {"title": "Twenty Six", "logo": "assets/logo.jpg"}

    def fields(self):
        return [
            {"name": "enable_bg_image", "label": "Background Image", "type": "select",
             "options": {"enabled": "Enable", "disabled": "Disable"}, "value": "disabled",
             "required": False, "multiple": False},
            {"name": "background_image", "label": "Background Image", "required": False, "type": "image"},
            {"name": "watermark_text", "label": "Footer Branding Text", "type": "text",
             "value": "Powered by BillPax", "required": False, "placeholder": "e.g. Powered by BillPax"},
            {"name": "seo_title", "label": "SEO Title", "type": "text", "required": False,
             "placeholder": "Enter SEO title (max 60 characters)"},
            {"name": "seo_description", "label": "SEO Description", "type": "textarea", "required": False,
             "placeholder": "Enter SEO description (max 160 characters)"},
            {"name": "seo_keywords", "label": "SEO Keywords", "type": "text", "required": False,
             "placeholder": "e.g. billing, invoicing, payments"},
            {"name": "analytics_code", "label": "Analytics & Tracking Code", "type": "textarea",
             "required": False, "placeholder": "Paste Google Analytics, GTM, or other tracking code"},
            {"name": "primary_color", "label": "Primary Color", "type": "color", "value": "#5f38f9",
             "required": False, "placeholder": ""},
            {"name": "text_color", "label": "Text Color", "type": "color", "value": "#FFFFFF",
             "required": False, "placeholder": ""},
        ]

    def supported_languages(self):
        return {"en": "English", "bn": "বাংলা", "hi": "हिन्दी", "ur": "اردو", "ar": "العربية"}

    def lang_text(self):
        return {
            "payment_link": {"en": "Payment Link", "bn": "পেমেন্ট লিঙ্ক", "hi": "भुगतान लिंक", "ur": "ادائیگی کا لنک", "ar": "رابط الدفع"},
            "checkout": {"en": "Checkout", "bn": "চেকআউট", "hi": "चेकआउट", "ur": "چیک آؤٹ", "ar": "الدفع"},
            "mobile_banking": {"en": "Mobile Banking", "bn": "মোবাইল ব্যাংকিং", "hi": "मोबाइल बैंकिंग", "ur": "موبائل بینکنگ", "ar": "الخدمات المصرفية عبر الهاتف المحمول"},
            "net_banking": {"en": "Net Banking", "bn": "নেট ব্যাংকিং", "hi": "नेट बैंकिंग", "ur": "نیٹ بینکنگ", "ar": "الخدمات المصرفية عبر الإنترنت"},
            "global": {"en": "Global", "bn": "গ্লোবাল", "hi": "ग्लोबल", "ur": "عالمی", "ar": "عالمي"},
            "pay_now": {"en": "Pay Now", "bn": "এখনই পরিশোধ করুন", "hi": "अभी भुगतान करें", "ur": "ابھی ادائیگی کریں", "ar": "ادفع الآن"},
            "invoice": {"en": "Invoice", "bn": "চালান", "hi": "चालान", "ur": "انوائس", "ar": "فاتورة"},
            "total": {"en": "Total", "bn": "মোট", "hi": "कुल", "ur": "کل", "ar": "الإجمالي"},
            "total_due": {"en": "Total Due", "bn": "মোট প্রদেয়", "hi": "कुल देय", "ur": "کل واجب الادا", "ar": "الإجمالي المستحق"},
            "subtotal": {"en": "Subtotal", "bn": "উপ-মোট", "hi": "उप-योग", "ur": "ذیلی مجموعہ", "ar": "المجموع الفرعي"},
            "discount": {"en": "Discount", "bn": "ছাড়", "hi": "छूट", "ur": "رعایت", "ar": "الخصم"},
            "status": {"en": "Status", "bn": "স্ট্যাটাস", "hi": "स्थिति", "ur": "حالت", "ar": "الحالة"},
            "payment_successful": {"en": "Payment Successful!", "bn": "পেমেন্ট সফল হয়েছে!", "hi": "भुगतान सफल हुआ!", "ur": "ادائیگی کامیاب ہو گئی!", "ar": "تمت عملية الدفع بنجاح!"},
            "payment_pending": {"en": "Payment Pending", "bn": "পেমেন্ট প্রক্রিয়াধীন", "hi": "भुगतान लंबित है", "ur": "ادائیگی زیر التواء ہے", "ar": "الدفع قيد الانتظار"},
            "payment_refunded": {"en": "Payment Refunded", "bn": "পেমেন্ট ফেরত দেওয়া হয়েছে", "hi": "भुगतान वापस कर दिया गया है", "ur": "ادائیگی واپس کر دی گئی ہے", "ar": "تم رد المبلغ"},
            "payment_canceled": {"en": "Payment Canceled", "bn": "পেমেন্ট বাতিল করা হয়েছে", "hi": "भुगतान रद्द कर दिया गया है", "ur": "ادائیگی منسوخ کر دی گئی ہے", "ar": "تم إلغاء الدفع"},
            "something_wrong": {"en": "Something Went Wrong!", "bn": "কিছু একটা সমস্যা হয়েছে!", "hi": "कुछ गलत हो गया!", "ur": "کچھ غلط ہو گیا!", "ar": "حدث خطأ ما!"},
            "copied_successfully": {"en": "Copied Successfully", "bn": "সফলভাবে কপি হয়েছে", "hi": "सफलतापूर्वक कॉपी हो गया", "ur": "کامیابی سے کاپی ہو گیا", "ar": "تم النسخ بنجاح"},
            "download_receipt": {"en": "Download Receipt", "bn": "রসিদ ডাউনলোড করুন", "hi": "रसीद डाउनलोड करें", "ur": "رسید ڈاؤن لوڈ کریں", "ar": "تحميل الإيصال"},
        }


THEMES = {"twenty-six": TwentySixTheme}


def get_theme(slug: str) -> ThemeBase:
    cls = THEMES.get(slug or "twenty-six", TwentySixTheme)
    return cls()
