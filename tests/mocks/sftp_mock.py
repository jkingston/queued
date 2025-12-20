"""Mock SFTP client for testing without network."""

from datetime import datetime
from typing import Callable, Optional

from queued.models import Host, RemoteFile


class MockSFTPClient:
    """Mock SFTP client for testing without network connections."""

    def __init__(self, files: Optional[list[RemoteFile]] = None):
        """Initialize with mock file listing.

        Args:
            files: List of RemoteFile objects to return from list_dir
        """
        self._files = files or []
        self._connected = False
        self._current_dir = "/"
        # Track calls for assertions
        self.connect_calls = 0
        self.disconnect_calls = 0
        self.list_dir_calls: list[str] = []

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
