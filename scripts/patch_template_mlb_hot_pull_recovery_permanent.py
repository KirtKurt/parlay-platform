from pathlib import Path

TEMPLATE = Path("template.yaml")
text = TEMPLATE.read_text()


def remove_resource_block(current: str, resource_name: str) -> str:
    """Remove a top-level CloudFormation resource block by logical id."""
    lines = current.splitlines(keepends=True)
    out = []
    i = 0
    needle = f"  {resource_name}:"
    while i < len(lines):
        if lines[i].startswith(needle):
            i += 1
            while i < len(lines):
                nxt = lines[i]
                is_next_resource = nxt.startswith("  ") and not nxt.startswith("    ")
                is_outputs = nxt.startswith("Outputs:")
                if is_next_resource or is_outputs:
                    break
                i += 1
            continue
        out.append(lines[i])
        i += 1
    return "".join(out)


# MLB Predictive Platform V1 makes MLBAuditedPullFunction the single production
# 15-minute path. The previous recovery Lambda created duplicate schedules and
# one of them used rate(15 minutes), which could drift off quarter-hour ET
# boundaries. Remove it from the patched template so deploys converge on one
# Odds API pull-store-predict pipeline.
text = remove_resource_block(text, "MLBHotPullRecoveryFunction")

TEMPLATE.write_text(text)
print("Removed obsolete MLBHotPullRecoveryFunction; MLB V1 uses MLBAuditedPullFunction only.")
