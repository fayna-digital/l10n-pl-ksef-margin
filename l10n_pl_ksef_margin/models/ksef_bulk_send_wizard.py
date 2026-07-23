"""
F-03: KSeF Bulk Send Wizard — масова відправка фактур до KSeF.

Дозволяє відправити кілька фактур за один раз зі списку.
Кожна фактура відправляється в окремій сесії (KSeF 2.0 не підтримує batch).
Помилки окремих фактур не блокують решту.
"""

import logging
from datetime import date

from odoo import _, api, fields, models
from odoo.exceptions import UserError

# KSeF стає обов'язковим з 01.04.2026.
# Фактури ДО цієї дати не підлягають масовій відправці — тільки вручну
# (окреме усвідомлене рішення бухгалтера по кожному рахунку).
KSEF_MANDATORY_DATE = date(2026, 4, 1)

_logger = logging.getLogger(__name__)

try:
    from ksef_client.exceptions import KsefApiError as _KsefApiError
    from ksef_client.services.workflows import OnlineSessionWorkflow

    KSEF_CLIENT_AVAILABLE = True
except ImportError:
    _KsefApiError = Exception
    KSEF_CLIENT_AVAILABLE = False

_FA3_FORM_CODE = {'systemCode': 'FA (3)', 'schemaVersion': '1-0E', 'value': 'FA'}


class KsefBulkSendWizard(models.TransientModel):
    _name = 'ksef.bulk.send.wizard'
    _description = 'KSeF — Masowa wysyłka faktur'

    # Invoices selected from the list view
    move_ids = fields.Many2many(
        'account.move',
        string='Faktury do wysyłki',
        readonly=True,
    )

    # Computed summary for the wizard dialog
    count_total = fields.Integer(
        string='Razem',
        compute='_compute_counts',
    )
    count_eligible = fields.Integer(
        string='Gotowych do wysyłki (od 01.04.2026)',
        compute='_compute_counts',
    )
    count_pre_mandatory = fields.Integer(
        string='Przed 01.04.2026 — tylko ręcznie',
        compute='_compute_counts',
    )
    count_skipped = fields.Integer(
        string='Pominięto (waiting/accepted)',
        compute='_compute_counts',
    )

    # Results (filled after send)
    result_sent = fields.Integer(string='Wysłano', default=0)
    result_failed = fields.Integer(string='Błędy', default=0)
    result_log = fields.Text(string='Szczegóły', readonly=True)
    state = fields.Selection(
        [
            ('confirm', 'Potwierdzenie'),
            ('done', 'Zakończono'),
        ],
        default='confirm',
    )

    @api.depends('move_ids')
    def _compute_counts(self):
        for wiz in self:
            posted = wiz.move_ids.filtered(
                lambda m: m.state == 'posted' and m.move_type in ('out_invoice', 'out_refund')
            )
            # До 01.04.2026 — тільки вручну, масова відправка заборонена
            pre_mandatory = posted.filtered(
                lambda m: m.invoice_date and m.invoice_date < KSEF_MANDATORY_DATE
            )
            # Від 01.04.2026, але вже відправлені або в черзі
            already_sent = posted.filtered(
                lambda m: (
                    m.invoice_date
                    and m.invoice_date >= KSEF_MANDATORY_DATE
                    and m.l10n_pl_ksef_status in ('waiting', 'accepted')
                )
            )
            # Готові до відправки: від 01.04.2026, ще не надіслані
            eligible = posted.filtered(
                lambda m: (
                    m.invoice_date
                    and m.invoice_date >= KSEF_MANDATORY_DATE
                    and m.l10n_pl_ksef_status not in ('waiting', 'accepted')
                )
            )
            wiz.count_total = len(wiz.move_ids)
            wiz.count_pre_mandatory = len(pre_mandatory)
            wiz.count_eligible = len(eligible)
            wiz.count_skipped = len(already_sent)

    @api.model
    def default_get(self, fields_list):
        res = super().default_get(fields_list)
        active_ids = self.env.context.get('active_ids', [])
        if active_ids:
            res['move_ids'] = [(6, 0, active_ids)]
        return res

    def action_send(self):
        """Відправляє всі eligible фактури в KSeF."""
        self.ensure_one()

        if not KSEF_CLIENT_AVAILABLE:
            raise UserError(
                _(
                    'Biblioteka ksef-client nie jest zainstalowana.\n'
                    'Uruchom: pip install ksef-client'
                )
            )

        from ..services.ksef_auth import KsefAuthService
        from ..services.ksef_xml_builder import KsefXmlBuilder

        eligible = self.move_ids.filtered(
            lambda m: (
                m.state == 'posted'
                and m.move_type in ('out_invoice', 'out_refund')
                and m.l10n_pl_ksef_status not in ('waiting', 'accepted')
                and m.invoice_date
                and m.invoice_date >= KSEF_MANDATORY_DATE
            )
        )

        if not eligible:
            raise UserError(
                _(
                    'Brak faktur gotowych do wysyłki.\n'
                    'Pamiętaj: tylko faktury od 01.04.2026 podlegają masowej wysyłce do KSeF.'
                )
            )

        sent = 0
        failed = 0
        log_lines = []

        # Group by company to reuse auth session per company
        by_company = {}
        for move in eligible:
            by_company.setdefault(move.company_id, []).append(move)

        for company, moves in by_company.items():
            auth = KsefAuthService(company)
            try:
                with auth.client_session() as (client, access_token, session_cert):
                    for move in moves:
                        ok, msg = self._send_single(
                            move,
                            client,
                            access_token,
                            session_cert,
                            KsefXmlBuilder,
                        )
                        if ok:
                            sent += 1
                            log_lines.append(f'✓ {move.name}')
                        else:
                            failed += 1
                            log_lines.append(f'✗ {move.name}: {msg}')
            except Exception as auth_err:
                # Auth failure for whole company
                for move in moves:
                    failed += 1
                    log_lines.append(f'✗ {move.name}: błąd autoryzacji — {auth_err}')
                _logger.error('KSeF bulk send auth error for %s: %s', company.name, auth_err)

        self.write(
            {
                'result_sent': sent,
                'result_failed': failed,
                'result_log': '\n'.join(log_lines),
                'state': 'done',
            }
        )

        # Stay open in "done" state to show results
        return {
            'type': 'ir.actions.act_window',
            'res_model': self._name,
            'res_id': self.id,
            'view_mode': 'form',
            'target': 'new',
        }

    def action_confirm_dialog(self):
        """Відкриває wizard як модальне вікно (викликається з server action)."""
        self.ensure_one()
        return {
            'type': 'ir.actions.act_window',
            'res_model': self._name,
            'res_id': self.id,
            'view_mode': 'form',
            'target': 'new',
        }

    def action_close(self):
        return {'type': 'ir.actions.act_window_close'}

    # ─────────────────────────────────────────────
    # Private
    # ─────────────────────────────────────────────

    @staticmethod
    def _send_single(
        move, client, access_token, session_cert, ksef_xml_builder_cls
    ) -> tuple[bool, str]:
        """Відправляє одну фактуру. Повертає (True, '') або (False, опис помилки)."""
        try:
            xml_bytes = ksef_xml_builder_cls(move).build()
            session_wf = OnlineSessionWorkflow(client.sessions)
            session = session_wf.open_session(
                form_code=_FA3_FORM_CODE,
                public_certificate=session_cert,
                access_token=access_token,
            )
            session_wf.send_invoice(
                session_reference_number=session.session_reference_number,
                invoice_xml=xml_bytes,
                encryption_data=session.encryption_data,
                access_token=access_token,
            )
            ksef_ref = session.session_reference_number
            move.write(
                {
                    'l10n_pl_ksef_reference': ksef_ref,
                    'l10n_pl_ksef_status': 'waiting',
                }
            )
            move.message_post(body=_('KSeF (bulk): faktura wysłana. Ref: %s') % ksef_ref)
            try:
                session_wf.close_session(
                    reference_number=ksef_ref,
                    access_token=access_token,
                )
            except Exception:
                # Non-fatal — the session reference is already persisted above.
                _logger.warning(
                    'KSeF bulk: close_session failed for %s (reference already saved)',
                    move.name,
                    exc_info=True,
                )
            return True, ''
        except Exception as e:
            _logger.error('KSeF bulk: błąd dla %s: %s', move.name, e, exc_info=True)
            body = getattr(e, 'response_body', None)
            return False, str(body) if body else str(e)
