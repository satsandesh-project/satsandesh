import sys
from pathlib import Path

import pytest

# services/ai/conftest.py already puts the repo root on sys.path when pytest
# is run from services/ai/; this makes the same true when this directory is
# run on its own (`pytest services/ai/moderation/tests` from the root).
_REPO_ROOT = Path(__file__).resolve().parents[4]
if str(_REPO_ROOT) not in sys.path:
    sys.path.insert(0, str(_REPO_ROOT))

FIXTURES = Path(__file__).parent / "fixtures"
SAMPLE_POLICY = FIXTURES / "policy_sample.md"
REAL_POLICY = _REPO_ROOT / "docs" / "policy-taxonomy.md"
CONTRACT_FIXTURES = _REPO_ROOT / "services" / "ai" / "tests" / "fixtures"


@pytest.fixture
def sample_policy():
    from services.ai.moderation.policy import load_policy

    return load_policy(SAMPLE_POLICY)


@pytest.fixture(scope="session")
def sample_policy_path() -> Path:
    return SAMPLE_POLICY


@pytest.fixture(scope="session")
def real_policy_path() -> Path:
    return REAL_POLICY


@pytest.fixture(scope="session")
def contract_fixtures_dir() -> Path:
    return CONTRACT_FIXTURES
