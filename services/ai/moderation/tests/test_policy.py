from pathlib import Path

import pytest
from contracts.ai.moderation import ModerationAction, ModerationLabel
from services.ai.moderation.policy import LABEL_BY_LETTER, load_policy


def test_sample_policy_parses_every_section(sample_policy) -> None:
    assert sample_policy.version == "policy@2026-10-01"
    assert sample_policy.taxonomy_source == "document"
    assert sample_policy.notices_source == "document"
    assert sample_policy.exemplar_count() == {"A": 2, "B": 1, "C": 1, "D": 1, "E": 1}
    assert sample_policy.has_exemplars


def test_sample_policy_actions_follow_the_table(sample_policy) -> None:
    assert sample_policy.action_for(ModerationLabel.A_DEVOTIONAL) is ModerationAction.ALLOW
    assert sample_policy.action_for(ModerationLabel.B_ORGANIZATIONAL) is ModerationAction.ALLOW
    assert sample_policy.action_for(ModerationLabel.C_PERSONAL) is ModerationAction.NUDGE
    assert sample_policy.action_for(ModerationLabel.D_DISPUTATIONAL) is ModerationAction.HOLD
    assert sample_policy.action_for(ModerationLabel.E_HARMFUL) is ModerationAction.BLOCK


def test_sample_policy_notices_come_from_the_document(sample_policy) -> None:
    assert sample_policy.notice_for(ModerationAction.ALLOW) is None
    assert "(sample)" in sample_policy.notice_for(ModerationAction.NUDGE)
    # The first D heading that reads as "held" wins; "released after review"
    # is a console-side text, not the hold notice.
    assert "waiting for a volunteer" in sample_policy.notice_for(ModerationAction.HOLD)
    assert "was not sent" in sample_policy.notice_for(ModerationAction.BLOCK)


def test_real_policy_document_loads_and_reports_its_state(real_policy_path: Path) -> None:
    """The committed docs/policy-taxonomy.md must always parse. Its exemplar
    section is empty until the organisation workshop lands -- this test
    pins that as a *reported* state, not a crash."""
    policy = load_policy(real_policy_path)
    assert policy.version.startswith("policy@2026")
    assert policy.taxonomy_source == "document"
    assert set(policy.taxonomy) == set(LABEL_BY_LETTER)
    assert policy.action_for(ModerationLabel.E_HARMFUL) is ModerationAction.BLOCK
    # Pre-workshop: no exemplars, builtin notices. When the workshop PR lands
    # this assertion flips -- update it then, deliberately.
    assert not policy.has_exemplars
    assert policy.notice_for(ModerationAction.NUDGE)  # builtin fallback still present


def test_missing_taxonomy_table_falls_back_to_builtin(tmp_path: Path) -> None:
    doc = tmp_path / "policy.md"
    doc.write_text("# Policy\n\n## Changelog\n\n- 2026-09-01: something\n", encoding="utf-8")
    policy = load_policy(doc)
    assert policy.taxonomy_source == "builtin"
    assert policy.version == "policy@2026-09-01"
    assert policy.action_for(ModerationLabel.C_PERSONAL) is ModerationAction.NUDGE
    assert policy.exemplar_count() == {"A": 0, "B": 0, "C": 0, "D": 0, "E": 0}


def test_partial_taxonomy_table_is_not_trusted(tmp_path: Path) -> None:
    doc = tmp_path / "policy.md"
    doc.write_text(
        "## Default taxonomy and actions\n\n| Label | Description | Action |\n|---|---|---|\n"
        "| A | Devotional | Allow |\n| E | Harmful | Blocked |\n",
        encoding="utf-8",
    )
    policy = load_policy(doc)
    assert policy.taxonomy_source == "builtin"


def test_no_changelog_means_unversioned(tmp_path: Path) -> None:
    doc = tmp_path / "policy.md"
    doc.write_text("## Exemplars\n\n### A\n\n- hello\n", encoding="utf-8")
    assert load_policy(doc).version == "policy@unversioned"


def test_missing_document_is_an_error(sample_policy_path: Path) -> None:
    with pytest.raises(FileNotFoundError):
        load_policy(sample_policy_path.with_name("does-not-exist.md"))
