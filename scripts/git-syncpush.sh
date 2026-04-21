#!/bin/sh
set -eu

branch=$(git rev-parse --abbrev-ref HEAD)

if [ "$branch" != "main" ]; then
  echo "❌ syncpush funguje jen na větvi main. Aktuálně: $branch" >&2
  exit 1
fi

if [ -n "$(git status --porcelain --untracked-files=no)" ]; then
  echo "❌ Nejdřív commitni nebo stashni rozpracované tracked změny." >&2
  git status --short --branch
  exit 1
fi

echo "→ Fetch origin/main"
git fetch origin main

echo "→ Rebase na origin/main"
git pull --rebase --autostash origin main

echo "→ Push na origin/main"
git push origin main

echo "✅ Hotovo"
