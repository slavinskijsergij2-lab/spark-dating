import html as _html
import json
import logging
import os

logger = logging.getLogger(__name__)

RESEND_API_KEY = os.getenv("RESEND_API_KEY", "")
RESEND_FROM = os.getenv("RESEND_FROM", "Spark <onboarding@resend.dev>")
APP_URL = os.getenv("APP_URL", "https://spark-dating.club")


def _resend_post(payload: dict) -> bool:
    """Send one email via Resend API using httpx (avoids urllib Cloudflare block)."""
    if not RESEND_API_KEY:
        return False
    try:
        import httpx
        r = httpx.post(
            "https://api.resend.com/emails",
            headers={"Authorization": f"Bearer {RESEND_API_KEY}", "Content-Type": "application/json"},
            json=payload,
            timeout=15,
        )
        if r.is_success:
            return True
        logger.error("Resend API %s: %s", r.status_code, r.text[:200])
        return False
    except Exception:
        logger.exception("Failed to post to Resend API")
        return False


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

    return _resend_post({"from": RESEND_FROM, "to": [to_email], "subject": subject, "html": html})


def send_password_reset_email(to_email: str, token: str, lang: str = "en") -> bool:
    if not RESEND_API_KEY:
        return False

    reset_url = f"{APP_URL}/reset-password/{token}"
    subjects = {
        "ru": "Сброс пароля — Spark",
        "uk": "Скидання пароля — Spark",
        "en": "Password reset — Spark",
    }
    subject = subjects.get(lang, subjects["en"])

    html = f"""<!DOCTYPE html>
<html>
<body style="font-family:Arial,sans-serif;background:#fff5f7;margin:0;padding:40px 20px;">
  <div style="max-width:480px;margin:0 auto;background:white;border-radius:20px;
              padding:40px;box-shadow:0 4px 24px rgba(0,0,0,.08);">
    <div style="text-align:center;margin-bottom:28px;">
      <div style="font-size:48px;">🔑</div>
      <div style="font-size:28px;font-weight:bold;color:#ec4899;">Spark</div>
    </div>
    <h2 style="color:#1f2937;margin-bottom:12px;font-size:20px;">{"Сброс пароля" if lang == "ru" else "Скидання пароля" if lang == "uk" else "Password Reset"}</h2>
    <p style="color:#6b7280;line-height:1.6;margin-bottom:32px;">
      {"Нажми кнопку ниже, чтобы задать новый пароль. Ссылка действительна 1 час." if lang == "ru" else
       "Натисни кнопку нижче, щоб задати новий пароль. Посилання дійсне 1 годину." if lang == "uk" else
       "Click the button to set a new password. The link is valid for 1 hour."}
    </p>
    <div style="text-align:center;margin-bottom:28px;">
      <a href="{reset_url}"
         style="display:inline-block;background:linear-gradient(135deg,#ec4899,#f43f5e);
                color:white;padding:14px 36px;border-radius:50px;font-weight:bold;
                font-size:16px;text-decoration:none;">
        {"Задать новый пароль" if lang == "ru" else "Задати новий пароль" if lang == "uk" else "Set new password"}
      </a>
    </div>
    <p style="color:#9ca3af;font-size:12px;text-align:center;">
      {"Если это были не вы — просто проигнорируйте письмо." if lang == "ru" else
       "Якщо це були не ви — просто проігноруйте лист." if lang == "uk" else
       "If you didn't request this, just ignore this email."}
    </p>
  </div>
</body>
</html>"""

    return _resend_post({"from": RESEND_FROM, "to": [to_email], "subject": subject, "html": html})


_REACTIV_SUBJECTS = {
    "ru": "Привет, {}! Тебя ждут в Spark 💘",
    "uk": "Привіт, {}! На тебе чекають у Spark 💘",
    "en": "Hey, {}! People are waiting for you on Spark 💘",
    "de": "Hey, {}! Menschen warten auf dich bei Spark 💘",
    "tr": "Hey, {}! Spark'ta insanlar seni bekliyor 💘",
    "ar": "{}، مرحباً! أناس ينتظرونك على Spark 💘",
}
_REACTIV_GREETING = {
    "ru": "Давно не заходил(а)!",
    "uk": "Давно не заходив(ла)!",
    "en": "You've been missed!",
    "de": "Du wurdest vermisst!",
    "tr": "Seni özledik!",
    "ar": "لقد كنت غائباً!",
}
_REACTIV_BODY_BOTH = {
    "ru": "У тебя <strong>{} взаимных матчей</strong> и <strong>{} новых лайков</strong> — люди ждут твоего сообщения!",
    "uk": "У тебе <strong>{} взаємних метчів</strong> і <strong>{} нових лайків</strong> — люди чекають твого повідомлення!",
    "en": "You have <strong>{} mutual matches</strong> and <strong>{} new likes</strong> — they're waiting to hear from you!",
    "de": "Du hast <strong>{} gegenseitige Matches</strong> und <strong>{} neue Likes</strong> — sie warten auf deine Nachricht!",
    "tr": "<strong>{} karşılıklı eşleşmen</strong> ve <strong>{} yeni beğenin</strong> var — mesajını bekliyorlar!",
    "ar": "لديك <strong>{} تطابقات متبادلة</strong> و<strong>{} إعجابات جديدة</strong> — ينتظرون رسالتك!",
}
_REACTIV_BODY_MATCHES = {
    "ru": "У тебя <strong>{} взаимных матчей</strong> — напиши им первым и познакомься!",
    "uk": "У тебе <strong>{} взаємних метчів</strong> — напиши їм першим і познайомся!",
    "en": "You have <strong>{} mutual matches</strong> — say hello and start a conversation!",
    "de": "Du hast <strong>{} gegenseitige Matches</strong> — sag Hallo und beginne ein Gespräch!",
    "tr": "<strong>{} karşılıklı eşleşmen</strong> var — merhaba de ve bir sohbet başlat!",
    "ar": "لديك <strong>{} تطابقات متبادلة</strong> — قل مرحباً وابدأ محادثة!",
}
_REACTIV_BODY_LIKES = {
    "ru": "<strong>{} человек(а)</strong> лайкнули тебя, пока тебя не было — может, среди них твоя вторая половинка?",
    "uk": "<strong>{} людини</strong> лайкнули тебе, поки тебе не було — можливо, серед них твоя половинка?",
    "en": "<strong>{} people</strong> liked you while you were away — your match might be among them!",
    "de": "<strong>{} Personen</strong> haben dich geliked, während du weg warst — dein Match könnte dabei sein!",
    "tr": "Yokken <strong>{} kişi</strong> seni beğendi — eşleşmen aralarında olabilir!",
    "ar": "<strong>{} شخص</strong> أعجب بك في غيابك — ربما يكون توافقك بينهم!",
}
_REACTIV_BTN = {
    "ru": "Открыть Spark",
    "uk": "Відкрити Spark",
    "en": "Open Spark",
    "de": "Spark öffnen",
    "tr": "Spark'ı Aç",
    "ar": "فتح Spark",
}
_REACTIV_FOOTER = {
    "ru": "Не хочешь получать напоминания? Отключи уведомления в настройках профиля.",
    "uk": "Не хочеш отримувати нагадування? Вимкни сповіщення в налаштуваннях профілю.",
    "en": "Don't want reminders? Disable notifications in your profile settings.",
    "de": "Keine Erinnerungen? Deaktiviere Benachrichtigungen in deinen Profileinstellungen.",
    "tr": "Hatırlatmaları istemiyorsan profil ayarlarından bildirimleri kapat.",
    "ar": "لا تريد التذكيرات؟ عطّل الإشعارات في إعدادات ملفك الشخصي.",
}


def send_reactivation_email(
    to_email: str, name: str, match_count: int, new_likes: int, lang: str = "ru"
) -> bool:
    """Reactivation email for users inactive 3+ days."""
    if not RESEND_API_KEY:
        return False

    safe_name = _html.escape(name)
    lang = lang if lang in _REACTIV_SUBJECTS else "en"

    subject  = _REACTIV_SUBJECTS[lang].format(safe_name)
    greeting = _REACTIV_GREETING[lang]
    btn      = _REACTIV_BTN[lang]
    footer   = _REACTIV_FOOTER[lang]

    if match_count > 0 and new_likes > 0:
        body = _REACTIV_BODY_BOTH[lang].format(match_count, new_likes)
    elif match_count > 0:
        body = _REACTIV_BODY_MATCHES[lang].format(match_count)
    else:
        body = _REACTIV_BODY_LIKES[lang].format(new_likes)

    html = f"""<!DOCTYPE html>
<html>
<body style="font-family:Arial,sans-serif;background:#fff5f7;margin:0;padding:40px 20px;">
  <div style="max-width:480px;margin:0 auto;background:white;border-radius:20px;
              padding:40px;box-shadow:0 4px 24px rgba(0,0,0,.08);">
    <div style="text-align:center;margin-bottom:32px;">
      <div style="font-size:56px;">💘</div>
      <div style="font-size:28px;font-weight:bold;color:#ec4899;">Spark</div>
    </div>
    <h2 style="color:#1f2937;margin-bottom:12px;font-size:20px;text-align:center;">{greeting}</h2>
    <p style="color:#6b7280;line-height:1.6;font-size:16px;margin-bottom:32px;text-align:center;">{body}</p>
    <div style="text-align:center;margin-bottom:32px;">
      <a href="{APP_URL}/matches"
         style="display:inline-block;background:linear-gradient(135deg,#ec4899,#f43f5e);
                color:white;padding:14px 40px;border-radius:50px;font-weight:bold;
                font-size:16px;text-decoration:none;">{btn}</a>
    </div>
    <p style="color:#9ca3af;font-size:12px;text-align:center;">{footer}</p>
  </div>
</body>
</html>"""

    return _resend_post({"from": RESEND_FROM, "to": [to_email], "subject": subject, "html": html})


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

    return _resend_post({"from": RESEND_FROM, "to": [to_email], "subject": subject, "html": html})
