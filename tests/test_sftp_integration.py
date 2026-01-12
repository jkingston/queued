"""Docker-based integration tests for SFTP operations.

These tests require Docker and are skipped if Docker is not available.
Run with: pytest tests/test_sftp_integration.py -m integration
"""

import subprocess
import tempfile
import time
from pathlib import Path

import pytest

from queued.models import Host
from queued.sftp import SFTPClient
from queued.transfer import verify_file


def docker_available() -> bool:
    """Check if Docker is available and running."""
    try:
        result = subprocess.run(
            ["docker", "info"],
            capture_output=True,
            timeout=5,
        )
        return result.returncode == 0
    except (subprocess.TimeoutExpired, FileNotFoundError, OSError):
        return False


def docker_compose_available() -> bool:
    """Check if docker-compose is available."""
    try:
        result = subprocess.run(
            ["docker-compose", "--version"],
            capture_output=True,
            timeout=5,
        )
        return result.returncode == 0
    except (subprocess.TimeoutExpired, FileNotFoundError, OSError):
        # Try docker compose (v2)
        try:
            result = subprocess.run(
                ["docker", "compose", "version"],
                capture_output=True,
                timeout=5,
            )
            return result.returncode == 0
        except (subprocess.TimeoutExpired, FileNotFoundError, OSError):
            return False


def _get_compose_command() -> list[str] | None:
    """Get the working docker-compose command."""
    try:
        result = subprocess.run(
            ["docker-compose", "--version"],
            capture_output=True,
            timeout=5,
        )
        if result.returncode == 0:
            return ["docker-compose"]
    except (subprocess.TimeoutExpired, FileNotFoundError, OSError):
        pass

    try:
        result = subprocess.run(
            ["docker", "compose", "version"],
            capture_output=True,
            timeout=5,
        )
        if result.returncode == 0:
            return ["docker", "compose"]
    except (subprocess.TimeoutExpired, FileNotFoundError, OSError):
        pass

    return None


# Known test file MD5 (created in tests/docker/test-files/testfile.txt)
KNOWN_TEST_FILE_MD5 = "3f3400c4480aa0db42ec8b69fb2bbef5"
DOCKER_COMPOSE_DIR = Path(__file__).parent / "docker"


@pytest.fixture(scope="module")
def ssh_server():
    """Start SSH server container for integration tests."""
    compose_cmd = _get_compose_command()
    if not docker_available() or compose_cmd is None:
        pytest.skip("Docker or docker-compose not available")

    compose_file = DOCKER_COMPOSE_DIR / "docker-compose.yml"
    if not compose_file.exists():
        pytest.skip("docker-compose.yml not found")

    # Start the container
    subprocess.run(
        [*compose_cmd, "-f", str(compose_file), "up", "-d"],
        cwd=str(DOCKER_COMPOSE_DIR),
        check=True,
        capture_output=True,
    )

    # Wait for container to be ready
    for _ in range(30):
        try:
            result = subprocess.run(
                [*compose_cmd, "-f", str(compose_file), "ps", "--quiet"],
                cwd=str(DOCKER_COMPOSE_DIR),
                capture_output=True,
                text=True,
            )
            if result.stdout.strip():
                break
        except subprocess.CalledProcessError:
            pass
        time.sleep(1)
    else:
        pytest.skip("SSH container failed to start")

    # Additional wait for SSH to be ready
    time.sleep(3)

    yield {
        "host": "localhost",
        "port": 2222,
        "username": "testuser",
        "password": "testpass",
    }

    # Cleanup
    subprocess.run(
        [*compose_cmd, "-f", str(compose_file), "down"],
        cwd=str(DOCKER_COMPOSE_DIR),
        capture_output=True,
    )


@pytest.fixture
async def real_sftp(ssh_server):
    """Real SFTP client connected to Docker SSH server."""
    from queued.sftp import SFTPError

    host = Host(
        hostname=ssh_server["host"],
        port=ssh_server["port"],
        username=ssh_server["username"],
        password=ssh_server["password"],
    )
    client = SFTPClient(host)

    # asyncssh requires known_hosts handling for localhost
    # We'll patch the connect to accept any host key for testing
    # Also disable client_keys to prevent "too many auth failures" from SSH agent keys
    import asyncssh

    original_connect = asyncssh.connect

    async def patched_connect(**kwargs):
        kwargs["known_hosts"] = None  # Accept any host key for testing
        kwargs["client_keys"] = []  # Don't use any SSH keys
        kwargs["agent_path"] = None  # Don't use SSH agent
        return await original_connect(**kwargs)

    asyncssh.connect = patched_connect

    try:
        await client.connect()
        yield client
    except SFTPError as e:
        pytest.skip(f"Could not connect to SSH server: {e}")
    finally:
        if client.connected:
            await client.disconnect()
        asyncssh.connect = original_connect


@pytest.mark.integration
@pytest.mark.skipif(
    not docker_available() or not docker_compose_available(),
    reason="Docker not available",
)
class TestRealSSH:
    """Integration tests using real SSH server."""

    @pytest.mark.asyncio
    async def test_compute_remote_md5_real(self, real_sftp):
        """Test MD5 computation on real SSH server."""
        result = await real_sftp.compute_remote_md5("/home/testuser/files/testfile.txt")
        assert result == KNOWN_TEST_FILE_MD5

    @pytest.mark.asyncio
    async def test_compute_remote_md5_nonexistent_file(self, real_sftp):
        """Test MD5 computation on nonexistent file returns None."""
        result = await real_sftp.compute_remote_md5("/home/testuser/files/nonexistent.txt")
        assert result is None

    @pytest.mark.asyncio
    async def test_list_dir_real(self, real_sftp):
        """Test directory listing on real SSH server."""
        files = await real_sftp.list_dir("/home/testuser/files")
        filenames = [f.name for f in files]
        assert "testfile.txt" in filenames

    @pytest.mark.asyncio
    async def test_download_real(self, real_sftp):
        """Test file download from real SSH server."""
        with tempfile.TemporaryDirectory() as tmpdir:
            local_path = Path(tmpdir) / "downloaded.txt"
            await real_sftp.download(
                "/home/testuser/files/testfile.txt",
                str(local_path),
            )

            assert local_path.exists()
            assert local_path.read_text() == "test content for verification"

    @pytest.mark.asyncio
    async def test_verify_file_real(self, real_sftp):
        """Test full verification flow on real SSH server."""
        with tempfile.TemporaryDirectory() as tmpdir:
            # Download the file first
            local_path = Path(tmpdir) / "testfile.txt"
            await real_sftp.download(
                "/home/testuser/files/testfile.txt",
                str(local_path),
            )

            # Verify the downloaded file
            success, message = await verify_file(
                "/home/testuser/files/testfile.txt",
                str(local_path),
                local_path.stat().st_size,
                real_sftp,
            )

            assert success is True
            assert "MD5 match" in message

    @pytest.mark.asyncio
    async def test_verify_file_real_mismatch(self, real_sftp):
        """Test verification failure with modified local file."""
        with tempfile.TemporaryDirectory() as tmpdir:
            # Create a file with different content
            local_path = Path(tmpdir) / "testfile.txt"
            local_path.write_text("different content")

            # Verify should fail
            success, message = await verify_file(
                "/home/testuser/files/testfile.txt",
                str(local_path),
                len("test content for verification"),
                real_sftp,
            )

            assert success is False
            assert "mismatch" in message.lower()
