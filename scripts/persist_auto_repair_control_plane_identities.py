from __future__ import annotations

from pathlib import Path


ROOT = Path(__file__).resolve().parents[1]


def replace_once(text: str, old: str, new: str, label: str) -> str:
    if new in text:
        return text
    if text.count(old) != 1:
        raise RuntimeError(f"{label}: expected exact anchor once")
    return text.replace(old, new, 1)


def patch_handler() -> None:
    path = ROOT / "aws_auto_repair" / "handler.py"
    text = path.read_text(encoding="utf-8")

    marker = (
        'DATA_MARKERS = ("bbd_kickoff_missing", "nfl_team_unrecognized", '
        '"authoritative kickoff", "missing authoritative", "source contract", '
        '"three_source_game_coverage_incomplete", '
        '"authoritative_card_deadline_missed", "no_future_pre_cutoff_slate")\n'
    )
    optional = marker + 'OPTIONAL_LOGICAL_IDS = frozenset({"SoccerDlqRecoveryFunction"})\n'
    if "OPTIONAL_LOGICAL_IDS = frozenset({" not in text:
        text = replace_once(text, marker, optional, "optional logical IDs")

    conditional_anchor = (
        "def conditional(exc: ClientError) -> bool:\n"
        "    return str((exc.response.get(\"Error\") or {}).get(\"Code\") or \"\") "
        '== "ConditionalCheckFailedException"\n'
    )
    helper = conditional_anchor + '''


def optional_logical_resource_absent(logical_id: str, exc: Exception) -> bool:
    """Recognize only an explicitly optional logical resource absent from its stack."""

    if logical_id not in OPTIONAL_LOGICAL_IDS or not isinstance(exc, ClientError):
        return False
    error = exc.response.get("Error") or {}
    code = str(error.get("Code") or "")
    message = str(error.get("Message") or "")
    lowered = message.lower()
    return bool(
        code == "ValidationError"
        and logical_id.lower() in lowered
        and any(
            marker in lowered
            for marker in ("does not exist", "doesn't exist", "not found")
        )
    )
'''
    if "def optional_logical_resource_absent(" not in text:
        text = replace_once(
            text,
            conditional_anchor,
            helper,
            "optional resource helper",
        )

    old_except = '''    except Exception as exc:
        classification, seconds, evidence = classify(error=exc)
        next_at = observed + timedelta(seconds=seconds)
        put_component(name, classification, next_at, evidence)
        detail["error"] = str(exc)[:1500]
        detail["repair"] = {"status": classification, "next_attempt_at": iso(next_at)}
        counts["failures"] += 1
        okay = advisory
    return detail, okay, counts
'''
    new_except = '''    except Exception as exc:
        if optional_logical_resource_absent(str(logical_id), exc):
            evidence = f"OPTIONAL_LOGICAL_RESOURCE_NOT_DEPLOYED:{logical_id}"
            next_at = observed + timedelta(seconds=DATA_COOLDOWN)
            put_component(
                name,
                "NOT_DEPLOYED_OPTIONAL",
                next_at,
                evidence,
            )
            detail["optional_not_deployed"] = True
            detail["repair"] = {
                "status": "NOT_DEPLOYED_OPTIONAL",
                "next_attempt_at": iso(next_at),
            }
            return detail, True, counts
        classification, seconds, evidence = classify(error=exc)
        next_at = observed + timedelta(seconds=seconds)
        put_component(name, classification, next_at, evidence)
        detail["error"] = str(exc)[:1500]
        detail["repair"] = {"status": classification, "next_attempt_at": iso(next_at)}
        counts["failures"] += 1
        okay = advisory
    return detail, okay, counts
'''
    if '"status": "NOT_DEPLOYED_OPTIONAL"' not in text:
        text = replace_once(
            text,
            old_except,
            new_except,
            "optional resource attempt handling",
        )

    path.write_text(text, encoding="utf-8")


def patch_tests() -> None:
    path = ROOT / "tests" / "unit" / "test_aws_native_auto_repair.py"
    text = path.read_text(encoding="utf-8")
    marker = "test_absent_optional_soccer_dlq_component_is_not_a_repair_failure"
    if marker in text:
        return
    block = '''


def test_absent_optional_soccer_dlq_component_is_not_a_repair_failure(monkeypatch):
    module = load_module(monkeypatch, "soccer")
    component = next(
        row
        for row in module.SPORT_CONFIGS["soccer"]
        if row[1] == "SoccerDlqRecoveryFunction"
    )

    class MissingOptionalResource:
        @staticmethod
        def describe_stack_resource(**kwargs):
            logical_id = kwargs["LogicalResourceId"]
            raise module.ClientError(
                {
                    "Error": {
                        "Code": "ValidationError",
                        "Message": (
                            f"Logical Resource ID '{logical_id}' doesn't exist"
                        ),
                    }
                },
                "DescribeStackResource",
            )

    writes = []
    monkeypatch.setattr(module, "CFN", MissingOptionalResource())
    monkeypatch.setattr(
        module,
        "put_component",
        lambda *args, **kwargs: writes.append((args, kwargs)),
    )

    detail, okay, counts = module.attempt(
        component,
        module.now(),
        dry_run=True,
    )

    assert okay is True
    assert detail["optional_not_deployed"] is True
    assert detail["repair"]["status"] == "NOT_DEPLOYED_OPTIONAL"
    assert counts["failures"] == 0
    assert counts["attempts"] == 0
    assert writes and writes[0][0][1] == "NOT_DEPLOYED_OPTIONAL"
'''
    path.write_text(text.rstrip() + block + "\n", encoding="utf-8")


def patch_workflows() -> None:
    replacements = {
        ".github/workflows/deploy-aws-native-auto-repair.yml": (
            (
                "function_prefix: parlay-platform-tennis-learning-",
                "function_prefix: parlay-platform-tennis-le-",
            ),
            (
                "rule_prefix: parlay-platform-tennis-learning-",
                "rule_prefix: parlay-platform-tennis-le-",
            ),
            (
                "function_prefix: parlay-platform-soccer-auto-",
                "function_prefix: parlay-platform-soccer-",
            ),
            (
                "rule_prefix: parlay-platform-soccer-auto-",
                "rule_prefix: parlay-platform-soccer-",
            ),
        ),
        ".github/workflows/deploy-aws-native-auto-repair-v2.yml": (
            (
                "function_prefix: parlay-platform-tennis-learning-",
                "function_prefix: parlay-platform-tennis-le-",
            ),
            (
                "rule_prefix: parlay-platform-tennis-learning-",
                "rule_prefix: parlay-platform-tennis-le-",
            ),
            (
                "function_prefix: parlay-platform-soccer-auto-",
                "function_prefix: parlay-platform-soccer-",
            ),
            (
                "rule_prefix: parlay-platform-soccer-auto-",
                "rule_prefix: parlay-platform-soccer-",
            ),
        ),
        ".github/workflows/deploy-aws-native-auto-repair-v3.yml": (
            (
                "'tennis|parlay-platform-auto-repair-tennis|parlay-platform-tennis-learning|parlay-platform-tennis-learning-|parlay-platform-tennis-learning-'",
                "'tennis|parlay-platform-auto-repair-tennis|parlay-platform-tennis-learning|parlay-platform-tennis-le-|parlay-platform-tennis-le-'",
            ),
            (
                "'soccer|parlay-platform-auto-repair-soccer|parlay-platform-soccer-auto|parlay-platform-soccer-auto-|parlay-platform-soccer-auto-'",
                "'soccer|parlay-platform-auto-repair-soccer|parlay-platform-soccer-auto|parlay-platform-soccer-|parlay-platform-soccer-'",
            ),
        ),
    }
    for filename, pairs in replacements.items():
        path = ROOT / filename
        text = path.read_text(encoding="utf-8")
        for old, new in pairs:
            text = replace_once(text, old, new, f"{filename}: {old}")
        path.write_text(text, encoding="utf-8")


def main() -> int:
    patch_handler()
    patch_tests()
    patch_workflows()
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
