"""
KSeF Authentication Service.

Централізована авторизація в KSeF API.
Один клас — одна відповідальність.

Важливо: KSeF має ДВА різних сертифікати:
  - KsefTokenEncryption    → для шифрування токену (authenticate_with_ksef_token)
  - SymmetricKeyEncryption → для шифрування ключа сесії (open_session)
"""

import logging
from contextlib import contextmanager

from odoo import _
from odoo.exceptions import UserError

_logger = logging.getLogger(__name__)

try:
    from ksef_client import KsefClient, KsefClientOptions
    from ksef_client.services.workflows import AuthCoordinator

    KSEF_CLIENT_AVAILABLE = True
except ImportError:
    KSEF_CLIENT_AVAILABLE = False

KSEF_URLS = {
    'test': 'https://api-test.ksef.mf.gov.pl/api/v2/',
    'prod': 'https://api.ksef.mf.gov.pl/api/v2/',
}


class KsefAuthService:
    """Авторизація і підключення до KSeF API.

    Usage::

        service = KsefAuthService(company)
        with service.client_session() as (client, access_token, session_cert):
            result = client.sessions.get_session_status(ref, access_token)
    """

    def __init__(self, company):
        self.company = company
        self._base_url = KSEF_URLS.get(company.l10n_pl_ksef_env or 'prod', KSEF_URLS['prod'])

    # ─────────────────────────────────────────────
    # Public API
    # ─────────────────────────────────────────────

    @contextmanager
    def client_session(self):
        """Context manager: відкриває KsefClient і авторизується.

        Yields:
            tuple: (client, access_token, session_cert)
                - client      — KsefClient (відкрите з'єднання)
                - access_token — рядок для передачі в API-методи
                - session_cert — SymmetricKeyEncryption cert для open_session()

        Raises:
            UserError: якщо конфіг неповний або авторизація провалилась
        """
        self._assert_available()
        self._validate_config()

        with KsefClient(KsefClientOptions(base_url=self._base_url)) as client:
            token_cert, session_cert = self._get_certs(client)
            access_token = self._authenticate(client, token_cert)
            yield client, access_token, session_cert

    def get_nip(self):
        """Повертає NIP компанії без префіксу 'PL'."""
        return (self.company.vat or '').replace('PL', '').replace(' ', '')

    # ─────────────────────────────────────────────
    # Private helpers
    # ─────────────────────────────────────────────

    def _assert_available(self):
        if not KSEF_CLIENT_AVAILABLE:
            raise UserError(
                _(
                    'Biblioteka ksef-client nie jest zainstalowana.\n'
                    'Uruchom: pip install ksef-client'
                )
            )

    def _validate_config(self):
        if not self.company.l10n_pl_ksef_token:
            raise UserError(
                _(
                    'Brak tokenu KSeF dla firmy %(company)s.\n'
                    'Uzupełnij: Ustawienia → Firma → zakładka KSeF.',
                    company=self.company.name,
                )
            )
        if not self.get_nip():
            raise UserError(
                _(
                    'Brak NIP firmy %(company)s.\nUzupełnij: Ustawienia → Firma → pole NIP.',
                    company=self.company.name,
                )
            )

    def _get_certs(self, client):
        """Pobiera oba certyfikaty KSeF.

        Returns:
            tuple: (token_cert, session_cert)
                - token_cert   — KsefTokenEncryption (dla authenticate_with_ksef_token)
                - session_cert — SymmetricKeyEncryption (dla open_session)
        """
        certs = client.security.get_public_key_certificates()
        token_cert = None
        session_cert = None

        for cert in certs:
            usage = cert.get('usage', []) if isinstance(cert, dict) else getattr(cert, 'usage', [])
            value = (
                cert.get('certificate')
                if isinstance(cert, dict)
                else getattr(cert, 'certificate', None)
            )
            if not value:
                continue
            if 'KsefTokenEncryption' in usage:
                token_cert = value
            if 'SymmetricKeyEncryption' in usage:
                session_cert = value

        if not token_cert:
            raise UserError(_('KSeF: Nie można pobrać certyfikatu KsefTokenEncryption.'))
        if not session_cert:
            raise UserError(_('KSeF: Nie można pobrać certyfikatu SymmetricKeyEncryption.'))

        return token_cert, session_cert

    def _authenticate(self, client, token_cert):
        """Авторизується в KSeF і повертає access_token."""
        try:
            auth_result = AuthCoordinator(client.auth).authenticate_with_ksef_token(
                token=self.company.l10n_pl_ksef_token,
                public_certificate=token_cert,
                context_identifier_type='nip',
                context_identifier_value=self.get_nip(),
            )
            return auth_result.tokens.access_token.token
        except Exception as e:
            _logger.error(
                'KSeF auth failed for company %s: %s', self.company.name, e, exc_info=True
            )
            raise UserError(_('Błąd autoryzacji KSeF: %s') % str(e)) from e
