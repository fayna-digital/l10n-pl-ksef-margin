try:
    import odoo  # noqa: F401
except ImportError:
    # Odoo not installed — e.g. local `pytest` outside a full Odoo
    # environment. conftest.py skips collection of the Odoo-dependent test
    # modules in that case; Odoo's own module loader (used in CI, see
    # .github/workflows/ci.yml) always has `odoo` importable and takes the
    # branch below.
    pass
else:
    from . import test_xml_builder, test_xml_parser  # noqa: F401
