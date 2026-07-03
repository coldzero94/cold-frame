# Coldframe terminal demo → assets/demo.gif
#
# Regenerate the animated GIF (records the REAL CLI, so the output is always true):
#   brew install charmbracelet/tap/vhs      # one-time (https://github.com/charmbracelet/vhs)
#   vhs packaging/demo/demo.tape            # needs `cold-frame` on PATH
#
# The static assets/demo.svg (assets/gen_demo.py) mirrors this same session for the README.

Output assets/demo.gif
Require cold-frame

Set Shell bash
Set FontSize 22
Set Width 1200
Set Height 760
Set Padding 26
Set TypingSpeed 45ms

# isolated throwaway DB — the demo never touches your real ~/.cold-frame memory
Hide
Type 'export COLD_FRAME_DB="$(mktemp -d)/memory.db"' Enter
Ctrl+L
Show

Sleep 500ms
Type 'cold-frame add "I prefer dark roast coffee, no sugar"' Sleep 300ms Enter
Sleep 800ms
Type 'cold-frame add "Deploy script is ship.sh now (was deploy.sh)"' Sleep 300ms Enter
Sleep 1s

Type '# a new session, days later — your agent still knows:' Enter
Sleep 500ms
Type 'cold-frame search "dark roast coffee"' Sleep 300ms Enter
Sleep 1400ms

Type '# it tracks belief strength and forgets the weak — see the bands:' Enter
Sleep 500ms
Type 'cold-frame list' Sleep 300ms Enter
Sleep 1600ms

Type '# one file you own — offline, no key, integrity-checked:' Enter
Sleep 500ms
Type 'cold-frame doctor' Sleep 300ms Enter
Sleep 3s
