from pathlib import Path

# Legacy compatibility wrapper.
# The old version of this file inserted a 1 AM kickoff and days_ahead=1, which
# could pollute tomorrow's MLB slate. Keep this path safe by delegating to the
# enforced v2 patch instead.
exec(Path('scripts/patch_template_mlb_hot_start_v2.py').read_text())
print('Delegated legacy MLB hot-start patch to same-day-only v2 patch.')
