"""
Tests for KsefXmlBuilder — FA(3) XML generation.

These are Odoo integration tests (require ORM / db).
Run via:
    odoo -u l10n_pl_ksef_margin --test-enable -d your_db ...

Each test creates a minimal in-memory invoice mock and checks the generated XML.
We use simple Python objects (Mock) to avoid DB setup in the builder unit tests.
"""

from unittest.mock import MagicMock
from xml.etree import ElementTree as ET

from odoo.tests.common import BaseCase

_NS = 'http://crd.gov.pl/wzor/2025/06/25/13775/'
_N = f'{{{_NS}}}'


def _make_tax(name: str, amount: float) -> MagicMock:
    t = MagicMock()
    t.name = name
    t.amount = amount
    return t


def _make_line(
    name: str = 'Test line',
    qty: float = 1.0,
    price_unit: float = 100.0,
    price_subtotal: float = 100.0,
    price_total: float = 123.0,
    taxes: list | None = None,
    uom_name: str = 'Units',
    display_type: str | bool = False,  # Odoo Selection fields are `False`, not '', when unset
) -> MagicMock:
    line = MagicMock()
    line.display_type = display_type
    line.name = name
    line.quantity = qty
    line.price_unit = price_unit
    line.price_subtotal = price_subtotal
    line.price_total = price_total
    line.tax_ids = taxes or []
    product = MagicMock()
    product.name = name
    product.with_context.return_value = product
    line.product_id = product
    line.product_uom_id = MagicMock()
    line.product_uom_id.name = uom_name
    return line


def _make_move(
    name: str = 'FV/2026/001',
    invoice_date: str = '2026-01-15',
    seller_nip: str = '1234567890',
    seller_name: str = 'ACME Sp. z o.o.',
    seller_street: str = 'ul. Testowa 1',
    seller_city: str = '00-001 Warszawa',
    seller_country_code: str = 'PL',
    buyer_nip: str = '0987654321',
    buyer_name: str = 'Klient S.A.',
    buyer_street: str = '',
    buyer_city: str = '',
    buyer_country_code: str = 'PL',
    amount_total: float = 123.0,
    amount_untaxed: float = 100.0,
    amount_tax: float = 23.0,
    lines: list | None = None,
    currency: str = 'PLN',
) -> MagicMock:
    move = MagicMock()
    move.name = name
    move.invoice_date = invoice_date

    # company
    move.company_id.name = seller_name
    move.company_id.vat = f'PL{seller_nip}'
    move.company_id.street = seller_street
    move.company_id.street2 = ''
    move.company_id.city = seller_city.split(' ', 1)[-1] if ' ' in seller_city else seller_city
    move.company_id.zip = seller_city.split(' ')[0] if ' ' in seller_city else ''
    move.company_id.country_id.code = seller_country_code

    # partner
    move.partner_id.name = buyer_name
    move.partner_id.vat = f'PL{buyer_nip}' if buyer_nip else ''
    move.partner_id.street = buyer_street
    move.partner_id.street2 = ''
    move.partner_id.city = buyer_city.split(' ', 1)[-1] if ' ' in buyer_city else buyer_city
    move.partner_id.zip = buyer_city.split(' ')[0] if ' ' in buyer_city else ''
    move.partner_id.country_id.code = buyer_country_code

    move.amount_total = amount_total
    move.amount_untaxed = amount_untaxed
    move.amount_tax = amount_tax
    move.currency_id.name = currency

    default_lines = lines if lines is not None else [_make_line(taxes=[_make_tax('VAT 23%', 23)])]
    # Keep invoice_line_ids as MagicMock so we can set .filtered on it
    lines_mock = MagicMock()
    lines_mock.__iter__ = MagicMock(return_value=iter(default_lines))
    lines_mock.__len__ = MagicMock(return_value=len(default_lines))
    lines_mock.filtered = lambda fn: [ln for ln in default_lines if fn(ln)]
    move.invoice_line_ids = lines_mock

    return move


# ── Helper: parse generated XML ───────────────────────────────────────────────


def _parse_xml(xml_bytes: bytes) -> ET.Element:
    return ET.fromstring(xml_bytes)


def _find(root, *tags) -> ET.Element | None:
    el = root
    for tag in tags:
        found = el.find(f'{_N}{tag}')
        if found is None:
            found = el.find(tag)
        if found is None:
            return None
        el = found
    return el


def _text(root, *tags) -> str | None:
    el = _find(root, *tags)
    return el.text if el is not None else None


# ── Tests ─────────────────────────────────────────────────────────────────────


class TestKsefXmlBuilderStandardVat(BaseCase):
    def setUp(self):
        from odoo.addons.l10n_pl_ksef_margin.services.ksef_xml_builder import KsefXmlBuilder

        move = _make_move()
        self.xml_bytes = KsefXmlBuilder(move).build()
        self.root = _parse_xml(self.xml_bytes)

    def test_returns_bytes(self):
        self.assertIsInstance(self.xml_bytes, bytes)

    def test_namespace(self):
        self.assertIn(_NS, self.root.tag)

    def test_seller_nip(self):
        nip = _text(self.root, 'Podmiot1', 'DaneIdentyfikacyjne', 'NIP')
        self.assertEqual(nip, '1234567890')

    def test_invoice_number(self):
        p2 = _text(self.root, 'Fa', 'P_2')
        self.assertEqual(p2, 'FV/2026/001')

    def test_invoice_date(self):
        p1 = _text(self.root, 'Fa', 'P_1')
        self.assertEqual(p1, '2026-01-15')

    def test_total(self):
        p15 = _text(self.root, 'Fa', 'P_15')
        self.assertAlmostEqual(float(p15), 123.0)

    def test_standard_vat_fields_present(self):
        p13_1 = _text(self.root, 'Fa', 'P_13_1')
        self.assertIsNotNone(p13_1)
        self.assertAlmostEqual(float(p13_1), 100.0)

    def test_no_marza_flag(self):
        fa = _find(self.root, 'Fa')
        adnotacje = _find(fa, 'Adnotacje')
        pmarzy = _find(adnotacje, 'PMarzy')
        # Should have P_PMarzyN=1 (not margin)
        p_pmarzy_n = _text(pmarzy, 'P_PMarzyN')
        self.assertEqual(p_pmarzy_n, '1')

    def test_fa_wiersz_exists(self):
        fa = _find(self.root, 'Fa')
        _tmp = fa.find(f'{_N}FaWiersz')
        wiersz = _tmp if _tmp is not None else fa.find('FaWiersz')
        self.assertIsNotNone(wiersz)

    def test_rodzaj_faktury(self):
        rodzaj = _text(self.root, 'Fa', 'RodzajFaktury')
        self.assertEqual(rodzaj, 'VAT')

    def test_xml_well_formed(self):
        # If ET.fromstring didn't raise, it's well-formed.
        self.assertIsNotNone(self.root)


class TestKsefXmlBuilderMarza(BaseCase):
    def setUp(self):
        from odoo.addons.l10n_pl_ksef_margin.services.ksef_xml_builder import KsefXmlBuilder

        marza_tax = _make_tax('VAT Marża turystyczna', 23)
        line = _make_line(
            name='Obóz letni Portugalia',
            price_total=500.0,
            price_subtotal=500.0,
            taxes=[marza_tax],
        )
        line.display_type = False
        move = _make_move(
            name='FVM/2026/001',
            amount_total=500.0,
            amount_untaxed=500.0,
            amount_tax=0.0,
            lines=[line],
        )
        self.xml_bytes = KsefXmlBuilder(move).build()
        self.root = _parse_xml(self.xml_bytes)

    def test_marza_flag_set(self):
        fa = _find(self.root, 'Fa')
        adnotacje = _find(fa, 'Adnotacje')
        pmarzy = _find(adnotacje, 'PMarzy')
        p_pmarzy = _text(pmarzy, 'P_PMarzy')
        self.assertEqual(p_pmarzy, '1')

    def test_p13_11_present(self):
        p13_11 = _text(self.root, 'Fa', 'P_13_11')
        self.assertIsNotNone(p13_11)
        self.assertAlmostEqual(float(p13_11), 500.0)

    def test_no_p13_1(self):
        # Standard VAT fields should not appear for Marża invoice
        p13_1 = _text(self.root, 'Fa', 'P_13_1')
        self.assertIsNone(p13_1)

    def test_line_has_no_p12(self):
        fa = _find(self.root, 'Fa')
        _tmp = fa.find(f'{_N}FaWiersz')
        wiersz = _tmp if _tmp is not None else fa.find('FaWiersz')
        p12 = wiersz.find(f'{_N}P_12') if wiersz is not None else None
        self.assertIsNone(p12)


class TestKsefXmlBuilderNoBuyerNip(BaseCase):
    """Buyer without NIP → BrakID=1 must appear."""

    def setUp(self):
        from odoo.addons.l10n_pl_ksef_margin.services.ksef_xml_builder import KsefXmlBuilder

        move = _make_move(buyer_nip='')
        self.root = _parse_xml(KsefXmlBuilder(move).build())

    def test_brak_id_present(self):
        brak_id = _text(self.root, 'Podmiot2', 'DaneIdentyfikacyjne', 'BrakID')
        self.assertEqual(brak_id, '1')

    def test_no_nip_in_podmiot2(self):
        nip = _text(self.root, 'Podmiot2', 'DaneIdentyfikacyjne', 'NIP')
        self.assertIsNone(nip)


class TestKsefXmlBuilderTaxCodes(BaseCase):
    """Dynamic tax code mapping test."""

    def _build_with_tax(self, tax_name: str, tax_amount: float) -> ET.Element:
        from odoo.addons.l10n_pl_ksef_margin.services.ksef_xml_builder import KsefXmlBuilder

        tax = _make_tax(tax_name, tax_amount)
        line = _make_line(taxes=[tax])
        line.display_type = False
        move = _make_move(lines=[line])
        return _parse_xml(KsefXmlBuilder(move).build())

    def test_tax_23(self):
        root = self._build_with_tax('VAT 23%', 23)
        fa = _find(root, 'Fa')
        _tmp = fa.find(f'{_N}FaWiersz')
        wiersz = _tmp if _tmp is not None else fa.find('FaWiersz')
        p12 = _text(wiersz, 'P_12') if wiersz is not None else None
        self.assertEqual(p12, '23')

    def test_tax_8(self):
        root = self._build_with_tax('VAT 8%', 8)
        fa = _find(root, 'Fa')
        _tmp = fa.find(f'{_N}FaWiersz')
        wiersz = _tmp if _tmp is not None else fa.find('FaWiersz')
        p12 = _text(wiersz, 'P_12') if wiersz is not None else None
        self.assertEqual(p12, '8')

    def test_tax_zw(self):
        root = self._build_with_tax('Zwolnienie z VAT', 0)
        fa = _find(root, 'Fa')
        _tmp = fa.find(f'{_N}FaWiersz')
        wiersz = _tmp if _tmp is not None else fa.find('FaWiersz')
        p12 = _text(wiersz, 'P_12') if wiersz is not None else None
        self.assertEqual(p12, 'ZW')


class TestKsefXmlBuilderEmptyZeroVatGroup(BaseCase):
    """Regression: invoice with one regular-VAT line at 0/0 + one non-VAT line
    must NOT emit a hanging <P_13_1>0.0</P_13_1>. KSeF rejects this with kod 450
    (Błąd weryfikacji semantyki — invalid child element 'P_13_7', expected 'P_14_1').

    Observed in production: an invoice mixing a zero-amount regular-VAT line
    ("Indywidualna asysta", 23% S) with a nonzero VAT-Marża line ("Rezerwacja")
    triggered exactly this reject.
    """

    def setUp(self):
        from odoo.addons.l10n_pl_ksef_margin.services.ksef_xml_builder import KsefXmlBuilder

        zw_tax = _make_tax('ПДВ Маржа - Туристичні Послуги', 0)
        vat23 = _make_tax('23% S', 23)
        rezerwacja = _make_line(
            name='Rezerwacja',
            price_subtotal=3250.0,
            price_total=3250.0,
            taxes=[zw_tax],
        )
        rezerwacja.display_type = False
        asysta_empty = _make_line(
            name='Indywidualna asysta',
            price_subtotal=0.0,
            price_total=0.0,
            taxes=[vat23],
        )
        asysta_empty.display_type = False
        move = _make_move(
            name='FVM/2026/TEST',
            amount_total=3250.0,
            amount_untaxed=3250.0,
            amount_tax=0.0,
            lines=[rezerwacja, asysta_empty],
        )
        self.root = _parse_xml(KsefXmlBuilder(move).build())

    def test_no_hanging_p13_1_zero(self):
        # Empty 23% group (net=0, tax=0) must be skipped — no <P_13_1>0.0</P_13_1>
        p13_1 = _text(self.root, 'Fa', 'P_13_1')
        self.assertIsNone(
            p13_1,
            f'Expected no <P_13_1> for empty 23% group, got {p13_1!r}. '
            'This regresses a production KSeF reject (kod 450).',
        )

    def test_p15_still_correct(self):
        p15 = _text(self.root, 'Fa', 'P_15')
        self.assertAlmostEqual(float(p15), 3250.0)
