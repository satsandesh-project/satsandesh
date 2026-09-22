"""
Load the content policy from docs/policy-taxonomy.md.

The policy document is the single source of truth for what the classifier is
told: the five categories and their actions, the organisation-authored
exemplars that go into the prompt, and the sender-facing notices. Changing
any of it is a docs PR the organisation's liaison approves (proposal
section 11, "policy-as-code") -- this module only reads.

Expected structure (tolerant: missing sections fall back to built-in
defaults, and /health/ready reports what was actually loaded):

    ## Default taxonomy and actions
    | Label | Description | Action |
    |---|---|---|
    | A | Devotional / positive-constructive | Allow |
    ...

    ## Exemplars
    ### A
    - Om Sai Ram. May everyone have a peaceful morning.
    - ...
    ### B
    - ...

    ## Sender notices
    ### C — nudge
    > This looks like a personal or business matter. ...
    ### D — held
    > Your message is waiting for a volunteer ...
    ### E — blocked
    > ...

    ## Changelog
    - 2026-07-24: Initial taxonomy scaffold ...
    - 2026-10-01: policy@2026-10-01 first organisation-approved taxonomy

`policy_version` is `policy@<date>` where <date> is the latest dated
changelog line -- so the version stamped on every decision changes exactly
when the document does.
"""

from __future__ import annotations

import re
from dataclasses import dataclass, field
from pathlib import Path

from contracts.ai.moderation import ModerationAction, ModerationLabel

LABEL_BY_LETTER: dict[str, ModerationLabel] = {
    "A": ModerationLabel.A_DEVOTIONAL,
    "B": ModerationLabel.B_ORGANIZATIONAL,
    "C": ModerationLabel.C_PERSONAL,
    "D": ModerationLabel.D_DISPUTATIONAL,
    "E": ModerationLabel.E_HARMFUL,
}
LETTER_BY_LABEL: dict[ModerationLabel, str] = {v: k for k, v in LABEL_BY_LETTER.items()}

# The proposal's default taxonomy (section 7.3). Used when the document's
# table is missing or unparseable, so the service still starts -- but
# /health/ready reports `taxonomy_source: "builtin"` when that happens.
_DEFAULT_TAXONOMY: dict[str, tuple[str, ModerationAction]] = {
    "A": ("Devotional / positive-constructive", ModerationAction.ALLOW),
    "B": (
        "Organizational / informational (schedules, seva coordination)",
        ModerationAction.ALLOW,
    ),
    "C": ("Personal / off-topic (commerce, gossip, private matters)", ModerationAction.NUDGE),
    "D": (
        "Argumentative / disputational (doctrinal quarrels, criticism of members)",
        ModerationAction.HOLD,
    ),
    "E": ("Harmful / abusive / unsafe", ModerationAction.BLOCK),
}

# English fallbacks, matching the drafts in docs/policy-taxonomy-workshop.md
# section 6. The organisation edits the real ones in the policy document;
# translation into the sender's language is the orchestrator's job (see
# README, "Open questions").
_DEFAULT_NOTICES: dict[ModerationAction, str] = {
    ModerationAction.NUDGE: (
        "This looks like a personal or business matter. SatSandesh circles are "
        "kept for satsang, so this was not sent to the group. You can rephrase "
        "it, or share it with the person directly."
    ),
    ModerationAction.HOLD: (
        "Your message is waiting for a volunteer to read it before it goes to "
        "the group. Nothing has been deleted. You will be told when it is sent "
        "or if there is a question."
    ),
    ModerationAction.BLOCK: (
        "This message was not sent because it may be hurtful or unsafe. A "
        "volunteer has been informed. If you think this is a mistake, reply to "
        "this note and a person will look at it."
    ),
}

_ACTION_WORDS: dict[str, ModerationAction] = {
    "allow": ModerationAction.ALLOW,
    "nudge": ModerationAction.NUDGE,
    "held": ModerationAction.HOLD,
    "hold": ModerationAction.HOLD,
    "block": ModerationAction.BLOCK,
}

_H2 = re.compile(r"^##\s+(.*?)\s*$")
_H3 = re.compile(r"^###\s+(.*?)\s*$")
_BULLET = re.compile(r"^\s*[-*]\s+(.*\S)\s*$")
_TABLE_ROW = re.compile(r"^\s*\|\s*([A-E])\s*\|\s*(.*?)\s*\|\s*(.*?)\s*\|\s*$")
_CHANGELOG_DATE = re.compile(r"^\s*[-*]\s+(\d{4}-\d{2}-\d{2})")


@dataclass(frozen=True)
class Policy:
    version: str
    source_path: Path
    taxonomy: dict[str, tuple[str, ModerationAction]]
    taxonomy_source: str  # "document" | "builtin"
    exemplars: dict[str, list[str]] = field(default_factory=dict)
    notices: dict[ModerationAction, str] = field(default_factory=dict)
    notices_source: str = "builtin"

    def action_for(self, label: ModerationLabel) -> ModerationAction:
        return self.taxonomy[LETTER_BY_LABEL[label]][1]

    def notice_for(self, action: ModerationAction) -> str | None:
        if action is ModerationAction.ALLOW:
            return None
        return self.notices.get(action) or _DEFAULT_NOTICES.get(action)

    def exemplar_count(self) -> dict[str, int]:
        return {letter: len(self.exemplars.get(letter, [])) for letter in LABEL_BY_LETTER}

    @property
    def has_exemplars(self) -> bool:
        """True once every category has at least one organisation-authored
        example. Until then the classifier is running on the category
        descriptions alone and /health/ready says so."""
        return all(count > 0 for count in self.exemplar_count().values())


def _sections(text: str) -> dict[str, list[str]]:
    """Split the document into {h2 heading (lower-cased): lines under it}."""
    out: dict[str, list[str]] = {}
    current: str | None = None
    for line in text.splitlines():
        m = _H2.match(line)
        if m:
            current = m.group(1).strip().lower()
            out.setdefault(current, [])
            continue
        if current is not None:
            out[current].append(line)
    return out


def _find_section(sections: dict[str, list[str]], *needles: str) -> list[str] | None:
    for heading, lines in sections.items():
        if all(n in heading for n in needles):
            return lines
    return None


def _parse_taxonomy(lines: list[str] | None) -> dict[str, tuple[str, ModerationAction]] | None:
    if not lines:
        return None
    found: dict[str, tuple[str, ModerationAction]] = {}
    for line in lines:
        m = _TABLE_ROW.match(line)
        if not m:
            continue
        letter, description, action_cell = m.group(1), m.group(2), m.group(3).lower()
        action = next((a for word, a in _ACTION_WORDS.items() if word in action_cell), None)
        if action is None:
            continue
        found[letter] = (description, action)
    return found if set(found) == set(LABEL_BY_LETTER) else None


def _parse_h3_groups(lines: list[str] | None) -> dict[str, list[str]]:
    """Under one H2, collect {h3 heading: [bullet lines or blockquote lines]}."""
    groups: dict[str, list[str]] = {}
    current: str | None = None
    for line in lines or []:
        m = _H3.match(line)
        if m:
            current = m.group(1).strip()
            groups[current] = []
            continue
        if current is None:
            continue
        b = _BULLET.match(line)
        if b:
            groups[current].append(b.group(1))
            continue
        stripped = line.strip()
        if stripped.startswith(">"):
            groups[current].append(stripped.lstrip("> ").strip())
    return groups


def _parse_exemplars(lines: list[str] | None) -> dict[str, list[str]]:
    exemplars: dict[str, list[str]] = {}
    for heading, items in _parse_h3_groups(lines).items():
        letter = heading[:1].upper()
        if letter in LABEL_BY_LETTER and items:
            exemplars.setdefault(letter, []).extend(items)
    return exemplars


def _parse_notices(lines: list[str] | None) -> dict[ModerationAction, str]:
    notices: dict[ModerationAction, str] = {}
    for heading, items in _parse_h3_groups(lines).items():
        if not items:
            continue
        h = heading.lower()
        text = " ".join(items).strip()
        if h.startswith("c") and ModerationAction.NUDGE not in notices:
            notices[ModerationAction.NUDGE] = text
        elif h.startswith("d") and ("held" in h or "waiting" in h or "hold" in h):
            notices.setdefault(ModerationAction.HOLD, text)
        elif h.startswith("e") and ModerationAction.BLOCK not in notices:
            notices[ModerationAction.BLOCK] = text
    return notices


def _parse_version(lines: list[str] | None) -> str:
    dates = [m.group(1) for line in lines or [] for m in [_CHANGELOG_DATE.match(line)] if m]
    return f"policy@{max(dates)}" if dates else "policy@unversioned"


def load_policy(path: Path) -> Policy:
    """Parse the policy document. Raises FileNotFoundError if it is absent --
    a moderation service with no policy must not start."""
    text = path.read_text(encoding="utf-8")
    sections = _sections(text)

    taxonomy = _parse_taxonomy(_find_section(sections, "taxonomy"))
    taxonomy_source = "document" if taxonomy else "builtin"

    notices = _parse_notices(_find_section(sections, "sender notices"))

    return Policy(
        version=_parse_version(_find_section(sections, "changelog")),
        source_path=path,
        taxonomy=taxonomy or dict(_DEFAULT_TAXONOMY),
        taxonomy_source=taxonomy_source,
        exemplars=_parse_exemplars(_find_section(sections, "exemplars")),
        notices=notices,
        notices_source="document" if notices else "builtin",
    )
