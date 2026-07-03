# Homebrew formula for Coldframe (a local-first, ownable memory layer for LLM agents).
#
# This is the TAP formula: `brew install coldzero94/coldframe/cold-frame`. It installs the
# self-contained binary built by the Release workflow (CLI + MCP server in one file — no Python,
# no dependency resolution, works offline). NOT distributed via PyPI.
#
# RELEASE: after the tag's Release workflow attaches the per-platform binaries, fill the three
# sha256 values below (`shasum -a 256 cold-frame-<target>`), then copy this file into the tap repo.
class ColdFrame < Formula
  desc "Local-first ownable memory layer for LLM agents (one offline SQLite file)"
  homepage "https://github.com/coldzero94/cold-frame"
  version "0.1.0"
  license "Apache-2.0"

  on_macos do
    on_arm do
      url "https://github.com/coldzero94/cold-frame/releases/download/v0.1.0/cold-frame-macos-arm64"
      sha256 "0000000000000000000000000000000000000000000000000000000000000000" # RELEASE: arm64
    end
    on_intel do
      url "https://github.com/coldzero94/cold-frame/releases/download/v0.1.0/cold-frame-macos-x86_64"
      sha256 "0000000000000000000000000000000000000000000000000000000000000000" # RELEASE: x86_64
    end
  end
  on_linux do
    url "https://github.com/coldzero94/cold-frame/releases/download/v0.1.0/cold-frame-linux-x86_64"
    sha256 "0000000000000000000000000000000000000000000000000000000000000000" # RELEASE: linux
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
