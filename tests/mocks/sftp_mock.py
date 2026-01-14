"""Mock SFTP client for testing without network."""

from collections.abc import Callable
from datetime import datetime
from typing import Optional

from queued.models import Host, RemoteFile


class MockSFTPClient:
    """Mock SFTP client for testing without network connections."""

    def __init__(
        self,
        files: Optional[list[RemoteFile]] = None,
        remote_md5_results: Optional[dict[str, str]] = None,
        md5_available: bool = True,
        file_contents: Optional[dict[str, bytes]] = None,
        fail_on_md5: bool = False,
        fail_on_list_dir: bool = False,
    ):
        """Initialize with mock file listing.

        Args:
            files: List of RemoteFile objects to return from list_dir
            remote_md5_results: Dict mapping paths to MD5 hashes for compute_remote_md5
            md5_available: If False, compute_remote_md5 returns None (simulates missing md5sum)
            file_contents: Dict mapping paths to file contents for read_file
            fail_on_md5: If True, compute_remote_md5 raises exception (simulates connection drop)
            fail_on_list_dir: If True, list_dir raises ConnectionResetError
        """
        self._files = files or []
        self._connected = False
        self._current_dir = "/"
        self._remote_md5_results = remote_md5_results or {}
        self._md5_available = md5_available
        self._file_contents = file_contents or {}
        self._fail_on_md5 = fail_on_md5
        self._fail_on_list_dir = fail_on_list_dir
        # Track calls for assertions
        self.connect_calls = 0
        self.disconnect_calls = 0
        self.list_dir_calls: list[str] = []
        self.compute_remote_md5_calls: list[str] = []
        self.read_file_calls: list[str] = []

    @property
    def connected(self) -> bool:
        return self._connected

    async def connect(self) -> None:
        self.connect_calls += 1
        self._connected = True

    async def disconnect(self) -> None:
        self.disconnect_calls += 1
        self._connected = False

    async def list_dir(self, path: str = ".") -> list[RemoteFile]:
        self.list_dir_calls.append(path)
        if self._fail_on_list_dir:
            raise ConnectionResetError(54, "Connection reset by peer")
        # Filter files by parent path
        if path == "/" or path == ".":
            return [f for f in self._files if "/" not in f.path.lstrip("/")]
        # Return files under this path
        result = []
        for f in self._files:
            parent = "/".join(f.path.rsplit("/", 1)[:-1]) or "/"
            if parent == path:
                result.append(f)
        return result

    async def get_file_info(self, path: str) -> RemoteFile:
        for f in self._files:
            if f.path == path:
                return f
        raise Exception(f"File not found: {path}")

    async def download(
        self,
        remote_path: str,
        local_path: str,
        progress_callback: Optional[Callable[[int, int], None]] = None,
        resume_offset: int = 0,
        bandwidth_limit: Optional[int] = None,
    ) -> None:
        # Simulate download progress
        for f in self._files:
            if f.path == remote_path:
                if progress_callback:
                    progress_callback(f.size, f.size)
                return
        raise Exception(f"File not found: {remote_path}")

    async def get_pwd(self) -> str:
        return self._current_dir

    async def file_exists(self, path: str) -> bool:
        return any(f.path == path for f in self._files)

    async def compute_remote_md5(self, path: str) -> Optional[str]:
        """Return configured MD5 hash or None if md5sum unavailable."""
        self.compute_remote_md5_calls.append(path)
        if self._fail_on_md5:
            raise Exception("Connection lost")
        if not self._md5_available:
            return None
        return self._remote_md5_results.get(path)

    async def read_file(self, path: str, max_size: int = 1024 * 1024) -> bytes:
        """Return configured file contents."""
        self.read_file_calls.append(path)
        if path in self._file_contents:
            return self._file_contents[path]
        raise Exception(f"File not found: {path}")


def create_mock_files() -> list[RemoteFile]:
    """Create a standard set of mock files for testing."""
    return [
        RemoteFile(
            name="file1.txt",
            path="/file1.txt",
            size=1024,
            is_dir=False,
            mtime=datetime(2024, 1, 15, 10, 30),
        ),
        RemoteFile(
            name="file2.txt",
            path="/file2.txt",
            size=2048,
            is_dir=False,
            mtime=datetime(2024, 1, 16, 14, 45),
        ),
        RemoteFile(
            name="largefile.bin",
            path="/largefile.bin",
            size=1024 * 1024 * 100,  # 100MB
            is_dir=False,
            mtime=datetime(2024, 1, 17, 9, 0),
        ),
        RemoteFile(
            name="subdir",
            path="/subdir",
            size=0,
            is_dir=True,
            mtime=datetime(2024, 1, 10, 8, 0),
        ),
        RemoteFile(
            name="nested.txt",
            path="/subdir/nested.txt",
            size=512,
            is_dir=False,
            mtime=datetime(2024, 1, 11, 12, 0),
        ),
    ]


def create_mock_host() -> Host:
    """Create a mock host for testing."""
    return Host(
        hostname="test.example.com",
        username="testuser",
        port=22,
    )
