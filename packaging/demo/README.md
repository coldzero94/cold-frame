# Demo assets

Two renderings of the same real, offline Coldframe session (`add` → `search` recall → `list`
strength bands → `doctor`):

- **`../../assets/demo.svg`** — a static terminal card, embedded in the README. Regenerate with
  `python3 assets/gen_demo.py` (stdlib only). Faithful to actual CLI output.
- **`../../assets/demo.gif`** — the animated version, recorded from the live CLI so its output is
  always true. Not committed by default (it's large); generate it when you want motion:

  ```bash
  brew install charmbracelet/tap/vhs     # one-time — https://github.com/charmbracelet/vhs
  vhs packaging/demo/demo.tape           # needs `cold-frame` on PATH → writes assets/demo.gif
  ```

  Then point the README's demo `<img>` at `assets/demo.gif` instead of `assets/demo.svg`.

`demo.tape` uses a throwaway `$COLD_FRAME_DB` (a temp dir), so recording never touches your real
`~/.cold-frame/memory.db`.
