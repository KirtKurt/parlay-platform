import sport_key_lookup


def apply(odds_module):
    if odds_module is None or getattr(odds_module, '_inqsi_key_patch', False):
        return
    original = odds_module.provider_keys_for

    def keys_for(app_sport):
        sport = odds_module.sport_key(app_sport)
        if sport == 'tennis':
            try:
                rows = odds_module.http_get_json(odds_module.sports_url())
                keys = sport_key_lookup.find_tennis_keys(rows)
                if keys:
                    return keys
            except Exception:
                pass
        return original(app_sport)

    odds_module.provider_keys_for = keys_for
    odds_module._inqsi_key_patch = True
