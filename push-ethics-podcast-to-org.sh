#!/usr/bin/env bash
# ethics-podcast/ also lives, with the same commit history, as the audio_out
# branch of the lab's exhibit repository. This publishes the commits made
# under ethics-podcast/ there (git subtree reproduces the original hashes).
set -euo pipefail
cd "$(dirname "$0")"
git remote get-url org >/dev/null 2>&1 || git remote add org https://github.com/ai-ccore-exhibit/Reachy.git
git subtree push --prefix=ethics-podcast org "${1:-audio_out}"
