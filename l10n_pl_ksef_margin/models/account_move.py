"""
KSeF outbound invoice integration — account.move extension.

Odpowiada wyłącznie za:
  - pola KSeF na fakturze
  - akcje użytkownika (send, check status)
  - cron co 15 minut

Logika budowania XML → services/ksef_xml_builder.py
Logika autoryzacji   → services/ksef_auth.py
"""

import base64
import logging
import re
from datetime import date

from odoo import _, api, fields, models
from odoo.exceptions import UserError

_logger = logging.getLogger(__name__)

try:
    from ksef_client.exceptions import KsefApiError as _KsefApiError
    from ksef_client.services.workflows import OnlineSessionWorkflow

    KSEF_CLIENT_AVAILABLE = True
except ImportError:
    _KsefApiError = Exception
    KSEF_CLIENT_AVAILABLE = False

# Form code for FA(3) schema — passed to open_session()
_FA3_FORM_CODE = {'systemCode': 'FA (3)', 'schemaVersion': '1-0E', 'value': 'FA'}

# KSeF became mandatory for VAT-registered businesses on this date — invoices
# issued before it are out of scope for auto-send. Mirrors the same constant
# in ksef_bulk_send_wizard.py (manual bulk send uses the identical cutoff).
KSEF_MANDATORY_DATE = date(2026, 4, 1)
_AUTO_SEND_BATCH_SIZE = 20


class AccountMove(models.Model):
    _inherit = 'account.move'

    # ─────────────────────────────────────────────
    # Fields
    # ─────────────────────────────────────────────

    l10n_pl_ksef_status = fields.Selection(
        [
            ('draft', 'Not Sent'),
            ('waiting', 'Waiting for Status'),
            ('accepted', 'Accepted by KSeF'),
            ('rejected', 'Rejected by KSeF'),
        ],
        string='KSeF Status',
        default='draft',
        copy=False,
        tracking=True,
    )

    l10n_pl_ksef_reference = fields.Char(
        string='KSeF ID (Nr KSeF)',
        copy=False,
        readonly=True,
        help="Session ref (SO-...) during 'waiting'; final Nr KSeF after 'accepted'.",
    )

    l10n_pl_ksef_session_token = fields.Char(
        string='KSeF Session Reference',
        copy=False,
        readonly=True,
        help='Session reference number (SO-...) preserved for UPO download (F-06). '
        'Unlike l10n_pl_ksef_reference (overwritten with Nr KSeF after acceptance), '
        'this field always holds the original session ref.',
    )

    l10n_pl_ksef_upo_attachment_id = fields.Many2one(
        'ir.attachment',
        string='UPO (Potwierdzenie KSeF)',
        copy=False,
        readonly=True,
        ondelete='set null',
        help='Urzędowe Potwierdzenie Odbioru — oficjalny dokument potwierdzenia z KSeF.',
    )

    # Rejection details — populated when KSeF rejects an invoice.
    # Cleared when the invoice is re-sent successfully (status flips back).
    l10n_pl_ksef_error_code = fields.Char(
        string='KSeF Error Code',
        copy=False,
        readonly=True,
        help='HTTP/business code returned by KSeF when the invoice was rejected.',
    )
    l10n_pl_ksef_error_message = fields.Text(
        string='KSeF Rejection Reason',
        copy=False,
        readonly=True,
        help='Human-readable description from KSeF for the rejection. Shown in dashboard.',
    )
    l10n_pl_ksef_rejected_at = fields.Datetime(
        string='KSeF Rejected At',
        copy=False,
        readonly=True,
        help='Timestamp when KSeF rejected the invoice (server time).',
    )
    l10n_pl_ksef_retry_count = fields.Integer(
        string='KSeF Retry Count',
        default=0,
        copy=False,
        readonly=True,
        help='How many times this invoice was re-submitted after a rejection.',
    )

    # ─────────────────────────────────────────────
    # Public actions
    # ─────────────────────────────────────────────

    def action_send_to_ksef(self):
        """Wysyła fakturę do KSeF i ustawia status 'waiting'."""
        self.ensure_one()

        from ..services.ksef_auth import KsefAuthService
        from ..services.ksef_xml_builder import KsefXmlBuilder

        if not KSEF_CLIENT_AVAILABLE:
            raise UserError(
                _(
                    'Biblioteka ksef-client nie jest zainstalowana.\n'
                    'Uruchom: pip install ksef-client'
                )
            )

        auth = KsefAuthService(self.company_id)

        try:
            with auth.client_session() as (client, access_token, session_cert):
                # Build FA(3) XML
                xml_bytes = KsefXmlBuilder(self).build()

                # Open interactive session (uses SymmetricKeyEncryption cert)
                session_wf = OnlineSessionWorkflow(client.sessions)
                session = session_wf.open_session(
                    form_code=_FA3_FORM_CODE,
                    public_certificate=session_cert,
                    access_token=access_token,
                )

                # Send invoice
                session_wf.send_invoice(
                    session_reference_number=session.session_reference_number,
                    invoice_xml=xml_bytes,
                    encryption_data=session.encryption_data,
                    access_token=access_token,
                )

                # Persist session reference before closing (never lose it)
                ksef_ref = session.session_reference_number
                self.write(
                    {
                        'l10n_pl_ksef_reference': ksef_ref,
                        'l10n_pl_ksef_session_token': ksef_ref,  # kept for UPO download (F-06)
                        'l10n_pl_ksef_status': 'waiting',
                    }
                )

                # Close session — error here is non-fatal (reference already saved)
                try:
                    session_wf.close_session(
                        reference_number=ksef_ref,
                        access_token=access_token,
                    )
                except _KsefApiError as close_err:
                    _logger.warning(
                        'KSeF close_session error (ignored): %s | body: %s',
                        close_err,
                        getattr(close_err, 'response_body', None),
                    )

                self.message_post(body=_('Faktura wysłana do KSeF. Nr referencyjny: %s') % ksef_ref)

        except UserError:
            raise
        except _KsefApiError as e:
            body = getattr(e, 'response_body', None)
            _logger.error('KSeF send error: %s | body: %s', e, body, exc_info=True)
            raise UserError(_('Błąd KSeF: %s') % (str(body) if body else str(e))) from e
        except Exception as e:
            _logger.error('KSeF send error: %s', e, exc_info=True)
            raise UserError(_('Błąd KSeF: %s') % str(e)) from e

    def action_retry_ksef(self):
        """F-07: Скидає статус rejected/draft і відправляє повторно.

        Resets reference (so a new session is started) and clears prior error
        traces. Bumps retry_count for analytics. Last error message is
        preserved in chatter via tracking.
        """
        self.ensure_one()
        if self.l10n_pl_ksef_status not in ('rejected', 'draft'):
            raise UserError(
                _(
                    "Można ponowić tylko faktury ze statusem 'Rejected' lub 'Not Sent'. "
                    'Aktualny status: %(status)s.',
                    status=self.l10n_pl_ksef_status,
                )
            )
        next_retry = (self.l10n_pl_ksef_retry_count or 0) + 1
        self.write(
            {
                'l10n_pl_ksef_status': 'draft',
                'l10n_pl_ksef_reference': False,
                'l10n_pl_ksef_error_code': False,
                'l10n_pl_ksef_error_message': False,
                'l10n_pl_ksef_rejected_at': False,
                'l10n_pl_ksef_retry_count': next_retry,
            }
        )
        self.message_post(body=_('KSeF: ponowna próba wysyłki (#%s).') % next_retry)
        self.action_send_to_ksef()

    def action_download_upo(self):
        """F-06: Pobiera UPO (Urzędowe Potwierdzenie Odbioru) z KSeF i zapisuje jako attachment."""
        self.ensure_one()

        if self.l10n_pl_ksef_status != 'accepted':
            raise UserError(_('UPO jest dostępne tylko dla zaakceptowanych faktur KSeF.'))
        if not self.l10n_pl_ksef_reference:
            raise UserError(_('Brak Nr KSeF — nie można pobrać UPO.'))
        if not self.l10n_pl_ksef_session_token:
            raise UserError(
                _(
                    'Brak nr referencyjnego sesji KSeF — nie można pobrać UPO. '
                    'UPO można pobrać tylko dla faktur wysłanych po aktualizacji modułu.'
                )
            )

        from ..services.ksef_auth import KsefAuthService

        auth = KsefAuthService(self.company_id)
        try:
            with auth.client_session() as (client, access_token, _session_cert):
                # get_session_invoice_upo_by_ksef(session_ref, ksef_number, access_token)
                upo_bytes = client.sessions.get_session_invoice_upo_by_ksef(
                    self.l10n_pl_ksef_session_token,
                    self.l10n_pl_ksef_reference,
                    access_token=access_token,
                )
                if not upo_bytes:
                    raise UserError(
                        _('KSeF zwrócił puste UPO dla Nr KSeF: %s') % self.l10n_pl_ksef_reference
                    )

                # Remove old UPO attachment if exists
                if self.l10n_pl_ksef_upo_attachment_id:
                    self.l10n_pl_ksef_upo_attachment_id.unlink()

                attachment = self.env[
                    'ir.attachment'
                ].create(
                    {
                        'name': f'UPO_{self.l10n_pl_ksef_reference}.xml',
                        'type': 'binary',
                        'datas': base64.b64encode(upo_bytes),
                        'mimetype': 'application/xml',
                        'res_model': self._name,
                        'res_id': self.id,
                        'index_content': False,  # do not index XML — prevents showing raw content in partner card
                    }
                )
                self.write({'l10n_pl_ksef_upo_attachment_id': attachment.id})
                self.message_post(
                    body=_('UPO pobrane z KSeF. Nr KSeF: %s') % self.l10n_pl_ksef_reference,
                    attachment_ids=[attachment.id],
                )

        except UserError:
            raise
        except Exception as e:
            _logger.error('KSeF UPO download error dla %s: %s', self.name, e, exc_info=True)
            raise UserError(_('Błąd pobierania UPO: %s') % str(e)) from e

        return {
            'type': 'ir.actions.client',
            'tag': 'display_notification',
            'params': {
                'title': _('UPO pobrane'),
                'message': _('UPO dla faktury %s zostało zapisane jako załącznik.') % self.name,
                'type': 'success',
                'sticky': False,
            },
        }

    def action_reset_ksef_status(self):
        """Resetuje status KSeF do 'draft' (Not Sent) — tylko dla rejected."""
        for move in self:
            if move.l10n_pl_ksef_status not in ('rejected', 'waiting'):
                raise UserError(
                    _(
                        'Nie można zresetować statusu dla faktury %(name)s '
                        '(aktualny status: %(status)s).',
                        name=move.name,
                        status=move.l10n_pl_ksef_status,
                    )
                )
            move.write(
                {
                    'l10n_pl_ksef_status': 'draft',
                    'l10n_pl_ksef_reference': False,
                }
            )
            move.message_post(body=_("Status KSeF zresetowany do 'Not Sent'."))

    # ─────────────────────────────────────────────
    # Cron — check outgoing status every 15 min
    # ─────────────────────────────────────────────

    @api.model
    def action_check_ksef_outgoing_status(self):
        """Sprawdza statusy faktur wychodzących w KSeF (wywoływane przez cron co 15 min).

        Dla każdej faktury w statusie 'waiting':
          - GET /sessions/{referenceNumber} → status.code + failedInvoiceCount
          - code 2xx + failed=0  → get_session_invoices → ksefNumber → 'accepted'
          - code 4xx lub failed>0 → 'rejected' + message
          - code 1xx / null      → bez zmian (spróbujemy za 15 min)
        """
        if not KSEF_CLIENT_AVAILABLE:
            _logger.warning('ksef-client nie jest dostępny — pomijam sprawdzanie statusów KSeF.')
            return

        waiting_moves = self.search(
            [
                ('l10n_pl_ksef_status', '=', 'waiting'),
                ('l10n_pl_ksef_reference', '!=', False),
            ]
        )
        if not waiting_moves:
            return

        from ..services.ksef_auth import KsefAuthService

        by_company = {}
        for move in waiting_moves:
            by_company.setdefault(move.company_id, []).append(move)

        for company, moves in by_company.items():
            auth = KsefAuthService(company)
            try:
                with auth.client_session() as (client, access_token, _session_cert):
                    for move in moves:
                        self._check_single_move_status(client, access_token, move)
            except Exception as e:
                _logger.error(
                    'KSeF: błąd sesji dla firmy %s: %s',
                    company.name,
                    e,
                    exc_info=True,
                )

    # ─────────────────────────────────────────────
    # Cron — auto-send draft invoices every 30 min
    # ─────────────────────────────────────────────

    @api.model
    def action_auto_send_ksef_draft(self):
        """Cron: wysyła faktury w statusie 'draft' do KSeF (max 20 na wywołanie).

        Wysyła faktury wystawione od 01.04.2026 (KSEF_MANDATORY_DATE).
        Każda wysyłka otwiera osobną sesję KSeF.
        Błędy pojedynczych faktur nie blokują reszty — faktura pozostaje 'draft'
        i zostanie podjęta przy następnym uruchomieniu cronu.
        """
        if not KSEF_CLIENT_AVAILABLE:
            _logger.warning('KSeF auto-send: ksef-client nie jest dostępny — pomijam.')
            return

        from ..services.ksef_auth import KsefAuthService
        from ..services.ksef_xml_builder import KsefXmlBuilder

        draft_moves = self.search(
            [
                ('state', '=', 'posted'),
                ('move_type', 'in', ('out_invoice', 'out_refund')),
                ('l10n_pl_ksef_status', '=', 'draft'),
                ('invoice_date', '>=', KSEF_MANDATORY_DATE),
            ],
            limit=_AUTO_SEND_BATCH_SIZE,
            order='invoice_date asc',
        )
        if not draft_moves:
            return

        _logger.info('KSeF auto-send: %d faktur(y) do wysyłki', len(draft_moves))

        by_company = {}
        for move in draft_moves:
            by_company.setdefault(move.company_id, []).append(move)

        sent = failed = 0
        for company, moves in by_company.items():
            auth = KsefAuthService(company)
            try:
                with auth.client_session() as (client, access_token, session_cert):
                    for move in moves:
                        ok, msg = self._auto_send_single(
                            move, client, access_token, session_cert, KsefXmlBuilder
                        )
                        if ok:
                            sent += 1
                        else:
                            failed += 1
                            _logger.warning('KSeF auto-send: błąd %s — %s', move.name, msg)
            except Exception as auth_err:
                failed += len(moves)
                _logger.error(
                    'KSeF auto-send: błąd autoryzacji dla %s — %s',
                    company.name,
                    auth_err,
                )

        _logger.info('KSeF auto-send: wysłano %d, błędów %d', sent, failed)

    # ─────────────────────────────────────────────
    # Private helpers
    # ─────────────────────────────────────────────

    def _check_single_move_status(self, client, access_token, move):
        """Sprawdza i aktualizuje status jednej faktury oczekującej."""
        ref = move.l10n_pl_ksef_reference
        try:
            result = client.sessions.get_session_status(ref, access_token=access_token)

            # Normalize typed SDK object → dict (same pattern as vendor buffer sync)
            if hasattr(result, 'to_dict'):
                result = result.to_dict()

            status_block = result.get('status', {}) if isinstance(result, dict) else {}
            code = status_block.get('code') if status_block else None
            description = status_block.get('description', '')
            failed = result.get('failedInvoiceCount', 0) if isinstance(result, dict) else 0

            if code is not None and 200 <= code < 300 and not failed:
                ksef_number = self._fetch_ksef_number(client, access_token, ref) or ref
                move.write(
                    {
                        'l10n_pl_ksef_status': 'accepted',
                        'l10n_pl_ksef_reference': ksef_number,
                        # Clear any previous rejection traces — invoice is now OK.
                        'l10n_pl_ksef_error_code': False,
                        'l10n_pl_ksef_error_message': False,
                        'l10n_pl_ksef_rejected_at': False,
                    }
                )
                move.message_post(body=_('KSeF: Faktura zaakceptowana. Nr KSeF: %s') % ksef_number)
                _logger.info('KSeF: %s zaakceptowana, Nr KSeF: %s', move.name, ksef_number)

            elif code is not None and (code >= 400 or failed):
                # kod 440 = duplikat: KSeF wcześniej zaakceptował tę fakturę.
                # Zamiast zapisywać jako rejected, wyciągamy Nr KSeF z details.
                if code == 440:
                    details_raw = status_block.get('details') or ''
                    details_str = (
                        details_raw[0]
                        if isinstance(details_raw, list) and details_raw
                        else str(details_raw)
                    )
                    m = re.search(r'numerze KSeF:\s*([\w-]+)', details_str)
                    ksef_number = m.group(1) if m else ref
                    move.write(
                        {
                            'l10n_pl_ksef_status': 'accepted',
                            'l10n_pl_ksef_reference': ksef_number,
                            'l10n_pl_ksef_error_code': False,
                            'l10n_pl_ksef_error_message': False,
                            'l10n_pl_ksef_rejected_at': False,
                        }
                    )
                    move.message_post(
                        body=_(
                            'KSeF (kod 440 — duplikat): faktura już zaakceptowana.'
                            ' Nr KSeF: %(num)s',
                            num=ksef_number,
                        )
                    )
                    _logger.info('KSeF: %s — duplikat (440), Nr KSeF: %s', move.name, ksef_number)
                    return

                # TZ-3: fetch invoice-level details (real XPath / kod 450 etc.)
                # Session-level gives only "445 — brak poprawnych faktur" fallback.
                real_code = str(code)
                real_msg = description or _('No description provided by KSeF.')
                try:
                    inv_result = client.sessions.get_session_invoices(
                        ref, access_token=access_token
                    )
                    if hasattr(inv_result, 'to_dict'):
                        inv_result = inv_result.to_dict()
                    inv_list = (
                        inv_result.get('invoices', [])
                        if isinstance(inv_result, dict)
                        else (inv_result or [])
                    )
                    for inv in inv_list:
                        inv_status = inv.get('status', {}) if isinstance(inv, dict) else {}
                        inv_code = inv_status.get('code') if inv_status else None
                        if inv_code is not None and inv_code >= 400:
                            inv_det = inv_status.get('details') or []
                            real_code = str(inv_code)
                            real_msg = (
                                inv_det[0] if inv_det else inv_status.get('description', real_msg)
                            )
                            break
                except Exception as fetch_err:
                    _logger.warning(
                        'KSeF TZ-3: błąd pobierania invoice-details dla %s: %s',
                        ref,
                        fetch_err,
                    )

                move.write(
                    {
                        'l10n_pl_ksef_status': 'rejected',
                        'l10n_pl_ksef_error_code': real_code,
                        'l10n_pl_ksef_error_message': real_msg,
                        'l10n_pl_ksef_rejected_at': fields.Datetime.now(),
                    }
                )
                move.message_post(
                    body=_(
                        'KSeF: Faktura odrzucona (kod: %(code)s — %(desc)s). Ref: %(ref)s',
                        code=real_code,
                        desc=real_msg,
                        ref=ref,
                    )
                )
                _logger.warning(
                    'KSeF: %s odrzucona, kod: %s, opis: %s',
                    move.name,
                    real_code,
                    real_msg,
                )
            else:
                _logger.info(
                    'KSeF: %s — sesja w toku (kod: %s), czekamy następnego cyklu.',
                    move.name,
                    code,
                )

        except _KsefApiError as e:
            _logger.warning(
                'KSeF status check dla %s: %s | body: %s',
                move.name,
                e,
                getattr(e, 'response_body', None),
            )
        except Exception as e:
            _logger.error(
                'KSeF status check nieoczekiwany błąd dla %s: %s',
                move.name,
                e,
                exc_info=True,
            )

    @staticmethod
    def _auto_send_single(move, client, access_token, session_cert, ksef_xml_builder_cls) -> tuple:
        """Відправляє одну фактуру в рамках авто-cron.  Повертає (True, '') або (False, msg)."""
        try:
            xml_bytes = ksef_xml_builder_cls(move).build()
            from ksef_client.services.workflows import OnlineSessionWorkflow

            wf = OnlineSessionWorkflow(client.sessions)
            session = wf.open_session(
                form_code=_FA3_FORM_CODE,
                public_certificate=session_cert,
                access_token=access_token,
            )
            wf.send_invoice(
                session_reference_number=session.session_reference_number,
                invoice_xml=xml_bytes,
                encryption_data=session.encryption_data,
                access_token=access_token,
            )
            ksef_ref = session.session_reference_number
            move.write({'l10n_pl_ksef_reference': ksef_ref, 'l10n_pl_ksef_status': 'waiting'})
            move.message_post(body=_('KSeF (auto): faktura wysłana. Ref: %s') % ksef_ref)
            try:
                wf.close_session(reference_number=ksef_ref, access_token=access_token)
            except Exception:
                # Non-fatal — the session reference is already persisted above.
                _logger.warning(
                    'KSeF auto-send: close_session failed for %s (reference already saved)',
                    move.name,
                    exc_info=True,
                )
            return True, ''
        except Exception as e:
            body = getattr(e, 'response_body', None)
            return False, str(body) if body else str(e)

    @staticmethod
    def _fetch_ksef_number(client, access_token: str, ref: str) -> str | None:
        """Pobiera finalny Nr KSeF z sesji (pole ksefNumber w tablicy invoices)."""
        try:
            invoices = client.sessions.get_session_invoices(ref, access_token=access_token)
            if hasattr(invoices, 'to_dict'):
                invoices = invoices.to_dict()
            inv_list = (
                invoices.get('invoices', []) if isinstance(invoices, dict) else (invoices or [])
            )
            return next(
                (
                    i.get('ksefNumber')
                    for i in inv_list
                    if isinstance(i, dict) and i.get('ksefNumber')
                ),
                None,
            )
        except Exception as ex:
            _logger.warning('KSeF: nie udało się pobrać ksefNumber dla ref %s: %s', ref, ex)
            return None
