from pathlib import Path

TEMPLATE = Path("template.yaml")
text = TEMPLATE.read_text()

admin_env = "        INQSI_ADMIN_API_TOKEN: !Ref InqsiAdminApiToken\n"
if admin_env not in text:
    marker = "        ODDS_API_KEY: !Ref OddsApiKey\n"
    if marker not in text:
        raise RuntimeError("ODDS_API_KEY global env marker not found; cannot inject INQSI_ADMIN_API_TOKEN")
    text = text.replace(marker, marker + admin_env, 1)

text = text.replace(
    "Handler: mlb_manual_pull.lambda_handler",
    "Handler: mlb_manual_pull_protected.lambda_handler",
    1,
)

if "Handler: mlb_manual_pull_protected.lambda_handler" not in text:
    raise RuntimeError("MLBAuditedPullFunction is not using the protected MLB pull handler")
if admin_env not in text:
    raise RuntimeError("INQSI_ADMIN_API_TOKEN was not injected into SAM globals")
if "MLBDailyPickLockFunction:" in text and "INQSI_ADMIN_API_TOKEN" not in text:
    raise RuntimeError("Daily lock function cannot see INQSI_ADMIN_API_TOKEN")

TEMPLATE.write_text(text)
print("Patched template.yaml to protect HTTP MLB ingest/lock writes with INQSI_ADMIN_API_TOKEN while preserving EventBridge scheduled invocations.")
