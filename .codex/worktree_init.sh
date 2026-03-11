#!/usr/bin/env bash
set -euo pipefail

script_dir="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
repo_root="$(cd "$script_dir/.." && pwd)"
venv_dir="${OUROBOROS_VENV_DIR:-$repo_root/.venv}"
python_bin="${PYTHON_BIN:-python3}"
dry_run=false
skip_install=false

while (($#)); do
  case "$1" in
    --dry-run)
      dry_run=true
      ;;
    --skip-install)
      skip_install=true
      ;;
    *)
      echo "Unknown argument: $1" >&2
      exit 2
      ;;
  esac
  shift
done

run() {
  echo "+ $*"
  if ! $dry_run; then
    "$@"
  fi
}

venv_has_bootstrap_prereqs() {
  "$1" - <<'PY' >/dev/null 2>&1
import importlib.util
import sys

required = ("setuptools", "wheel")
sys.exit(0 if all(importlib.util.find_spec(name) for name in required) else 1)
PY
}

if ! command -v "$python_bin" >/dev/null 2>&1; then
  echo "Missing required interpreter: $python_bin" >&2
  exit 1
fi

echo "repo_root=$repo_root"
echo "venv_dir=$venv_dir"
if $dry_run; then
  echo "mode=dry-run"
  run "$python_bin" -m venv --system-site-packages "$venv_dir"
  if ! $skip_install; then
    run "$venv_dir/bin/python" -m pip install --no-build-isolation -e ".[dev]"
  else
    echo "Skipping editable install"
  fi
  if [ -f ".env.example" ]; then
    run cp .env.example .env
  fi
  cat <<'EOF'

Next steps:
1. Fill secrets in .env as needed.
2. Append workflow/session-handover.md for the active branch.
3. Run: python3 scripts/session_handover_check.py --strict
4. Run a targeted test for the area you plan to change.
EOF
  exit 0
fi

cd "$repo_root"

venv_python="$venv_dir/bin/python"
rebuild_venv=false
rebuild_reason=""

if [ ! -d "$venv_dir" ]; then
  run "$python_bin" -m venv --system-site-packages "$venv_dir"
elif [ ! -f "$venv_dir/pyvenv.cfg" ] || ! grep -q '^include-system-site-packages = true$' "$venv_dir/pyvenv.cfg"; then
  rebuild_venv=true
  rebuild_reason="missing system site packages"
elif ! venv_has_bootstrap_prereqs "$venv_python"; then
  rebuild_venv=true
  rebuild_reason="missing setuptools/wheel bootstrap prerequisites"
else
  echo "Using existing virtualenv at $venv_dir"
fi

if $rebuild_venv; then
  echo "Rebuilding virtualenv at $venv_dir ($rebuild_reason)"
  run rm -rf "$venv_dir"
  run "$python_bin" -m venv --system-site-packages "$venv_dir"
fi

if ! $skip_install; then
  run "$venv_python" -m pip install --no-build-isolation -e ".[dev]"
else
  echo "Skipping editable install"
fi

if [ ! -f ".env" ] && [ -f ".env.example" ]; then
  run cp .env.example .env
elif [ -f ".env" ]; then
  echo "Keeping existing .env"
fi

cat <<'EOF'

Next steps:
1. Fill secrets in .env as needed.
2. Append workflow/session-handover.md for the active branch.
3. Run: python3 scripts/session_handover_check.py --strict
4. Run a targeted test for the area you plan to change.
EOF
