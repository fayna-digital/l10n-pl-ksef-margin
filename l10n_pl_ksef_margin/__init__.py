try:
    import odoo  # noqa: F401
except ImportError:
    # Odoo not installed — e.g. local `pytest`/lint tooling (the portfolio gate
    # script) running outside a full Odoo environment. Any real Odoo install
    # always has `odoo` importable and takes the branch below, so this guard
    # changes nothing about production behavior — it only lets tooling reach
    # tests/conftest.py's own Odoo-awareness without crashing first. See
    # tests/__init__.py for the identical pattern one level down.
    pass
else:
    from . import models, services  # noqa: F401
