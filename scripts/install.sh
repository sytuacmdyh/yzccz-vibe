#!/usr/bin/env bash
set -euo pipefail

# Resolve repo root relative to this script's location
REPO_ROOT="$(cd "$(dirname "$0")/.." && pwd)"
SKILLS_SRC="$REPO_ROOT/skills"

# Preflight
if [ ! -d "$SKILLS_SRC" ]; then
    echo "ERROR: skills/ directory not found at $SKILLS_SRC" >&2
    exit 1
fi

shopt -s nullglob
skill_dirs=("$SKILLS_SRC"/*/)
shopt -u nullglob

if [ ${#skill_dirs[@]} -eq 0 ]; then
    echo "ERROR: no skills found in $SKILLS_SRC" >&2
    exit 1
fi

TARGETS=(
    "$HOME/.claude/skills"
    "$HOME/.codex/skills"
)

skill_count=0
for TARGET in "${TARGETS[@]}"; do
    mkdir -p "$TARGET"
    YZC_DIR="$TARGET/yzc"

    for skill_src in "${skill_dirs[@]}"; do
        skill_name="$(basename "$skill_src")"
        TMP_DST="$YZC_DIR/.tmp.$skill_name"
        FINAL_DST="$YZC_DIR/$skill_name"

        # Stage to temp location
        mkdir -p "$YZC_DIR"
        rm -rf "$TMP_DST"
        cp -rL "$skill_src" "$TMP_DST"

        # Atomic swap
        rm -rf "$FINAL_DST"
        mv "$TMP_DST" "$FINAL_DST"

        echo "  yzc/$skill_name -> $FINAL_DST"
        skill_count=$((skill_count + 1))
    done
done

echo ""
echo "Deployed $((skill_count / ${#TARGETS[@]})) skill(s) to ${#TARGETS[@]} targets."
