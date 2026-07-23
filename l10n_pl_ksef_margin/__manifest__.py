{
    'name': 'Fayna KSeF — Krajowy System e-Faktur',
    'version': '17.0.2.2.6',
    'category': 'Accounting/Localizations',
    'summary': 'Fayna Digital: integracja z KSeF 2.0 (FA 3) — wysyłka, statusy, UPO, VAT Marża turystyczna.',
    'author': 'Fayna Digital — Volodymyr Shevchenko',
    'website': 'https://fayna.agency',
    'license': 'LGPL-3',
    'depends': ['account', 'l10n_pl'],
    'data': [
        'security/ksef_security.xml',
        'security/ir.model.access.csv',
        'data/ksef_cron.xml',
        'views/res_company_views.xml',
        'views/account_move_views.xml',
        'views/ksef_bulk_send_wizard_views.xml',
        'views/ksef_vendor_buffer_views.xml',
        'views/ksef_dashboard_views.xml',
    ],
    'assets': {
        'web.assets_backend': [
            # Alert Banner — injected into accounting dashboard kanban
            'l10n_pl_ksef_margin/static/src/components/ksef_dashboard_card/ksef_dashboard_card.js',
            'l10n_pl_ksef_margin/static/src/components/ksef_dashboard_card/ksef_dashboard_card.xml',
            'l10n_pl_ksef_margin/static/src/components/ksef_dashboard_card/ksef_dashboard_patch.js',
            # KSeF Dashboard — dedicated client action page
            'l10n_pl_ksef_margin/static/src/components/ksef_dashboard/ksef_dashboard.js',
            'l10n_pl_ksef_margin/static/src/components/ksef_dashboard/ksef_dashboard.xml',
            # Styles
            'l10n_pl_ksef_margin/static/src/scss/ksef_dashboard.scss',
        ],
    },
    'installable': True,
    'application': True,
    'auto_install': False,
}
