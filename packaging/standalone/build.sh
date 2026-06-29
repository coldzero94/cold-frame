#!/usr/bin/env bash
# Build a self-contained `cold-frame` binary (no Python needed at runtime) via PyInstaller.
#
# The binary bundles the [mcp] extra, so BOTH the CLI and the MCP server work from one file:
#   ./cold-frame add "..."          # offline CLI
#   claude mcp add cold-frame -- /path/to/cold-frame mcp   # the auto-capture drain + tools
#
# PyInstaller freezes for the HOST os/arch only — run this once per target (macOS arm64, macOS
# x86_64, Linux x86_64) in CI to populate a GitHub Release. Verified flags (the mcp deps are pulled
# via targeted hidden-imports; mcp's optional `mcp.cli` is excluded — it sys.exit(1)s without typer).
set -euo pipefail

ROOT="$(cd "$(dirname "$0")/../.." && pwd)"
HERE="$ROOT/packaging/standalone"
OUT="${1:-$ROOT/dist-bin}"          # where the binary lands
BUILD="$(mktemp -d)"
trap 'rm -rf "$BUILD"' EXIT

echo ">> building isolated env (cold-frame[mcp] + pyinstaller)"
python3 -m venv "$BUILD/venv"
"$BUILD/venv/bin/pip" install --quiet --upgrade pip
"$BUILD/venv/bin/pip" install --quiet "$ROOT[mcp]" pyinstaller

echo ">> freezing the binary → $OUT/cold-frame"
"$BUILD/venv/bin/pyinstaller" --onefile --name cold-frame --clean --noconfirm \
  --icon "$HERE/coldframe.icns" \
  --distpath "$OUT" --workpath "$BUILD/work" --specpath "$BUILD" \
  --collect-submodules cold_frame \
  --hidden-import mcp.server.fastmcp --hidden-import mcp.types \
  --hidden-import anyio.from_thread --hidden-import anyio.to_thread \
  --copy-metadata mcp \
  --exclude-module mcp.cli --exclude-module typer --exclude-module rich \
  "$HERE/launcher.py"

echo ">> smoke test (offline, isolated HOME)"
SMOKE="$(mktemp -d)"
COLD_FRAME_DB="$SMOKE/m.db" "$OUT/cold-frame" --version
COLD_FRAME_DB="$SMOKE/m.db" "$OUT/cold-frame" add "I prefer dark roast coffee" >/dev/null
COLD_FRAME_DB="$SMOKE/m.db" "$OUT/cold-frame" search "coffee" | grep -q coffee \
  && echo ">> OK: $OUT/cold-frame is self-contained" || { echo "!! smoke failed"; exit 1; }
rm -rf "$SMOKE"
