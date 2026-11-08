#!/bin/sh
# Install the $switchit agent skill for tools that read ~/.agents/skills.
# Idempotent: re-running refreshes the installed copy.
set -eu

SCRIPT_DIR=$(CDPATH= cd -- "$(dirname -- "$0")" && pwd)
SOURCE="$SCRIPT_DIR/../skills/switchit/SKILL.md"
TARGET_DIR="${HOME}/.agents/skills/switchit"

if [ ! -f "$SOURCE" ]; then
    echo "error: skill not found at $SOURCE" >&2
    exit 1
fi

mkdir -p "$TARGET_DIR"
if [ -e "$TARGET_DIR/SKILL.md" ] && cmp -s "$SOURCE" "$TARGET_DIR/SKILL.md"; then
    echo "The switchit skill is already installed and up to date at $TARGET_DIR/SKILL.md"
    exit 0
fi
cp "$SOURCE" "$TARGET_DIR/SKILL.md"
echo "Installed the switchit skill to $TARGET_DIR/SKILL.md"
echo "Invoke it from any project with \$switchit."
