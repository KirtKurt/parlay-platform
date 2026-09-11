#!/usr/bin/env bash
set -euo pipefail

: "${OPENAI_API_KEY:?OPENAI_API_KEY is required}"
: "${GITHUB_TOKEN:?GITHUB_TOKEN is required}"

ROOT="$(git rev-parse --show-toplevel)"
cd "$ROOT"
HOST_PATH="$PATH"

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

# actions/checkout persists a repository-scoped credential in local git config.
# Remove it before Codex starts. Merely dropping GITHUB_TOKEN from the child
# environment is insufficient because git can otherwise authenticate through
# that persisted extraheader and publish before independent validation.
git config --local --unset-all http.https://github.com/.extraheader 2>/dev/null || true

PROMPT=$(cat <<'EOF'
You are the bounded Inqsi ARB coding worker in KirtKurt/parlay-platform.

Select exactly one highest-priority unresolved, actionable item from inqsi-arb/ARB_BACKLOG.md and implement one small, shippable increment.

STRICT WRITE SCOPE:
- inqsi-arb/**
- .github/workflows/inqsi-arb-*

Do not modify anything else. Do not modify MLB, Tennis, Soccer, KS1, engineering_console, shared prediction authority, credentials, or unrelated workflows. Do not place wagers or add wager-placement capability. Do not weaken fail-closed settlement, identity, freshness, security, arbitrage proof, or no-wager protections. Do not merge or deploy. Do not push, open a pull request, alter remotes, or publish repository changes. The supervising wrapper alone may publish after independent validation.

Before finishing, run the relevant ARB tests. Keep the repository building. If the requested backlog item depends on an unavailable credential or external entitlement, implement only the independent safe portion and document the blocker in an ARB-scoped status/backlog file.
EOF
)

# The workflow prepares the ephemeral Ubuntu runner so Codex can use its
# current workspace-write sandbox. Keep the coding worker non-interactive and
# sandboxed. Do not pass GitHub/AWS credentials to the coding subprocess.
env \
  -u GITHUB_TOKEN -u GH_TOKEN \
  -u AWS_ACCESS_KEY_ID -u AWS_SECRET_ACCESS_KEY -u AWS_SESSION_TOKEN \
  -u AWS_REGION -u AWS_DEFAULT_REGION -u AWS_PROFILE \
  PATH="$HOST_PATH" \
  codex --ask-for-approval never exec --sandbox workspace-write "$PROMPT"

# Codex may leave worktree edits, staged edits, or local commits. Treat all
# repository differences from the controller-selected base as candidate output.
mapfile -t CHANGED < <(
  {
    git diff --name-only "$BASE_SHA"...HEAD
    git diff --name-only
    git diff --name-only --cached
    git ls-files --others --exclude-standard
  } | sed '/^$/d' | sort -u
)
if [[ ${#CHANGED[@]} -eq 0 ]]; then
  echo "Codex produced no repository changes" >&2
  exit 23
fi

validate_scope() {
  local path
  for path in "$@"; do
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
}
validate_scope "${CHANGED[@]}"

# Normalize any Codex-created local commits back into uncommitted candidate
# changes. This makes the wrapper the sole commit/publish authority and ensures
# its independent validation covers the exact bytes that will be published.
if [[ "$(git rev-parse HEAD)" != "$BASE_SHA" ]]; then
  git reset --mixed "$BASE_SHA"
fi

# Restore the host toolchain path before independent validation. setup-python's
# Python 3.11 location is required by SAM's PythonPipBuilder runtime check.
export PATH="$HOST_PATH"
python -m pytest -q inqsi-arb/tests

if command -v sam >/dev/null 2>&1; then
  sam validate --lint --template-file inqsi-arb/template.yaml
  sam build --no-cached --template-file inqsi-arb/template.yaml
fi

mapfile -t FINAL_CHANGED < <(
  {
    git diff --name-only
    git diff --name-only --cached
    git ls-files --others --exclude-standard
  } | sed '/^$/d' | sort -u
)
if [[ ${#FINAL_CHANGED[@]} -eq 0 ]]; then
  echo "No candidate changes remain after validation" >&2
  exit 26
fi
validate_scope "${FINAL_CHANGED[@]}"

# Restore repository authentication only after Codex has exited and the exact
# candidate bytes have passed independent validation. Avoid `gh auth setup-git`:
# hosted runners can contain credential-helper state that makes its unset step
# fail. A short-lived local extraheader gives only the supervising wrapper's
# push access, is never placed in the remote URL, and is removed immediately.
cleanup_publish_auth() {
  git config --local --unset-all http.https://github.com/.extraheader 2>/dev/null || true
}
PUBLISH_BASIC="$(printf 'x-access-token:%s' "$GITHUB_TOKEN" | base64 | tr -d '\n')"
git config --local http.https://github.com/.extraheader "AUTHORIZATION: basic ${PUBLISH_BASIC}"
unset PUBLISH_BASIC
trap cleanup_publish_auth EXIT

git config user.name "inqsi-arb-aec[bot]"
git config user.email "inqsi-arb-aec@users.noreply.github.com"
git add -- 'inqsi-arb' '.github/workflows/inqsi-arb-'* 2>/dev/null || true
if git diff --cached --quiet; then
  echo "No allowed changes staged" >&2
  exit 27
fi

git commit -m "ARB AEC: autonomous Codex increment"
git push --set-upstream origin "$BRANCH"
cleanup_publish_auth
trap - EXIT

# gh uses GITHUB_TOKEN from the environment; Git's temporary publication
# credential is already gone before the draft-PR request.
PR_URL=$(gh pr create \
  --draft \
  --base main \
  --head "$BRANCH" \
  --title "ARB AEC: autonomous Codex increment" \
  --body "Autonomous bounded Inqsi ARB engineering increment. Codex CLI was denied repository publication credentials and restricted to ARB-scoped paths. The supervising wrapper independently normalized the candidate, validated scope, reran the complete ARB test suite and available SAM validation/build, then committed and pushed these exact validated bytes before requesting this draft PR. No merge or deployment was performed by the coding worker.")

echo "draft_pr=$PR_URL"
