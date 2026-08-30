from pathlib import Path

path = Path('hello_world/mlb_slate_coverage_patch.py')
text = path.read_text(encoding='utf-8')
old = '''    if str(item.get("game_identity") or "") != expected_identity:\n        errors.append("terminal_manifest_game_identity_mismatch")\n    if str(item.get("game_id") or "") != expected_game_id:\n        errors.append("terminal_manifest_game_id_mismatch")\n    if (\n        not expected_official_pk\n        or str(item.get("officialGamePk") or "") != expected_official_pk\n    ):\n        errors.append("terminal_manifest_official_game_pk_mismatch")\n'''
new = '''    observed_official_pk = str(item.get("officialGamePk") or "")\n    terminal_official_pk_exact = bool(\n        expected_official_pk\n        and observed_official_pk == expected_official_pk\n    )\n    # MLB StatsAPI gamePk is the durable official game identity. Historical\n    # terminal rows can retain a pre-rebind provider alias in game_id /\n    # game_identity after the manifest moved to the canonical StatsAPI ID.\n    # Accept only those alias differences when the immutable official gamePk\n    # is an exact match. Start time, teams, manifest fingerprint, slate and\n    # every other terminal-authority check below remain fail-closed.\n    if (\n        not terminal_official_pk_exact\n        and str(item.get("game_identity") or "") != expected_identity\n    ):\n        errors.append("terminal_manifest_game_identity_mismatch")\n    if (\n        not terminal_official_pk_exact\n        and str(item.get("game_id") or "") != expected_game_id\n    ):\n        errors.append("terminal_manifest_game_id_mismatch")\n    if not terminal_official_pk_exact:\n        errors.append("terminal_manifest_official_game_pk_mismatch")\n'''
count = text.count(old)
if count != 1:
    raise SystemExit(f'expected one terminal identity block, found {count}')
path.write_text(text.replace(old, new), encoding='utf-8')
