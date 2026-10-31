from __future__ import annotations

import os
import tempfile
import uuid

import pytest

os.environ.setdefault("DATABASE_URL", "sqlite:///./test_miis.db")
os.environ.setdefault("SHARE_DEFAULT_TTL_DAYS", "7")

from fastapi.testclient import TestClient  # noqa: E402

from app.core.db import Base, engine  # noqa: E402
from app.main import app  # noqa: E402


@pytest.fixture(scope="function", autouse=True)
def clean_db():  # noqa: ANN201
    Base.metadata.drop_all(bind=engine)
    Base.metadata.create_all(bind=engine)
    yield
    Base.metadata.drop_all(bind=engine)


@pytest.fixture
def client() -> TestClient:  # noqa: ANN201
    return TestClient(app)


@pytest.fixture
def attending() -> dict[str, str]:
    return {"X-User-Id": "u-attending", "X-User-Roles": "attending"}


@pytest.fixture
def planner() -> dict[str, str]:
    return {"X-User-Id": "u-planner", "X-User-Roles": "planner"}


@pytest.fixture
def resident() -> dict[str, str]:
    return {"X-User-Id": "u-resident", "X-User-Roles": "resident"}


@pytest.fixture
def reviewer() -> dict[str, str]:
    return {"X-User-Id": "u-reviewer", "X-User-Roles": "reviewer"}


@pytest.fixture
def admin() -> dict[str, str]:
    return {"X-User-Id": "u-admin", "X-User-Roles": "admin"}


@pytest.fixture
def idem():  # noqa: ANN201
    def _make() -> dict[str, str]:
        return {"Idempotency-Key": str(uuid.uuid4())}

    return _make


@pytest.fixture
def study_uid() -> str:
    return f"1.2.840.113619.{uuid.uuid4().int}"


@pytest.fixture
def case(client, attending, idem, study_uid):  # noqa: ANN001
    r = client.post(
        "/api/v1/cases",
        json={"patient_ref": "MRN:P001234", "studies": [study_uid], "source": "pacs", "modality": "CT"},
        headers={**attending, **idem()},
    )
    assert r.status_code == 201, r.text
    return r.json()