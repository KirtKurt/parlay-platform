from pathlib import Path

TEMPLATE = Path("template.yaml")
text = TEMPLATE.read_text()

admin_env = "        INQSI_ADMIN_API_TOKEN: !Ref InqsiAdminApiToken\n"
if admin_env not in text:
    marker = "        ODDS_API_KEY: !Ref OddsApiKey\n"
    if marker not in text:
        raise RuntimeError("ODDS_API_KEY global env marker not found; cannot inject INQSI_ADMIN_API_TOKEN")
    text = text.replace(marker, marker + admin_env, 1)

text = text.replace("Handler: mlb_manual_pull.lambda_handler", "Handler: mlb_manual_pull_protected.lambda_handler", 1)
text = text.replace("Handler: mlb_daily_pick_lock.lambda_handler", "Handler: mlb_daily_pick_lock_protected.lambda_handler", 1)

required = [
    "Handler: mlb_manual_pull_protected.lambda_handler",
    "Handler: mlb_daily_pick_lock_protected.lambda_handler",
    "INQSI_ADMIN_API_TOKEN: !Ref InqsiAdminApiToken",
]
missing = [token for token in required if token not in text]
if missing:
    raise RuntimeError("MLB security patch failed; missing: " + ", ".join(missing))

TEMPLATE.write_text(text)
print("Patched template.yaml to protect HTTP MLB ingest and lock writes with INQSI_ADMIN_API_TOKEN while preserving EventBridge scheduled invocations.")
