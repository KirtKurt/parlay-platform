#!/usr/bin/env bash
set -euo pipefail

: "${OPENAI_API_KEY:?OPENAI_API_KEY is required}"
: "${GITHUB_TOKEN:?GITHUB_TOKEN is required}"

ROOT="$(git rev-parse --show-toplevel)"
cd "$ROOT"

if ! command -v codex >/dev/null 2>&1; then
  echo "codex CLI is not installed" >&2
  exit 20
fi
if ! command -v gh >/dev/null 2>&1; then
  echo "gh CLI is not installed" >&2
  exit 21
fi

if [[ -n "$(git status --porcelain)" ]]; then
  echo "refusing to run Codex from a dirty checkout" >&2
  exit 22
fi

BASE_SHA="${ARB_AEC_MAIN_SHA:-$(git rev-parse HEAD)}"
RUN_KEY="${GITHUB_RUN_ID:-$(date +%s)}"
BRANCH="agent/inqsi-arb-aec-${RUN_KEY}"

git fetch --no-tags origin main
git checkout --detach "$BASE_SHA"
git checkout -b "$BRANCH"

PROMPT=$(cat <<'EOF'
You are the bounded Inqsi ARB coding worker in KirtKurt/parlay-platform.

Select exactly one highest-priority unresolved, actionable item from inqsi-arb/ARB_BACKLOG.md and implement one small, shippable increment.

STRICT WRITE SCOPE:
- inqsi-arb/**
- .github/workflows/inqsi-arb-*

Do not modify anything else. Do not modify MLB, Tennis, Soccer, KS1, engineering_console, shared prediction authority, credentials, or unrelated workflows. Do not place wagers or add wager-placement capability. Do not weaken fail-closed settlement, identity, freshness, security, arbitrage proof, or no-wager protections. Do not merge or deploy.

Before finishing, run the relevant ARB tests. Keep the repository building. If the requested backlog item depends on an unavailable credential or external entitlement, implement only the independent safe portion and document the blocker in an ARB-scoped status/backlog file.
EOF
)

codex exec --full-auto "$PROMPT"

mapfile -t CHANGED < <(git status --porcelain=v1 | sed -E 's/^.. //' | sed -E 's/.* -> //' | sed '/^$/d')
if [[ ${#CHANGED[@]} -eq 0 ]]; then
  echo "Codex produced no repository changes" >&2
  exit 23
fi

for path in "${CHANGED[@]}"; do
  case "$path" in
    inqsi-arb/*|.github/workflows/inqsi-arb-*) ;;
    *)
      echo "scope violation: $path" >&2
      git reset --hard "$BASE_SHA"
      git clean -fd
      exit 24
      ;;
  esac
done

python -m pytest -q inqsi-arb/tests

if command -v sam >/dev/null 2>&1; then
  sam validate --lint --template-file inqsi-arb/template.yaml
  sam build --no-cached --template-file inqsi-arb/template.yaml
fi

# Re-check after tests/build in case a tool unexpectedly modified tracked files.
mapfile -t FINAL_CHANGED < <(git status --porcelain=v1 | sed -E 's/^.. //' | sed -E 's/.* -> //' | sed '/^$/d')
for path in "${FINAL_CHANGED[@]}"; do
  case "$path" in
    inqsi-arb/*|.github/workflows/inqsi-arb-*) ;;
    *)
      echo "post-test scope violation: $path" >&2
      git reset --hard "$BASE_SHA"
      git clean -fd
      exit 25
      ;;
  esac
done

git config user.name "inqsi-arb-aec[bot]"
git config user.email "inqsi-arb-aec@users.noreply.github.com"
git add -- 'inqsi-arb' '.github/workflows/inqsi-arb-'* 2>/dev/null || true
if git diff --cached --quiet; then
  echo "No allowed changes staged" >&2
  exit 26
fi

git commit -m "ARB AEC: autonomous Codex increment"
git push --set-upstream origin "$BRANCH"

PR_URL=$(gh pr create \
  --draft \
  --base main \
  --head "$BRANCH" \
  --title "ARB AEC: autonomous Codex increment" \
  --body "Autonomous bounded Inqsi ARB engineering increment. Codex CLI was restricted to ARB-scoped paths, then the ARB test suite and available SAM validation/build were run before this draft PR was opened. No merge or deployment was performed by the coding worker.")

echo "draft_pr=$PR_URL"
