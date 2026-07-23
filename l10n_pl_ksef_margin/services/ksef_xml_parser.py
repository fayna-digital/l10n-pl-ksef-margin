"""
KSeF FA(3) XML Parser.

Парсить вхідні фактури FA(3) (namespace 2025/06/25/13775) та повертає
структурований словник з усіма полями, потрібними для створення vendor bill.

Namespace: http://crd.gov.pl/wzor/2025/06/25/13775/
"""

import logging
from datetime import date, datetime

try:
    from defusedxml import ElementTree as ET  # hardened against XML entity/DoS attacks

    _DEFUSED = True
except ImportError:  # pragma: no cover - optional hardening dependency
    from xml.etree import ElementTree as ET

    _DEFUSED = False

_logger = logging.getLogger(__name__)
if not _DEFUSED:
    _logger.warning(
        'defusedxml not installed — falling back to stdlib xml.etree for KSeF '
        'response parsing. Run `pip install defusedxml` to harden against XML '
        'entity-expansion attacks.'
    )

# ── Namespace ────────────────────────────────────────────────────────────────
_NS = 'http://crd.gov.pl/wzor/2025/06/25/13775/'
_N = f'{{{_NS}}}'  # prefix for ElementTree tag lookup, e.g. "{http://...}NIP"

# ── Reverse-map: FA(3) P_13_x → tax-rate int ────────────────────────────────
_FIELD_TO_TAX_RATE = {
    'P_13_1': 23,
    'P_13_2': 8,
    'P_13_3': 5,
    'P_13_4': 4,
    'P_13_5': 3,
    'P_13_6': 0,  # ZW
    'P_13_7': -1,  # NP
    'P_13_11': None,  # Marża (no standard rate)
}


class KsefXmlParseError(ValueError):
    """Raised when the XML cannot be parsed as a valid FA(3) document."""


class KsefXmlParser:
    """Парсить один FA(3) XML-документ.

    Usage::

        data = KsefXmlParser(xml_bytes).parse()
        # → {
        #     'seller_nip': '1234567890',
        #     'seller_name': 'ACME Sp. z o.o.',
        #     'invoice_number': 'FV/2026/001',
        #     'invoice_date': date(2026, 1, 15),
        #     'due_date': date(2026, 1, 30) | None,
        #     'payment_iban': 'PL61109010140000071219812874' | None,
        #     'lines': [
        #         {'name': str, 'qty': float, 'uom': str,
        #          'unit_price': float, 'net': float, 'tax_rate': int | None},
        #     ],
        #     'total_net': float,
        #     'total_gross': float,
        #     'tax_breakdown': {23: float, 8: float, ...},   # tax_rate → net_amount
        #     'is_marza': bool,
        # }
    """

    def __init__(self, xml_bytes: bytes):
        if not xml_bytes:
            raise KsefXmlParseError('XML is empty.')
        self._xml_bytes = xml_bytes
        self._root: ET.Element | None = None

    # ─────────────────────────────────────────────
    # Public API
    # ─────────────────────────────────────────────

    def parse(self) -> dict:
        """Parses the FA(3) XML and returns a structured dict.

        Raises:
            KsefXmlParseError: if XML is malformed or missing required fields.
        """
        self._root = self._parse_xml()
        fa = self._get_fa_element()

        is_marza = self._detect_marza(fa)

        return {
            'seller_nip': self._get_seller_nip(),
            'seller_name': self._get_seller_name(),
            'invoice_number': self._get_text(fa, 'P_2'),
            'invoice_date': self._parse_date(self._get_text(fa, 'P_1')),
            'due_date': self._get_due_date(fa),
            'payment_iban': self._get_payment_iban(fa),
            'lines': self._parse_lines(fa, is_marza),
            'total_net': self._parse_float(self._get_text(fa, 'P_15') or '0'),
            'total_gross': self._parse_float(self._get_text(fa, 'P_16') or '0'),
            'tax_breakdown': self._parse_tax_breakdown(fa),
            'is_marza': is_marza,
        }

    # ─────────────────────────────────────────────
    # XML structure helpers
    # ─────────────────────────────────────────────

    def _parse_xml(self) -> ET.Element:
        try:
            return ET.fromstring(self._xml_bytes)
        except ET.ParseError as e:
            raise KsefXmlParseError(f'Malformed XML: {e}') from e

    def _get_fa_element(self) -> ET.Element:
        """Returns the <Fa> element (main invoice body).

        Only called from ``parse()`` after ``self._root`` has been set by
        ``_parse_xml()`` — the guard below documents that precondition for
        mypy and fails loudly (not silently, unlike ``assert`` under ``-O``)
        if a future caller breaks it.
        """
        if self._root is None:
            raise KsefXmlParseError('_get_fa_element called before _root was parsed')
        # Root may be <Faktura> or <FA> depending on wrapping
        fa = self._root.find(f'{_N}Fa')
        if fa is None:
            # Try without namespace (some test documents omit it)
            fa = self._root.find('Fa')
        if fa is None:
            raise KsefXmlParseError('Element <Fa> not found in XML.')
        return fa

    def _get_podmiot1(self) -> ET.Element | None:
        """Returns <Podmiot1> (seller block). See ``_get_fa_element`` for the
        ``self._root`` precondition."""
        if self._root is None:
            raise KsefXmlParseError('_get_podmiot1 called before _root was parsed')
        el = self._root.find(f'{_N}Podmiot1')
        return el if el is not None else self._root.find('Podmiot1')

    def _get_text(self, element: ET.Element, tag: str) -> str | None:
        """Finds direct child <tag> and returns its text, or None."""
        child = element.find(f'{_N}{tag}')
        if child is None:
            child = element.find(tag)
        if child is None:
            return None
        return (child.text or '').strip() or None

    # ─────────────────────────────────────────────
    # Seller
    # ─────────────────────────────────────────────

    def _get_seller_nip(self) -> str | None:
        podmiot1 = self._get_podmiot1()
        if podmiot1 is None:
            return None
        osoba_tmp = podmiot1.find(f'{_N}OsobaNiefizyczna')
        osoba = osoba_tmp if osoba_tmp is not None else podmiot1.find('OsobaNiefizyczna')
        if osoba is not None:
            return self._get_text(osoba, 'NIP')
        osoba_tmp = podmiot1.find(f'{_N}OsobaFizyczna')
        osoba = osoba_tmp if osoba_tmp is not None else podmiot1.find('OsobaFizyczna')
        if osoba is not None:
            return self._get_text(osoba, 'NIP')
        return None

    def _get_seller_name(self) -> str | None:
        podmiot1 = self._get_podmiot1()
        if podmiot1 is None:
            return None
        # OsobaNiefizyczna has <PelnaNazwa>
        osoba_tmp = podmiot1.find(f'{_N}OsobaNiefizyczna')
        osoba = osoba_tmp if osoba_tmp is not None else podmiot1.find('OsobaNiefizyczna')
        if osoba is not None:
            return self._get_text(osoba, 'PelnaNazwa')
        # OsobaFizyczna has <ImiePierwsze> + <Nazwisko>
        osoba_tmp = podmiot1.find(f'{_N}OsobaFizyczna')
        osoba = osoba_tmp if osoba_tmp is not None else podmiot1.find('OsobaFizyczna')
        if osoba is not None:
            first = self._get_text(osoba, 'ImiePierwsze') or ''
            last = self._get_text(osoba, 'Nazwisko') or ''
            return f'{first} {last}'.strip() or None
        return None

    # ─────────────────────────────────────────────
    # Dates
    # ─────────────────────────────────────────────

    def _get_due_date(self, fa: ET.Element) -> date | None:
        """Reads <TerminPlatnosci><Termin> (first payment term date)."""
        # FA(3) stores payment terms under <Platnosci><Platnosc><TerminPlatnosci>
        platnosci_tmp = fa.find(f'{_N}Platnosci')
        platnosci = platnosci_tmp if platnosci_tmp is not None else fa.find('Platnosci')
        if platnosci is None:
            return None
        platnosc_tmp = platnosci.find(f'{_N}Platnosc')
        platnosc = platnosc_tmp if platnosc_tmp is not None else platnosci.find('Platnosc')
        if platnosc is None:
            return None
        termin_el_tmp = platnosc.find(f'{_N}TerminPlatnosci')
        termin_el = termin_el_tmp if termin_el_tmp is not None else platnosc.find('TerminPlatnosci')
        if termin_el is None:
            return None
        termin_text = self._get_text(termin_el, 'Termin')
        return self._parse_date(termin_text)

    def _get_payment_iban(self, fa: ET.Element) -> str | None:
        """Reads IBAN from <Platnosci><Platnosc><RachunekBankowy><NrRB>."""
        platnosci_tmp = fa.find(f'{_N}Platnosci')
        platnosci = platnosci_tmp if platnosci_tmp is not None else fa.find('Platnosci')
        if platnosci is None:
            return None
        platnosc_tmp = platnosci.find(f'{_N}Platnosc')
        platnosc = platnosc_tmp if platnosc_tmp is not None else platnosci.find('Platnosc')
        if platnosc is None:
            return None
        rachunek_tmp = platnosc.find(f'{_N}RachunekBankowy')
        rachunek = rachunek_tmp if rachunek_tmp is not None else platnosc.find('RachunekBankowy')
        if rachunek is None:
            return None
        return self._get_text(rachunek, 'NrRB')

    # ─────────────────────────────────────────────
    # Invoice lines
    # ─────────────────────────────────────────────

    def _parse_lines(self, fa: ET.Element, is_marza: bool) -> list[dict]:
        lines = []
        wiersze = fa.findall(f'{_N}FaWiersz') or fa.findall('FaWiersz')
        for wiersz in wiersze:
            line = self._parse_single_line(wiersz, is_marza)
            if line:
                lines.append(line)
        return lines

    def _parse_single_line(self, wiersz: ET.Element, is_marza: bool) -> dict | None:
        name = self._get_text(wiersz, 'P_7') or ''
        qty = self._parse_float(self._get_text(wiersz, 'P_8B') or '1')
        uom = self._get_text(wiersz, 'P_8A') or 'szt'

        # P_9A = net unit price (standard); P_9B = gross unit price (some vendors use this)
        p9a = self._get_text(wiersz, 'P_9A')
        p9b = self._get_text(wiersz, 'P_9B')
        unit_price = self._parse_float(p9a or p9b or '0')

        if is_marza:
            # VAT Marża: P_11 = gross (total including margin VAT)
            net = self._parse_float(self._get_text(wiersz, 'P_11') or '0')
            tax_rate = None
        else:
            # P_11 = net amount; P_11A = gross amount (some vendors omit P_11 and use P_11A)
            p11 = self._get_text(wiersz, 'P_11')
            p11a = self._get_text(wiersz, 'P_11A')
            if p11:
                net = self._parse_float(p11)
            elif p11a and p9b and not p9a:
                # Only gross available — back-calculate net from gross unit price * qty
                gross = self._parse_float(p11a)
                tax_rate_val = self._get_line_tax_rate(wiersz)
                rate = (tax_rate_val or 0) / 100
                net = round(gross / (1 + rate), 2) if rate >= 0 else gross
            else:
                net = self._parse_float(p11a or '0')
            tax_rate = self._get_line_tax_rate(wiersz)

        return {
            'name': name,
            'qty': qty,
            'uom': uom,
            'unit_price': unit_price,
            'net': net,
            'tax_rate': tax_rate,
        }

    def _get_line_tax_rate(self, wiersz: ET.Element) -> int | None:
        """Reads <P_12> and returns integer tax rate (23, 8, 5, 0, -1) or None."""
        p12 = self._get_text(wiersz, 'P_12')
        if p12 is None:
            return None
        mapping = {
            '23': 23,
            '08': 8,
            '8': 8,
            '05': 5,
            '5': 5,
            '04': 4,
            '4': 4,
            '03': 3,
            '3': 3,
            '00': 0,
            '0': 0,
            'ZW': 0,
            'NP': -1,
        }
        return mapping.get(p12.upper())

    # ─────────────────────────────────────────────
    # Tax breakdown (P_13_x)
    # ─────────────────────────────────────────────

    def _parse_tax_breakdown(self, fa: ET.Element) -> dict:
        """Returns {tax_rate_int: net_amount} from P_13_x elements."""
        breakdown = {}
        for field, rate in _FIELD_TO_TAX_RATE.items():
            value = self._get_text(fa, field)
            if value is not None:
                amount = self._parse_float(value)
                if amount:
                    breakdown[rate] = amount
        return breakdown

    # ─────────────────────────────────────────────
    # VAT Marża detection
    # ─────────────────────────────────────────────

    def _detect_marza(self, fa: ET.Element) -> bool:
        """Returns True if the invoice uses VAT Marża procedure.

        FA(3) signals this via <PMarzy><P_PMarzy>1</P_PMarzy> or
        <PMarzy><P_PMarzy_3_3>1</P_PMarzy_3_3> (tourism margin).
        Also detected if P_13_11 (Marża net base) is present.
        """
        pmarzy_tmp = fa.find(f'{_N}PMarzy')
        pmarzy = pmarzy_tmp if pmarzy_tmp is not None else fa.find('PMarzy')
        if pmarzy is not None:
            p_pmarzy = self._get_text(pmarzy, 'P_PMarzy')
            p_pmarzy_33 = self._get_text(pmarzy, 'P_PMarzy_3_3')
            if p_pmarzy == '1' or p_pmarzy_33 == '1':
                return True
        # Fallback: check if P_13_11 has a value
        return self._get_text(fa, 'P_13_11') is not None

    # ─────────────────────────────────────────────
    # Type converters
    # ─────────────────────────────────────────────

    @staticmethod
    def _parse_float(value: str | None) -> float:
        if not value:
            return 0.0
        try:
            return float(value.replace(',', '.').strip())
        except (ValueError, AttributeError):
            return 0.0

    @staticmethod
    def _parse_date(value: str | None) -> date | None:
        if not value:
            return None
        for fmt in ('%Y-%m-%d', '%d-%m-%Y', '%Y%m%d'):
            try:
                return datetime.strptime(value, fmt).date()
            except ValueError:
                continue
        _logger.warning('KsefXmlParser: cannot parse date %r', value)
        return None
