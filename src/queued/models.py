"""Data models for Queued."""

from dataclasses import dataclass, field
from datetime import datetime
from enum import Enum
from pathlib import Path


class TransferDirection(Enum):
    """Transfer direction."""

    DOWNLOAD = "download"
    UPLOAD = "upload"


class TransferStatus(Enum):
    """Transfer status states."""

    QUEUED = "queued"
    CONNECTING = "connecting"
    TRANSFERRING = "transferring"
    PAUSED = "paused"
    STOPPED = "stopped"  # Queue was stopped while transfer was active
    COMPLETED = "completed"
    FAILED = "failed"
    VERIFYING = "verifying"


@dataclass
class Host:
    """Remote host connection info."""

    hostname: str
    username: str
    port: int = 22
    key_path: str | None = None
    password: str | None = None
    last_used: datetime | None = None
    last_directory: str | None = None

    @classmethod
    def from_string(
        cls, connection_string: str, port: int = 22, key_path: str | None = None
    ) -> "Host":
        """Parse user@host connection string."""
        if "@" in connection_string:
            username, hostname = connection_string.split("@", 1)
        else:
            username = ""
            hostname = connection_string
        return cls(hostname=hostname, username=username, port=port, key_path=key_path)

    def to_dict(self) -> dict:
        """Convert to dictionary for JSON serialization."""
        import base64

        d = {
            "hostname": self.hostname,
            "username": self.username,
            "port": self.port,
            "key_path": self.key_path,
            "last_used": self.last_used.isoformat() if self.last_used else None,
            "last_directory": self.last_directory,
        }
        # Store password with base64 obfuscation (not real security, just prevents casual viewing)
        if self.password:
            d["_pw"] = base64.b64encode(self.password.encode()).decode()
        return d

    @classmethod
    def from_dict(cls, data: dict) -> "Host":
        """Create from dictionary."""
        import base64

        last_used = None
        if data.get("last_used"):
            last_used = datetime.fromisoformat(data["last_used"])

        # Decode password if present
        password = None
        if "_pw" in data:
            try:
                password = base64.b64decode(data["_pw"]).decode()
            except Exception:
                pass

        return cls(
            hostname=data["hostname"],
            username=data["username"],
            port=data.get("port", 22),
            key_path=data.get("key_path"),
            password=password,
            last_used=last_used,
            last_directory=data.get("last_directory"),
        )

    def __str__(self) -> str:
        """Return connection string."""
        return f"{self.username}@{self.hostname}:{self.port}"

    @property
    def host_key(self) -> str:
        """Return unique identifier for this host."""
        return f"{self.username}@{self.hostname}:{self.port}"


@dataclass
class RemoteFile:
    """Remote file or directory info."""

    name: str
    path: str
    size: int
    is_dir: bool
    mtime: datetime | None = None
    permissions: str | None = None

    @property
    def size_human(self) -> str:
        """Return human-readable file size."""
        if self.is_dir:
            return "<DIR>"
        units = ["B", "KB", "MB", "GB", "TB"]
        size = float(self.size)
        for unit in units[:-1]:
            if size < 1024:
                return f"{size:.1f} {unit}"
            size /= 1024
        return f"{size:.1f} {units[-1]}"


@dataclass
class Transfer:
    """A file transfer (download or upload)."""

    id: str
    remote_path: str
    local_path: str
    direction: TransferDirection
    size: int
    host_key: str = ""  # "user@hostname:port" identifier for multi-server queue
    status: TransferStatus = TransferStatus.QUEUED
    bytes_transferred: int = 0
    speed: float = 0.0  # bytes per second
    error: str | None = None
    started_at: datetime | None = None
    completed_at: datetime | None = None
    checksum: str | None = None  # Expected checksum if known
    _smoothed_eta_seconds: float | None = field(default=None, repr=False)  # Smoothed ETA

    @property
    def progress(self) -> float:
        """Return progress percentage (0-100)."""
        if self.size == 0:
            return 100.0
        return (self.bytes_transferred / self.size) * 100

    @property
    def speed_human(self) -> str:
        """Return human-readable transfer speed."""
        if self.speed == 0:
            return "0 B/s"
        units = ["B/s", "KB/s", "MB/s", "GB/s"]
        speed = float(self.speed)
        for unit in units[:-1]:
            if speed < 1024:
                return f"{speed:.1f} {unit}"
            speed /= 1024
        return f"{speed:.1f} {units[-1]}"

    @property
    def eta(self) -> str | None:
        """Return estimated time remaining (smoothed for stability)."""
        if self.status != TransferStatus.TRANSFERRING:
            return None

        # Use smoothed ETA if available, otherwise calculate from raw speed
        seconds = self._smoothed_eta_seconds
        if seconds is None:
            if self.speed <= 0:
                return None
            remaining_bytes = self.size - self.bytes_transferred
            seconds = remaining_bytes / self.speed

        if seconds <= 0:
            return None
        if seconds < 60:
            return f"{int(seconds)}s"
        elif seconds < 3600:
            return f"{int(seconds // 60)}m {int(seconds % 60)}s"
        else:
            hours = int(seconds // 3600)
            minutes = int((seconds % 3600) // 60)
            return f"{hours}h {minutes}m"

    @property
    def filename(self) -> str:
        """Return just the filename from the path."""
        return Path(self.remote_path).name

    def to_dict(self) -> dict:
        """Convert to dictionary for JSON serialization."""
        return {
            "id": self.id,
            "remote_path": self.remote_path,
            "local_path": self.local_path,
            "direction": self.direction.value,
            "size": self.size,
            "host_key": self.host_key,
            "status": self.status.value,
            "bytes_transferred": self.bytes_transferred,
            "error": self.error,
            "started_at": self.started_at.isoformat() if self.started_at else None,
            "completed_at": self.completed_at.isoformat() if self.completed_at else None,
            "checksum": self.checksum,
        }

    @classmethod
    def from_dict(cls, data: dict) -> "Transfer":
        """Create from dictionary."""
        return cls(
            id=data["id"],
            remote_path=data["remote_path"],
            local_path=data["local_path"],
            direction=TransferDirection(data["direction"]),
            size=data["size"],
            host_key=data.get("host_key", ""),
            status=TransferStatus(data["status"]),
            bytes_transferred=data.get("bytes_transferred", 0),
            error=data.get("error"),
            started_at=(
                datetime.fromisoformat(data["started_at"]) if data.get("started_at") else None
            ),
            completed_at=(
                datetime.fromisoformat(data["completed_at"]) if data.get("completed_at") else None
            ),
            checksum=data.get("checksum"),
        )


@dataclass
class TransferQueue:
    """Queue of transfers with state management."""

    transfers: list[Transfer] = field(default_factory=list)
    max_concurrent: int = 10

    @property
    def active_count(self) -> int:
        """Count of currently active transfers."""
        return len([t for t in self.transfers if t.status == TransferStatus.TRANSFERRING])

    @property
    def can_start_more(self) -> bool:
        """Whether more transfers can be started."""
        return self.active_count < self.max_concurrent

    def get_next_queued(self) -> Transfer | None:
        """Get next queued transfer."""
        for t in self.transfers:
            if t.status == TransferStatus.QUEUED:
                return t
        return None

    def get_by_id(self, transfer_id: str) -> Transfer | None:
        """Get transfer by ID."""
        for t in self.transfers:
            if t.id == transfer_id:
                return t
        return None

    def get_by_remote_path(self, remote_path: str, host_key: str = "") -> Transfer | None:
        """Get transfer by remote file path (and optionally host).

        Args:
            remote_path: The remote file path to search for
            host_key: Optional host key to match (if empty, matches any host)

        Returns:
            The first matching transfer, or None if not found
        """
        for t in self.transfers:
            if t.remote_path == remote_path:
                if not host_key or t.host_key == host_key:
                    return t
        return None

    def is_queued(self, remote_path: str, host_key: str = "") -> bool:
        """Check if a file is in the queue (any non-completed/failed status).

        Args:
            remote_path: The remote file path to check
            host_key: Optional host key to match (if empty, matches any host)

        Returns:
            True if file is in queue and not completed/failed
        """
        t = self.get_by_remote_path(remote_path, host_key)
        return t is not None and t.status not in (TransferStatus.COMPLETED, TransferStatus.FAILED)

    def remove(self, transfer_id: str) -> bool:
        """Remove a transfer from the queue."""
        for i, t in enumerate(self.transfers):
            if t.id == transfer_id:
                self.transfers.pop(i)
                return True
        return False

    def move_up(self, transfer_id: str) -> bool:
        """Move a transfer up in the queue."""
        for i, t in enumerate(self.transfers):
            if t.id == transfer_id and i > 0:
                self.transfers[i], self.transfers[i - 1] = self.transfers[i - 1], self.transfers[i]
                return True
        return False

    def move_down(self, transfer_id: str) -> bool:
        """Move a transfer down in the queue."""
        for i, t in enumerate(self.transfers):
            if t.id == transfer_id and i < len(self.transfers) - 1:
                self.transfers[i], self.transfers[i + 1] = self.transfers[i + 1], self.transfers[i]
                return True
        return False

    def has_queued_in_directory(self, dir_path: str, host_key: str = "") -> bool:
        """Check if any files under a directory are in the queue.

        Args:
            dir_path: The directory path to check (e.g., "/media/files")
            host_key: Optional host key to match

        Returns:
            True if any file under dir_path is queued (not completed/failed)
        """
        prefix = dir_path if dir_path.endswith("/") else dir_path + "/"
        for t in self.transfers:
            if t.remote_path.startswith(prefix):
                if not host_key or t.host_key == host_key:
                    if t.status not in (TransferStatus.COMPLETED, TransferStatus.FAILED):
                        return True
        return False


@dataclass
class AppSettings:
    """Application settings."""

    max_concurrent_transfers: int = 10
    bandwidth_limit: int | None = None  # bytes per second, None = unlimited
    download_dir: str = "~/Downloads"
    auto_refresh_interval: int = 30  # seconds, 0 = disabled
    verify_checksums: bool = True
    resume_transfers: bool = True

    def to_dict(self) -> dict:
        """Convert to dictionary for JSON serialization."""
        return {
            "max_concurrent_transfers": self.max_concurrent_transfers,
            "bandwidth_limit": self.bandwidth_limit,
            "download_dir": self.download_dir,
            "auto_refresh_interval": self.auto_refresh_interval,
            "verify_checksums": self.verify_checksums,
            "resume_transfers": self.resume_transfers,
        }

    @classmethod
    def from_dict(cls, data: dict) -> "AppSettings":
        """Create from dictionary."""
        return cls(
            max_concurrent_transfers=data.get("max_concurrent_transfers", 10),
            bandwidth_limit=data.get("bandwidth_limit"),
            download_dir=data.get("download_dir", "~/Downloads"),
            auto_refresh_interval=data.get("auto_refresh_interval", 30),
            verify_checksums=data.get("verify_checksums", True),
            resume_transfers=data.get("resume_transfers", True),
        )
