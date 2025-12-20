# Queued

[![CI](https://github.com/jkingston/queued/actions/workflows/ci.yml/badge.svg)](https://github.com/jkingston/queued/actions/workflows/ci.yml)
[![Python 3.11+](https://img.shields.io/badge/python-3.11+-blue.svg)](https://www.python.org/downloads/)
[![License: MIT](https://img.shields.io/badge/License-MIT-yellow.svg)](https://opensource.org/licenses/MIT)

A TUI SFTP download manager. Browse remote files, queue downloads, and watch transfer progress - all from your terminal.

## Features

- **Dual-pane interface** - Browse remote files on the left, manage transfers on the right
- **Download queue** - Queue multiple files with configurable concurrent transfers (1-5)
- **Resume support** - Interrupted downloads resume from where they left off
- **Integrity verification** - Automatic verification using `.sfv` or `.md5` files
- **Bandwidth limiting** - Cap download speed when needed
- **Recent hosts** - Quickly reconnect to previously used servers

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
| `Enter` | Open directory / Queue file for download |
| `Space` | Toggle file selection |
| `a` | Select all files |
| `Escape` | Clear selection |
| `Backspace` | Go to parent directory |
| `r` | Refresh directory listing |

### Transfers
| Key | Action |
|-----|--------|
| `p` | Pause/Resume transfer |
| `x` | Remove transfer |
| `↑/↓` | Move transfer in queue |

### General
| Key | Action |
|-----|--------|
| `Tab` | Switch between panes |
| `d` | Download selected files |
| `?` | Show help |
| `q` | Quit |

## Configuration

Settings are stored in `~/.config/queued/settings.json`:

```json
{
  "max_concurrent_transfers": 3,
  "bandwidth_limit": null,
  "download_dir": "~/Downloads",
  "auto_refresh_interval": 30,
  "verify_checksums": true,
  "resume_transfers": true
}
```

Recent hosts are cached in `~/.cache/queued/hosts.json` for quick reconnection.

## Development

```bash
git clone https://github.com/jkingston/queued
cd queued
uv sync
uv run pytest
uv run ruff check src/
```

## License

MIT
