import os
import shutil
import tempfile
from typing import List

from alembic.command import upgrade as alembic_upgrade
from alembic.config import Config as AlembicConfig
from fastapi.testclient import TestClient
from pytest import fixture

import quetz
from quetz.config import Config
from quetz.dao import Dao
from quetz.database import get_engine, get_session_maker
from quetz.db_models import Base


@fixture
def sqlite_url():
    return "sqlite:///:memory:"


@fixture
def database_url(sqlite_url):
    db_url = os.environ.get("QUETZ_TEST_DATABASE", sqlite_url)
    return db_url


@fixture
def engine(config, database_url):
    # we need to import the plugins before creating the db tables
    # because plugins make define some extra db models
    engine = get_engine(database_url, echo=False)
    yield engine
    engine.dispose()


@fixture
def use_migrations():
    return False


@fixture
def session_maker(engine, home, database_url, use_migrations):

    # run the tests with a separate external DB transaction
    # so that we can easily rollback all db changes (even if commited)
    # done by the test client

    # Note: that won't work when rollback is explictly called in the implementation

    # see also: https://docs.sqlalchemy.org/en/13/orm/session_transaction.html#joining-a-session-into-an-external-transaction-such-as-for-test-suites # noqa

    connection = engine.connect()

    if use_migrations:

        alembic_config_path = os.path.join(home, "alembic.ini")
        alembic_config = AlembicConfig(alembic_config_path)
        alembic_config.set_main_option('sqlalchemy.url', database_url)
        alembic_config.attributes["connection"] = connection
        alembic_config.set_main_option(
            "script_location", os.path.join(home, "migrations")
        )
        alembic_upgrade(alembic_config, 'head', sql=False)

    else:
        Base.metadata.create_all(engine)

    trans = connection.begin()

    yield get_session_maker(connection)
    trans.rollback()
    connection.close()


@fixture
def db(session_maker):

    session = session_maker()

    yield session

    session.close()


@fixture
def config_base(database_url, plugins):
    return f"""
[github]
# Register the app here: https://github.com/settings/applications/new
client_id = "aaa"
client_secret = "bbb"

[sqlalchemy]
database_url = "{database_url}"

[session]
secret = "eWrkA6xpa7LTSSYUwZEEVoOU62501Ucf9lmLcgzTj1I="
https_only = false

[plugins]
enabled = {plugins}
"""


@fixture
def config_extra():
    return ""


@fixture
def config_str(config_base, config_extra):
    return "\n".join([config_base, config_extra])


@fixture
def home():
    return os.path.abspath(os.path.curdir)


@fixture
def config_dir(home):
    path = tempfile.mkdtemp()
    yield path
    shutil.rmtree(path)


@fixture
def config(config_str, config_dir):

    config_path = os.path.join(config_dir, "config.toml")
    with open(config_path, "w") as fid:
        fid.write(config_str)
    old_dir = os.path.abspath(os.curdir)
    os.chdir(config_dir)
    os.environ["QUETZ_CONFIG_FILE"] = config_path
    data_dir = os.path.join(os.path.dirname(quetz.__file__), "tests", "data")
    for filename in os.listdir(data_dir):
        full_path = os.path.join(data_dir, filename)
        dest = os.path.join(config_dir, filename)
        if os.path.isfile(full_path):
            shutil.copy(full_path, dest)

    Config._instances = {}
    config = Config()
    yield config
    os.chdir(old_dir)


@fixture
def plugins() -> List[str]:
    return []


@fixture
def app(config, db, mocker):
    # disabling/enabling specific plugins for tests

    from quetz.deps import get_db
    from quetz.main import app

    # mocking is required for some functions that do not use fastapi
    # dependency injection (mainly non-request functions)
    mocker.patch("quetz.database.get_session", lambda _: db)

    # overiding dependency works with all requests handlers that
    # depend on quetz.deps.get_db
    app.dependency_overrides[get_db] = lambda: db

    yield app
    app.dependency_overrides.pop(get_db)


@fixture
def client(app):
    client = TestClient(app)
    return client


@fixture
def dao(db) -> Dao:
    return Dao(db)
