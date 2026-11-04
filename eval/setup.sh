#!/usr/bin/env bash
# Prepare an evaluation workspace in WSL: copy the practice referee and snapshot bot
# versions, so edits in the repo never change a batch that is already running.
#   bash eval/setup.sh /mnt/e/rm/practice_v1.1/rmtc_practice new=WORKTREE old=059875d
set -euo pipefail
REPO="$(cd -- "$(dirname -- "$0")/.." && pwd)"
PRACTICE_SRC="$1"; shift
EVAL="${RMTC_EVAL_HOME:-$HOME/rmtc-eval}"
mkdir -p "$EVAL/versions"
rm -rf "$EVAL/practice"
cp -r "$PRACTICE_SRC" "$EVAL/practice"
chmod +x "$EVAL/practice/bin/"* "$EVAL/practice/"*.sh
for spec in "$@"; do
  name="${spec%%=*}"; rev="${spec#*=}"
  dest="$EVAL/versions/$name"
  rm -rf "$dest"; mkdir -p "$dest"
  if [[ "$rev" == WORKTREE ]]; then
    cp -r "$REPO/rmtc_bot" "$dest/"
  else
    git -C "$REPO" archive "$rev" rmtc_bot | tar -x -C "$dest"
  fi
  find "$dest" -name __pycache__ -prune -exec rm -rf {} +
  echo "version $name <- $rev"
done
echo "workspace: $EVAL"
