"""Configuration and host cache management."""

import json
import logging
import os
from datetime import datetime
from pathlib import Path

from queued.models import AppSettings, Host, Transfer, TransferStatus

logger = logging.getLogger(__name__)


def _secure_write(path: Path, content: str) -> None:
    """Write file with secure permissions (0600)."""
    path.write_text(content)
    os.chmod(path, 0o600)


def get_cache_dir() -> Path:
    """Get the cache directory path."""
    cache_dir = Path.home() / ".cache" / "queued"
    cache_dir.mkdir(parents=True, exist_ok=True)
    return cache_dir


def get_config_dir() -> Path:
    """Get the config directory path."""
    config_dir = Path.home() / ".config" / "queued"
    config_dir.mkdir(parents=True, exist_ok=True)
    return config_dir


class HostCache:
    """Manages recently used hosts."""

    def __init__(self, max_hosts: int = 10):
        self.max_hosts = max_hosts
        self.cache_file = get_cache_dir() / "hosts.json"
        self._hosts: list[Host] = []
        self._load()

    def _load(self) -> None:
        """Load hosts from cache file."""
        if self.cache_file.exists():
            try:
                data = json.loads(self.cache_file.read_text())
                self._hosts = [Host.from_dict(h) for h in data.get("hosts", [])]
            except (json.JSONDecodeError, KeyError):
                self._hosts = []

    def _save(self) -> None:
        """Save hosts to cache file."""
        data = {"hosts": [h.to_dict() for h in self._hosts]}
        _secure_write(self.cache_file, json.dumps(data, indent=2))

    def add(self, host: Host) -> None:
        """Add or update a host in the cache."""
        host.last_used = datetime.now()
        # Remove existing entry for same host
        self._hosts = [
            h
            for h in self._hosts
            if not (h.hostname == host.hostname and h.username == host.username)
        ]
        # Add to front
        self._hosts.insert(0, host)
        # Trim to max size
        self._hosts = self._hosts[: self.max_hosts]
        self._save()

    def get_recent(self, limit: int = 5) -> list[Host]:
        """Get recently used hosts."""
        return self._hosts[:limit]

    def get_by_hostname(self, hostname: str) -> Host | None:
        """Find a host by hostname."""
        for host in self._hosts:
            if host.hostname == hostname:
                return host
        return None

    def get_by_key(self, host_key: str) -> Host | None:
        """Find a host by its key (user@hostname:port)."""
        for host in self._hosts:
            if host.host_key == host_key:
                return host
        return None


class DownloadDirCache:
    """Manages recently used download directories."""

    def __init__(self, max_dirs: int = 6):
        self.max_dirs = max_dirs
        self.cache_file = get_cache_dir() / "download_dirs.json"
        self._dirs: list[str] = []
        self._load()

    def _load(self) -> None:
        """Load directories from cache file."""
        if self.cache_file.exists():
            try:
                data = json.loads(self.cache_file.read_text())
                self._dirs = data.get("dirs", [])
            except (json.JSONDecodeError, KeyError):
                self._dirs = []

    def _save(self) -> None:
        """Save directories to cache file."""
        data = {"dirs": self._dirs}
        _secure_write(self.cache_file, json.dumps(data, indent=2))

    def add(self, directory: str) -> None:
        """Add directory to cache.

        - Removes existing entry (deduplication)
        - Adds to front of list
        - Trims to max_dirs
        - Saves immediately
        """
        # Remove if exists
        self._dirs = [d for d in self._dirs if d != directory]
        # Add to front
        self._dirs.insert(0, directory)
        # Trim to max
        self._dirs = self._dirs[: self.max_dirs]
        self._save()

    def get_recent(self, limit: int = 6) -> list[str]:
        """Get recently used directories."""
        return self._dirs[:limit]


class TransferStateCache:
    """Manages transfer state for resume support."""

    def __init__(self):
        self.cache_file = get_cache_dir() / "transfers.json"
        self._state: dict = {}
        self._load()

    def _load(self) -> None:
        """Load transfer state from cache file."""
        if self.cache_file.exists():
            try:
                self._state = json.loads(self.cache_file.read_text())
            except json.JSONDecodeError:
                self._state = {}

    def _save(self) -> None:
        """Save transfer state to cache file."""
        _secure_write(self.cache_file, json.dumps(self._state, indent=2))

    def save_transfer(
        self,
        transfer_id: str,
        remote_path: str,
        local_path: str,
        bytes_transferred: int,
        total_size: int,
    ) -> None:
        """Save transfer progress for resume."""
        self._state[transfer_id] = {
            "remote_path": remote_path,
            "local_path": local_path,
            "bytes_transferred": bytes_transferred,
            "total_size": total_size,
            "updated_at": datetime.now().isoformat(),
        }
        self._save()

    def get_resume_offset(self, remote_path: str, local_path: str) -> int:
        """Get bytes already transferred for a file (for resume)."""
        for _transfer_id, data in self._state.items():
            if data["remote_path"] == remote_path and data["local_path"] == local_path:
                # Verify local file exists and matches expected size
                local_file = Path(local_path)
                if local_file.exists():
                    local_size = local_file.stat().st_size
                    if local_size == data["bytes_transferred"]:
                        return data["bytes_transferred"]
        return 0

    def clear_transfer(self, transfer_id: str) -> None:
        """Clear completed transfer from cache."""
        if transfer_id in self._state:
            del self._state[transfer_id]
            self._save()

    def clear_by_path(self, remote_path: str, local_path: str) -> None:
        """Clear transfer by paths."""
        to_remove = []
        for transfer_id, data in self._state.items():
            if data["remote_path"] == remote_path and data["local_path"] == local_path:
                to_remove.append(transfer_id)
        for tid in to_remove:
            del self._state[tid]
        if to_remove:
            self._save()


class QueueCache:
    """Persists download queue across app restarts."""

    def __init__(self):
        self.cache_file = get_cache_dir() / "queue.json"

    def save(self, transfers: list[Transfer], queue_paused: bool = False) -> None:
        """Save queue state (exclude completed/failed transfers).

        Args:
            transfers: List of transfers to save
            queue_paused: Whether the queue is currently paused/stopped
        """
        # Only save transfers that should be resumed
        active = [
            t
            for t in transfers
            if t.status not in (TransferStatus.COMPLETED, TransferStatus.FAILED)
        ]
        data = {
            "queue_paused": queue_paused,
            "transfers": [t.to_dict() for t in active],
        }
        _secure_write(self.cache_file, json.dumps(data, indent=2))

    def load(self) -> tuple[list[Transfer], bool]:
        """Load saved queue.

        Returns:
            Tuple of (transfers, queue_paused). State transitions on load:
            - TRANSFERRING → QUEUED (was active when app quit, resume automatically)
            - STOPPED → QUEUED (queue was stopped, resume automatically)
            - PAUSED → PAUSED (user explicitly paused, stay paused)
        """
        if not self.cache_file.exists():
            return [], False
        try:
            data = json.loads(self.cache_file.read_text())
            transfers = [Transfer.from_dict(t) for t in data.get("transfers", [])]
            # Convert TRANSFERRING and STOPPED to QUEUED for auto-resume
            # PAUSED stays PAUSED (user explicitly paused these)
            for t in transfers:
                if t.status in (TransferStatus.TRANSFERRING, TransferStatus.STOPPED):
                    t.status = TransferStatus.QUEUED
            # Don't restore queue_paused state - start fresh with queue running
            return transfers, False
        except (json.JSONDecodeError, KeyError):
            return [], False

    def clear(self) -> None:
        """Clear the queue cache file."""
        if self.cache_file.exists():
            self.cache_file.unlink()


class SettingsManager:
    """Manages application settings."""

    def __init__(self):
        self.config_file = get_config_dir() / "settings.json"
        self._settings = AppSettings()
        self._load()

    def _load(self) -> None:
        """Load settings from config file."""
        if self.config_file.exists():
            try:
                data = json.loads(self.config_file.read_text())
                self._settings = AppSettings.from_dict(data)
                logger.debug("Loaded settings from %s", self.config_file)
            except (json.JSONDecodeError, KeyError):
                self._settings = AppSettings()
                logger.debug("Using default settings (config file invalid)")
        else:
            logger.debug("Using default settings (no config file)")

    def _save(self) -> None:
        """Save settings to config file."""
        _secure_write(self.config_file, json.dumps(self._settings.to_dict(), indent=2))
        logger.debug("Saved settings to %s", self.config_file)

    @property
    def settings(self) -> AppSettings:
        """Get current settings."""
        return self._settings

    def update(self, **kwargs) -> None:
        """Update settings."""
        for key, value in kwargs.items():
            if hasattr(self._settings, key):
                setattr(self._settings, key, value)
        self._save()
