"""
Tests for KsefXmlParser — FA(3) XML parsing.

Run via Odoo test runner:
    odoo -u l10n_pl_ksef_margin --test-enable -d your_db ...
Or directly with pytest (no Odoo ORM dependency — parser is pure Python):
    pytest l10n_pl_ksef_margin/tests/test_xml_parser.py
"""

from datetime import date

from odoo.addons.l10n_pl_ksef_margin.services.ksef_xml_parser import (
    KsefXmlParseError,
    KsefXmlParser,
)
from odoo.tests.common import BaseCase

# ── Test fixtures ─────────────────────────────────────────────────────────────

_NS = 'http://crd.gov.pl/wzor/2025/06/25/13775/'

_STANDARD_VAT_XML = f"""<?xml version="1.0" encoding="UTF-8"?>
<Faktura xmlns="{_NS}">
    <Podmiot1>
        <OsobaNiefizyczna>
            <NIP>1234567890</NIP>
            <PelnaNazwa>ACME Sp. z o.o.</PelnaNazwa>
        </OsobaNiefizyczna>
    </Podmiot1>
    <Podmiot2>
        <OsobaNiefizyczna>
            <NIP>0987654321</NIP>
            <PelnaNazwa>Klient Testowy S.A.</PelnaNazwa>
        </OsobaNiefizyczna>
    </Podmiot2>
    <Fa>
        <P_1>2026-01-15</P_1>
        <P_2>FV/2026/001</P_2>
        <P_6>2026-01-15</P_6>
        <P_13_1>1000.00</P_13_1>
        <P_14_1>230.00</P_14_1>
        <P_15>1000.00</P_15>
        <P_16>1230.00</P_16>
        <Adnotacje>
            <P_16>2</P_16>
            <P_17>2</P_17>
            <P_18>2</P_18>
            <P_18A>2</P_18A>
            <Zwolnienie><P_19N>1</P_19N></Zwolnienie>
            <NoweSrodkiTransportu><P_22N>1</P_22N></NoweSrodkiTransportu>
            <P_23>2</P_23>
            <PMarzy><P_PMarzyN>1</P_PMarzyN></PMarzy>
        </Adnotacje>
        <RodzajFaktury>VAT</RodzajFaktury>
        <FaWiersz>
            <NrWierszaFa>1</NrWierszaFa>
            <P_7>Usługa turystyczna</P_7>
            <P_8A>szt</P_8A>
            <P_8B>2</P_8B>
            <P_9A>500.00</P_9A>
            <P_11>1000.00</P_11>
            <P_12>23</P_12>
        </FaWiersz>
        <Platnosci>
            <Platnosc>
                <TerminPlatnosci>
                    <Termin>2026-01-30</Termin>
                </TerminPlatnosci>
                <RachunekBankowy>
                    <NrRB>PL61109010140000071219812874</NrRB>
                </RachunekBankowy>
            </Platnosc>
        </Platnosci>
    </Fa>
</Faktura>""".encode()

_MARZA_XML = f"""<?xml version="1.0" encoding="UTF-8"?>
<Faktura xmlns="{_NS}">
    <Podmiot1>
        <OsobaNiefizyczna>
            <NIP>1111111111</NIP>
            <PelnaNazwa>Camp Scout Sp. z o.o.</PelnaNazwa>
        </OsobaNiefizyczna>
    </Podmiot1>
    <Podmiot2>
        <OsobaNiefizyczna>
            <BrakID>1</BrakID>
            <Nazwa>Jan Kowalski</Nazwa>
        </OsobaNiefizyczna>
    </Podmiot2>
    <Fa>
        <P_1>2026-03-01</P_1>
        <P_2>FVM/2026/001</P_2>
        <P_6>2026-03-01</P_6>
        <P_13_11>500.00</P_13_11>
        <P_15>500.00</P_15>
        <P_16>500.00</P_16>
        <Adnotacje>
            <P_16>2</P_16>
            <P_17>2</P_17>
            <P_18>2</P_18>
            <P_18A>2</P_18A>
            <Zwolnienie><P_19N>1</P_19N></Zwolnienie>
            <NoweSrodkiTransportu><P_22N>1</P_22N></NoweSrodkiTransportu>
            <P_23>2</P_23>
            <PMarzy>
                <P_PMarzy>1</P_PMarzy>
                <P_PMarzy_3_3>1</P_PMarzy_3_3>
            </PMarzy>
        </Adnotacje>
        <RodzajFaktury>VAT</RodzajFaktury>
        <FaWiersz>
            <NrWierszaFa>1</NrWierszaFa>
            <P_7>Obóz letni Portugalia 2026</P_7>
            <P_8A>szt</P_8A>
            <P_8B>1</P_8B>
            <P_9A>500.00</P_9A>
            <P_11>500.00</P_11>
        </FaWiersz>
    </Fa>
</Faktura>""".encode()

_NATURAL_PERSON_SELLER_XML = f"""<?xml version="1.0" encoding="UTF-8"?>
<Faktura xmlns="{_NS}">
    <Podmiot1>
        <OsobaFizyczna>
            <NIP>9999999999</NIP>
            <ImiePierwsze>Anna</ImiePierwsze>
            <Nazwisko>Nowak</Nazwisko>
        </OsobaFizyczna>
    </Podmiot1>
    <Fa>
        <P_1>2026-02-10</P_1>
        <P_2>FV/2026/002</P_2>
        <P_6>2026-02-10</P_6>
        <P_13_6>200.00</P_13_6>
        <P_15>200.00</P_15>
        <P_16>200.00</P_16>
        <Adnotacje>
            <P_16>2</P_16><P_17>2</P_17><P_18>2</P_18><P_18A>2</P_18A>
            <Zwolnienie><P_19N>1</P_19N></Zwolnienie>
            <NoweSrodkiTransportu><P_22N>1</P_22N></NoweSrodkiTransportu>
            <P_23>2</P_23>
            <PMarzy><P_PMarzyN>1</P_PMarzyN></PMarzy>
        </Adnotacje>
        <RodzajFaktury>VAT</RodzajFaktury>
        <FaWiersz>
            <NrWierszaFa>1</NrWierszaFa>
            <P_7>Usługa ZW</P_7>
            <P_8A>godz</P_8A>
            <P_8B>4</P_8B>
            <P_9A>50.00</P_9A>
            <P_11>200.00</P_11>
            <P_12>ZW</P_12>
        </FaWiersz>
    </Fa>
</Faktura>""".encode()


# ── Test cases ────────────────────────────────────────────────────────────────


class TestKsefXmlParserStandardVat(BaseCase):
    def setUp(self):
        self.data = KsefXmlParser(_STANDARD_VAT_XML).parse()

    def test_seller_nip(self):
        self.assertEqual(self.data['seller_nip'], '1234567890')

    def test_seller_name(self):
        self.assertEqual(self.data['seller_name'], 'ACME Sp. z o.o.')

    def test_invoice_number(self):
        self.assertEqual(self.data['invoice_number'], 'FV/2026/001')

    def test_invoice_date(self):
        self.assertEqual(self.data['invoice_date'], date(2026, 1, 15))

    def test_due_date(self):
        self.assertEqual(self.data['due_date'], date(2026, 1, 30))

    def test_payment_iban(self):
        self.assertEqual(self.data['payment_iban'], 'PL61109010140000071219812874')

    def test_not_marza(self):
        self.assertFalse(self.data['is_marza'])

    def test_lines_count(self):
        self.assertEqual(len(self.data['lines']), 1)

    def test_line_details(self):
        line = self.data['lines'][0]
        self.assertEqual(line['name'], 'Usługa turystyczna')
        self.assertAlmostEqual(line['qty'], 2.0)
        self.assertEqual(line['uom'], 'szt')
        self.assertAlmostEqual(line['unit_price'], 500.0)
        self.assertAlmostEqual(line['net'], 1000.0)
        self.assertEqual(line['tax_rate'], 23)

    def test_total_net(self):
        self.assertAlmostEqual(self.data['total_net'], 1000.0)

    def test_total_gross(self):
        self.assertAlmostEqual(self.data['total_gross'], 1230.0)

    def test_tax_breakdown(self):
        self.assertIn(23, self.data['tax_breakdown'])
        self.assertAlmostEqual(self.data['tax_breakdown'][23], 1000.0)


class TestKsefXmlParserMarza(BaseCase):
    def setUp(self):
        self.data = KsefXmlParser(_MARZA_XML).parse()

    def test_is_marza(self):
        self.assertTrue(self.data['is_marza'])

    def test_seller_nip(self):
        self.assertEqual(self.data['seller_nip'], '1111111111')

    def test_seller_name(self):
        self.assertEqual(self.data['seller_name'], 'Camp Scout Sp. z o.o.')

    def test_invoice_number(self):
        self.assertEqual(self.data['invoice_number'], 'FVM/2026/001')

    def test_line_no_tax_rate(self):
        line = self.data['lines'][0]
        self.assertIsNone(line['tax_rate'])

    def test_line_name(self):
        self.assertEqual(self.data['lines'][0]['name'], 'Obóz letni Portugalia 2026')

    def test_tax_breakdown_has_marza(self):
        self.assertIn(None, self.data['tax_breakdown'])

    def test_total(self):
        self.assertAlmostEqual(self.data['total_gross'], 500.0)

    def test_no_due_date(self):
        self.assertIsNone(self.data['due_date'])

    def test_no_iban(self):
        self.assertIsNone(self.data['payment_iban'])


class TestKsefXmlParserNaturalPerson(BaseCase):
    def setUp(self):
        self.data = KsefXmlParser(_NATURAL_PERSON_SELLER_XML).parse()

    def test_seller_name_composed(self):
        self.assertEqual(self.data['seller_name'], 'Anna Nowak')

    def test_seller_nip(self):
        self.assertEqual(self.data['seller_nip'], '9999999999')

    def test_zw_tax_rate(self):
        self.assertEqual(self.data['lines'][0]['tax_rate'], 0)

    def test_uom_godz(self):
        self.assertEqual(self.data['lines'][0]['uom'], 'godz')

    def test_tax_breakdown_zw(self):
        self.assertIn(0, self.data['tax_breakdown'])


class TestKsefXmlParserErrors(BaseCase):
    def test_empty_bytes_raises(self):
        with self.assertRaises(KsefXmlParseError):
            KsefXmlParser(b'').parse()

    def test_malformed_xml_raises(self):
        with self.assertRaises(KsefXmlParseError):
            KsefXmlParser(b'<not valid xml').parse()

    def test_missing_fa_element_raises(self):
        xml = b'<?xml version="1.0"?><Faktura><Podmiot1/></Faktura>'
        with self.assertRaises(KsefXmlParseError):
            KsefXmlParser(xml).parse()


class TestKsefXmlParserDateFormats(BaseCase):
    """Parser should accept multiple date formats."""

    def _parse_date(self, value: str):
        return KsefXmlParser._parse_date(value)

    def test_iso_format(self):
        self.assertEqual(self._parse_date('2026-03-15'), date(2026, 3, 15))

    def test_dmy_format(self):
        self.assertEqual(self._parse_date('15-03-2026'), date(2026, 3, 15))

    def test_compact_format(self):
        self.assertEqual(self._parse_date('20260315'), date(2026, 3, 15))

    def test_none_returns_none(self):
        self.assertIsNone(self._parse_date(None))

    def test_invalid_returns_none(self):
        self.assertIsNone(self._parse_date('not-a-date'))
