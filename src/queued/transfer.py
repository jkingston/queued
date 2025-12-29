"""Transfer queue manager with concurrent download support."""

import asyncio
import hashlib
import logging
import re
import uuid
from collections.abc import Callable
from datetime import datetime
from pathlib import Path, PurePosixPath

from queued.config import QueueCache, TransferStateCache
from queued.models import (
    AppSettings,
    Host,
    RemoteFile,
    Transfer,
    TransferDirection,
    TransferQueue,
    TransferStatus,
)
from queued.sftp import SFTPClient, SFTPConnectionPool, SFTPError

logger = logging.getLogger(__name__)


class TransferManager:
    """Manages file transfers with queue, resume, and verification support."""

    def __init__(
        self,
        sftp_client: SFTPClient | None = None,
        settings: AppSettings | None = None,
        on_progress: Callable[[Transfer], None] | None = None,
        on_status_change: Callable[[Transfer], None] | None = None,
        connection_pool: SFTPConnectionPool | None = None,
        queue_cache: QueueCache | None = None,
    ):
        self.sftp = sftp_client  # Primary client for file browser operations
        self.pool = connection_pool  # For multi-server downloads
        self.settings = settings or AppSettings()
        self.on_progress = on_progress
        self.on_status_change = on_status_change

        self.queue = TransferQueue(max_concurrent=self.settings.max_concurrent_transfers)
        self.state_cache = TransferStateCache()
        self.queue_cache = queue_cache or QueueCache()

        self._running = False
        self._queue_paused = False  # True when queue processing is stopped (s key)
        self._tasks: dict[str, asyncio.Task] = {}
        self._speed_trackers: dict[str, SpeedTracker] = {}
        self._individually_paused: set[str] = set()  # Track individual pause requests

        # Load any persisted transfers and queue state from previous session
        transfers, queue_paused = self.queue_cache.load()
        for transfer in transfers:
            self.queue.transfers.append(transfer)
        self._queue_paused = queue_paused

    def add_download(
        self,
        remote_file: RemoteFile,
        host: Host | None = None,
        local_dir: str | None = None,
        base_dir: str | None = None,
    ) -> Transfer | None:
        """Add a file to the download queue.

        Args:
            remote_file: The remote file to download
            host: Optional host for multi-server support
            local_dir: Override download directory
            base_dir: Base directory for preserving folder structure.
                      If provided, the relative path from base_dir is preserved.

        Returns None if the file is already in the queue (duplicate prevention).
        """
        host_key = host.host_key if host else ""

        # Check for duplicate - don't add if already in queue
        if self.queue.is_queued(remote_file.path, host_key):
            return None

        if local_dir is None:
            local_dir = str(Path(self.settings.download_dir).expanduser())

        download_base = Path(local_dir).resolve()

        if base_dir:
            # Preserve directory structure relative to base_dir
            rel_path = PurePosixPath(remote_file.path).relative_to(base_dir)
            # Validate each component to prevent path traversal
            for part in rel_path.parts:
                if part in (".", "..") or not part:
                    raise ValueError(f"Invalid path component: {part}")
            local_path = (download_base / rel_path).resolve()
            # Create parent directories
            local_path.parent.mkdir(parents=True, exist_ok=True)
        else:
            # Single file - just use filename
            safe_name = Path(remote_file.name).name
            if not safe_name or safe_name in (".", ".."):
                raise ValueError(f"Invalid filename: {remote_file.name}")
            local_path = (download_base / safe_name).resolve()

        # Security check - ensure path is under download directory
        if not str(local_path).startswith(str(download_base)):
            raise ValueError(f"Path traversal attempt blocked: {remote_file.path}")

        local_path = str(local_path)

        transfer = Transfer(
            id=str(uuid.uuid4()),
            remote_path=remote_file.path,
            local_path=local_path,
            direction=TransferDirection.DOWNLOAD,
            size=remote_file.size,
            host_key=host_key,
            status=TransferStatus.QUEUED,
        )

        self.queue.transfers.append(transfer)
        self._notify_status_change(transfer)
        self._persist_queue()
        logger.info("Added download to queue: %s", remote_file.path)
        return transfer

    def _persist_queue(self) -> None:
        """Save queue state to disk for persistence across restarts."""
        self.queue_cache.save(self.queue.transfers, self._queue_paused)

    async def _get_sftp_for_transfer(self, transfer: Transfer) -> SFTPClient:
        """Get the appropriate SFTP client for a transfer."""
        # If transfer has a host_key and we have a connection pool, use it
        if transfer.host_key and self.pool:
            return await self.pool.get_connection(transfer.host_key)
        # Fall back to the primary SFTP client
        if self.sftp and self.sftp.connected:
            return self.sftp
        raise SFTPError("No SFTP connection available for transfer")

    def add_upload(self, local_path: str, remote_dir: str) -> Transfer:
        """Add a file to the upload queue."""
        local_file = Path(local_path)
        if not local_file.exists():
            raise FileNotFoundError(f"Local file not found: {local_path}")

        remote_path = f"{remote_dir}/{local_file.name}"

        transfer = Transfer(
            id=str(uuid.uuid4()),
            remote_path=remote_path,
            local_path=str(local_file),
            direction=TransferDirection.UPLOAD,
            size=local_file.stat().st_size,
            status=TransferStatus.QUEUED,
        )

        self.queue.transfers.append(transfer)
        self._notify_status_change(transfer)
        return transfer

    async def start(self) -> None:
        """Start processing the queue."""
        self._running = True
        while self._running:
            # Only start new transfers if queue processing is not paused
            if not self._queue_paused:
                while self.queue.can_start_more:
                    transfer = self.queue.get_next_queued()
                    if transfer is None:
                        break
                    self._start_transfer(transfer)

            await asyncio.sleep(0.1)

    async def stop(self) -> None:
        """Stop processing the queue and wait for tasks to finish."""
        self._running = False
        # Cancel all active transfers (copy list to avoid mutation during iteration)
        tasks = list(self._tasks.values())
        for task in tasks:
            task.cancel()
        # Wait for all tasks to complete
        if tasks:
            await asyncio.gather(*tasks, return_exceptions=True)

    def _start_transfer(self, transfer: Transfer) -> None:
        """Start a single transfer."""
        logger.debug("Starting transfer: %s", transfer.remote_path)
        transfer.status = TransferStatus.TRANSFERRING
        transfer.started_at = datetime.now()
        self._speed_trackers[transfer.id] = SpeedTracker()
        self._notify_status_change(transfer)

        if transfer.direction == TransferDirection.DOWNLOAD:
            task = asyncio.create_task(self._do_download(transfer))
        else:
            task = asyncio.create_task(self._do_upload(transfer))

        self._tasks[transfer.id] = task

    async def _do_download(self, transfer: Transfer) -> None:
        """Execute a download."""
        # Register with global limiter
        await self._bandwidth_limiter.register(transfer.id)

        try:
            # Get the appropriate SFTP connection for this transfer
            sftp = await self._get_sftp_for_transfer(transfer)

            # Check for resume
            resume_offset = 0
            if self.settings.resume_transfers:
                resume_offset = self.state_cache.get_resume_offset(
                    transfer.remote_path, transfer.local_path
                )
                if resume_offset > 0:
                    transfer.bytes_transferred = resume_offset

            def progress_callback(bytes_done: int, total: int) -> None:
                transfer.bytes_transferred = bytes_done
                speed_tracker = self._speed_trackers.get(transfer.id)
                if speed_tracker:
                    transfer.speed = speed_tracker.update(bytes_done)
                    # Calculate smoothed ETA for stable display
                    remaining = transfer.size - bytes_done
                    transfer._smoothed_eta_seconds = speed_tracker.get_smoothed_eta(
                        remaining, transfer.speed
                    )
                self._notify_progress(transfer)

                # Save state for resume
                if self.settings.resume_transfers:
                    self.state_cache.save_transfer(
                        transfer.id,
                        transfer.remote_path,
                        transfer.local_path,
                        bytes_done,
                        total,
                    )

            # Use global limiter if enabled, otherwise no limiting
            global_limiter = self._bandwidth_limiter if self._bandwidth_limit_enabled else None

            await sftp.download(
                transfer.remote_path,
                transfer.local_path,
                progress_callback=progress_callback,
                resume_offset=resume_offset,
                global_limiter=global_limiter,
                transfer_id=transfer.id,
            )

            # Verify if checksums available
            if self.settings.verify_checksums:
                transfer.status = TransferStatus.VERIFYING
                self._notify_status_change(transfer)
                verified = await self._verify_checksum(transfer, sftp)
                if not verified:
                    transfer.status = TransferStatus.FAILED
                    transfer.error = "Checksum verification failed"
                    self._notify_status_change(transfer)
                    self._persist_queue()
                    return

            transfer.status = TransferStatus.COMPLETED
            transfer.completed_at = datetime.now()
            self.state_cache.clear_transfer(transfer.id)
            self._persist_queue()
            logger.info("Download completed: %s", transfer.remote_path)

        except SFTPError as e:
            transfer.status = TransferStatus.FAILED
            transfer.error = str(e)
            self._persist_queue()
            logger.error("Download failed: %s - %s", transfer.remote_path, e)
        except asyncio.CancelledError:
            # Check if this was an individual pause request
            if transfer.id in self._individually_paused:
                self._individually_paused.discard(transfer.id)
                transfer.status = TransferStatus.PAUSED
            else:
                # Queue was stopped
                transfer.status = TransferStatus.STOPPED
            self._persist_queue()
        except OSError as e:
            transfer.status = TransferStatus.FAILED
            transfer.error = f"I/O error: {e}"
            self._persist_queue()
        except ValueError as e:
            transfer.status = TransferStatus.FAILED
            transfer.error = f"Invalid data: {e}"
            self._persist_queue()
        finally:
            await self._bandwidth_limiter.unregister(transfer.id)
            self._tasks.pop(transfer.id, None)
            self._speed_trackers.pop(transfer.id, None)
            self._notify_status_change(transfer)

    async def _do_upload(self, transfer: Transfer) -> None:
        """Execute an upload."""
        # Register with global limiter
        await self._bandwidth_limiter.register(transfer.id)

        try:
            # Get the appropriate SFTP connection for this transfer
            sftp = await self._get_sftp_for_transfer(transfer)

            def progress_callback(bytes_done: int, total: int) -> None:
                transfer.bytes_transferred = bytes_done
                speed_tracker = self._speed_trackers.get(transfer.id)
                if speed_tracker:
                    transfer.speed = speed_tracker.update(bytes_done)
                    # Calculate smoothed ETA for stable display
                    remaining = transfer.size - bytes_done
                    transfer._smoothed_eta_seconds = speed_tracker.get_smoothed_eta(
                        remaining, transfer.speed
                    )
                self._notify_progress(transfer)

            # Use global limiter if enabled, otherwise no limiting
            global_limiter = self._bandwidth_limiter if self._bandwidth_limit_enabled else None

            await sftp.upload(
                transfer.local_path,
                transfer.remote_path,
                progress_callback=progress_callback,
                global_limiter=global_limiter,
                transfer_id=transfer.id,
            )

            transfer.status = TransferStatus.COMPLETED
            transfer.completed_at = datetime.now()
            self._persist_queue()

        except SFTPError as e:
            transfer.status = TransferStatus.FAILED
            transfer.error = str(e)
            self._persist_queue()
        except asyncio.CancelledError:
            # Check if this was an individual pause request
            if transfer.id in self._individually_paused:
                self._individually_paused.discard(transfer.id)
                transfer.status = TransferStatus.PAUSED
            else:
                # Queue was stopped
                transfer.status = TransferStatus.STOPPED
            self._persist_queue()
        except OSError as e:
            transfer.status = TransferStatus.FAILED
            transfer.error = f"I/O error: {e}"
            self._persist_queue()
        except ValueError as e:
            transfer.status = TransferStatus.FAILED
            transfer.error = f"Invalid data: {e}"
            self._persist_queue()
        finally:
            await self._bandwidth_limiter.unregister(transfer.id)
            self._tasks.pop(transfer.id, None)
            self._speed_trackers.pop(transfer.id, None)
            self._notify_status_change(transfer)

    async def _verify_checksum(self, transfer: Transfer, sftp: SFTPClient) -> bool:
        """Verify download checksum if verification file exists."""
        remote_dir = str(Path(transfer.remote_path).parent)
        remote_name = Path(transfer.remote_path).name

        # Look for .sfv or .md5 files
        try:
            files = await sftp.list_dir(remote_dir)
            for f in files:
                if f.name.endswith(".sfv"):
                    checksum = await self._check_sfv(
                        f"{remote_dir}/{f.name}", remote_name, transfer.local_path, sftp
                    )
                    if checksum is not None:
                        return checksum
                elif f.name.endswith(".md5"):
                    checksum = await self._check_md5(
                        f"{remote_dir}/{f.name}", remote_name, transfer.local_path, sftp
                    )
                    if checksum is not None:
                        return checksum
        except SFTPError:
            pass

        # No checksum file found, consider verified
        return True

    async def _check_sfv(
        self, sfv_path: str, filename: str, local_path: str, sftp: SFTPClient
    ) -> bool | None:
        """Check file against SFV (Simple File Verification) file."""
        try:
            content = await sftp.read_file(sfv_path)
            lines = content.decode("utf-8", errors="ignore").splitlines()

            for line in lines:
                line = line.strip()
                if line.startswith(";") or not line:
                    continue
                # SFV format: filename CRC32
                match = re.match(r"(.+?)\s+([0-9a-fA-F]{8})$", line)
                if match and match.group(1).lower() == filename.lower():
                    expected_crc = match.group(2).lower()
                    actual_crc = self._calculate_crc32(local_path)
                    return actual_crc == expected_crc
        except (SFTPError, UnicodeDecodeError):
            pass
        return None

    async def _check_md5(
        self, md5_path: str, filename: str, local_path: str, sftp: SFTPClient
    ) -> bool | None:
        """Check file against MD5 checksum file."""
        try:
            content = await sftp.read_file(md5_path)
            lines = content.decode("utf-8", errors="ignore").splitlines()

            for line in lines:
                line = line.strip()
                if not line:
                    continue
                # MD5 format: hash *filename or hash  filename
                match = re.match(r"([0-9a-fA-F]{32})\s+\*?(.+)$", line)
                if match and match.group(2).strip().lower() == filename.lower():
                    expected_md5 = match.group(1).lower()
                    actual_md5 = self._calculate_md5(local_path)
                    return actual_md5 == expected_md5
        except (SFTPError, UnicodeDecodeError):
            pass
        return None

    def _calculate_crc32(self, filepath: str) -> str:
        """Calculate CRC32 checksum of a file."""
        import zlib

        crc = 0
        with open(filepath, "rb") as f:
            for chunk in iter(lambda: f.read(65536), b""):
                crc = zlib.crc32(chunk, crc)
        return format(crc & 0xFFFFFFFF, "08x")

    def _calculate_md5(self, filepath: str) -> str:
        """Calculate MD5 checksum of a file."""
        md5 = hashlib.md5()
        with open(filepath, "rb") as f:
            for chunk in iter(lambda: f.read(65536), b""):
                md5.update(chunk)
        return md5.hexdigest()

    def pause_transfer(self, transfer_id: str) -> bool:
        """Pause a transfer."""
        transfer = self.queue.get_by_id(transfer_id)
        if transfer and transfer.status in (TransferStatus.TRANSFERRING, TransferStatus.QUEUED):
            if transfer.status == TransferStatus.QUEUED:
                # Just mark as paused, no task to cancel
                transfer.status = TransferStatus.PAUSED
                self._notify_status_change(transfer)
                self._persist_queue()
                return True
            # TRANSFERRING - cancel the task
            self._individually_paused.add(transfer_id)  # Mark as individual pause
            task = self._tasks.get(transfer_id)
            if task:
                task.cancel()
            # Persist is called in the _do_download/upload exception handler
            return True
        return False

    def resume_transfer(self, transfer_id: str) -> bool:
        """Resume a paused or stopped transfer."""
        transfer = self.queue.get_by_id(transfer_id)
        if transfer and transfer.status in (TransferStatus.PAUSED, TransferStatus.STOPPED):
            transfer.status = TransferStatus.QUEUED
            self._notify_status_change(transfer)
            self._persist_queue()
            return True
        return False

    def remove_transfer(self, transfer_id: str) -> bool:
        """Remove a transfer from the queue."""
        transfer = self.queue.get_by_id(transfer_id)
        if transfer:
            # Cancel if running
            if transfer.status == TransferStatus.TRANSFERRING:
                task = self._tasks.get(transfer_id)
                if task:
                    task.cancel()
            # Clear state cache
            self.state_cache.clear_transfer(transfer_id)
            result = self.queue.remove(transfer_id)
            self._persist_queue()
            return result
        return False

    def pause_all(self) -> int:
        """Pause all active transfers. Returns count of paused transfers."""
        count = 0
        for t in self.queue.transfers:
            if t.status == TransferStatus.TRANSFERRING:
                if self.pause_transfer(t.id):
                    count += 1
        return count

    def resume_all(self) -> int:
        """Resume all paused transfers. Returns count of resumed transfers."""
        count = 0
        for t in self.queue.transfers:
            if t.status == TransferStatus.PAUSED:
                if self.resume_transfer(t.id):
                    count += 1
        return count

    def stop_queue(self) -> int:
        """Stop queue processing and stop all active transfers.

        This truly stops the queue - no new transfers will start until resume_queue() is called.
        Active transfers are marked as STOPPED (distinct from user-paused).
        Returns the count of transfers that were stopped.
        """
        logger.info("Stopping queue processing")
        self._queue_paused = True
        count = 0
        for t in self.queue.transfers:
            if t.status == TransferStatus.TRANSFERRING:
                task = self._tasks.get(t.id)
                if task:
                    task.cancel()
                # Status will be set to STOPPED in CancelledError handler
                count += 1
        self._persist_queue()
        logger.debug("Stopped %d active transfers", count)
        return count

    def resume_queue(self) -> int:
        """Resume queue processing.

        This resumes queue processing - stopped transfers will start automatically.
        Only STOPPED transfers are resumed; PAUSED transfers remain paused.
        Returns the count of transfers that were set back to queued.
        """
        logger.info("Resuming queue processing")
        self._queue_paused = False
        # Only resume STOPPED transfers, not user-PAUSED ones
        count = 0
        for t in self.queue.transfers:
            if t.status == TransferStatus.STOPPED:
                t.status = TransferStatus.QUEUED
                self._notify_status_change(t)
                count += 1
        self._persist_queue()
        logger.debug("Resumed %d stopped transfers", count)
        return count

    @property
    def is_queue_paused(self) -> bool:
        """Check if queue processing is paused."""
        return self._queue_paused

    def _notify_progress(self, transfer: Transfer) -> None:
        """Notify progress callback."""
        if self.on_progress:
            self.on_progress(transfer)

    def _notify_status_change(self, transfer: Transfer) -> None:
        """Notify status change callback."""
        if self.on_status_change:
            self.on_status_change(transfer)

    @property
    def total_speed(self) -> float:
        """Total download speed across all active transfers."""
        return sum(t.speed for t in self.queue.transfers if t.status == TransferStatus.TRANSFERRING)


class SpeedTracker:
    """Tracks transfer speed with smoothing."""

    def __init__(self, window_size: int = 10):
        self.window_size = window_size
        self._samples: list[tuple[float, int]] = []
        self._last_bytes = 0
        self._smoothed_eta: float | None = None
        self._alpha = 0.15  # EMA factor: lower = smoother, higher = more responsive

    def update(self, current_bytes: int) -> float:
        """Update with current bytes transferred, return smoothed speed."""
        import time

        now = time.time()
        bytes_delta = current_bytes - self._last_bytes
        self._last_bytes = current_bytes

        self._samples.append((now, bytes_delta))

        # Keep only recent samples
        cutoff = now - 2.0  # 2 second window
        self._samples = [(t, b) for t, b in self._samples if t > cutoff]

        if len(self._samples) < 2:
            return 0.0

        # Calculate speed from samples
        time_span = self._samples[-1][0] - self._samples[0][0]
        if time_span <= 0:
            return 0.0

        total_bytes = sum(b for _, b in self._samples)
        return total_bytes / time_span

    def get_smoothed_eta(self, remaining_bytes: int, current_speed: float) -> float | None:
        """Return smoothed ETA in seconds using exponential moving average."""
        if current_speed <= 0:
            return None

        raw_eta = remaining_bytes / current_speed

        if self._smoothed_eta is None:
            self._smoothed_eta = raw_eta
        else:
            # EMA: blend new value with previous for smoother transitions
            self._smoothed_eta = self._alpha * raw_eta + (1 - self._alpha) * self._smoothed_eta

        return self._smoothed_eta
