# Homebrew formula for Coldframe (a local-first, ownable memory layer for LLM agents).
#
# This is the TAP formula: `brew install coldzero94/coldframe/cold-frame`. It installs the
# self-contained binary built by the Release workflow (CLI + MCP server in one file — no Python,
# no dependency resolution, works offline). NOT distributed via PyPI.
#
# RELEASE: the tap copy (coldzero94/homebrew-coldframe) is REGENERATED AUTOMATICALLY on every tag by
# the Release workflow's `bump-tap` job (packaging/homebrew/bump-tap.sh rewrites version + urls +
# sha256 and pushes) — no manual step. This in-repo copy is a human-readable reference/fallback; if
# you ever bump it by hand, fill the sha256 values with `shasum -a 256 cold-frame-<target>`.
class ColdFrame < Formula
  desc "Local-first ownable memory layer for LLM agents (one offline SQLite file)"
  homepage "https://github.com/coldzero94/cold-frame"
  version "0.1.0"
  license "Apache-2.0"

  # macOS = Apple Silicon only (Intel Macs are out of scope for v1).
  on_macos do
    on_arm do
      url "https://github.com/coldzero94/cold-frame/releases/download/v0.1.0/cold-frame-macos-arm64"
      sha256 "744901491f18dd1c2f712510214d8cc93472ea6aedbfaeebe285cd6f62fd8bdd"
    end
  end
  on_linux do
    url "https://github.com/coldzero94/cold-frame/releases/download/v0.1.0/cold-frame-linux-x86_64"
    sha256 "db65d271afdf45dec797e17e63f1829141e423125bac3148f869edaad95a83f9"
  end

  def install
    # the downloaded single-file asset is named cold-frame-<target>; install it as `cold-frame`.
    binary = Dir["cold-frame-*"].first
    bin.install binary => "cold-frame"
    chmod 0755, bin/"cold-frame"
  end

  test do
    assert_match "0.1.0", shell_output("#{bin}/cold-frame --version")
  end

  def caveats
    <<~EOS
      Turn on AUTOMATIC memory in Claude Code (recall every session, capture as you work):
        cold-frame hook install
        claude mcp add cold-frame -- cold-frame mcp

      Your memory lives in ~/.cold-frame/memory.db — one file, yours, offline. Browse it:
        cold-frame ui
    EOS
  end
end
