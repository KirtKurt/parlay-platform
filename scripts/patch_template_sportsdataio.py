from pathlib import Path

path = Path("template.yaml")
text = path.read_text()

if "  SportsDataIoApiKey:" not in text:
    text = text.replace(
        "  InqsiAdminApiToken:\n",
        "  SportsDataIoApiKey:\n"
        "    Type: String\n"
        "    NoEcho: true\n"
        "    Default: \"\"\n"
        "    Description: SportsDataIO MLB fundamentals feed key\n"
        "  InqsiAdminApiToken:\n",
    )

if "        SPORTSDATAIO_API_KEY:" not in text:
    text = text.replace(
        "        ODDS_API_KEY: !Ref OddsApiKey\n",
        "        ODDS_API_KEY: !Ref OddsApiKey\n"
        "        SPORTSDATAIO_API_KEY: !Ref SportsDataIoApiKey\n"
        "        INQSI_MLB_USE_SPORTSDATAIO_FUNDAMENTALS: \"true\"\n"
        "        INQSI_REQUIRE_SPORTSDATAIO_FINAL_GATE: \"true\"\n",
    )

path.write_text(text)
print("SportsDataIO enabled: SAM template now wires SPORTSDATAIO_API_KEY and turns MLB fundamentals on.")
