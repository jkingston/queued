"""Async SFTP client wrapper using asyncssh."""

from __future__ import annotations

import asyncio
import logging
import shlex
from collections.abc import Callable
from datetime import datetime
from pathlib import Path
from typing import TYPE_CHECKING

import asyncssh

from queued.models import Host, RemoteFile

if TYPE_CHECKING:
    from queued.config import HostCache

logger = logging.getLogger(__name__)


class SFTPError(Exception):
    """SFTP operation error."""

    pass


def is_connection_error(error: Exception) -> bool:
    """Check if an exception indicates a dropped connection."""
    if isinstance(error, (ConnectionResetError, ConnectionAbortedError, BrokenPipeError)):
        return True
    if isinstance(error, OSError) and error.errno in (54, 32, 104):  # Connection reset codes
        return True
    if isinstance(error, asyncssh.Error):
        # Check for connection-related asyncssh errors
        error_str = str(error).lower()
        if any(x in error_str for x in ("connection", "disconnect", "closed")):
            return True
    return False


# Chunk size for manual reads (resume case) - increased from 32KB for better throughput
CHUNK_SIZE = 262144  # 256KB

# SFTP block size for asyncssh pipelining
SFTP_BLOCK_SIZE = 262144  # 256KB


class BandwidthLimiter:
    """Async bandwidth limiter using token bucket algorithm."""

    def __init__(self, limit: int | None = None):
        """Initialize limiter with bytes per second limit (None = unlimited)."""
        self.limit = limit
        self._last_check_time = asyncio.get_event_loop().time()
        self._bytes_since_check = 0

    async def throttle(self, bytes_transferred: int) -> None:
        """Throttle if necessary based on bytes transferred."""
        if not self.limit or self.limit <= 0:
            return

        self._bytes_since_check += bytes_transferred
        current_time = asyncio.get_event_loop().time()
        elapsed = current_time - self._last_check_time

        if elapsed > 0:
            current_rate = self._bytes_since_check / elapsed
            if current_rate > self.limit:
                sleep_time = (self._bytes_since_check / self.limit) - elapsed
                if sleep_time > 0:
                    await asyncio.sleep(sleep_time)
                self._bytes_since_check = 0
                self._last_check_time = asyncio.get_event_loop().time()


class SFTPClient:
    """Async SFTP client wrapper."""

    def __init__(self, host: Host):
        self.host = host
        self._conn: asyncssh.SSHClientConnection | None = None
        self._sftp: asyncssh.SFTPClient | None = None
        self._connected = False

    @property
    def connected(self) -> bool:
        """Check if connected."""
        return self._connected and self._conn is not None and self._sftp is not None

    async def connect(self) -> None:
        """Establish connection to the remote host."""
        logger.debug(
            "Connecting to %s@%s:%d", self.host.username, self.host.hostname, self.host.port
        )
        try:
            # Use system known_hosts file for host key verification
            known_hosts_path = Path.home() / ".ssh" / "known_hosts"
            known_hosts = str(known_hosts_path) if known_hosts_path.exists() else ()

            connect_kwargs = {
                "host": self.host.hostname,
                "port": self.host.port,
                "username": self.host.username,
                "known_hosts": known_hosts,
                # Performance optimizations
                "compression_algs": None,  # Disable compression (3.5x speedup)
                "encryption_algs": [
                    "aes128-gcm@openssh.com",
                    "aes256-gcm@openssh.com",
                    "aes128-ctr",
                    "aes256-ctr",
                ],
            }

            if self.host.key_path:
                connect_kwargs["client_keys"] = [self.host.key_path]
                logger.debug("Using SSH key: %s", self.host.key_path)
            elif self.host.password:
                connect_kwargs["password"] = self.host.password
                logger.debug("Using password authentication")

            self._conn = await asyncssh.connect(**connect_kwargs, connect_timeout=30)
            self._sftp = await self._conn.start_sftp_client()
            self._connected = True
            logger.info("Connected to %s", self.host.host_key)
        except asyncssh.HostKeyNotVerifiable as e:
            raise SFTPError(
                f"Host key verification failed: {e}. Add host to ~/.ssh/known_hosts"
            ) from e
        except asyncssh.Error as e:
            raise SFTPError(f"Failed to connect: {e}") from e
        except OSError as e:
            raise SFTPError(f"Connection error: {e}") from e

    async def disconnect(self) -> None:
        """Close the connection."""
        logger.debug("Disconnecting from %s", self.host.host_key)
        if self._sftp:
            self._sftp.exit()
            self._sftp = None
        if self._conn:
            self._conn.close()
            await self._conn.wait_closed()
            self._conn = None
        self._connected = False
        logger.info("Disconnected from %s", self.host.host_key)

    async def list_dir(self, path: str = ".") -> list[RemoteFile]:
        """List directory contents."""
        if not self._sftp:
            raise SFTPError("Not connected")

        logger.debug("Listing directory: %s", path)
        try:
            entries = await self._sftp.readdir(path)
            files = []
            for entry in entries:
                if entry.filename in (".", ".."):
                    continue
                attrs = entry.attrs
                mtime = None
                if attrs.mtime:
                    mtime = datetime.fromtimestamp(attrs.mtime)

                full_path = f"{path}/{entry.filename}" if path != "." else entry.filename
                perms = self._format_permissions(attrs.permissions) if attrs.permissions else None
                files.append(
                    RemoteFile(
                        name=entry.filename,
                        path=full_path,
                        size=attrs.size or 0,
                        is_dir=attrs.type == asyncssh.FILEXFER_TYPE_DIRECTORY,
                        mtime=mtime,
                        permissions=perms,
                    )
                )
            # Sort: directories first, then by name
            files.sort(key=lambda f: (not f.is_dir, f.name.lower()))
            return files
        except asyncssh.SFTPError as e:
            raise SFTPError(f"Failed to list directory: {e}") from e

    def _format_permissions(self, mode: int) -> str:
        """Format permissions as rwxrwxrwx string."""
        perms = ""
        for i in range(8, -1, -1):
            if mode & (1 << i):
                perms += "rwx"[2 - (i % 3)]
            else:
                perms += "-"
        return perms

    async def get_file_info(self, path: str) -> RemoteFile:
        """Get info for a single file."""
        if not self._sftp:
            raise SFTPError("Not connected")

        try:
            attrs = await self._sftp.stat(path)
            mtime = None
            if attrs.mtime:
                mtime = datetime.fromtimestamp(attrs.mtime)

            perms = self._format_permissions(attrs.permissions) if attrs.permissions else None
            return RemoteFile(
                name=Path(path).name,
                path=path,
                size=attrs.size or 0,
                is_dir=attrs.type == asyncssh.FILEXFER_TYPE_DIRECTORY,
                mtime=mtime,
                permissions=perms,
            )
        except asyncssh.SFTPError as e:
            raise SFTPError(f"Failed to get file info: {e}") from e

    async def download(
        self,
        remote_path: str,
        local_path: str,
        progress_callback: Callable[[int, int], None] | None = None,
        resume_offset: int = 0,
        bandwidth_limit: int | None = None,
    ) -> None:
        """
        Download a file from the remote server.

        Args:
            remote_path: Path to the remote file
            local_path: Path to save locally
            progress_callback: Callback(bytes_transferred, total_size)
            resume_offset: Byte offset to resume from
            bandwidth_limit: Max bytes per second (None = unlimited)
        """
        if not self._sftp:
            raise SFTPError("Not connected")

        try:
            # Get file size for progress tracking
            attrs = await self._sftp.stat(remote_path)
            total_size = attrs.size or 0

            # Ensure local directory exists
            Path(local_path).parent.mkdir(parents=True, exist_ok=True)

            if resume_offset > 0:
                # Resume: use manual reads with seeking
                await self._download_resume(
                    remote_path,
                    local_path,
                    total_size,
                    progress_callback,
                    resume_offset,
                    bandwidth_limit,
                )
            else:
                # Fresh download: use sftp.get() with pipelining (50-100x faster)
                await self._download_fast(remote_path, local_path, total_size, progress_callback)

        except asyncssh.SFTPError as e:
            raise SFTPError(f"Download failed: {e}") from e
        except OSError as e:
            raise SFTPError(f"Local file error: {e}") from e

    async def _download_fast(
        self,
        remote_path: str,
        local_path: str,
        total_size: int,
        progress_callback: Callable[[int, int], None] | None = None,
    ) -> None:
        """Fast download using sftp.get() with pipelining (128 parallel requests)."""

        def progress_handler(srcpath: bytes, dstpath: bytes, bytes_copied: int, total: int) -> None:
            if progress_callback:
                progress_callback(bytes_copied, total_size)

        await self._sftp.get(
            remote_path,
            local_path,
            progress_handler=progress_handler if progress_callback else None,
            block_size=SFTP_BLOCK_SIZE,
        )

    async def _download_resume(
        self,
        remote_path: str,
        local_path: str,
        total_size: int,
        progress_callback: Callable[[int, int], None] | None = None,
        resume_offset: int = 0,
        bandwidth_limit: int | None = None,
    ) -> None:
        """Resume download using manual reads with seeking."""
        limiter = BandwidthLimiter(bandwidth_limit)

        async with self._sftp.open(remote_path, "rb") as remote_file:
            await remote_file.seek(resume_offset)

            with open(local_path, "ab") as local_file:
                bytes_transferred = resume_offset

                while True:
                    chunk = await remote_file.read(CHUNK_SIZE)
                    if not chunk:
                        break

                    local_file.write(chunk)
                    bytes_transferred += len(chunk)

                    # Throttle if bandwidth limit is set
                    await limiter.throttle(len(chunk))

                    if progress_callback:
                        progress_callback(bytes_transferred, total_size)

    async def upload(
        self,
        local_path: str,
        remote_path: str,
        progress_callback: Callable[[int, int], None] | None = None,
        bandwidth_limit: int | None = None,
    ) -> None:
        """
        Upload a file to the remote server.

        Args:
            local_path: Path to local file
            remote_path: Path on remote server
            progress_callback: Callback(bytes_transferred, total_size)
            bandwidth_limit: Max bytes per second (None = unlimited)
        """
        if not self._sftp:
            raise SFTPError("Not connected")

        limiter = BandwidthLimiter(bandwidth_limit)

        try:
            local_file_path = Path(local_path)
            if not local_file_path.exists():
                raise SFTPError(f"Local file not found: {local_path}")

            total_size = local_file_path.stat().st_size

            async with self._sftp.open(remote_path, "wb") as remote_file:
                with open(local_path, "rb") as local_file:
                    bytes_transferred = 0

                    while True:
                        chunk = local_file.read(CHUNK_SIZE)
                        if not chunk:
                            break

                        await remote_file.write(chunk)
                        bytes_transferred += len(chunk)

                        # Throttle if bandwidth limit is set
                        await limiter.throttle(len(chunk))

                        if progress_callback:
                            progress_callback(bytes_transferred, total_size)

        except asyncssh.SFTPError as e:
            raise SFTPError(f"Upload failed: {e}") from e
        except OSError as e:
            raise SFTPError(f"Local file error: {e}") from e

    async def get_pwd(self) -> str:
        """Get current working directory."""
        if not self._sftp:
            raise SFTPError("Not connected")
        try:
            return await self._sftp.getcwd() or "/"
        except asyncssh.SFTPError:
            return "/"

    async def file_exists(self, path: str) -> bool:
        """Check if a file exists."""
        if not self._sftp:
            raise SFTPError("Not connected")
        try:
            await self._sftp.stat(path)
            return True
        except asyncssh.SFTPError:
            return False

    async def get_checksum(self, path: str, algorithm: str = "md5") -> str | None:
        """
        Try to get checksum using SFTP check-file extension.
        Returns None if not supported by server.
        """
        if not self._sftp:
            raise SFTPError("Not connected")

        # The check-file extension is not widely supported
        # This is a placeholder for when it is available
        return None

    async def compute_remote_md5(self, path: str) -> str | None:
        """
        Compute MD5 hash of remote file by running md5sum via SSH.

        Returns the MD5 hash string, or None if:
        - Not connected
        - md5sum command not available on remote
        - Command execution fails
        """
        if not self._conn:
            return None

        try:
            # Run md5sum command on remote server
            # Output format: "hash  filename" or "hash *filename"
            result = await self._conn.run(f"md5sum {shlex.quote(path)}", check=True, timeout=300)
            # Parse the hash from output (first field)
            output = result.stdout.strip()
            if output:
                return output.split()[0]
            return None
        except Exception as e:
            logger.debug("Failed to compute remote MD5 for %s: %s", path, e)
            return None

    async def read_file(self, path: str, max_size: int = 1024 * 1024) -> bytes:
        """Read a small file (for .sfv, .md5 files)."""
        if not self._sftp:
            raise SFTPError("Not connected")

        try:
            attrs = await self._sftp.stat(path)
            if attrs.size and attrs.size > max_size:
                raise SFTPError(f"File too large: {attrs.size} bytes")

            async with self._sftp.open(path, "rb") as f:
                return await f.read()
        except asyncssh.SFTPError as e:
            raise SFTPError(f"Failed to read file: {e}") from e


class SFTPConnectionPool:
    """Manages SFTP connections to multiple hosts."""

    def __init__(self, host_cache: HostCache):
        """
        Initialize connection pool.

        Args:
            host_cache: HostCache instance for looking up host credentials
        """

        self._connections: dict[str, SFTPClient] = {}
        self._host_cache = host_cache

    async def get_connection(self, host_key: str) -> SFTPClient:
        """
        Get or create an SFTP connection for a host.

        Args:
            host_key: Host identifier (user@hostname:port)

        Returns:
            Connected SFTPClient

        Raises:
            SFTPError: If host is unknown or connection fails
        """
        # Check for existing connected client
        if host_key in self._connections:
            client = self._connections[host_key]
            if client.connected:
                return client
            # Connection was lost, remove and reconnect
            del self._connections[host_key]

        # Look up host from cache
        host = self._host_cache.get_by_key(host_key)
        if not host:
            raise SFTPError(f"Unknown host: {host_key}")

        # Create and connect new client
        client = SFTPClient(host)
        await client.connect()
        self._connections[host_key] = client
        return client

    def add_connection(self, host_key: str, client: SFTPClient) -> None:
        """
        Add an existing connected client to the pool.

        Useful for adding the initial connection made during app startup.
        """
        if client.connected:
            self._connections[host_key] = client

    async def disconnect(self, host_key: str) -> None:
        """Disconnect a specific host."""
        if host_key in self._connections:
            client = self._connections.pop(host_key)
            await client.disconnect()

    async def disconnect_all(self) -> None:
        """Close all connections gracefully."""
        for client in self._connections.values():
            try:
                await client.disconnect()
            except Exception:
                pass  # Ignore errors during shutdown
        self._connections.clear()

    @property
    def connected_hosts(self) -> list[str]:
        """List of currently connected host keys."""
        return [k for k, c in self._connections.items() if c.connected]
