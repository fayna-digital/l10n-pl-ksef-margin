"""Skip Odoo-dependent tests when Odoo isn't importable in this environment.

``test_xml_builder.py`` and ``test_xml_parser.py`` subclass
``odoo.tests.common.BaseCase`` and (for the parser tests) import via
``odoo.addons.l10n_pl_ksef_margin.services...``, which needs Odoo's own
addon-path machinery — not just the ``odoo`` package importable.

Locally (e.g. the portfolio gate script, which just runs plain ``pytest``),
Odoo is not installed, so `pytest` here would fail with a raw
``ModuleNotFoundError`` rather than a meaningful result. We skip collection
in that case instead. The real run happens in CI (see
``.github/workflows/ci.yml``) through Odoo's own ``--test-enable`` runner
against a dockerized ``odoo:17.0`` + Postgres service, which is the only
environment where these tests can actually exercise the ORM.
"""

import importlib.util

collect_ignore: list[str] = []
if importlib.util.find_spec('odoo') is None:
    collect_ignore = ['test_xml_builder.py', 'test_xml_parser.py']
