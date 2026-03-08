#!/usr/bin/env bash
# Install git hooks for HeartClaws development
set -e
REPO_ROOT="$(cd "$(dirname "$0")/.." && pwd)"

cp "$REPO_ROOT/scripts/pre-commit" "$REPO_ROOT/.git/hooks/pre-commit"
chmod +x "$REPO_ROOT/.git/hooks/pre-commit"

echo "✅ Git hooks installed."
