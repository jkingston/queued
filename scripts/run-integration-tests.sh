#!/bin/bash
# Run Docker-based integration tests for queued
#
# Usage: ./scripts/run-integration-tests.sh
#
# This script:
# 1. Starts the SSH test server container
# 2. Waits for it to be ready
# 3. Runs integration tests
# 4. Cleans up the container

set -e

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
PROJECT_DIR="$(dirname "$SCRIPT_DIR")"
COMPOSE_FILE="$PROJECT_DIR/tests/docker/docker-compose.yml"

# Colors for output
RED='\033[0;31m'
GREEN='\033[0;32m'
YELLOW='\033[1;33m'
NC='\033[0m' # No Color

cleanup() {
    echo -e "${YELLOW}Cleaning up...${NC}"
    docker compose -f "$COMPOSE_FILE" down 2>/dev/null || true
}

# Always cleanup on exit
trap cleanup EXIT

echo -e "${YELLOW}Starting SSH test server...${NC}"
docker compose -f "$COMPOSE_FILE" up -d

# Wait for SSH to be ready (up to 30 seconds)
echo -e "${YELLOW}Waiting for SSH server to be ready...${NC}"
for i in {1..30}; do
    if docker compose -f "$COMPOSE_FILE" exec -T ssh-server nc -z localhost 2222 2>/dev/null; then
        echo -e "${GREEN}SSH server is ready!${NC}"
        break
    fi
    if [ $i -eq 30 ]; then
        echo -e "${RED}SSH server failed to start${NC}"
        exit 1
    fi
    sleep 1
done

# Additional wait for SSH to fully initialize
sleep 2

echo -e "${YELLOW}Running integration tests...${NC}"
cd "$PROJECT_DIR"
uv run pytest tests/test_sftp_integration.py -m integration -v

echo -e "${GREEN}Integration tests complete!${NC}"
