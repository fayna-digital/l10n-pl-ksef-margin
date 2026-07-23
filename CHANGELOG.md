# CHANGELOG — l10n_pl_ksef_margin

Формат: `## [version] — YYYY-MM-DD`

---

## [portfolio] — 2026-07-24 (без зміни поведінки, версія модуля лишається 17.0.2.2.6)

### Changed

- Витягнуто в публічний showcase-репозиторій за процедурою Project Sunset:
  генералізовано клієнт-специфічні дані (репозиторій-URL, deploy-шляхи, db/
  container names), прибрано внутрішні операційні документи з PII третіх
  осіб (бухгалтерський контакт), додано `tests/conftest.py` + сентинел-тест
  для коректного `pytest` без живого Odoo, CI на docker `odoo:17.0` + postgres.
- Import-guard для опційної залежності `ksef-client` звужено з `except
  Exception` до `except ImportError` (models/account_move.py,
  ksef_bulk_send_wizard.py, ksef_vendor_buffer.py, services/ksef_auth.py).
- `KSEF_MANDATORY_DATE` піднято з локальної змінної в
  `action_auto_send_ksef_draft` до модульної константи в
  `account_move.py`, узгоджено з ідентичною константою в
  `ksef_bulk_send_wizard.py`.

---

## [docs] — 2026-06-08 (без зміни коду, версія модуля лишається 17.0.2.2.6)

### Changed (документація)

- **Приведено репо до REPO_STANDARD** — перше зразкове репо Fayna.
- `docs/TZ.md` переписано у 6 областей spec-driven (Objective/Commands/Project Structure/Code Style/Testing/Boundaries + Success/Open Questions). Закриті TZ-1..4 → таблиця історії.
- `docs/PLAN.md` — новий (план майбутніх фаз).
- `TECHNICAL_DOCS.md` → `docs/ARCHITECTURE.md` (`git mv`, історія збережена) + банер контексту.
- `CLAUDE.md` — **виправлено фактичну помилку**: VAT Marża описувалась як `TP/FP/MK`, реально код використовує `P_PMarzy`/`P_PMarzy_3_3`+`P_13_11` (звірено з `ksef_xml_parser._detect_marza`). Оновлено версію 2.2.1→2.2.6, deploy-шлях, зв'язки library/tools.
- `AI_CONTEXT.md` — deprecation-банер; `docs/INDEX.md` — оновлена навігація.

### Fixed (відповідність golden rules + skills)

- **LICENSE:** Apache 2.0 → офіційний **LGPL-3** (узгоджено з `__manifest__.py`; LGPL-3 правильна для Odoo Community addon, depends `account`+`l10n_pl`).
- **`.gitignore`:** додано захист секретів (`.env`, `*.pem`, `*.key`, `*.crt`, `*_token*`) + кеші (`.ruff_cache`, `.pytest_cache`, `.mypy_cache`).
- **`CLAUDE.md`:** помітний банер **#4ZONES** — ніколи не працювати напряму на сервері (локально → GitHub → staging → prod).

> ⚠️ Код (`services/`, `models/`) НЕ змінювався — лише документація/конфіг/ліцензія. Поведінка KSeF-відправки незмінна.

---

## [17.0.2.2.6] — 2026-06-07

### Fixed

- **TZ-3: Реальна причина відхилення KSeF.** При статусі `rejected` тепер викликається `get_session_invoices` → береться перша invoice з code≥400 → `l10n_pl_ksef_error_code` і `l10n_pl_ksef_error_message` отримують справжній XPath/опис (наприклад `kod 450 — invalid child element 'P_13_7'`). Раніше завжди показувало fallback `445 — Błąd weryfikacji, brak poprawnych faktur`. Fallback зберігається якщо `get_session_invoices` недоступний.

---

## [17.0.2.2.5] — 2026-06-07

### Fixed

- **TZ-2: KSeF kod 440 (дублікат) → accepted.** Коли KSeF повертає kod 440 при повторній відправці — фактура вже прийнята раніше. Тепер: парсимо Nr KSeF з поля `details` (паттерн `numerze KSeF: <number>`), записуємо `status=accepted` з оригінальним Nr KSeF. Раніше записувалось `status=rejected, error_code=445` — хибна інформація.

---

## [17.0.2.2.4] — 2026-06-07

### Added

- **TZ-1: Авто-відправка рахунків до KSeF.** Новий cron `ir_cron_ksef_auto_send_draft` кожні 30 хв автоматично відправляє до 20 підтверджених рахунків зі статусом `draft` (Not Sent) і датою ≥ 01.04.2026. Раніше рахунки залишались у `draft` назавжди — потрібна була ручна відправка або bulk wizard.
  - Метод `action_auto_send_ksef_draft` на `account.move`
  - Static helper `_auto_send_single` (аналог `KsefBulkSendWizard._send_single`)
  - Помилки окремих рахунків не блокують решту; рахунок залишається `draft` і потрапить до наступного batch

---

## [17.0.2.2.3] — 2026-06-07

### Fixed

- **VAT consistency на Marża-фактурах.** Новий метод `_effective_marza()` в `ksef_xml_builder.py`: на Marża-фактурі рядки з числовим кодом P_12 (23/8/…) але нульовим фактичним ПДВ (`price_total == price_subtotal`) переводяться до P_13_11 замість P_13_1. Усуває XSD-несумісність `P_13_1` > `P_14_1`.
- **WARNING-лог при відсутньому польському перекладі продукту.** `_get_line_name_pl()` виводить `WARNING: P_7 zawiera cyrylicę` коли `with_context(lang='pl_PL').name` містить кирилицю.

---

## [17.0.2.2.2] — 2026-06-07

### Fixed

- **TZ-4: Кириличні назви Марж.** `_detect_marza`, `_line_is_marza`, `_get_tax_code` у `ksef_xml_builder.py` тепер розпізнають українські назви ПДВ: `'маржа', 'маржі', 'маржу'`. До фіксу: CampScout-prod використовує `'ПДВ Маржа - Туристичні Послуги'` → `_is_marza=False` → `P_PMarzyN` замість `P_PMarzy` у XML.

---

## [17.0.2.1.x] — 2026-05-19

### Fixed

- **INC 2026-05-19 root fix (commit 8227039).** `<P_13_1>0.0</P_13_1>` без `<P_14_1>` → KSeF reject kod 450. Пропускаємо групи з net=0 AND tax=0 у `_render_summary`.

---

## [17.0.2.0.0] — 2026-04-xx (initial)

- Початкова версія: відправка FA(3), перевірка статусів, UPO, VAT Marża, bulk wizard, KSeF dashboard.
