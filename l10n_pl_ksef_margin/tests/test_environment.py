"""Sentinel test — always collectible, with or without Odoo installed.

``test_xml_builder.py`` and ``test_xml_parser.py`` need a live Odoo registry
and run for real in CI against a dockerized Odoo 17 + Postgres — see
``.github/workflows/ci.yml``. This file exists so a plain local ``pytest``
run — without Odoo installed — still exits 0 instead of reporting a
misleading "no tests collected" error.
"""


def test_placeholder_odoo_suite_documented_in_ci() -> None:
    assert True
