# PLAN — l10n_pl_ksef_margin (план реалізації)

> Друга черга після [docs/TZ.md](TZ.md). Dependency graph + фази + checkpoints.
> Що ВЖЕ зроблено — у [CHANGELOG.md](../../CHANGELOG.md). Модуль на
> `17.0.2.2.6`, **production-ready**.

---

## Overview

Базовий функціонал (надсилання FA(3), VAT Marża, faktury korygujące, буфер
вхідних, dashboard) — **готовий і в production**. Цей план — про **відкриті
покращення**, не про MVP.

---

## Dependency graph

```
[account, l10n_pl]  ── Odoo core / localization
        │
        ▼
[services/ksef_auth, ksef_xml_builder, ksef_xml_parser]  ── ядро, незалежне від UI
        │
   ┌────┴─────────────────────┐
   ▼                          ▼
[models/account_move]   [models/ksef_vendor_buffer]
 → вихідні + auto-send    → вхідні + буфер
        │
        ▼
[views/ dashboard + bulk_send_wizard]  ── UI поверх готового ядра
```

Vendor-buffer auto-booking незалежний від решти — може стартувати будь-коли.

---

## Task List

### Phase 1: Вхідні фактури — автоматизація

- [ ] **Task: Авто-створення account.move з буфера**
  - Acceptance: оператор тисне «Zaksięguj» на `ksef.vendor.buffer` записі →
    створюється чернетка `account.move` (vendor bill) з даними з буфера
  - Verify: ручний тест — синхронізувати вхідні → створити bill → перевірити поля
  - Files: `models/ksef_vendor_buffer.py`, `views/ksef_vendor_buffer_views.xml`

- [ ] **Task: FaWiersz — повні рядки інвойсу в FA(3)**
  - Acceptance: XML містить усі рядки (`FaWiersz`) з кількістю/ціною, не лише
    агреговані суми
  - Verify: XSD-валідація + перевірка прийняття KSeF
  - Files: `services/ksef_xml_builder.py`, `tests/test_xml_builder.py`

### Checkpoint: Phase 1
Вхідні фактури автоматизовані, XML повний. Тести зелені, XSD ok.

### Phase 2: Dashboard polish (низький пріоритет)

> Alert Banner + dedicated page вже реалізовані.

- [ ] **Task: Лінійний графік активності за 30 днів** (замість статичних стовпчиків)
  - Acceptance: графік по датах з tooltip (дата + кількість + сума PLN)
  - Files: `static/src/components/ksef_dashboard/ksef_dashboard.js`
- [ ] **Task: Таблиця останніх операцій + клік → account.move**
- [ ] **Task: rejection reason — людиночитабельний вивід у chatter**

### Checkpoint: Phase 2
Dashboard повноцінний, UX на рівні нативних карток Odoo.

### Phase 3: Roadmap

- [ ] Показ XPath-причини reject прямо в Odoo view (не лише chatter)
- [ ] Моніторинг черги (cron 30хв вже є) — алерти при застряганні >24год
- [ ] Готовність до FA(4) коли вийде нова схема

---

## Backlog / можливі покращення

- [ ] Dashboard статистики — фільтри за компанією/періодом
- [ ] Автотести на реальному KSeF test environment (зараз мокований `ksef-client`)

---

## Зв'язки

[docs/TZ.md](TZ.md) · [CHANGELOG.md](../../CHANGELOG.md)
