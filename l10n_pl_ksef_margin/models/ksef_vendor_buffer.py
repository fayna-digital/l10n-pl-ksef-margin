"""
KSeF Vendor Invoice Buffer — ksef.vendor.buffer

Przechowuje metadane faktur przychodzących pobranych z KSeF.
Umożliwia:
  - pobieranie pełnego XML z KSeF (F-01)
  - parsowanie XML → dane do vendor bill
  - tworzenie faktury kosztowej w Odoo (F-02)

Autoryzacja  → services/ksef_auth.py
Parsowanie   → services/ksef_xml_parser.py
"""

import base64
import json
import logging
from datetime import datetime, timedelta

from odoo import _, api, fields, models
from odoo.exceptions import UserError

_logger = logging.getLogger(__name__)

try:
    from ksef_client.models import (
        InvoiceQueryDateRange,
        InvoiceQueryDateType,
        InvoiceQueryFilters,
        InvoiceQuerySubjectType,
    )

    KSEF_CLIENT_AVAILABLE = True
except ImportError:
    KSEF_CLIENT_AVAILABLE = False


class KsefVendorBuffer(models.Model):
    _name = 'ksef.vendor.buffer'
    _description = 'KSeF Vendor Invoice Buffer'
    _order = 'issue_date desc'
    _rec_name = 'name'

    # ─────────────────────────────────────────────
    # Identity
    # ─────────────────────────────────────────────

    name = fields.Char(
        string='KSeF ID (Nr KSeF)',
        required=True,
        copy=False,
        index=True,
    )
    invoice_number = fields.Char(string='Nr faktury')

    # ─────────────────────────────────────────────
    # Seller
    # ─────────────────────────────────────────────

    vendor_name = fields.Char(string='Nazwa sprzedawcy')
    vendor_nip = fields.Char(string='NIP sprzedawcy')

    # ─────────────────────────────────────────────
    # Dates & payment (F-01: from parsed XML)
    # ─────────────────────────────────────────────

    issue_date = fields.Date(string='Data wystawienia')
    receiving_date = fields.Date(string='Data otrzymania')
    due_date = fields.Date(string='Termin płatności')
    payment_iban = fields.Char(string='IBAN do zapłaty')

    # ─────────────────────────────────────────────
    # Amounts
    # ─────────────────────────────────────────────

    amount_net = fields.Monetary(string='Netto', currency_field='currency_id')
    amount_gross = fields.Monetary(string='Brutto', currency_field='currency_id')
    currency_id = fields.Many2one(
        'res.currency',
        default=lambda self: self.env.company.currency_id,
    )
    is_marza = fields.Boolean(string='VAT Marża', default=False)

    # ─────────────────────────────────────────────
    # XML & parsed data (F-01)
    # ─────────────────────────────────────────────

    xml_attachment_id = fields.Many2one(
        'ir.attachment',
        string='Plik XML',
        readonly=True,
        ondelete='set null',
    )
    xml_downloaded = fields.Boolean(
        string='XML pobrany',
        default=False,
        readonly=True,
    )
    line_items_json = fields.Text(
        string='Pozycje faktury (JSON)',
        readonly=True,
        help='Pozycje faktury sparsowane z XML KSeF. Przechowywane jako JSON.',
    )

    # ─────────────────────────────────────────────
    # State & link
    # ─────────────────────────────────────────────

    state = fields.Selection(
        [
            ('new', 'Nowa'),
            ('processed', 'Zaksięgowana'),
        ],
        string='Status',
        default='new',
    )

    move_id = fields.Many2one(
        'account.move',
        string='Faktura w Odoo',
        readonly=True,
        ondelete='set null',
    )

    # ─────────────────────────────────────────────
    # Computed helpers
    # ─────────────────────────────────────────────

    @property
    def _line_items(self) -> list[dict]:
        """Deserializes line_items_json to a list of dicts."""
        try:
            return json.loads(self.line_items_json or '[]') or []
        except (json.JSONDecodeError, TypeError):
            return []

    # ─────────────────────────────────────────────
    # Actions — user-facing buttons
    # ─────────────────────────────────────────────

    def action_mark_processed(self):
        for record in self:
            record.state = 'processed'

    def action_download_xml(self):
        """Pobiera pełny XML faktury z KSeF, parsuje go i zapisuje jako attachment (F-01)."""
        self.ensure_one()

        from ..services.ksef_auth import KsefAuthService
        from ..services.ksef_xml_parser import KsefXmlParseError, KsefXmlParser

        auth = KsefAuthService(self.env.company)

        try:
            with auth.client_session() as (client, access_token, _session_cert):
                # GET /invoices/ksef/{ksefNumber} → InvoiceContent(.content str)
                invoice_content = client.invoices.get_invoice(
                    self.name,
                    access_token=access_token,
                )
                if not invoice_content or not invoice_content.content:
                    raise UserError(_('KSeF zwrócił pusty XML dla Nr KSeF: %s') % self.name)
                xml_bytes = invoice_content.content.encode('utf-8')

                # Parse XML
                try:
                    data = KsefXmlParser(xml_bytes).parse()
                except KsefXmlParseError as parse_err:
                    _logger.warning(
                        'KSeF: nie udało się sparsować XML dla %s: %s',
                        self.name,
                        parse_err,
                    )
                    data = {}

                # Save as ir.attachment
                attachment = self.env['ir.attachment'].create(
                    {
                        'name': f'ksef_{self.name}.xml',
                        'type': 'binary',
                        'datas': base64.b64encode(xml_bytes),
                        'mimetype': 'application/xml',
                        'res_model': self._name,
                        'res_id': self.id,
                    }
                )

                # Update record with parsed data
                write_vals = {
                    'xml_attachment_id': attachment.id,
                    'xml_downloaded': True,
                }
                if data:
                    write_vals.update(
                        {
                            'due_date': data.get('due_date'),
                            'payment_iban': data.get('payment_iban'),
                            'is_marza': data.get('is_marza', False),
                            'line_items_json': json.dumps(
                                [
                                    {
                                        'name': ln.get('name', ''),
                                        'qty': ln.get('qty', 1.0),
                                        'uom': ln.get('uom', 'szt'),
                                        'unit_price': ln.get('unit_price', 0.0),
                                        'net': ln.get('net', 0.0),
                                        'tax_rate': ln.get('tax_rate'),
                                    }
                                    for ln in data.get('lines', [])
                                ],
                                ensure_ascii=False,
                                indent=2,
                            ),
                        }
                    )
                    # Overwrite amounts with more precise parsed values
                    if data.get('total_net'):
                        write_vals['amount_net'] = data['total_net']
                    if data.get('total_gross'):
                        write_vals['amount_gross'] = data['total_gross']
                    if data.get('seller_name') and not self.vendor_name:
                        write_vals['vendor_name'] = data['seller_name']
                    if data.get('seller_nip') and not self.vendor_nip:
                        write_vals['vendor_nip'] = data['seller_nip']
                    if data.get('invoice_number') and not self.invoice_number:
                        write_vals['invoice_number'] = data['invoice_number']

                self.write(write_vals)

            return {
                'type': 'ir.actions.client',
                'tag': 'display_notification',
                'params': {
                    'title': _('XML pobrany'),
                    'message': _('Pełny XML faktury %s został pobrany i sparsowany.') % self.name,
                    'type': 'success',
                    'sticky': False,
                },
            }

        except UserError:
            raise
        except Exception as e:
            _logger.error('KSeF download XML błąd dla %s: %s', self.name, e, exc_info=True)
            raise UserError(_('Błąd pobierania XML z KSeF: %s') % str(e)) from e

    def action_create_vendor_invoice(self):
        """Tworzy fakturę kosztową (vendor bill) w Odoo.

        Jeśli XML został pobrany (xml_downloaded=True), używa sparsowanych danych
        do wypełnienia pozycji faktury. W przeciwnym razie — tylko dane z metadanych.
        """
        self.ensure_one()

        if self.move_id:
            return {
                'type': 'ir.actions.act_window',
                'res_model': 'account.move',
                'res_id': self.move_id.id,
                'view_mode': 'form',
            }

        partner = self._get_or_create_partner()
        invoice_lines = self._build_invoice_lines(partner)

        move_vals = {
            'move_type': 'in_invoice',
            'partner_id': partner.id if partner else False,
            'invoice_date': self.issue_date,
            'invoice_date_due': self.due_date or False,
            'ref': self.invoice_number,
            'narration': _('KSeF ID: %s') % self.name,
            'invoice_line_ids': invoice_lines,
        }
        move = self.env['account.move'].create(move_vals)
        self.write({'move_id': move.id, 'state': 'processed'})

        return {
            'type': 'ir.actions.act_window',
            'res_model': 'account.move',
            'res_id': move.id,
            'view_mode': 'form',
        }

    # ─────────────────────────────────────────────
    # Sync cron — daily
    # ─────────────────────────────────────────────

    @api.model
    def action_sync_from_ksef(self):
        """Synchronizuje faktury przychodzące z KSeF za ostatnie 30 dni.
        Wywoływana przez cron raz dziennie.
        """
        try:
            from ..services.ksef_auth import KsefAuthService
        except ImportError as e:
            raise UserError(_('Błąd importu KSeF auth: %s') % str(e)) from e

        if not KSEF_CLIENT_AVAILABLE:
            _logger.warning('ksef-client nie jest dostępny — pomijam synchronizację KSeF.')
            return

        try:
            company = self.env.company
            auth = KsefAuthService(company)

            date_to = datetime.now()
            date_from = date_to - timedelta(days=30)

            with auth.client_session() as (client, access_token, _session_cert):
                query_payload = InvoiceQueryFilters(
                    subject_type=InvoiceQuerySubjectType.SUBJECT2,
                    date_range=InvoiceQueryDateRange(
                        date_type=InvoiceQueryDateType.PERMANENTSTORAGE,
                        from_=date_from.strftime('%Y-%m-%dT%H:%M:%S+00:00'),
                        to=date_to.strftime('%Y-%m-%dT%H:%M:%S+00:00'),
                    ),
                )
                metadata_obj = client.invoices.query_invoice_metadata(
                    query_payload,
                    access_token=access_token,
                    page_offset=0,
                    page_size=100,
                    sort_order='Desc',
                )
                # Normalize to dict regardless of SDK version
                if hasattr(metadata_obj, 'to_dict'):
                    metadata = metadata_obj.to_dict()
                else:
                    metadata = metadata_obj or {}

                invoices = (
                    (metadata or {}).get('invoices') or (metadata or {}).get('invoiceList') or []
                )
                _logger.info('KSeF sync: pobrano %s faktur z API.', len(invoices))
                created_count = self._upsert_invoices(invoices)

            return {
                'type': 'ir.actions.client',
                'tag': 'display_notification',
                'params': {
                    'title': _('Synchronizacja zakończona'),
                    'message': _('Pobrano %s nowych faktur z KSeF.') % created_count,
                    'type': 'success',
                    'sticky': False,
                },
            }

        except UserError:
            raise
        except Exception as e:
            _logger.error('KSeF sync błąd: %s', e, exc_info=True)
            raise UserError(_('Błąd synchronizacji KSeF: %s') % str(e)) from e

    # ─────────────────────────────────────────────
    # Private helpers
    # ─────────────────────────────────────────────

    def _get_or_create_partner(self):
        """Zwraca istniejącego partnera po NIP lub tworzy nowego."""
        Partner = self.env['res.partner']
        if self.vendor_nip:
            partner = Partner.search([('vat', 'like', self.vendor_nip)], limit=1)
            if partner:
                return partner
        if self.vendor_name:
            return Partner.create(
                {
                    'name': self.vendor_name,
                    'vat': f'PL{self.vendor_nip}' if self.vendor_nip else False,
                    'company_type': 'company',
                }
            )
        return Partner

    def _build_invoice_lines(self, partner) -> list:
        """Buduje listę pozycji faktury do invoice_line_ids.

        Jeśli XML sparsowany → używa line_items_json.
        Jeśli nie → jedna linia zbiorcza z kwotą brutto.
        """
        lines = self._line_items
        if not lines:
            return [
                (
                    0,
                    0,
                    {
                        'name': self.invoice_number or self.name,
                        'price_unit': self.amount_gross,
                        'quantity': 1,
                    },
                )
            ]

        result = []
        for ln in lines:
            tax_ids = []
            if ln.get('tax_rate') is not None and ln['tax_rate'] >= 0:
                tax = self.env['account.tax'].search(
                    [
                        ('type_tax_use', '=', 'purchase'),
                        ('amount', '=', ln['tax_rate']),
                        ('company_id', '=', self.env.company.id),
                    ],
                    limit=1,
                )
                if tax:
                    tax_ids = [(6, 0, [tax.id])]

            result.append(
                (
                    0,
                    0,
                    {
                        'name': ln.get('name') or self.invoice_number or self.name,
                        'quantity': ln.get('qty', 1.0),
                        'price_unit': ln.get('unit_price', 0.0) or ln.get('net', 0.0),
                        'tax_ids': tax_ids,
                    },
                )
            )
        return result

    def _upsert_invoices(self, invoices: list) -> int:
        """Tworzy nowe rekordy bufora dla faktur jeszcze nieznanych. Zwraca liczbę nowych."""
        created = 0
        for inv in invoices:
            ksef_number = inv.get('ksefNumber')
            if not ksef_number:
                continue
            if self.search([('name', '=', ksef_number)], limit=1):
                continue

            seller = inv.get('seller') or {}
            self.create(
                {
                    'name': ksef_number,
                    'invoice_number': inv.get('invoiceNumber') or 'Brak',
                    'vendor_name': seller.get('name') or '',
                    'vendor_nip': seller.get('nip') or '',
                    'issue_date': inv.get('invoicingDate') or inv.get('issueDate'),
                    'receiving_date': (inv.get('acquisitionDate') or '')[:10] or None,
                    'amount_net': inv.get('netAmount') or 0.0,
                    'amount_gross': inv.get('grossAmount') or 0.0,
                }
            )
            created += 1
        return created
