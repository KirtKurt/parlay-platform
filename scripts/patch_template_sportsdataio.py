from pathlib import Path

# MLB Predictive Platform V1 is Odds API line-movement only.
# SportsDataIO is intentionally not part of the MLB production path.
# This no-op patch is kept only so any older workflow call does not fail.
path = Path("template.yaml")
text = path.read_text()
path.write_text(text)
print("SportsDataIO patch skipped: MLB V1 uses The Odds API only.")
