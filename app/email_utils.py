import html as _html
import json
import logging
import os
import urllib.request
import urllib.error

logger = logging.getLogger(__name__)

RESEND_API_KEY = os.getenv("RESEND_API_KEY", "")
RESEND_FROM = os.getenv("RESEND_FROM", "Spark <onboarding@resend.dev>")
APP_URL = os.getenv("APP_URL", "http://localhost:8000")


def is_smtp_configured() -> bool:
    return bool(RESEND_API_KEY)


_SUBJECTS = {
    "ru": "Подтверди email — Spark",
    "uk": "Підтвердь email — Spark",
    "en": "Confirm your email — Spark",
    "de": "E-Mail bestätigen — Spark",
    "tr": "E-postanı onayla — Spark",
    "ar": "تأكيد البريد الإلكتروني — Spark",
}
_GREETING = {
    "ru": "Добро пожаловать в Spark! 💘",
    "uk": "Ласкаво просимо до Spark! 💘",
    "en": "Welcome to Spark! 💘",
    "de": "Willkommen bei Spark! 💘",
    "tr": "Spark'a hoş geldiniz! 💘",
    "ar": "!💘 مرحباً في Spark",
}
_BODY = {
    "ru": "Нажми кнопку ниже, чтобы подтвердить свой email и начать знакомства.",
    "uk": "Натисни кнопку нижче, щоб підтвердити свій email і почати знайомства.",
    "en": "Click the button below to confirm your email address and start dating.",
    "de": "Klicke auf die Schaltfläche, um deine E-Mail zu bestätigen und loszulegen.",
    "tr": "E-posta adresini onaylamak için aşağıdaki düğmeye tıkla.",
    "ar": "انقر على الزر أدناه لتأكيد بريدك الإلكتروني والبدء.",
}
_BTN = {
    "ru": "Подтвердить email",
    "uk": "Підтвердити email",
    "en": "Confirm email",
    "de": "E-Mail bestätigen",
    "tr": "E-postayı onayla",
    "ar": "تأكيد البريد",
}
_EXPIRE = {
    "ru": "Ссылка действительна 24 часа.",
    "uk": "Посилання дійсне 24 години.",
    "en": "Link valid for 24 hours.",
    "de": "Link 24 Stunden gültig.",
    "tr": "Bağlantı 24 saat geçerlidir.",
    "ar": "الرابط صالح لمدة 24 ساعة.",
}


_MATCH_SUBJECTS = {
    "ru": "У вас новый матч в Spark! 💘",
    "uk": "У вас новий метч у Spark! 💘",
    "en": "You have a new match on Spark! 💘",
    "de": "Du hast ein neues Match bei Spark! 💘",
    "tr": "Spark'ta yeni bir eşleşmen var! 💘",
    "ar": "!💘 لديك تطابق جديد في Spark",
}
_MATCH_BODY = {
    "ru": "Поздравляем! Вы и {} взаимно понравились друг другу. Откройте приложение, чтобы начать общение!",
    "uk": "Вітаємо! Ви і {} взаємно сподобались одне одному. Відкрийте застосунок, щоб почати спілкування!",
    "en": "Congratulations! You and {} liked each other. Open the app to start chatting!",
    "de": "Glückwunsch! Du und {} mögt euch gegenseitig. Öffne die App, um zu chatten!",
    "tr": "Tebrikler! Sen ve {} birbirinizi beğendiniz. Sohbet başlatmak için uygulamayı aç!",
    "ar": "تهانينا! أنت و{} أعجب كل منكما بالآخر. افتح التطبيق لبدء المحادثة!",
}
_MATCH_BTN = {
    "ru": "Написать",
    "uk": "Написати",
    "en": "Say hello",
    "de": "Hallo sagen",
    "tr": "Merhaba de",
    "ar": "قل مرحباً",
}


def send_match_email(to_email: str, partner_name: str, matches_url: str = "", lang: str = "en") -> bool:
    if not RESEND_API_KEY:
        return False

    subject = _MATCH_SUBJECTS.get(lang, _MATCH_SUBJECTS["en"])
    # MEDIUM-18: escape partner_name before embedding in HTML email body
    body    = _MATCH_BODY.get(lang, _MATCH_BODY["en"]).format(_html.escape(partner_name))
    btn     = _MATCH_BTN.get(lang, _MATCH_BTN["en"])
    link    = matches_url or f"{APP_URL}/matches"

    html = f"""<!DOCTYPE html>
<html>
<body style="font-family:Arial,sans-serif;background:#fff5f7;margin:0;padding:40px 20px;">
  <div style="max-width:480px;margin:0 auto;background:white;border-radius:20px;
              padding:40px;box-shadow:0 4px 24px rgba(0,0,0,.08);">
    <div style="text-align:center;margin-bottom:32px;">
      <div style="font-size:56px;">💘</div>
      <div style="font-size:28px;font-weight:bold;color:#ec4899;">Spark</div>
    </div>
    <p style="color:#6b7280;line-height:1.6;font-size:16px;margin-bottom:32px;text-align:center;">{body}</p>
    <div style="text-align:center;">
      <a href="{link}"
         style="display:inline-block;background:linear-gradient(135deg,#ec4899,#f43f5e);
                color:white;padding:14px 40px;border-radius:50px;font-weight:bold;
                font-size:16px;text-decoration:none;">{btn}</a>
    </div>
  </div>
</body>
</html>"""

    payload = json.dumps({"from": RESEND_FROM, "to": [to_email], "subject": subject, "html": html}).encode()
    req = urllib.request.Request(
        "https://api.resend.com/emails", data=payload,
        headers={"Authorization": f"Bearer {RESEND_API_KEY}", "Content-Type": "application/json"},
        method="POST",
    )
    try:
        import ssl
        try:
            import certifi
            ctx = ssl.create_default_context(cafile=certifi.where())
        except ImportError:
            ctx = ssl.create_default_context()
        opener = urllib.request.build_opener(urllib.request.HTTPSHandler(context=ctx))
        with opener.open(req, timeout=10):
            return True
    except Exception:
        logger.exception("Failed to send match email to %s", to_email)
        return False


def send_verification_email(to_email: str, token: str, lang: str = "en") -> bool:
    if not RESEND_API_KEY:
        logger.warning("RESEND_API_KEY not set — skipping verification email for %s", to_email)
        return False

    verify_url = f"{APP_URL}/verify-email/{token}"
    subject  = _SUBJECTS.get(lang, _SUBJECTS["en"])
    greeting = _GREETING.get(lang, _GREETING["en"])
    body     = _BODY.get(lang, _BODY["en"])
    btn      = _BTN.get(lang, _BTN["en"])
    expire   = _EXPIRE.get(lang, _EXPIRE["en"])

    html = f"""<!DOCTYPE html>
<html>
<body style="font-family:Arial,sans-serif;background:#fff5f7;margin:0;padding:40px 20px;">
  <div style="max-width:480px;margin:0 auto;background:white;border-radius:20px;
              padding:40px;box-shadow:0 4px 24px rgba(0,0,0,.08);">
    <div style="text-align:center;margin-bottom:32px;">
      <div style="font-size:48px;">💘</div>
      <div style="font-size:28px;font-weight:bold;color:#ec4899;">Spark</div>
    </div>
    <h2 style="color:#1f2937;margin-bottom:12px;font-size:20px;">{greeting}</h2>
    <p style="color:#6b7280;line-height:1.6;margin-bottom:32px;">{body}</p>
    <div style="text-align:center;">
      <a href="{verify_url}"
         style="display:inline-block;background:linear-gradient(135deg,#ec4899,#f43f5e);
                color:white;padding:14px 36px;border-radius:50px;font-weight:bold;
                font-size:16px;text-decoration:none;">{btn}</a>
    </div>
    <p style="color:#9ca3af;font-size:12px;margin-top:32px;text-align:center;">{expire}</p>
  </div>
</body>
</html>"""

    payload = json.dumps({
        "from": RESEND_FROM,
        "to": [to_email],
        "subject": subject,
        "html": html,
    }).encode()

    req = urllib.request.Request(
        "https://api.resend.com/emails",
        data=payload,
        headers={
            "Authorization": f"Bearer {RESEND_API_KEY}",
            "Content-Type": "application/json",
            "User-Agent": "python-requests/2.31.0",
        },
        method="POST",
    )

    try:
        import ssl
        try:
            import certifi
            ctx = ssl.create_default_context(cafile=certifi.where())
        except ImportError:
            ctx = ssl.create_default_context()

        opener = urllib.request.build_opener(urllib.request.HTTPSHandler(context=ctx))
        with opener.open(req, timeout=10) as resp:
            logger.info("Verification email sent to %s (status %s)", to_email, resp.status)
            return True
    except urllib.error.HTTPError as e:
        logger.error("Resend API error %s: %s", e.code, e.read().decode())
        return False
    except Exception:
        logger.exception("Failed to send verification email to %s", to_email)
        return False
