#!/usr/bin/env bash
# Stands the whole thing up: repo, secrets, GitHub Pages, first build.
#
#   ./bootstrap.sh
#
# Safe to re-run. If the repo already exists it just pushes and re-triggers.
# Needs: git, gh (brew install gh), and your Anthropic API key.

set -euo pipefail

REPO="${REPO:-altar-events}"
OWNER="${OWNER:-}"                     # blank = your personal account
# Public by default: GitHub Pages will not serve a site from a private repo
# unless the account is on a paid plan, and this calendar has to be reachable
# by customers. Nothing secret lives in here — the API keys are set as repo
# secrets in step 3, never committed. If you are on GitHub Pro and would
# rather keep it closed, run: VISIBILITY=private ./bootstrap.sh
VISIBILITY="${VISIBILITY:-public}"
DOMAIN="${DOMAIN-calendar.altar.bike}"  # set DOMAIN= (empty) to stay on github.io
BRANCH=main

say()  { printf '\n\033[1m%s\033[0m\n' "$*"; }
ok()   { printf '  \033[32mok\033[0m   %s\n' "$*"; }
warn() { printf '  \033[33mnote\033[0m %s\n' "$*"; }
die()  { printf '\n\033[31mstopped:\033[0m %s\n\n' "$*" >&2; exit 1; }

# ---------------------------------------------------------------- 0. checks
say "Checking your setup"

command -v git >/dev/null || die "git not installed"
command -v gh  >/dev/null || die "gh not installed. Run: brew install gh"
ok "git and gh present"

gh auth status >/dev/null 2>&1 || die "not logged in to GitHub. Run: gh auth login"
ME=$(gh api user --jq .login)
TARGET="${OWNER:-$ME}/$REPO"
ok "authenticated as $ME"

[[ -f sources.yml && -f build.py ]] || die "run this from inside the altar-events folder"

if [[ -z "${ANTHROPIC_API_KEY:-}" ]]; then
  printf '\n  Anthropic API key (from console.anthropic.com, starts sk-ant-): '
  read -rs ANTHROPIC_API_KEY; echo
fi
[[ "$ANTHROPIC_API_KEY" == sk-ant-* ]] || die "that doesn't look like an Anthropic key"
ok "API key captured"

# --------------------------------------------------------- 1. local commit
say "Preparing the repo"

if [[ ! -d .git ]]; then
  git init -q -b "$BRANCH"
  ok "git initialised"
else
  git symbolic-ref -q HEAD "refs/heads/$BRANCH" 2>/dev/null || git checkout -q -B "$BRANCH"
  ok "existing repo, on $BRANCH"
fi

git config user.name  >/dev/null 2>&1 || git config user.name  "$ME"
git config user.email >/dev/null 2>&1 || git config user.email "$ME@users.noreply.github.com"

git add -A
if git diff --cached --quiet 2>/dev/null; then
  ok "nothing new to commit"
else
  git commit -qm "WNC cycling calendar: aggregator, feeds and site"
  ok "committed $(git diff --stat HEAD~1 2>/dev/null | tail -1 | xargs || echo 'initial import')"
fi

# ------------------------------------------------------------ 2. the remote
say "Creating $TARGET"

if gh repo view "$TARGET" >/dev/null 2>&1; then
  warn "repo already exists — pushing to it"
  git remote get-url origin >/dev/null 2>&1 \
    || git remote add origin "https://github.com/$TARGET.git"
else
  gh repo create "$TARGET" "--$VISIBILITY" \
    --description "WNC cycling events, aggregated for altar.bike" \
    --source=. --remote=origin
  ok "repo created ($VISIBILITY)"
fi

git push -q -u origin "$BRANCH"
ok "pushed to $BRANCH"

# --------------------------------------------------------------- 3. secrets
say "Adding secrets"
gh secret set ANTHROPIC_API_KEY --repo "$TARGET" --body "$ANTHROPIC_API_KEY"
ok "ANTHROPIC_API_KEY set"
if [[ -n "${RWGPS_API_KEY:-}" ]]; then
  gh secret set RWGPS_API_KEY --repo "$TARGET" --body "$RWGPS_API_KEY"
  ok "RWGPS_API_KEY set (Asheville on Bikes rides enabled)"
else
  warn "no RWGPS_API_KEY — AoB's weekly rides stay off. Optional, see README."
fi

# ----------------------------------------------------------------- 4. Pages
say "Turning on GitHub Pages"
if gh api "repos/$TARGET/pages" >/dev/null 2>&1; then
  ok "Pages already enabled"
else
  gh api --method POST "repos/$TARGET/pages" \
    -f "build_type=workflow" >/dev/null 2>&1 \
    && ok "Pages enabled (source: Actions)" \
    || warn "couldn't enable Pages via API — do it by hand: Settings > Pages > Source: GitHub Actions"
fi

# site/CNAME tells the deploy which hostname to keep. Registering the same
# domain on the Pages config is a separate step, and skipping it means the
# custom domain silently reverts to github.io on the next run.
if [[ -n "$DOMAIN" ]]; then
  gh api --method PUT "repos/$TARGET/pages" -f "cname=$DOMAIN" >/dev/null 2>&1 \
    && ok "custom domain set to $DOMAIN" \
    || warn "couldn't set the domain via API — Settings > Pages > Custom domain > $DOMAIN"
  printf '%s\n' "$DOMAIN" > site/CNAME
fi

# ------------------------------------------------------------- 5. first run
say "Running the first build"
gh workflow run "Build events calendar" --repo "$TARGET" --ref "$BRANCH" \
  && ok "build queued" \
  || warn "couldn't queue it — go to Actions and hit Run workflow"

sleep 6
RUN=$(gh run list --repo "$TARGET" --limit 1 --json databaseId --jq '.[0].databaseId' 2>/dev/null || echo "")

if [[ -n "$DOMAIN" ]]; then
  SITE_URL="https://$DOMAIN/"
else
  SITE_URL="https://$(echo "${OWNER:-$ME}" | tr '[:upper:]' '[:lower:]').github.io/$REPO/"
fi

cat <<EOF

──────────────────────────────────────────────────────────
  Repo      https://github.com/$TARGET
  Actions   https://github.com/$TARGET/actions
  Calendar  $SITE_URL

  Watch it build:
    gh run watch ${RUN:-<run-id>} --repo $TARGET
EOF

if [[ -n "$DOMAIN" ]]; then
cat <<EOF

  DNS — this part is not something I can do for you. At your
  registrar, add one record:

    type    CNAME
    name    ${DOMAIN%%.*}
    value   $(echo "${OWNER:-$ME}" | tr '[:upper:]' '[:lower:]').github.io

  Until that resolves, $DOMAIN will 404 while the
  github.io address above works fine. Once it does resolve, turn
  on Settings > Pages > Enforce HTTPS (the cert can take an hour).
EOF
fi

cat <<EOF

  Then, and this is the one step that still needs you:
    python3 -m pip install -r requirements.txt
    python3 probe.py --check

  That tests all thirteen sources against the live sites and tells
  you which endpoints are wrong. Three results are expected and
  are NOT bugs:

    blue-ridge-bicycle-club  FAIL   members-only, marked optional
    nica-nc / pisgah-rage / ic-imagine   empty in Jul-Nov;
                             NC is a spring league, this is seasonal

  Anything else that says FAIL:

    python3 probe.py <their events page>

  It prints every endpoint that returned events. Put the winner in
  sources.yml, commit, done.
──────────────────────────────────────────────────────────

EOF
