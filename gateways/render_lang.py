"""
Gateway-render language packs that live in pp-functions.php rather than in
plugin classes: the 6 framework keys injected by pp_gateway_render()
(verify/transaction_id/enter_transaction_id/upload_slip/mobile_number/submit)
and the bank-tab fallback (bank_step_* keys + 8 instruction steps,
pp-functions.php:2539-2670).
"""
from __future__ import annotations

FRAMEWORK_LANG = {
    "verify": {"en": "Verify", "bn": "যাচাই করুন", "hi": "सत्यापित करें", "ur": "تصدیق کریں", "ar": "تحقق"},
    "transaction_id": {"en": "Transaction ID", "bn": "ট্রানজ্যাকশন আইডি", "hi": "लेन-देन आईडी",
                       "ur": "لین دین آئی ڈی", "ar": "معرّف المعاملة"},
    "enter_transaction_id": {"en": "Enter transaction ID", "bn": "ট্রানজ্যাকশন আইডি লিখুন",
                             "hi": "लेन-देन आईडी दर्ज करें", "ur": "لین دین آئی ڈی درج کریں",
                             "ar": "أدخل معرّف المعاملة"},
    "upload_slip": {"en": "Upload Payment Slip", "bn": "পেমেন্ট স্লিপ আপলোড করুন",
                    "hi": "भुगतान स्लिप अपलोड करें", "ur": "ادائیگی سلپ اپ لوڈ کریں",
                    "ar": "ارفع إيصال الدفع"},
    "mobile_number": {"en": "Mobile Number", "bn": "মোবাইল নম্বর", "hi": "मोबाइल नंबर",
                      "ur": "موبائل نمبر", "ar": "رقم الجوال"},
    "submit": {"en": "Submit", "bn": "জমা দিন", "hi": "जमा करें", "ur": "جمع کریں", "ar": "إرسال"},
}

BANK_SUPPORTED = {"en": "English", "bn": "বাংলা", "hi": "हिन्दी", "ur": "اردو", "ar": "العربية"}

BANK_LANG_TEXT = {
    "bank_step_bank_name": {"en": "Bank Name: {bank_name}", "bn": "ব্যাংকের নাম: {bank_name}",
                            "hi": "बैंक का नाम: {bank_name}", "ur": "بینک کا نام: {bank_name}",
                            "ar": "اسم البنك: {bank_name}"},
    "bank_step_account_name": {"en": "Account Name: {account_holder_name}",
                               "bn": "অ্যাকাউন্টের নাম: {account_holder_name}",
                               "hi": "खाते का नाम: {account_holder_name}",
                               "ur": "اکاؤنٹ کا نام: {account_holder_name}",
                               "ar": "اسم الحساب: {account_holder_name}"},
    "bank_step_account_number": {"en": "Account Number: {account_number}",
                                 "bn": "অ্যাকাউন্ট নম্বর: {account_number}",
                                 "hi": "खाता संख्या: {account_number}",
                                 "ur": "اکاؤنٹ نمبر: {account_number}",
                                 "ar": "رقم الحساب: {account_number}"},
    "bank_step_branch_name": {"en": "Branch Name: {branch_name}", "bn": "শাখার নাম: {branch_name}",
                              "hi": "शाखा का नाम: {branch_name}", "ur": "برانچ کا نام: {branch_name}",
                              "ar": "اسم الفرع: {branch_name}"},
    "bank_step_routing_number": {"en": "Routing Number: {routing_number}",
                                 "bn": "রাউটিং নম্বর: {routing_number}",
                                 "hi": "रूटिंग नंबर: {routing_number}",
                                 "ur": "روٹنگ نمبر: {routing_number}",
                                 "ar": "رقم التوجيه: {routing_number}"},
    "bank_step_swift_code": {"en": "Swift Code: {swift_code}", "bn": "সুইফট কোড: {swift_code}",
                             "hi": "स्विफ्ट कोड: {swift_code}", "ur": "سوئفٹ کوڈ: {swift_code}",
                             "ar": "رمز السويفت: {swift_code}"},
    "bank_step_amount": {"en": "Amount: {amount} {currency}", "bn": "পরিমাণ: {amount} {currency}",
                         "hi": "राशि: {amount} {currency}", "ur": "رقم: {amount} {currency}",
                         "ar": "المبلغ: {amount} {currency}"},
    "bank_step_slip": {"en": "Upload the Payment Slip in the box below and press Submit",
                       "bn": "নিচের বক্সে পেমেন্ট স্লিপ আপলোড করুন এবং জমা দিন চাপুন।",
                       "hi": "नीचे दिए गए बॉक्स में भुगतान रसीद अपलोड करें और \"सबमिट\" दबाएँ।",
                       "ur": "نیچے دیے گئے باکس میں ادائیگی کی رسید اپ لوڈ کریں اور \"Submit\" دبائیں۔",
                       "ar": "قم برفع إيصال الدفع في المربع أدناه ثم اضغط على \"إرسال\"."},
}


def _comma(amount) -> str:
    try:
        return f"{float(str(amount).replace(',', '')):,.2f}"
    except (ValueError, TypeError):
        return str(amount)


def bank_instructions(options: dict, transaction: dict) -> list:
    """The 8 bank fallback steps (options may miss keys -> '' like PHP ?? '')."""
    def opt(key: str) -> str:
        return str(options.get(key, "") or "")

    amount = _comma(transaction.get("local_net_amount", ""))
    currency = str(transaction.get("local_currency", ""))
    return [
        {"icon": "", "text": "bank_step_bank_name", "copy": True, "value": opt("bank_name"),
         "vars": {"{bank_name}": opt("bank_name")}},
        {"icon": "", "text": "bank_step_account_name", "copy": True, "value": opt("account_holder_name"),
         "vars": {"{account_holder_name}": opt("account_holder_name")}},
        {"icon": "", "text": "bank_step_account_number", "copy": True, "value": opt("account_number"),
         "vars": {"{account_number}": opt("account_number")}},
        {"icon": "", "text": "bank_step_branch_name", "copy": True, "value": opt("branch_name"),
         "vars": {"{branch_name}": opt("branch_name")}},
        {"icon": "", "text": "bank_step_routing_number", "copy": True, "value": opt("routing_number"),
         "vars": {"{routing_number}": opt("routing_number")}},
        {"icon": "", "text": "bank_step_swift_code", "copy": True, "value": opt("swift_code"),
         "vars": {"{swift_code}": opt("swift_code")}},
        {"icon": "", "text": "bank_step_amount", "copy": True,
         "value": str(transaction.get("local_net_amount", "")),
         "vars": {"{amount}": amount, "{currency}": currency}},
        {"icon": "", "text": "bank_step_slip", "copy": False},
    ]


def resolve_bank_steps(options: dict, transaction: dict, language: str) -> list:
    """Bank steps with lang text + <span class=dynamic-value> var substitution."""
    steps = []
    for i, spec in enumerate(bank_instructions(options, transaction), start=1):
        text = BANK_LANG_TEXT.get(spec["text"], {}).get(language,
               BANK_LANG_TEXT.get(spec["text"], {}).get("en", spec["text"]))
        for k, v in (spec.get("vars") or {}).items():
            text = text.replace(k, f'<span class="dynamic-value">{v}</span>')
        step = dict(spec)
        step["label"] = text
        step["text"] = text
        step["row"] = i
        steps.append(step)
    return steps


def framework_built(language: str) -> dict:
    return {k: v.get(language, v.get("en", k)) for k, v in FRAMEWORK_LANG.items()}
