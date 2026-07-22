#!/usr/bin/env python3
from pathlib import Path

path = Path('.github/patches/apply_mlb_lock_status_snapshot_hotpath.py')
text = path.read_text(encoding='utf-8')
old = '''replace_once(
    "hello_world/mlb_daily_per_game_lock_patch.py",
    \'\'\'        pulls = sorted(module._pulls_for_date(slate), key=lambda pull: _pull_at(module, pull) or datetime.min.replace(tzinfo=timezone.utc))
        manifest = module._latest_games_for_date(slate, pulls)
\'\'\',
    \'\'\'        pulls = sorted(
            _status_query_pulls(module, slate),
            key=lambda pull: _pull_at(module, pull)
            or datetime.min.replace(tzinfo=timezone.utc),
        )
        manifest = module._latest_games_for_date(slate, pulls)
\'\'\',
)
'''
new = '''replace_once(
    "hello_world/mlb_daily_per_game_lock_patch.py",
    \'\'\'        raw_existing = daily_item if daily_cached else module._get_lock_item(slate)
        pulls = sorted(module._pulls_for_date(slate), key=lambda pull: _pull_at(module, pull) or datetime.min.replace(tzinfo=timezone.utc))
        manifest = module._latest_games_for_date(slate, pulls)
        # Pull discovery determines the durable manifest and therefore the
\'\'\',
    \'\'\'        raw_existing = daily_item if daily_cached else module._get_lock_item(slate)
        pulls = sorted(
            _status_query_pulls(module, slate),
            key=lambda pull: _pull_at(module, pull)
            or datetime.min.replace(tzinfo=timezone.utc),
        )
        manifest = module._latest_games_for_date(slate, pulls)
        # Pull discovery determines the durable manifest and therefore the
\'\'\',
)
'''
if text.count(old) != 1:
    raise SystemExit(f'expected one patcher target, found {text.count(old)}')
path.write_text(text.replace(old, new, 1), encoding='utf-8')
print('Narrowed status-only pull query replacement')
