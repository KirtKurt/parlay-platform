from pathlib import Path

TEMPLATE = Path("template.yaml")
text = TEMPLATE.read_text()


def remove_indented_event_block(current: str, event_name: str) -> str:
    """Remove a SAM Function Events child block by event logical id."""
    lines = current.splitlines(keepends=True)
    output = []
    i = 0
    needle = f"        {event_name}:"
    while i < len(lines):
        line = lines[i]
        if line.startswith(needle):
            i += 1
            while i < len(lines):
                nxt = lines[i]
                is_next_event = nxt.startswith("        ") and not nxt.startswith("          ")
                is_next_resource = nxt.startswith("  ") and not nxt.startswith("    ")
                is_outputs = nxt.startswith("Outputs:")
                if is_next_event or is_next_resource or is_outputs:
                    break
                i += 1
            continue
        output.append(line)
        i += 1
    return "".join(output)


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
    text = text.replace(marker, block + marker, 1)


# These alias routes are owned by MLBResultSignalsFunction, inserted from
# patch_template_mlb_v1.py. Keeping another GET/POST pair here makes SAM reject
# the template with duplicate API method errors for /v1/mlb/result-signals.
for alias_event in ["MLBResultSignalsAliasGet", "MLBResultSignalsAliasPost"]:
    text = remove_indented_event_block(text, alias_event)

ensure_results_event("MLBFinalScoresGet", "/v1/results/mlb/final-scores")
ensure_results_event("MLBSettlementGet", "/v1/results/mlb/settlement")
ensure_results_event("MLBSettlementProofGet", "/v1/results/mlb/proof")
ensure_results_event("MLBSignalLearningGet", "/v1/results/mlb/signal-learning")
ensure_results_event("MLBResultSignalsGet", "/v1/results/mlb/result-signals")
ensure_results_event("MLBResultSignalsPost", "/v1/results/mlb/result-signals", "POST")

TEMPLATE.write_text(text)
print("Patched template.yaml with MLB final score, settlement, proof, signal-learning, and canonical result-signal routes. Alias routes are de-duplicated.")
