def find_tennis_keys(rows):
    out = []
    for row in rows if isinstance(rows, list) else []:
        if not isinstance(row, dict):
            continue
        key = str(row.get('key') or '').lower()
        group = str(row.get('group') or '').lower()
        title = str(row.get('title') or '').lower()
        active = row.get('active', True)
        text = ' '.join([key, group, title])
        if active and ('tennis' in text or 'atp' in text or 'wta' in text):
            out.append(key)
    return sorted(set(out))
