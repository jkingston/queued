# VHS tape file for recording queued demo GIF
#
# Prerequisites:
#   ./docs/setup-demo.sh
#   docker compose -f docs/docker-compose.demo.yml up -d
#   ssh-keyscan -p 2222 localhost >> ~/.ssh/known_hosts
#
# Run with: vhs docs/demo.tape

Output docs/demo.gif

Set Shell bash
Set FontSize 18
Set Width 1400
Set Height 800
Set Theme "Catppuccin Mocha"
Set TypingSpeed 80ms
Set Padding 20

# Add host key to known_hosts (hidden from GIF)
Hide
Type "ssh-keyscan -p 2222 localhost >> ~/.ssh/known_hosts 2>/dev/null"
Enter
Sleep 1s
Type "clear"
Enter
Sleep 500ms
Show

# Launch queued (server must already be running)
Type "uv run queued demo@localhost -p 2222 -i docs/ssh-user-keys/demo_key"
Enter
Sleep 4s

# Navigate to files directory
Type "j"
Sleep 300ms
Type "l"
Sleep 1s

# Navigate into movies directory
Type "j"
Sleep 300ms
Type "j"
Sleep 300ms
Type "l"
Sleep 1s

# Select all movie files
Type "a"
Sleep 500ms

# Queue for download
Type "d"
Sleep 1s

# Switch to transfers pane
Type "Tab"
Sleep 500ms

# Show progress
Sleep 3s

# Pause/resume a transfer
Type "p"
Sleep 1s
Type "p"
Sleep 1s

# Switch back, go up, show help
Type "Tab"
Sleep 500ms
Type "h"
Sleep 500ms
Type "?"
Sleep 2s

# Close help and quit
Type "Escape"
Sleep 500ms
Type "q"
Sleep 1s
