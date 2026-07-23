# Fayna KSeF — CLAUDE.md

> 🔒 **No-AI-signature policy.** Це публічний/портфоліо-репозиторій. Коміти —
> **без** AI co-author трейлерів та будь-яких згадок AI-асистента. Гвардія:
> `.pre-commit-config.yaml` → `no-ai-signature` (блокує і в контенті, і в
> commit-message). Автор комітів — людина.

> 🔐 **Секрети** (токен KSeF API) — лише в захищеному полі `res.company`
> (`groups='base.group_system'`) або `ir.config_parameter`. Ніколи в коді,
> UI, логах чи git.

> Як працювати з репо. **Що** будуємо — у [docs/TZ.md](l10n_pl_ksef_margin/docs/TZ.md)
> (6 областей за REPO_STANDARD). План реалізації — у
> [docs/PLAN.md](l10n_pl_ksef_margin/docs/PLAN.md).

## Призначення

Інтеграція Odoo 17 з польською системою е-фактур **KSeF 2.0** (схема
**FA(3)**) — надсилання, статуси, UPO, повна підтримка **VAT Marża**
(туристична схема маржі, art. 119 Ustawy o VAT).

**Версія:** `17.0.2.2.6` | Модуль: `l10n_pl_ksef_margin` | License: LGPL-3
**Depends:** `account`, `l10n_pl`
**External:** python `ksef-client` (опційна — модуль встановлюється і без
неї, KSeF-функціонал просто неактивний, `KSEF_CLIENT_AVAILABLE=False`)

## Структура модуля

```
l10n_pl_ksef_margin/
  models/          # account_move (вихідні), ksef_vendor_buffer (вхідні), res_company (токен+env)
  services/
    ksef_auth.py        # авторизація (token + challenge, RSA-OAEP)
    ksef_xml_builder.py # генерація XML FA(3) — головний сервіс (VAT Marża, P_13_x, KOR)
    ksef_xml_parser.py  # парсинг UPO, статусів, VAT Marża detection
  views/           # account_move, res_company, vendor_buffer, dashboard, bulk_send_wizard
  data/ksef_cron.xml  # 3 cron: auto-send, перевірка статусу, синхр. вхідних
  static/src/components/  # Owl: ksef_dashboard (page) + ksef_dashboard_card (alert banner)
  security/  tests/  i18n/  docs/
```

## Команди

```bash
pip install ksef-client                              # опційна залежність

pre-commit run --all-files                            # lint + types + guards (без Odoo)

# Тести — потребують живого Odoo (BaseCase), локально без Odoo автоматично
# skip-аються (tests/conftest.py). Реально ганяються у CI через
# docker odoo:17.0 + postgres (.github/workflows/ci.yml).
pytest l10n_pl_ksef_margin/tests/ -v

# Локальна розробка
docker-compose up
```

## XML FA(3) — критичні правила

- `<P_7>` = назва продукту → **польською** (не кирилицею) → інакше KSeF reject
- `<P_13_1>` (нетто) без `<P_14_1>` (ПДВ) → **XSD reject kod 450**
- Дати `<P_1>` (видача), `<P_6>` (продаж) → формат `YYYY-MM-DD`
- **VAT Marża:** `<PMarzy><P_PMarzy>1</P_PMarzy>` + `<P_PMarzy_3_3>` (туризм) +
  база нетто `P_13_11` (істина — `ksef_xml_parser._detect_marza`)

## Coding conventions

- Польські рядки в XML: перевіряти diacritics (ą ę ó ś ź ż ć ń ł)
- `_MARZA_KEYWORDS` — case-insensitive, включно з кирилицею (латиниця +
  `маржа/маржі/маржу`)
- Логувати XML на DEBUG, ніколи в prod UI
- Токен KSeF = секрет → тільки `res.company` (`groups='base.group_system'`) /
  `ir.config_parameter`, ніколи не хардкод
- Optional-dependency guard (`try/except ImportError` навколо `ksef_client`
  імпортів) — модуль має лишатись installable без пакету
- Semantic Versioning: одна сесія = один bump

## Тестування

```bash
pytest l10n_pl_ksef_margin/tests/ -v
```

57 тестів: `test_xml_builder.py` (генерація FA(3), VAT Marża, KOR) +
`test_xml_parser.py` (парсинг UPO/статусів/Marża). Кожен regression-фікс —
з характеризаційним тестом.

## Boundaries

- **Always:** обгортати виклик до зовнішнього KSeF API у try/except — помилка
  однієї фактури не блокує решту батчу; XSD-валідація перед деплоєм змін
  XML-логіки; ≥1 тест на кожен fix.
- **Ask first:** зміна публічності репо, зміна ліцензії, зміна версії схеми
  FA (FA(3) → майбутні), зміна KSeF середовища test↔prod.
- **Never:** комітити `.env`/`*.key`/`*_token*`; додавати AI co-author
  трейлери чи будь-яку AI-атрибуцію; повертати реальні клієнтські дані
  вихідного проєкту (NIP, реальні номери фактур) у цей репозиторій.

## Зв'язки

Стандарт: [docs/TZ.md](l10n_pl_ksef_margin/docs/TZ.md) ·
[docs/PLAN.md](l10n_pl_ksef_margin/docs/PLAN.md) · [CHANGELOG.md](CHANGELOG.md)
