#!/usr/bin/env bash
# Converts FBX, glTF, GLB and OBJ models to tidy .glb files (see CONTRIBUTING.md, "Getting a model
# into shape").
#
#   tools/convert.sh <file-or-folder>... --out <folder>
#
# Godot changes its working directory to the project it runs, so this script makes every path
# absolute first. Set GODOT to the Godot 4.7 binary if it is not on your PATH as `godot` (on macOS
# /Applications/Godot.app/Contents/MacOS/Godot is tried).
set -euo pipefail

here="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
godot="${GODOT:-}"
if [ -z "$godot" ]; then
  if command -v godot >/dev/null 2>&1; then
    godot=godot
  elif [ -x /Applications/Godot.app/Contents/MacOS/Godot ]; then
    godot=/Applications/Godot.app/Contents/MacOS/Godot
  else
    echo "Godot 4.7 not found: put it on PATH as 'godot' or set GODOT=<binary>" >&2
    exit 2
  fi
fi

absolute() {
  case "$1" in
    /*) printf '%s' "$1" ;;
    *) printf '%s/%s' "$PWD" "${1#./}" ;;
  esac
}

args=()
while [ $# -gt 0 ]; do
  case "$1" in
    --out)
      [ $# -ge 2 ] || { echo "--out needs a folder" >&2; exit 2; }
      args+=(--out "$(absolute "$2")")
      shift 2
      ;;
    --out=*)
      args+=("--out=$(absolute "${1#--out=}")")
      shift
      ;;
    --*)
      args+=("$1")
      shift
      ;;
    *)
      args+=("$(absolute "$1")")
      shift
      ;;
  esac
done

exec "$godot" --headless --path "$here/convert" --script res://convert.gd -- ${args[@]+"${args[@]}"}
