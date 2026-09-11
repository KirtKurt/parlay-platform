import pytest

from arb_supervisor import promote


def good_pr():
    return {
        "state": "open",
        "draft": True,
        "merged": False,
        "user": {"login": "github-actions[bot]"},
        "base": {"ref": "main"},
        "head": {"ref": "agent/inqsi-arb-aec-123", "sha": "a" * 40},
    }


def test_verify_pr_accepts_exact_actions_draft():
    promote.verify_pr(good_pr(), branch="agent/inqsi-arb-aec-123", head_sha="a" * 40, require_draft=True)


@pytest.mark.parametrize("mutation", ["author", "branch", "sha", "base", "closed", "merged", "not-draft"])
def test_verify_pr_fails_closed_on_identity_or_state_drift(mutation):
    pr = good_pr()
    if mutation == "author": pr["user"]["login"] = "human"
    elif mutation == "branch": pr["head"]["ref"] = "other"
    elif mutation == "sha": pr["head"]["sha"] = "b" * 40
    elif mutation == "base": pr["base"]["ref"] = "dev"
    elif mutation == "closed": pr["state"] = "closed"
    elif mutation == "merged": pr["merged"] = True
    else: pr["draft"] = False
    with pytest.raises(promote.PromotionError):
        promote.verify_pr(pr, branch="agent/inqsi-arb-aec-123", head_sha="a" * 40, require_draft=True)


def test_ready_pr_can_be_rechecked_without_draft_requirement():
    pr = good_pr()
    pr["draft"] = False
    promote.verify_pr(pr, branch="agent/inqsi-arb-aec-123", head_sha="a" * 40, require_draft=False)
