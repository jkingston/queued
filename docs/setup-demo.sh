#!/bin/bash
# Generate demo files and SSH keys for VHS recording

set -e

SCRIPT_DIR="$(cd "$(dirname "$0")" && pwd)"
cd "$SCRIPT_DIR"

echo "Generating SSH host keys..."
mkdir -p ssh-host-keys
[ -f ssh-host-keys/ssh_host_ed25519_key ] || ssh-keygen -t ed25519 -f ssh-host-keys/ssh_host_ed25519_key -N ""
[ -f ssh-host-keys/ssh_host_rsa_key ] || ssh-keygen -t rsa -f ssh-host-keys/ssh_host_rsa_key -N ""

echo "Generating demo user SSH key..."
mkdir -p ssh-user-keys
[ -f ssh-user-keys/demo_key ] || ssh-keygen -t ed25519 -f ssh-user-keys/demo_key -N "" -C "demo@localhost"

echo "Generating demo files..."
mkdir -p demo-files/{movies,music,docs}
[ -f demo-files/movies/movie1.mkv ] || dd if=/dev/urandom of=demo-files/movies/movie1.mkv bs=1M count=10 2>/dev/null
[ -f demo-files/movies/movie2.mkv ] || dd if=/dev/urandom of=demo-files/movies/movie2.mkv bs=1M count=15 2>/dev/null
[ -f demo-files/movies/movie3.mkv ] || dd if=/dev/urandom of=demo-files/movies/movie3.mkv bs=1M count=8 2>/dev/null
[ -f demo-files/music/album.flac ] || dd if=/dev/urandom of=demo-files/music/album.flac bs=1M count=5 2>/dev/null
[ -f demo-files/music/track.mp3 ] || dd if=/dev/urandom of=demo-files/music/track.mp3 bs=1M count=2 2>/dev/null
[ -f demo-files/docs/report.pdf ] || dd if=/dev/urandom of=demo-files/docs/report.pdf bs=1M count=1 2>/dev/null
[ -f demo-files/docs/data.csv ] || dd if=/dev/urandom of=demo-files/docs/data.csv bs=512K count=1 2>/dev/null

echo "Done! Run 'docker compose -f docs/docker-compose.demo.yml up -d' to start the demo server."
