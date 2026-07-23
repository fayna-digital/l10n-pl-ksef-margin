"""
KSeF XML Builder — генерація FA(3) XML для KSeF 2.0.

Namespace: http://crd.gov.pl/wzor/2025/06/25/13775/
Schemat:   FA(3) v1-0E

Відповідальність: ТІЛЬКИ генерація XML.
Не знає про Odoo ORM, не пише в базу, не викликає API.
"""

import logging
import re
from xml.sax.saxutils import escape as xml_escape

from odoo import _, fields
from odoo.exceptions import UserError

_logger = logging.getLogger(__name__)

# ─────────────────────────────────────────────────────────
# Константи
# ─────────────────────────────────────────────────────────

FA3_NAMESPACE = 'http://crd.gov.pl/wzor/2025/06/25/13775/'
FA3_FORM_CODE = {'systemCode': 'FA (3)', 'schemaVersion': '1-0E', 'value': 'FA'}

# Маппінг: числова ставка Odoo → код KSeF FA(3)
# Джерело: Rozporządzenie MF w sprawie KSeF, załącznik FA(3) XSD
TAX_CODE_MAP = {
    23: '23',
    8: '8',
    5: '5',
    4: '4',
    3: '3',
    0: 'zw',  # Zwolnienie z VAT — lowercase per FA(3) XSD TStawkaPodatku
    -1: 'np I',  # Nie podlega VAT (usługi poza PL) — lowercase per FA(3) XSD
}

# Маппінг: назва UoM Odoo → kod KSeF (P_8A)
UOM_CODE_MAP = {
    'Units': 'szt',
    'Unit(s)': 'szt',
    'szt': 'szt',
    'pcs': 'szt',
    'Days': 'dni',
    'day(s)': 'dni',
    'Hours': 'godz',
    'h': 'godz',
    'km': 'km',
    'kg': 'kg',
    'g': 'g',
    'l': 'l',
    'Persons': 'os',
    'Months': 'mc',
}
UOM_DEFAULT = 'szt'


class KsefXmlBuilder:
    """Будує FA(3) XML з рахунку Odoo.

    Usage::

        builder = KsefXmlBuilder(invoice)
        xml_bytes = builder.build()

    Raises:
        UserError: якщо обов'язкові дані відсутні (NIP продавця, дата)
    """

    def __init__(self, move):
        """
        Args:
            move: account.move — підтверджений рахунок клієнта (out_invoice)
        """
        self.move = move
        self.company = move.company_id
        self.partner = move.partner_id
        self._is_marza = False  # set before _detect_marza to avoid recursion in _line_is_marza
        self._is_marza = self._detect_marza()

    # ─────────────────────────────────────────────
    # Public
    # ─────────────────────────────────────────────

    def build(self) -> bytes:
        """Генерує FA(3) XML і повертає як bytes (UTF-8).

        Returns:
            bytes: валідний FA(3) XML

        Raises:
            UserError: якщо NIP продавця або дата рахунку відсутні
        """
        self._validate()
        xml = self._render_xml()
        return xml.encode('utf-8')

    # ─────────────────────────────────────────────
    # Validation
    # ─────────────────────────────────────────────

    def _validate(self):
        seller_nip = self._get_seller_nip()
        if not seller_nip:
            raise UserError(
                _(
                    'Brak NIP firmy %(company)s. Uzupełnij: Ustawienia → Firma → pole NIP.',
                    company=self.company.name,
                )
            )
        if not self.move.invoice_date:
            raise UserError(
                _(
                    'Faktura %(name)s nie ma daty wystawienia.',
                    name=self.move.name,
                )
            )

    # ─────────────────────────────────────────────
    # Marża detection
    # ─────────────────────────────────────────────

    def _detect_marza(self) -> bool:
        """Визначає, чи рахунок є VAT Marża (хоча б один рядок має явний податок marża).

        Only checks for explicit marża keyword in tax names.
        Does NOT use self._is_marza (which isn't set yet during __init__).
        """
        for line in self.move.invoice_line_ids:
            for tax in line.tax_ids:
                name_lower = (tax.name or '').lower()
                if any(
                    kw in name_lower
                    for kw in ('marż', 'marza', 'margin', 'маржа', 'маржі', 'маржу')
                ):
                    return True
        return False

    def _line_is_marza(self, line) -> bool:
        """True якщо рядок — VAT Marża або не має жодного податку (exempt/marża).

        Логіка: рядки без ПДВ на marża-фактурі — частина marża (нема окремого ПДВ).
        Рядки з конкретною ненульовою ставкою (23%, 8% тощо) — regular VAT.
        """
        for tax in line.tax_ids:
            name_lower = (tax.name or '').lower()
            if any(
                kw in name_lower for kw in ('marż', 'marza', 'margin', 'маржа', 'маржі', 'маржу')
            ):
                return True
            # Line has a specific regular VAT rate → NOT marża
            if tax.amount and tax.amount > 0:
                return False
        # No taxes at all — treat as marża on a marża invoice
        return self._is_marza

    def _effective_marza(self, line) -> bool:
        """Extends _line_is_marza: also catches numeric-code lines with 0 actual computed VAT.

        On a Marża invoice a line with P_12='23' (or any numeric code) but
        price_total == price_subtotal (no actual VAT) would make P_13_1 overstated
        vs P_14_1 — a semantic inconsistency KSeF may reject.  Such lines are
        reclassified as Marża (contribute to P_13_11 instead).
        """
        if self._line_is_marza(line):
            return True
        if not self._is_marza:
            return False
        actual_tax = round(float(line.price_total) - float(line.price_subtotal), 2)
        if actual_tax != 0:
            return False
        # 0 actual computed VAT — check if the would-be tax code is numeric
        tax_code = self._get_tax_code(line)
        if tax_code is None:
            return True  # safety — already caught by _line_is_marza
        try:
            float(tax_code)  # numeric: '23', '8', '5', …
        except (ValueError, TypeError):
            return False  # 'zw', 'np I', 'oo' etc. — leave in regular group
        _logger.info(
            'KSeF: %s linia "%s" ma P_12=%s ale 0 rzeczywistego VAT na fakturze Marża'
            ' — przenoszona do P_13_11',
            self.move.name,
            line.name or '',
            tax_code,
        )
        return True

    # ─────────────────────────────────────────────
    # XML rendering
    # ─────────────────────────────────────────────

    def _render_xml(self) -> str:
        invoice_date = str(self.move.invoice_date)
        now_str = fields.Datetime.now().strftime('%Y-%m-%dT%H:%M:%S') + 'Z'

        return (
            f'<?xml version="1.0" encoding="UTF-8"?>\n'
            f'<Faktura xmlns="{FA3_NAMESPACE}"\n'
            f'         xmlns:xsi="http://www.w3.org/2001/XMLSchema-instance">\n'
            f'{self._render_naglowek(now_str)}'
            f'{self._render_podmiot1()}'
            f'{self._render_podmiot2()}'
            f'{self._render_fa(invoice_date)}'
            f'</Faktura>'
        )

    def _render_naglowek(self, now_str: str) -> str:
        return (
            f'    <Naglowek>\n'
            f'        <KodFormularza kodSystemowy="FA (3)" wersjaSchemy="1-0E">FA</KodFormularza>\n'
            f'        <WariantFormularza>3</WariantFormularza>\n'
            f'        <DataWytworzeniaFa>{now_str}</DataWytworzeniaFa>\n'
            f'        <SystemInfo>Odoo 17 / l10n_pl_ksef_margin</SystemInfo>\n'
            f'    </Naglowek>\n'
        )

    def _render_podmiot1(self) -> str:
        """Sprzedawca (продавець)."""
        nip = self._get_seller_nip()
        name = xml_escape(self.company.name)
        street = xml_escape(
            (self.company.street or '')
            + (' ' + self.company.street2 if self.company.street2 else '')
        )
        city = xml_escape(((self.company.zip or '') + ' ' + (self.company.city or '')).strip())
        country = xml_escape(self.company.country_id.code or 'PL')
        adres_l2 = f'\n            <AdresL2>{city}</AdresL2>' if city else ''

        return (
            f'    <Podmiot1>\n'
            f'        <DaneIdentyfikacyjne>\n'
            f'            <NIP>{nip}</NIP>\n'
            f'            <Nazwa>{name}</Nazwa>\n'
            f'        </DaneIdentyfikacyjne>\n'
            f'        <Adres>\n'
            f'            <KodKraju>{country}</KodKraju>\n'
            f'            <AdresL1>{street or name}</AdresL1>{adres_l2}\n'
            f'        </Adres>\n'
            f'    </Podmiot1>\n'
        )

    def _render_podmiot2(self) -> str:
        """Nabywca (покупець). NIP для B2B, BrakID=1 для фізосіб."""
        raw_nip = (self.partner.vat or '').replace('PL', '').replace(' ', '')
        name = xml_escape(self.partner.name or '')

        identifier = f'<NIP>{raw_nip}</NIP>' if raw_nip else '<BrakID>1</BrakID>'

        adres_xml = self._render_adres_partnera()

        return (
            f'    <Podmiot2>\n'
            f'        <DaneIdentyfikacyjne>\n'
            f'            {identifier}\n'
            f'            <Nazwa>{name}</Nazwa>\n'
            f'        </DaneIdentyfikacyjne>{adres_xml}\n'
            f'        <JST>2</JST>\n'
            f'        <GV>2</GV>\n'
            f'    </Podmiot2>\n'
        )

    def _render_adres_partnera(self) -> str:
        street = xml_escape(
            (self.partner.street or '')
            + (' ' + self.partner.street2 if self.partner.street2 else '')
        )
        city = xml_escape(((self.partner.zip or '') + ' ' + (self.partner.city or '')).strip())
        if not street and not city:
            return ''
        country = xml_escape(self.partner.country_id.code or 'PL')
        adres_l2 = f'\n            <AdresL2>{city}</AdresL2>' if city else ''
        adres_l1 = street or xml_escape(self.partner.name)
        return (
            f'\n        <Adres>\n'
            f'            <KodKraju>{country}</KodKraju>\n'
            f'            <AdresL1>{adres_l1}</AdresL1>{adres_l2}\n'
            f'        </Adres>'
        )

    def _render_fa(self, invoice_date: str) -> str:
        lines_xml = self._render_all_lines()
        summary = self._render_summary()
        adnotacje = self._render_adnotacje()

        is_refund = self.move.move_type == 'out_refund'
        rodzaj = 'KOR' if is_refund else 'VAT'
        kor_section = self._render_dane_fa_korygowanej() if is_refund else ''

        return (
            f'    <Fa>\n'
            f'        <KodWaluty>{self.move.currency_id.name}</KodWaluty>\n'
            f'        <P_1>{invoice_date}</P_1>\n'
            f'        <P_2>{xml_escape(self.move.name)}</P_2>\n'
            f'        <P_6>{invoice_date}</P_6>\n'
            f'{summary}'
            f'{adnotacje}'
            f'        <RodzajFaktury>{rodzaj}</RodzajFaktury>\n'
            f'{kor_section}'
            f'{lines_xml}'
            f'    </Fa>\n'
        )

    def _render_dane_fa_korygowanej(self) -> str:
        """Sekcja korygująca FA(3) — bezpośrednio po <RodzajFaktury>KOR</RodzajFaktury>.

        XSD structure (xs:sequence minOccurs="0" after RodzajFaktury):
          PrzyczynaKorekty (opt)
          DaneFaKorygowanej (required, maxOccurs=50000):
            DataWystFaKorygowanej  — data oryginalnej faktury
            NrFaKorygowanej        — numer oryginalnej faktury
            choice:
              NrKSeF=1 + NrKSeFFaKorygowanej  — gdy znamy Nr KSeF oryginału
              NrKSeFN=1                         — gdy oryginał był poza KSeF

        Odoo: reversed_entry_id.l10n_pl_ksef_reference → Nr KSeF oryginału (jeśli zaakceptowana).
        """
        orig = self.move.reversed_entry_id
        if orig:
            orig_number = xml_escape(orig.name or '')
            orig_date = str(orig.invoice_date or self.move.invoice_date)
            # Nr KSeF of original: only if it was accepted (starts with NIP, not SO-)
            orig_ksef = orig.l10n_pl_ksef_reference or ''
            orig_ksef_known = (
                orig_ksef and not orig_ksef.startswith('2026') and not orig_ksef.startswith('20')
            )
            # More reliable: accepted invoices have 20-digit NIP prefix like '6222847059-...'
            orig_ksef_known = bool(
                orig_ksef and '-' in orig_ksef and not orig_ksef.startswith('20260')
            )
        else:
            # Manual credit note — ref field contains original invoice number
            orig_number = xml_escape((self.move.ref or '').strip())
            orig_date = str(self.move.invoice_date)
            orig_ksef = ''
            orig_ksef_known = False

        if not orig_number:
            raise UserError(
                _(
                    'Faktura korygująca %(name)s nie ma wskazanej oryginalnej faktury. '
                    "Utwórz notę kredytową przez przycisk 'Dodaj notę kredytową' lub "
                    "uzupełnij pole 'Odwołanie' z numerem korygowanej faktury.",
                    name=self.move.name,
                )
            )

        if orig_ksef_known:
            ksef_ref_xml = (
                f'                <NrKSeF>1</NrKSeF>\n'
                f'                <NrKSeFFaKorygowanej>{xml_escape(orig_ksef)}</NrKSeFFaKorygowanej>\n'
            )
        else:
            ksef_ref_xml = '                <NrKSeFN>1</NrKSeFN>\n'

        return (
            f'        <DaneFaKorygowanej>\n'
            f'            <DataWystFaKorygowanej>{orig_date}</DataWystFaKorygowanej>\n'
            f'            <NrFaKorygowanej>{orig_number}</NrFaKorygowanej>\n'
            f'{ksef_ref_xml}'
            f'        </DaneFaKorygowanej>\n'
        )

    # XSD-required order for P_13_x groups (Fa sequence)
    _P13_XSD_ORDER = ['1', '2', '3', '4', '5', '6_1', '6_2', '6_3', '7', '8', '9', '10', '11']

    def _render_summary(self) -> str:
        """Build P_13_x / P_14_x / P_15 summary in strict XSD sequence order.

        FA(3) XSD requires:
          P_13_1 + P_14_1  (23%)
          P_13_2 + P_14_2  (8%)
          ...
          P_13_7           (zw)
          P_13_8..P_13_10  (np I/II, oo)
          P_13_11          (marża — ALWAYS last among P_13_x)
          P_15             (total gross)

        For mixed invoices (marża + regular VAT lines):
          - marża lines → P_13_11
          - regular VAT lines → P_13_1..P_13_10 in XSD order
        """
        total = round(float(self.move.amount_total), 2)

        # Split lines into marża and regular groups
        product_lines = self.move.invoice_line_ids.filtered(
            lambda ln: ln.display_type not in ('line_section', 'line_note')
        )

        marza_total = 0.0
        regular_groups: dict[str, list[float]] = {}

        for line in product_lines:
            if self._effective_marza(line):
                marza_total += float(line.price_total)
            else:
                code = self._get_tax_code(line) or '23'
                net = float(line.price_subtotal)
                tax_amt = float(line.price_total) - net
                if code not in regular_groups:
                    regular_groups[code] = [0.0, 0.0]
                regular_groups[code][0] += net
                regular_groups[code][1] += tax_amt

        marza_total = round(marza_total, 2)

        # Sort regular groups by XSD P_13 position
        def _xsd_pos(code: str) -> int:
            fn = self._tax_code_to_field_number(code)
            try:
                return self._P13_XSD_ORDER.index(fn) if fn else 99
            except ValueError:
                return 99

        summary = ''
        for code, (net_amt, tax_amt) in sorted(
            regular_groups.items(), key=lambda kv: _xsd_pos(kv[0])
        ):
            # Skip groups that are net=0 AND tax=0 — emitting <P_13_X>0.0</P_13_X>
            # without <P_14_X> triggers KSeF semantic reject (kod 450):
            # "invalid child element 'P_13_7' ... expected: 'P_14_1'".
            if round(net_amt, 2) == 0 and round(tax_amt, 2) == 0:
                continue
            field_num = self._tax_code_to_field_number(code)
            if field_num:
                summary += f'        <P_13_{field_num}>{round(net_amt, 2)}</P_13_{field_num}>\n'
                if round(tax_amt, 2):
                    summary += f'        <P_14_{field_num}>{round(tax_amt, 2)}</P_14_{field_num}>\n'

        if marza_total:
            summary += f'        <P_13_11>{marza_total}</P_13_11>\n'

        if not summary:
            # Fallback: pure invoice with no structured tax rates
            net = round(float(self.move.amount_untaxed), 2)
            tax = round(float(self.move.amount_tax), 2)
            summary = f'        <P_13_1>{net}</P_13_1>\n'
            if tax:
                summary += f'        <P_14_1>{tax}</P_14_1>\n'

        summary += f'        <P_15>{total}</P_15>\n'
        return summary

    def _detect_zwolnienie(self) -> bool:
        """Чи є хоча б один рядок з податком zwolnienie z VAT (zw)."""
        for line in self.move.invoice_line_ids:
            for tax in line.tax_ids:
                name_lower = (tax.name or '').lower()
                if 'zw' in name_lower or 'zwolni' in name_lower:
                    return True
        return False

    def _render_adnotacje(self) -> str:
        if self._is_marza:
            pmarzy = (
                '            <PMarzy>\n'
                '                <P_PMarzy>1</P_PMarzy>\n'
                '                <P_PMarzy_2>1</P_PMarzy_2>\n'
                '            </PMarzy>\n'
            )
        else:
            pmarzy = (
                '            <PMarzy>\n'
                '                <P_PMarzyN>1</P_PMarzyN>\n'
                '            </PMarzy>\n'
            )

        # Zwolnienie: P_19=1 + P_19A (art. 43 ust. 1) if zw lines exist, else P_19N=1
        if self._detect_zwolnienie():
            zwolnienie = (
                '            <Zwolnienie>\n'
                '                <P_19>1</P_19>\n'
                '                <P_19A>art. 43 ust. 1</P_19A>\n'
                '            </Zwolnienie>\n'
            )
        else:
            zwolnienie = (
                '            <Zwolnienie>\n'
                '                <P_19N>1</P_19N>\n'
                '            </Zwolnienie>\n'
            )

        return (
            '        <Adnotacje>\n'
            '            <P_16>2</P_16>\n'
            '            <P_17>2</P_17>\n'
            '            <P_18>2</P_18>\n'
            '            <P_18A>2</P_18A>\n'
            f'{zwolnienie}'
            '            <NoweSrodkiTransportu>\n'
            '                <P_22N>1</P_22N>\n'
            '            </NoweSrodkiTransportu>\n'
            '            <P_23>2</P_23>\n'
            f'{pmarzy}'
            '        </Adnotacje>\n'
        )

    def _render_all_lines(self) -> str:
        product_lines = self.move.invoice_line_ids.filtered(
            lambda ln: ln.display_type not in ('line_section', 'line_note')
        )
        if not product_lines:
            raise UserError(
                _(
                    'Faktura %(name)s nie ma pozycji do wysłania do KSeF.',
                    name=self.move.name,
                )
            )
        return ''.join(
            self._render_line(line, idx) for idx, line in enumerate(product_lines, start=1)
        )

    def _render_line(self, line, idx: int) -> str:
        name = self._get_line_name_pl(line)
        qty = round(float(line.quantity), 4)
        unit_price = round(float(line.price_unit), 2)
        uom_code = self._get_uom_code(line)

        if self._effective_marza(line):
            # Marża line: P_11 = brutto, no P_12
            amount = round(float(line.price_total), 2)
            tax_xml = ''
        else:
            # Regular VAT line: P_11 = netto, P_12 = tax rate code
            amount = round(float(line.price_subtotal), 2)
            tax_code = self._get_tax_code(line)
            tax_xml = f'\n        <P_12>{tax_code}</P_12>' if tax_code else ''

        return (
            f'    <FaWiersz>\n'
            f'        <NrWierszaFa>{idx}</NrWierszaFa>\n'
            f'        <P_7>{name}</P_7>\n'
            f'        <P_8A>{uom_code}</P_8A>\n'
            f'        <P_8B>{qty}</P_8B>\n'
            f'        <P_9A>{unit_price}</P_9A>\n'
            f'        <P_11>{amount}</P_11>{tax_xml}\n'
            f'    </FaWiersz>\n'
        )

    # ─────────────────────────────────────────────
    # Helpers
    # ─────────────────────────────────────────────

    # Regex: "Дата: DD.MM - DD.MM.YYYY" or "DD.MM - DD.MM.YYYY" (with – or -)
    _DATE_RANGE_RE = re.compile(r'(?:Дата:\s*)?(\d{2}\.\d{2})\s*[-–]\s*(\d{2}\.\d{2}\.\d{4})')

    def _get_line_name_pl(self, line) -> str:
        """Build P_7 name: Polish product name + date range from line description."""
        # Polish product name
        if line.product_id:
            pl_name = line.product_id.with_context(lang='pl_PL').name or ''
            # Cyrillic in P_7 means the product is missing a Polish translation
            if pl_name and any('Ѐ' <= c <= 'ӿ' for c in pl_name):
                _logger.warning(
                    'KSeF: %s — produkt "%s" nie ma polskiego tłumaczenia'
                    ' (P_7 zawiera cyrylicę). Dodaj tłumaczenie: Odoo → Produkty → Tłumaczenia.',
                    self.move.name,
                    pl_name,
                )
        else:
            pl_name = ''

        if not pl_name:
            pl_name = 'Usługa'

        # Extract date range from line.name (e.g. "Дата: 23.07 - 01.08.2026")
        date_suffix = ''
        if line.name:
            m = self._DATE_RANGE_RE.search(line.name)
            if m:
                date_suffix = f' ({m.group(1)}–{m.group(2)})'

        return xml_escape((pl_name + date_suffix)[:256])

    def _get_seller_nip(self) -> str:
        return (self.company.vat or '').replace('PL', '').replace(' ', '')

    def _get_tax_code(self, line) -> str | None:
        """Повертає код ставки KSeF для рядка або None (для marża).

        Значення відповідають TStawkaPodatku в FA(3) XSD (case-sensitive!):
        '23','22','8','7','5','4','3','0 KR','0 WDT','0 EX','zw','oo','np I','np II'

        Пріоритет: перший знайдений податок з відомою ставкою.
        Fallback: '23'.
        """
        for tax in line.tax_ids:
            name_lower = (tax.name or '').lower()
            # Marża — без коdu P_12
            if any(
                kw in name_lower for kw in ('marż', 'marza', 'margin', 'маржа', 'маржі', 'маржу')
            ):
                return None
            # NP — Nie Podlega (np I = poza terytorium kraju)
            if 'np' in name_lower or 'nie podlega' in name_lower:
                return 'np I'
            # Zwolnienie — musi być lowercase 'zw'
            if 'zw' in name_lower or 'zwolni' in name_lower:
                return 'zw'
            # Числова ставка
            amount = int(tax.amount) if tax.amount == int(tax.amount) else None
            if amount is not None and amount in TAX_CODE_MAP:
                return TAX_CODE_MAP[amount]
        return '23'  # fallback

    def _get_uom_code(self, line) -> str:
        """Повертає код одиниці виміру KSeF для рядка."""
        if line.product_uom_id:
            uom_name = line.product_uom_id.name or ''
            if uom_name in UOM_CODE_MAP:
                return UOM_CODE_MAP[uom_name]
            # Шукаємо по частковому збігу
            for key, val in UOM_CODE_MAP.items():
                if key.lower() in uom_name.lower():
                    return val
        return UOM_DEFAULT

    def _compute_tax_groups(self) -> dict:
        """Групує рядки по ставках ПДВ для розрахунку P_13_x / P_14_x.

        Returns:
            dict: {tax_code: (net_amount, tax_amount)}
        """
        groups = {}
        for line in self.move.invoice_line_ids.filtered(
            lambda ln: ln.display_type not in ('line_section', 'line_note')
        ):
            code = self._get_tax_code(line) or '23'
            net = float(line.price_subtotal)
            tax_amt = float(line.price_total) - net
            if code not in groups:
                groups[code] = [0.0, 0.0]
            groups[code][0] += net
            groups[code][1] += tax_amt
        return {k: tuple(v) for k, v in groups.items()}

    def _tax_code_to_field_number(self, tax_code: str) -> str | None:
        """Конвертує код ставки в номер поля FA(3) (P_13_X).

        Офіційний маппінг з XSD FA(3) v1-0E (https://crd.gov.pl/wzor/2025/06/25/13775/schemat.xsd):
          P_13_1   = 23% / 22%
          P_13_2   = 8% / 7%
          P_13_3   = 5%
          P_13_4   = 4% (ryczałt taksówki)
          P_13_5   = OSS
          P_13_6_1 = 0 KR (krajowe)
          P_13_6_2 = 0 WDT (wewnątrzwspólnotowa)
          P_13_6_3 = 0 EX (eksport)
          P_13_7   = zw (zwolnione od podatku)
          P_13_8   = np I (poza terytorium kraju)
          P_13_9   = np II (art. 100 ust. 1 pkt 4)
          P_13_10  = oo (odwrotne obciążenie)
          P_13_11  = marża (generowane osobno)

        UWAGA: P_13_6 (bez podpunktu) NIE ISTNIEJE. P_14_x nie istnieje dla zw/np*/oo/marża.
        """
        mapping = {
            '23': '1',
            '22': '1',
            '8': '2',
            '7': '2',
            '5': '3',
            '4': '4',
            '3': '5',
            '0 KR': '6_1',
            '0 WDT': '6_2',
            '0 EX': '6_3',
            'zw': '7',
            'np I': '8',
            'np II': '9',
            'oo': '10',
        }
        return mapping.get(str(tax_code))
