from pathlib import Path

path = Path("template.yaml")
text = path.read_text()

if "SportsDataIoApiKey:" not in text:
    marker = "  InqsiAdminApiToken:\n"
    insert = (
        "  SportsDataIoApiKey:\n"
        "    Type: String\n"
        "    NoEcho: true\n"
        "    Default: \"\"\n"
        "    Description: SportsDataIO MLB API key\n"
    )
    text = text.replace(marker, insert + marker, 1)

if "SPORTSDATAIO_API_KEY:" not in text:
    marker = "        ODDS_API_KEY: !Ref OddsApiKey\n"
    insert = "        SPORTSDATAIO_API_KEY: !Ref SportsDataIoApiKey\n"
    text = text.replace(marker, marker + insert, 1)

path.write_text(text)
