import itertools
import pytest

from types import SimpleNamespace

from orchestrator.app import app, current_account, current_user
from orchestrator.migrate import upgrade
from tests import pg

_names = itertools.count()


@pytest.fixture(autouse=True)
def logged_in():
    """Endpoint tests run as a logged-in user unless they remove this override."""
    app.dependency_overrides[current_user] = lambda: "tester"
    app.dependency_overrides[current_account] = lambda: SimpleNamespace(username="tester", role="approver")
    yield
    app.dependency_overrides.pop(current_user, None)
    app.dependency_overrides.pop(current_account, None)


@pytest.fixture(scope="session")
def postgres(tmp_path_factory):
    server, socket_dir = pg.start(tmp_path_factory.mktemp("pgdata"))
    yield server, socket_dir
    if isinstance(server, pg.Server):
        server.stop()


@pytest.fixture
def database_url(postgres) -> str:
    """A fresh database per test, schema from the Alembic migrations."""
    server, socket_dir = postgres
    name = f"test_{next(_names)}"
    server.psql(f"CREATE DATABASE {name};")
    url = f"postgresql+asyncpg://postgres@/{name}?host={socket_dir}"
    upgrade(url)
    return url


@pytest.fixture(autouse=True)
def fresh_ports_in_use():
    """The ports-in-use cache is per process: each test reads its own NetBox."""
    from orchestrator import controlplane, faceplate

    faceplate._IN_USE.clear()
    controlplane._PLANNED = None   # likewise the planned partners
    yield
