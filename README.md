# l10n_pl_ksef_margin — Odoo 17 × KSeF 2.0 (FA(3))

![Odoo Version](https://img.shields.io/badge/Odoo-17.0%20Community-purple)
![Python](https://img.shields.io/badge/Python-3.10%2B-blue)
![KSeF](https://img.shields.io/badge/KSeF-2.0%20FA(3)-red)
![License](https://img.shields.io/badge/License-LGPL--3-green.svg)
![Status](https://img.shields.io/badge/status-production-brightgreen)

**Розроблено [Fayna Digital](https://www.fayna.agency) для [CampScout](https://campscout.eu)**
**Автор: Volodymyr Shevchenko**

---

## Проблема

З 1 лютого 2026 польський **Krajowy System e-Faktur (KSeF)** стає обов'язковим
для VAT-платників — усі B2C/B2B фактури мають надсилатись через державний
API у стандартизованому XML-форматі **FA(3)**, з шифруванням (RSA-OAEP +
AES-256-CBC), офіційним підтвердженням (**UPO**) і статус-трекінгом. Odoo
Community з коробки цього не вміє — і для бізнесу на **VAT Marża** (туристична
схема маржі, art. 119 ustawy o VAT — фактична база оподаткування не
дорівнює сумі рахунку) стандартні польські локалізаційні модулі взагалі не
рахують правильний XML.

## Рішення

`l10n_pl_ksef_margin` — Odoo 17 модуль, що інтегрує бухгалтерію напряму з
KSeF 2.0:

- **Авто-відправка** — cron кожні 30 хв підхоплює підтверджені рахунки і
  відправляє батчами по 20; нічого вручну.
- **FA(3) XML генерація** з коректним `P_PMarzy` / `P_PMarzy_3_3` для рядків
  Marża (розпізнає ключові слова латиницею **і кирилицею** — `marż` /
  `маржа` / `маржі`) та звичайним `P_13_1..P_13_10` для решти.
- **Перевірка статусу** — cron кожні 15 хв: `waiting` → `accepted` (+ Nr
  KSeF) або `rejected` з **реальною причиною** (XPath + kod з
  `get_session_invoices`, не просто generic-фолбек).
- **Дублікат kod 440** — повторна відправка вже прийнятої фактури більше не
  падає в `rejected`: парситься оригінальний Nr KSeF і статус лишається
  `accepted`.
- **Faktury korygujące (KOR)** — коригувальні фактури з коректним
  `DaneFaKorygowanej` / `NrKSeFFaKorygowanej`.
- **Буфер вхідних** — синхронізація фактур постачальників з KSeF в
  ізольований буфер, без прямого впливу на бухгалтерію до ручного
  опрацювання.
- **UPO** — Urzędowe Potwierdzenie Odbioru завантажується автоматично для
  прийнятих фактур і зберігається як attachment.
- **KSeF Dashboard** — Owl-компонент: прийнято / очікує / відхилено / не
  надіслано, з графіком активності.
- **Bulk wizard** — ручна масова відправка довільного набору рахунків.

Кожен виклик до зовнішнього KSeF API ізольований — помилка окремої фактури
не блокує решту батчу, фактура лишається `draft`/`waiting` і буде підхоплена
наступним cron-циклом.

## Результат

- **57 тестів** — генерація XML (`test_xml_builder.py`) + парсинг UPO/статусів/
  VAT Marża (`test_xml_parser.py`), покриваючи regression-кейси реальних
  KSeF-reject'ів (kod 450 — hanging `<P_13_1>` без `<P_14_1>`; кирилична
  «Маржа»; дублікати kod 440).
- Production-використання: тисячі фактур VAT Marża пройшли XSD-валідацію FA(3)
  і прийняті KSeF без ручного втручання.
- Кириличні назви товарів логуються `WARNING` замість тихого reject —
  оператор бачить проблему до того, як KSeF її поверне.

## Стек

Odoo 17.0 Community · `ksef-client` (KSeF 2.0 SDK, RSA-OAEP + AES-256-CBC) ·
FA(3) schema `crd.gov.pl/wzor/2025/06/25/13775` · lxml/ElementTree · Owl
(dashboard UI) · pytest (Odoo `BaseCase`).

## Архітектура

```
l10n_pl_ksef_margin/
├── models/
│   ├── account_move.py          # поля KSeF, дії користувача, auto-send cron, перевірка статусу
│   ├── ksef_bulk_send_wizard.py # ручна масова відправка
│   ├── ksef_vendor_buffer.py    # буфер вхідних фактур від постачальників
│   └── res_company.py           # токен KSeF + середовище (test/prod)
├── services/
│   ├── ksef_auth.py             # авторизація (challenge → token → session, RSA-OAEP)
│   ├── ksef_xml_builder.py      # генерація FA(3) XML — головна бізнес-логіка (VAT Marża, KOR)
│   └── ksef_xml_parser.py       # парсинг UPO, статусів, VAT Marża detection
├── views/                       # account_move, res_company, vendor_buffer, dashboard, bulk_send_wizard
├── data/ksef_cron.xml           # 3 cron: auto-send, перевірка статусу, синхр. вхідних
├── static/src/components/       # Owl: KSeF dashboard (сторінка) + alert-картка
├── security/  i18n/  docs/
└── tests/                       # 57 тестів (XML builder + parser)
```

## Flow

```
Рахунок підтверджено (posted)
        ↓
status = 'draft' (Not Sent)
        ↓  ← cron кожні 30 хв, batch 20
status = 'waiting'
        ↓  ← cron кожні 15 хв
    ┌── accepted ──→ Nr KSeF + chatter
    └── rejected  ──→ XPath-причина + chatter
            ↓
       Ponów → 'draft' → cron підхопить
            ↓
       або kod 440 → автоматично 'accepted'
```

---

## Встановлення

```bash
pip install ksef-client

cd /path/to/odoo/extra-addons
git clone https://github.com/fayna-digital/l10n-pl-ksef-margin.git

odoo -c /etc/odoo/odoo.conf -d your_db -i l10n_pl_ksef_margin --stop-after-init
```

`odoo.conf`:
```ini
addons_path = ...,/path/to/odoo/extra-addons/l10n-pl-ksef-margin
```

Модуль підключається як **extra-addons** (не custom-addons), бо це
localization plugin. Репо має вкладену теку `l10n_pl_ksef_margin/` —
Odoo підхопить її автоматично, якщо `addons_path` вказує на репозиторій.

## Налаштування

**Налаштування → Компанії → [компанія] → вкладка KSeF:**

| Поле | |
|------|-|
| Токен KSeF API | Токен з ksef.mf.gov.pl (Tokeny → Generuj token → InvoiceWrite) |
| Середовище | `Test` (`ksef-test.mf.gov.pl`) або `Production` (`ksef.mf.gov.pl`) |

## Використання

**Автоматично (рекомендовано):** після підтвердження рахунку — нічого робити
не треба, cron кожні 30 хв відправить до KSeF.

**Вручну:** вкладка **KSeF** на фактурі → **Wyślij do KSeF**.

**Bulk:** список рахунків → виділити → **Дія → Masowa wysyłka do KSeF**.

**Відхилений рахунок:** вкладка KSeF показує реальну причину (kod + XPath).
Виправити дані → **Ponów wysyłkę** → cron підхопить.

## VAT Marża

Модуль визначає рядки Marża за ключовими словами в назві податку:
- Латиниця: `marż`, `marza`, `margin`
- Кирилиця: `маржа`, `маржі`, `маржу`

**Назви продуктів у KSeF (`P_7`) мають бути польською.** Якщо продукт не має
перекладу `pl_PL` — в логах з'явиться `WARNING: P_7 zawiera cyrylicę`.

## Тести

```bash
pre-commit run --all-files    # ruff + ruff-format + mypy + gitleaks + no-ai-signature

# Повний прогін Odoo BaseCase — потребує живого Odoo; локально без нього
# тести автоматично skip-аються (див. tests/conftest.py). У CI ганяються
# реально через docker odoo:17.0 + postgres (.github/workflows/ci.yml).
pytest l10n_pl_ksef_margin/tests/ -v
```

## Документація

- [CLAUDE.md](CLAUDE.md) — як працювати з репо
- [docs/TZ.md](l10n_pl_ksef_margin/docs/TZ.md) — технічне завдання (6 областей за REPO_STANDARD)
- [docs/PLAN.md](l10n_pl_ksef_margin/docs/PLAN.md) — dependency graph + фази + checkpoints
- [CHANGELOG.md](CHANGELOG.md) — історія змін

---

## Ліцензія

LGPL-3 — see [LICENSE](LICENSE). © Fayna Digital.

---

*Розроблено [Fayna Digital](https://www.fayna.agency) · Volodymyr Shevchenko*
