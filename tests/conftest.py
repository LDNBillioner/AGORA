"""
conftest.py — Shared pytest fixtures.

- Adds src/ to sys.path so flat imports (database, models, Engine) resolve.
- Sets DATABASE_URL to SQLite *before* any src module is imported so tests
  never touch PostgreSQL.
- Provides an in-memory SQLite-backed TestClient for the FastAPI app.
"""

import os
import sys
from pathlib import Path

SRC_DIR = Path(__file__).resolve().parent.parent / "src"
sys.path.insert(0, str(SRC_DIR))

# Force SQLite so tests can never touch a developer's real PostgreSQL,
# even if DATABASE_URL is exported in their shell or defined in .env.
os.environ["DATABASE_URL"] = "sqlite:///./agora_test.db"
os.environ.setdefault("META_VERIFY_TOKEN", "agora_verify_token")

import pytest  # noqa: E402
from fastapi.testclient import TestClient  # noqa: E402
from sqlalchemy import create_engine  # noqa: E402
from sqlalchemy.orm import sessionmaker  # noqa: E402
from sqlalchemy.pool import StaticPool  # noqa: E402

from database import Base  # noqa: E402
import models  # noqa: E402,F401  (registers models on Base.metadata)


@pytest.fixture(scope="session", autouse=True)
def _cleanup_sqlite_file():
    """Remove the on-disk test SQLite file before and after the run."""
    db_file = Path.cwd() / "agora_test.db"
    db_file.unlink(missing_ok=True)
    yield
    db_file.unlink(missing_ok=True)


@pytest.fixture()
def db_engine():
    """Fresh in-memory SQLite engine per test, with schema created."""
    engine = create_engine(
        "sqlite://",
        connect_args={"check_same_thread": False},
        poolclass=StaticPool,
    )
    Base.metadata.create_all(bind=engine)
    yield engine
    engine.dispose()


@pytest.fixture()
def client(db_engine):
    """TestClient whose dependencies use the in-memory SQLite DB."""
    TestingSessionLocal = sessionmaker(
        autocommit=False, autoflush=False, bind=db_engine
    )
    from Engine import app, get_db

    def override_get_db():
        db = TestingSessionLocal()
        try:
            yield db
        finally:
            db.close()

    app.dependency_overrides[get_db] = override_get_db
    yield TestClient(app)
    app.dependency_overrides.clear()
