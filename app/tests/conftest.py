import pytest
import os
from pathlib import Path
import re
import tempfile


def pytest_configure(config):
    os.environ.setdefault("ZX_ALLOW_DEMO_SESSIONS", "1")
    """Always isolate import-time ASGI databases for every test process.

    This is only fixture infrastructure; it does not change application worker
    concurrency or override databases explicitly configured inside tests.
    """
    target = os.environ.get('ZX_TEST_IMPORT_DB_DIR')
    if not target:
        # Collection can import the ASGI app before fixtures run. Never fall
        # back to a developer's persisted bank just because a runner omitted
        # an optional environment variable. Keep the temporary DB for diagnosis.
        target = tempfile.mkdtemp(prefix='zhenxing-pytest-import-')
    directory = Path(target).resolve()
    if not directory.is_dir():
        raise pytest.UsageError('Explicit test import DB directory does not exist')
    worker = os.environ.get('PYTEST_XDIST_WORKER', 'master')
    if not re.fullmatch(r'(?:master|gw[0-9]+)', worker):
        raise pytest.UsageError('Unexpected test worker identity')
    os.environ['ZX_DATABASE_URL'] = 'sqlite:///' + (directory/f'app-import-{worker}.sqlite').as_posix()
    os.environ['ZX_KNOWLEDGE_DATABASE_URL'] = 'sqlite:///' + (directory/f'knowledge-import-{worker}.sqlite').as_posix()


@pytest.fixture(autouse=True)
def explicit_legacy_fixture_mode(monkeypatch):
    # Historical v1 regression tests explicitly use the compatibility fixture.
    # Production defaults to v2 only; tests can pass allow_legacy_commands=False.
    monkeypatch.setenv("ZX_ENABLE_LEGACY_COMMANDS", "1")


@pytest.fixture(autouse=True)
def isolated_seed_password_provider(monkeypatch, request):
    # These fixed values belong only to disposable test accounts, not deployment defaults.
    if request.node.get_closest_marker('release_credentials'):
        return
    from zhenxing import deployment_credentials, accounts
    monkeypatch.setattr(deployment_credentials, 'initial_customer_password', lambda username: username+'88')
    monkeypatch.setattr(deployment_credentials, 'initial_admin_password', lambda: 'admin88')
    monkeypatch.setattr(accounts, 'initial_customer_password', lambda username: username+'88')
    monkeypatch.setattr(accounts, 'initial_admin_password', lambda: 'admin88')
