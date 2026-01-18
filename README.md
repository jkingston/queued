# Queued

[![CI](https://github.com/jkingston/queued/actions/workflows/ci.yml/badge.svg)](https://github.com/jkingston/queued/actions/workflows/ci.yml)
[![Python 3.11+](https://img.shields.io/badge/python-3.11+-blue.svg)](https://www.python.org/downloads/)
[![License: MIT](https://img.shields.io/badge/License-MIT-yellow.svg)](https://opensource.org/licenses/MIT)

![Queued Demo](docs/demo.gif)

A TUI SFTP download manager. Browse remote files, queue downloads, and watch transfer progress - all from your terminal.

## Features

- **Dual-pane interface** - Browse remote files on the left, manage transfers on the right
- **Download queue** - Queue multiple files with configurable concurrent transfers (1-50)
- **Resume support** - Interrupted downloads resume from where they left off
- **Integrity verification** - Automatic verification using `.sfv` or `.md5` files
- **Auto-reconnect** - Automatically reconnects if connection is lost
- **Recent hosts** - Quickly reconnect to previously used servers
- **Session persistence** - Queue state preserved across app restarts

## Installation

### With uvx (recommended)

```bash
# Run directly from GitHub
uvx --from git+https://github.com/jkingston/queued queued user@server.example.com

# Or install globally
uv tool install git+https://github.com/jkingston/queued
```

### With Nix

```bash
# Run directly
nix run github:jkingston/queued -- user@server.example.com

# Install to profile
nix profile install github:jkingston/queued
```

### From source

```bash
git clone https://github.com/jkingston/queued
cd queued
uv sync
uv run queued
```

## Usage

```bash
# Connect directly
queued user@server.example.com

# With custom port and SSH key
queued user@host -p 2222 -i ~/.ssh/server_key

# Interactive mode (prompts for connection)
queued
```

## Keybindings

### File Browser
| Key | Action |
|-----|--------|
| `Enter` / `l` | Open directory / Queue file for download |
| `Backspace` / `h` | Go to parent directory |
| `↑` / `k` | Move cursor up |
| `↓` / `j` | Move cursor down |
| `Space` | Toggle file selection |
| `a` | Select all files |
| `Escape` | Clear selection |
| `r` | Refresh directory listing |
| `d` | Download cursor/selected (recursive for dirs) |
| `Ctrl+f` | Page down |
| `Ctrl+b` | Page up |

### Transfers
| Key | Action |
|-----|--------|
| `Enter` / `p` | Pause/Resume selected transfer |
| `Space` | Stop/Resume all downloads |
| `x` / `Delete` | Remove transfer |
| `↑` / `k` | Move cursor up |
| `↓` / `j` | Move cursor down |
| `Shift+↑` / `K` | Move transfer up in queue |
| `Shift+↓` / `J` | Move transfer down in queue |

### General
| Key | Action |
|-----|--------|
| `Tab` | Switch between panes |
| `?` / `F1` | Show help |
| `s` | Settings |
| `q` | Quit |

## Configuration

Settings are stored in `~/.config/queued/settings.json`:

```json
{
  "max_concurrent_transfers": 10,
  "download_dir": "~/Downloads",
  "auto_refresh_interval": 30,
  "verify_checksums": true,
  "resume_transfers": true
}
```

Recent hosts are cached in `~/.cache/queued/hosts.json` for quick reconnection.

## Troubleshooting

### Washed-out colors over SSH

If colors appear washed out when running over SSH (especially with modern terminals like Ghostty), ensure `COLORTERM` is set on the remote host:

```bash
# Add to your remote .bashrc or .zshrc
export COLORTERM=truecolor
```

Or pass it when connecting:
```bash
ssh -o SendEnv=COLORTERM user@host
```

Note: The remote SSH server must be configured to accept `COLORTERM` via `AcceptEnv` in `/etc/ssh/sshd_config`.

## Development

```bash
git clone https://github.com/jkingston/queued
cd queued
uv sync
uv run pytest
uv run ruff check src/
```

### Recording Demo GIF

Requires: [VHS](https://github.com/charmbracelet/vhs), Docker

```bash
# Setup demo environment (one-time)
./docs/setup-demo.sh

# Start demo server
docker compose -f docs/docker-compose.demo.yml up -d

# Add host key (first time only)
ssh-keyscan -p 2222 localhost >> ~/.ssh/known_hosts

# Record GIF
vhs docs/demo.tape

# Stop demo server
docker compose -f docs/docker-compose.demo.yml down
```

## License

MIT
