# TZ — l10n_pl_ksef_margin

> Специфікація за REPO_STANDARD — 6 обов'язкових областей (Objective,
> Commands, Project Structure, Code Style, Testing, Boundaries) + Success
> Criteria + Open Questions. Версія модуля: **17.0.2.2.6** | License: LGPL-3.

---

## 1. Objective

**Що:** інтеграція Odoo 17 Community з польською системою е-фактур **KSeF 2.0**
(схема **FA(3)**).

**Для кого:** туристичне агентство на VAT Marża (маржа, art. 119 Ustawy o VAT).

**Можливості:**
- Надсилання фактур клієнтів у KSeF з форми Odoo (auth → XML FA(3) → send → UPO)
- Автоматична підтримка **VAT Marża** (`P_PMarzy` / `P_PMarzy_3_3`, база нетто `P_13_11`)
- Синхронізація вхідних фактур постачальників у буфер (`ksef.vendor.buffer`)
- KSeF Dashboard (dedicated page) + Alert Banner у бухгалтерії
- Test і Production середовища KSeF

**Успіх:** фактура VAT Marża проходить XSD-валідацію FA(3), приймається KSeF
(status `accepted`, є Nr KSeF), кирилична «Маржа» розпізнається, дублікати
не надсилаються повторно.

**Технології:** Python 3.10+ · Odoo 17 · PostgreSQL · Docker/docker-compose ·
lxml/XML · xmllint · KSeF 2.0 SDK (`ksef-client`).

---

## 2. Commands

```bash
# Тести
pytest l10n_pl_ksef_margin/tests/ -v

# Lint (pre-commit: ruff + ruff-format + mypy + gitleaks + OCA)
pre-commit run --all-files

# Локальна розробка
docker-compose up

# Встановлення в живий Odoo
odoo -u l10n_pl_ksef_margin -d <database> --stop-after-init
```

---

## 3. Project Structure

```
l10n_pl_ksef_margin/
  __manifest__.py        # v17.0.2.2.6, depends: account, l10n_pl
  models/
    account_move.py      # вихідні: статус KSeF, генерація+відправка, auto-send cron
    ksef_bulk_send_wizard.py # ручна масова відправка
    ksef_vendor_buffer.py# вхідні: синхронізація у буфер
    res_company.py       # KSeF токен + середовище (test/prod)
  services/
    ksef_auth.py          # авторизація (token + challenge, RSA-OAEP)
    ksef_xml_builder.py   # генерація XML FA(3) — головний сервіс
    ksef_xml_parser.py    # парсинг UPO, статусів, VAT Marża detection
  views/                 # account_move, res_company, vendor_buffer, dashboard, bulk_send_wizard
  data/ksef_cron.xml     # 3 cron: auto-send, перевірка статусу, синхр. вхідних
  static/src/components/  # Owl: ksef_dashboard (page) + ksef_dashboard_card (alert banner)
  security/              # ksef_security.xml + ir.model.access.csv
  tests/                 # test_xml_builder.py, test_xml_parser.py (57 тестів)
  docs/                  # TZ.md (цей), PLAN.md
  i18n/                  # uk_UA.po, pl_PL.po
```

---

## 4. Code Style

```python
# VAT Marża detection — case-insensitive, включно з кирилицею
is_marza = any(k in name.lower() for k in _MARZA_KEYWORDS)
# _MARZA_KEYWORDS містить PL + кирилицю: 'marż','margin','маржа','маржі','маржу'
```

- **Польські рядки в XML:** завжди перевіряти diacritics (ą ę ó ś ź ż ć ń ł)
- **Секрети** (токен KSeF) — тільки через `res.company`
  (`groups='base.group_system'`) / `ir.config_parameter`, ніколи не хардкод,
  ніколи в UI/лог
- **Optional dependency:** `ksef-client` імпортується під `try/except
  ImportError` — модуль встановлюється і без нього (`KSEF_CLIENT_AVAILABLE`)
- **Логування XML** — рівень DEBUG, ніколи в prod UI
- **Semantic Versioning** у `__manifest__.py`: одна сесія = один bump

---

## 5. Testing Strategy

- **Фреймворк:** Odoo `BaseCase` (`pytest`/`odoo --test-enable`)
- **Покриття:** критичні шляхи — `test_xml_builder.py` (генерація FA(3), VAT
  Marża, faktury korygujące), `test_xml_parser.py` (парсинг UPO/статусів/
  Marża detection) — 57 тестів
- **XSD-валідація:** кожна зміна XML-логіки → перевірка проти офіційної схеми
  FA(3) перед merge
- **Regression:** кожен production-reject (kod 450/415/440) → характеризаційний тест

---

## 6. Boundaries

**Always:**
- XSD-валідація перед деплоєм XML-змін
- ≥1 тест на кожен fix; CHANGELOG-запис; Semantic Version bump
- Виклик до зовнішнього KSeF API — завжди в try/except з логуванням, помилка
  однієї фактури не блокує батч

**Ask first:**
- Зміна версії схеми FA (FA(3) → майбутні)
- Додавання нових Python-залежностей
- Зміна KSeF середовища test↔prod

**Never:**
- Секрети (токен KSeF) у код / UI / лог
- `<P_13_1>` (нетто) без `<P_14_1>` (ПДВ) → XSD reject kod 450
- Назва продукту кирилицею в `<P_7>` без перекладу → KSeF reject (польська обов'язкова)

---

## Success Criteria

- [x] Фактура VAT Marża проходить XSD FA(3) і приймається KSeF (status `accepted`)
- [x] Кирилична «Маржа» розпізнається
- [x] Дублікати (kod 440) не створюють помилок
- [x] Причина rejected зберігається і видима в chatter
- [x] Черга застряглих фактур обробляється авто-cron
- [ ] Вхідні фактури авто-створюють `account.move` (зараз ручний буфер) — див. PLAN.md

---

## Open Questions

- KSeF API endpoints: звірити в `ksef_auth.py` фактичні URL (test:
  `api-test.ksef.mf.gov.pl/api/v2/`, prod: `api.ksef.mf.gov.pl/api/v2/`)
- Чи переносити VAT Marża на майбутню FA(4) коли вийде

---

## Історія

Детальна історія версій — [CHANGELOG.md](../../CHANGELOG.md).

---

## Зв'язки

- Стандарт: REPO_STANDARD (Fayna Digital internal convention)
- План реалізації: **docs/PLAN.md**
