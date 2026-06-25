from pathlib import Path

TEMPLATE = Path("template.yaml")
text = TEMPLATE.read_text()


def ensure_results_event(logical_name: str, path: str, method: str = "GET") -> None:
    global text
    if f"        {logical_name}:\n" in text:
        return
    marker = "        MLBResultsEvery6Hours:\n"
    block = f"""        {logical_name}:
          Type: Api
          Properties:
            Path: {path}
            Method: {method}
"""
    if marker not in text:
        raise RuntimeError("MLBResultsEvery6Hours marker not found in template.yaml")
    text = text.replace(marker, block + marker)


ensure_results_event("MLBFinalScoresGet", "/v1/results/mlb/final-scores")
ensure_results_event("MLBSettlementGet", "/v1/results/mlb/settlement")
ensure_results_event("MLBSettlementProofGet", "/v1/results/mlb/proof")
ensure_results_event("MLBSignalLearningGet", "/v1/results/mlb/signal-learning")
ensure_results_event("MLBResultSignalsGet", "/v1/results/mlb/result-signals")
ensure_results_event("MLBResultSignalsPost", "/v1/results/mlb/result-signals", "POST")
ensure_results_event("MLBResultSignalsAliasGet", "/v1/mlb/result-signals")
ensure_results_event("MLBResultSignalsAliasPost", "/v1/mlb/result-signals", "POST")

TEMPLATE.write_text(text)
print("Patched template.yaml with MLB final score, settlement, proof, signal-learning, and result-signal routes.")
