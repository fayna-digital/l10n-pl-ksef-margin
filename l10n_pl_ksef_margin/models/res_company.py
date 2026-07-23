from odoo import fields, models


class ResCompany(models.Model):
    _inherit = 'res.company'

    l10n_pl_ksef_token = fields.Char(
        string='KSeF API Token',
        groups='base.group_system',
    )
    l10n_pl_ksef_env = fields.Selection(
        [
            ('test', 'Test (ksef-test.mf.gov.pl)'),
            ('prod', 'Production (ksef.mf.gov.pl)'),
        ],
        string='KSeF Environment',
        default='test',
    )
