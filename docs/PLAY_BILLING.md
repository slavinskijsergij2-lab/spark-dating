# Google Play Billing для Android-приложения (TWA)

На сайте премиум продаётся через Stripe. В приложении из Google Play кнопки Stripe
скрыты, а покупка идёт через Google Play (Digital Goods API + Payment Request).

## Как это работает

1. Android-приложение (TWA) открывает сайт с referrer `android-app://…`.
   `base.html` запоминает это в `sessionStorage`, и `/premium` прячет всё, что помечено `data-web-billing`.
2. Страница берёт цены из Google Play (`getDetails`) и запускает покупку (`PaymentRequest`).
3. Токен покупки уходит на `POST /billing/play/verify`. Сервер проверяет его через
   Google Play Developer API, выдаёт премиум и подтверждает (acknowledge) покупку.
   Неподтверждённые покупки Google возвращает покупателю через 3 дня.
4. Продления, отмены и возвраты приходят через Pub/Sub на `POST /billing/play/rtdn`.
5. При каждом открытии `/premium` в приложении уже купленное (`listPurchases`) отправляется
   на проверку повторно. Это восстанавливает покупки, если сеть оборвалась.

Один токен покупки привязан к аккаунту, который проверил его первым (таблица `play_purchases`).

## Настройка (один раз)

### 1. Play Console → Монетизация
- **Подписки** → создать `premium_monthly` с базовым планом «ежемесячно, с автопродлением».
- **Продукты в приложении** → создать `premium_lifetime`.
- ID товаров должны совпадать с `PLAY_SKU_MONTHLY` / `PLAY_SKU_LIFETIME`.

### 2. Сервисный аккаунт для проверки покупок
1. Google Cloud Console → IAM → Service Accounts → создать аккаунт и скачать JSON-ключ.
2. Включить **Google Play Android Developer API** в этом проекте.
3. Play Console → **Пользователи и разрешения** → пригласить e-mail сервисного аккаунта,
   выдать доступ к приложению: «Просмотр финансовых данных» и «Управление заказами и подписками».
4. Содержимое JSON записать в `PLAY_SERVICE_ACCOUNT_JSON` на Railway (можно в base64).

### 3. Уведомления о продлениях (RTDN)
1. Google Cloud → Pub/Sub → создать топик, например `play-billing`.
2. Выдать роль **Pub/Sub Publisher** аккаунту `google-play-developer-notifications@system.gserviceaccount.com`.
3. Создать **push**-подписку на топик с адресом
   `https://spark-dating.club/billing/play/rtdn?token=<PLAY_RTDN_TOKEN>`.
4. Play Console → Монетизация → **Настройка монетизации** → указать топик и нажать «Отправить тестовое уведомление».

### 4. Переменные на Railway
См. `.env.example`: `PLAY_PACKAGE_NAME`, `PLAY_SKU_MONTHLY`, `PLAY_SKU_LIFETIME`,
`PLAY_SERVICE_ACCOUNT_JSON`, `PLAY_RTDN_TOKEN`, `ANDROID_CERT_SHA256`.
Секрет для RTDN: `python3 -c "import secrets; print(secrets.token_urlsafe(32))"`.

### 5. Android-обёртка (Bubblewrap)
В `twa-manifest.json` включить Play Billing:

```json
"features": { "playBilling": { "enabled": true } }
```

`ANDROID_CERT_SHA256` — отпечаток SHA-256 **ключа подписи приложения** из
Play Console → Целостность приложения (если включена подпись Google Play, это не ключ загрузки).
По нему `/.well-known/assetlinks.json` разрешает приложению открывать сайт без адресной строки.

## Проверка
- Play Console → Настройка → **Тестирование лицензий**: добавить свой Gmail, тогда покупки бесплатные.
- Установить сборку из внутреннего тестирования (не через `adb install`: Play Billing работает
  только с приложением, установленным из Google Play).
