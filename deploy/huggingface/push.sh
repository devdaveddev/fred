#!/bin/sh
# Push the current commit to a Hugging Face Space (Docker SDK).
#   sh deploy/huggingface/push.sh <hf-username>/<space-name>
# Needs a Hugging Face login with write access (run `hf auth login` once, or use a git credential helper).
set -e
SPACE="$1"
[ -n "$SPACE" ] || { echo "usage: $0 <user>/<space>"; exit 1; }
ROOT=$(git rev-parse --show-toplevel)
TMP=$(mktemp -d)
git -C "$ROOT" archive HEAD | tar -x -C "$TMP"
cp "$ROOT/deploy/huggingface/README.md" "$TMP/README.md"   # Spaces read their config from README front matter
cd "$TMP"
git init -q -b main
git add -A
git -c user.name="fred-deploy" -c user.email="deploy@localhost" commit -q -m "Deploy $(git -C "$ROOT" rev-parse --short HEAD)"
git push -f "https://huggingface.co/spaces/$SPACE" main
echo "Pushed. Build logs: https://huggingface.co/spaces/$SPACE?logs=build"
rm -rf "$TMP"
