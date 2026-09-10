def surface_from_sport_key(sport_key: str) -> str:
    key = (sport_key or "").lower()
    if "wimbledon" in key or "grass" in key:
        return "grass"
    if "french" in key or "roland" in key or "clay" in key:
        return "clay"
    return "hard"
