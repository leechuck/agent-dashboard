import json
from datetime import UTC, datetime, timedelta

import pytest

from agentdash.node import group


@pytest.fixture
def sources(tmp_path):
    pa, org = tmp_path / "pa", tmp_path / "org"
    (pa / "configs").mkdir(parents=True)
    org.mkdir()
    (pa / "configs/group_roster.yaml").write_text(
        "members:\n  - {slug: alice, name: Alice, role: PhD}\n"
    )
    (org / "main.org").write_text(
        "* Personnel\n** Students\n*** Visiting\n- [[file:bob.org][Bob]]\n*** Alumni\n- [[file:old.org][Old]]\n* Projects\n"
    )
    (org / "alice.org").write_text("* Outcome <2026-09-18 Fri>\nMilestone complete.")
    return pa, org


def test_roster_includes_non_reporting_members_but_not_alumni(sources):
    pa, org = sources
    result = group.show(pa, org)
    assert [m["slug"] for m in result["members"]] == ["alice", "bob"]
    assert all(m["state"] == "unknown" for m in result["members"])
    assert not result["members"][1]["has_notes"]


def test_review_is_invalidated_by_new_evidence_and_age(sources):
    pa, org = sources
    context = group.evidence(pa, org, group.roster(pa, org)[0])
    text = json.dumps(
        {
            "state": "on_track",
            "reason": "Milestone complete",
            "evidence": ["* Outcome <2026-09-18 Fri>"],
        }
    )
    review = group.save(pa, "alice", text, context)
    assert group.show(pa, org)["members"][0]["state"] == "on_track"
    (org / "alice.org").write_text("New blocker")
    member = group.show(pa, org)["members"][0]
    assert member["state"] == "unknown" and member["stale"]
    (org / "alice.org").write_text(context["org_notes"])
    review["reviewed_at"] = (datetime.now(UTC) - timedelta(days=15)).isoformat()
    group.review_path(pa, "alice").write_text(json.dumps(review))
    assert group.show(pa, org)["members"][0]["state"] == "unknown"


def test_unsubstantiated_review_does_not_replace_existing(sources):
    pa, org = sources
    context = group.evidence(pa, org, group.roster(pa, org)[0])
    group.save(
        pa,
        "alice",
        json.dumps({"state": "unknown", "reason": "Not enough evidence", "evidence": []}),
        context,
    )
    for refs in [[], ["invented reference"]]:
        with pytest.raises(ValueError):
            group.save(
                pa,
                "alice",
                json.dumps({"state": "attention", "reason": "Blocked", "evidence": refs}),
                context,
            )
    assert group.show(pa, org)["members"][0]["review"]["state"] == "unknown"
