from pathlib import Path

path = Path("template.yaml")
text = path.read_text()

if "  SportsDataIOApiKey:" not in text and "  SportsDataIoApiKey:" not in text:
    text = text.replace(
        "  InqsiAdminApiToken:\n",
        "  SportsDataIOApiKey:\n"
        "    Type: String\n"
        "    NoEcho: true\n"
        "    Default: \"\"\n"
        "    Description: SportsDataIO MLB fundamentals feed key\n"
        "  InqsiAdminApiToken:\n",
        1,
    )
elif "  SportsDataIoApiKey:" in text and "  SportsDataIOApiKey:" not in text:
    text = text.replace("  SportsDataIoApiKey:", "  SportsDataIOApiKey:")
    text = text.replace("!Ref SportsDataIoApiKey", "!Ref SportsDataIOApiKey")

if "        SPORTSDATAIO_API_KEY:" not in text:
    text = text.replace(
        "        ODDS_API_KEY: !Ref OddsApiKey\n",
        "        ODDS_API_KEY: !Ref OddsApiKey\n"
        "        SPORTSDATAIO_API_KEY: !Ref SportsDataIOApiKey\n"
        "        INQSI_MLB_USE_SPORTSDATAIO_FUNDAMENTALS: \"true\"\n"
        "        INQSI_REQUIRE_SPORTSDATAIO_FINAL_GATE: \"false\"\n"
        "        SPORTSDATAIO_TIMEOUT_SECONDS: \"25\"\n",
        1,
    )
else:
    text = text.replace("!Ref SportsDataIoApiKey", "!Ref SportsDataIOApiKey")
    if "INQSI_MLB_USE_SPORTSDATAIO_FUNDAMENTALS" not in text:
        text = text.replace("        SPORTSDATAIO_API_KEY: !Ref SportsDataIOApiKey\n", "        SPORTSDATAIO_API_KEY: !Ref SportsDataIOApiKey\n        INQSI_MLB_USE_SPORTSDATAIO_FUNDAMENTALS: \"true\"\n")
    if "INQSI_REQUIRE_SPORTSDATAIO_FINAL_GATE" not in text:
        text = text.replace("        INQSI_MLB_USE_SPORTSDATAIO_FUNDAMENTALS: \"true\"\n", "        INQSI_MLB_USE_SPORTSDATAIO_FUNDAMENTALS: \"true\"\n        INQSI_REQUIRE_SPORTSDATAIO_FINAL_GATE: \"false\"\n")
    else:
        text = text.replace("        INQSI_REQUIRE_SPORTSDATAIO_FINAL_GATE: \"true\"", "        INQSI_REQUIRE_SPORTSDATAIO_FINAL_GATE: \"false\"")
    if "SPORTSDATAIO_TIMEOUT_SECONDS" not in text:
        text = text.replace("        INQSI_REQUIRE_SPORTSDATAIO_FINAL_GATE: \"false\"\n", "        INQSI_REQUIRE_SPORTSDATAIO_FINAL_GATE: \"false\"\n        SPORTSDATAIO_TIMEOUT_SECONDS: \"25\"\n")

path.write_text(text)
print("SportsDataIO enabled: SAM template wires SPORTSDATAIO_API_KEY, turns MLB fundamentals on, and keeps final-gate blocking disabled.")
