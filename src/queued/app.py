"""Main Textual application for Queued."""

import asyncio
from pathlib import Path
from typing import Optional

from textual import work
from textual.app import App, ComposeResult
from textual.binding import Binding
from textual.containers import Container, Horizontal, Vertical
from textual.screen import ModalScreen
from textual.widgets import Button, Footer, Header, Input, Label, Static

from queued.config import HostCache, QueueCache, SettingsManager
from queued.models import Host, RemoteFile, Transfer
from queued.sftp import SFTPClient, SFTPConnectionPool, SFTPError
from queued.transfer import TransferManager
from queued.widgets.file_browser import FileBrowser
from queued.widgets.status_bar import StatusBar
from queued.widgets.transfer_list import TransferList


class HelpScreen(ModalScreen):
    """Help overlay showing keybindings."""

    BINDINGS = [
        Binding("escape", "dismiss", "Close"),
        Binding("?", "dismiss", "Close"),
    ]

    DEFAULT_CSS = """
    HelpScreen {
        align: center middle;
    }

    HelpScreen > Container {
        width: 60;
        height: auto;
        max-height: 80%;
        background: $surface;
        border: thick $primary;
        padding: 1 2;
    }

    HelpScreen .help-title {
        text-align: center;
        text-style: bold;
        margin-bottom: 1;
    }

    HelpScreen .help-section {
        margin-top: 1;
        text-style: bold;
        color: $primary;
    }

    HelpScreen .help-item {
        margin-left: 2;
    }
    """

    def compose(self) -> ComposeResult:
        with Container():
            yield Label("Queued - Help", classes="help-title")

            yield Label("File Browser", classes="help-section")
            yield Label("Enter     Open directory / Queue file", classes="help-item")
            yield Label("Space     Toggle file selection", classes="help-item")
            yield Label("a         Select all files", classes="help-item")
            yield Label("Escape    Clear selection", classes="help-item")
            yield Label("Backspace Go to parent directory", classes="help-item")
            yield Label("r         Refresh directory", classes="help-item")

            yield Label("Transfers", classes="help-section")
            yield Label("d         Download selected file(s)", classes="help-item")
            yield Label("s         Stop all downloads", classes="help-item")
            yield Label("r         Resume all downloads", classes="help-item")
            yield Label("p         Pause/Resume selected", classes="help-item")
            yield Label("x         Remove selected", classes="help-item")
            yield Label("↑/↓       Move in queue", classes="help-item")

            yield Label("General", classes="help-section")
            yield Label("Tab       Switch focus", classes="help-item")
            yield Label("q         Quit (saves queue)", classes="help-item")
            yield Label("?         Show this help", classes="help-item")


class ErrorModal(ModalScreen[None]):
    """Modal for displaying error messages."""

    BINDINGS = [
        Binding("escape", "dismiss", "Close"),
        Binding("enter", "dismiss", "Close"),
    ]

    DEFAULT_CSS = """
    ErrorModal {
        align: center middle;
    }

    ErrorModal > Container {
        width: 70;
        height: auto;
        max-height: 80%;
        background: $surface;
        border: thick $error;
        padding: 1 2;
    }

    ErrorModal .error-title {
        text-align: center;
        text-style: bold;
        color: $error;
        margin-bottom: 1;
    }

    ErrorModal .error-message {
        margin-bottom: 1;
    }

    ErrorModal Button {
        width: 100%;
    }
    """

    def __init__(self, message: str) -> None:
        super().__init__()
        self.message = message

    def compose(self) -> ComposeResult:
        with Container():
            yield Label("Connection Failed", classes="error-title")
            yield Label(self.message, classes="error-message")
            yield Button("OK", variant="primary", id="ok-btn")

    def on_button_pressed(self, event: Button.Pressed) -> None:
        self.dismiss(None)

    def action_dismiss(self) -> None:
        self.dismiss(None)


class FileExistsModal(ModalScreen[Optional[str]]):
    """Modal for handling file conflicts during download."""

    BINDINGS = [
        Binding("escape", "cancel", "Cancel"),
    ]

    DEFAULT_CSS = """
    FileExistsModal {
        align: center middle;
    }

    FileExistsModal > Container {
        width: 60;
        height: auto;
        max-height: 80%;
        background: $surface;
        border: thick $warning;
        padding: 1 2;
    }

    FileExistsModal .modal-title {
        text-align: center;
        text-style: bold;
        color: $warning;
        margin-bottom: 1;
    }

    FileExistsModal .file-info {
        margin-bottom: 0;
    }

    FileExistsModal .button-row {
        margin-top: 1;
        height: auto;
        align: center middle;
    }

    FileExistsModal Button {
        margin: 0 1;
    }
    """

    def __init__(self, filename: str, local_size: int, remote_size: int) -> None:
        super().__init__()
        self.filename = filename
        self.local_size = local_size
        self.remote_size = remote_size
        # Only allow continue (resume) if remote file is larger than local
        self.can_continue = remote_size > local_size

    def compose(self) -> ComposeResult:
        with Container():
            yield Label("File Already Exists", classes="modal-title")
            yield Label(f"File: {self.filename}", classes="file-info")
            yield Label(f"Local size:  {self._format_size(self.local_size)}", classes="file-info")
            yield Label(f"Remote size: {self._format_size(self.remote_size)}", classes="file-info")

            if self.can_continue:
                yield Label(
                    f"Resume available: {self._format_size(self.remote_size - self.local_size)} remaining",
                    classes="file-info"
                )
            else:
                yield Label(
                    "Resume not available (local file >= remote size)",
                    classes="file-info"
                )

            with Horizontal(classes="button-row"):
                if self.can_continue:
                    yield Button("Continue", variant="primary", id="continue-btn")
                yield Button("Replace", variant="warning", id="replace-btn")
                yield Button("Cancel", id="cancel-btn")

    def _format_size(self, size: int) -> str:
        """Format size in human-readable format."""
        units = ["B", "KB", "MB", "GB", "TB"]
        size_float = float(size)
        for unit in units[:-1]:
            if size_float < 1024:
                return f"{size_float:.1f} {unit}"
            size_float /= 1024
        return f"{size_float:.1f} {units[-1]}"

    def on_button_pressed(self, event: Button.Pressed) -> None:
        if event.button.id == "continue-btn":
            self.dismiss("continue")
        elif event.button.id == "replace-btn":
            self.dismiss("replace")
        else:
            self.dismiss(None)

    def action_cancel(self) -> None:
        self.dismiss(None)


class DownloadDirModal(ModalScreen[Optional[str]]):
    """Modal for changing download directory."""

    BINDINGS = [
        Binding("escape", "cancel", "Cancel"),
    ]

    DEFAULT_CSS = """
    DownloadDirModal {
        align: center middle;
    }

    DownloadDirModal > Container {
        width: 70;
        height: auto;
        background: $surface;
        border: thick $primary;
        padding: 1 2;
    }

    DownloadDirModal .modal-title {
        text-align: center;
        text-style: bold;
        margin-bottom: 1;
    }

    DownloadDirModal .field-label {
        margin-top: 0;
        height: 1;
    }

    DownloadDirModal Input {
        margin-bottom: 0;
    }

    DownloadDirModal .button-row {
        margin-top: 1;
        height: auto;
        align: center middle;
    }

    DownloadDirModal Button {
        margin: 0 1;
    }
    """

    def __init__(self, current_dir: str) -> None:
        super().__init__()
        self.current_dir = current_dir

    def compose(self) -> ComposeResult:
        with Container():
            yield Label("Download Directory", classes="modal-title")
            yield Label("Enter path (~ expands to home):", classes="field-label")
            yield Input(value=self.current_dir, id="dir-input")
            with Horizontal(classes="button-row"):
                yield Button("Save", variant="primary", id="save-btn")
                yield Button("Cancel", id="cancel-btn")

    def on_button_pressed(self, event: Button.Pressed) -> None:
        if event.button.id == "save-btn":
            path = self.query_one("#dir-input", Input).value.strip()
            self.dismiss(path)
        else:
            self.dismiss(None)

    def on_input_submitted(self, event: Input.Submitted) -> None:
        path = event.value.strip()
        self.dismiss(path)

    def action_cancel(self) -> None:
        self.dismiss(None)


class ConnectionScreen(ModalScreen[Optional[Host]]):
    """Connection dialog for entering host details."""

    BINDINGS = [
        Binding("escape", "cancel", "Cancel"),
    ]

    DEFAULT_CSS = """
    ConnectionScreen {
        align: center middle;
    }

    ConnectionScreen > Container {
        width: 60;
        height: auto;
        max-height: 80%;
        background: $surface;
        border: thick $primary;
        padding: 1 2;
    }

    ConnectionScreen .dialog-title {
        text-align: center;
        text-style: bold;
        margin-bottom: 1;
    }

    ConnectionScreen .field-label {
        margin-top: 0;
        height: 1;
    }

    ConnectionScreen Input {
        margin-bottom: 0;
    }

    ConnectionScreen .button-row {
        margin-top: 1;
        height: auto;
        align: center middle;
    }

    ConnectionScreen Button {
        margin: 0 1;
    }

    ConnectionScreen .recent-btn {
        width: 100%;
        margin-top: 0;
    }
    """

    def __init__(self, recent_hosts: list[Host] = None) -> None:
        super().__init__()
        self.recent_hosts = recent_hosts or []

    def compose(self) -> ComposeResult:
        with Container():
            yield Label("Connect to Server", classes="dialog-title")

            yield Label("Host (user@hostname):", classes="field-label")
            yield Input(placeholder="user@server.example.com", id="host-input")

            yield Label("Port:", classes="field-label")
            yield Input(placeholder="22", id="port-input", value="22")

            yield Label("SSH Key (optional):", classes="field-label")
            yield Input(placeholder="~/.ssh/id_rsa", id="key-input")

            yield Label("Password (if no key):", classes="field-label")
            yield Input(placeholder="", id="password-input", password=True)

            with Horizontal(classes="button-row"):
                yield Button("Connect", variant="primary", id="connect-btn")
                yield Button("Cancel", id="cancel-btn")

            if self.recent_hosts:
                yield Label("Recent:", classes="field-label")
                for i, host in enumerate(self.recent_hosts[:3]):
                    yield Button(str(host), id=f"recent-{i}", classes="recent-btn")

    def on_button_pressed(self, event: Button.Pressed) -> None:
        """Handle button presses."""
        if event.button.id == "connect-btn":
            self._do_connect()
        elif event.button.id == "cancel-btn":
            self.dismiss(None)
        elif event.button.id and event.button.id.startswith("recent-"):
            idx = int(event.button.id.split("-")[1])
            if idx < len(self.recent_hosts):
                self._prefill_form(self.recent_hosts[idx])

    def _prefill_form(self, host: Host) -> None:
        """Pre-fill form with host details including saved password."""
        self.query_one("#host-input", Input).value = f"{host.username}@{host.hostname}"
        self.query_one("#port-input", Input).value = str(host.port)
        if host.key_path:
            self.query_one("#key-input", Input).value = host.key_path
        if host.password:
            self.query_one("#password-input", Input).value = host.password
        self.query_one("#password-input", Input).focus()

    def on_input_submitted(self, event: Input.Submitted) -> None:
        """Handle Enter in input fields."""
        self._do_connect()

    def _do_connect(self) -> None:
        """Validate and return connection info."""
        host_input = self.query_one("#host-input", Input)
        port_input = self.query_one("#port-input", Input)
        key_input = self.query_one("#key-input", Input)
        password_input = self.query_one("#password-input", Input)

        host_str = host_input.value.strip()
        if not host_str:
            host_input.focus()
            return

        try:
            port = int(port_input.value) if port_input.value else 22
        except ValueError:
            port = 22

        key_path = key_input.value.strip() or None
        password = password_input.value or None

        host = Host.from_string(host_str, port=port, key_path=key_path)
        host.password = password
        self.dismiss(host)

    def action_cancel(self) -> None:
        """Cancel the dialog."""
        self.dismiss(None)


class QueuedApp(App):
    """Main Queued TUI application."""

    TITLE = "Queued"
    CSS = """
    #main-container {
        height: 1fr;
    }

    #file-browser {
        height: 2fr;
    }

    #transfer-list {
        height: 1fr;
        min-height: 5;
    }

    #transfer-list.hidden {
        display: none;
    }

    #footer-bar {
        height: 1;
        dock: bottom;
        background: $surface;
    }

    #footer-bar .key {
        background: $primary;
        color: $text;
        padding: 0 1;
    }

    #footer-bar .label {
        padding: 0 1;
    }
    """

    BINDINGS = [
        Binding("q", "quit", "Quit", show=True),
        Binding("?", "help", "Help", show=True),
        Binding("d", "download", "Download", show=True),
        Binding("u", "upload", "Upload", show=True),
        Binding("s", "stop_all", "Stop All", show=True),
        Binding("r", "resume_all", "Resume All", show=True),
        Binding("o", "options", "Options", show=True),
        Binding("t", "toggle_transfers", "Transfers", show=True),
        Binding("tab", "focus_next", "Switch Pane", show=False),
        Binding("shift+tab", "focus_previous", "Switch Pane", show=False),
    ]

    def __init__(self, host: Optional[Host] = None, download_dir: Optional[str] = None) -> None:
        super().__init__()
        self.initial_host = host
        self.sftp: Optional[SFTPClient] = None
        self.transfer_manager: Optional[TransferManager] = None
        self.host_cache = HostCache()
        self.settings_manager = SettingsManager()
        self.queue_cache = QueueCache()
        self.connection_pool = SFTPConnectionPool(self.host_cache)
        self._transfer_task: Optional[asyncio.Task] = None
        self._refresh_timer: Optional[asyncio.Task] = None
        self._current_host: Optional[Host] = None

        # Override download dir if provided via CLI
        if download_dir:
            self.settings_manager.update(download_dir=download_dir)

    def compose(self) -> ComposeResult:
        yield Header()
        with Vertical(id="main-container"):
            yield FileBrowser(id="file-browser")
            yield TransferList(id="transfer-list")
        yield StatusBar(id="status-bar")
        yield Footer()

    async def on_mount(self) -> None:
        """Initialize the app."""
        # Show download directory in status bar
        status_bar = self.query_one("#status-bar", StatusBar)
        status_bar.set_download_dir(self.settings_manager.settings.download_dir)

        if self.initial_host:
            await self._connect(self.initial_host)
        else:
            # Show connection dialog
            self.call_after_refresh(self._show_connection_dialog)

    def _show_connection_dialog(self) -> None:
        """Show the connection dialog."""
        recent = self.host_cache.get_recent(5)
        self.push_screen(ConnectionScreen(recent), self._on_connection_result)

    def _on_connection_result(self, host: Optional[Host]) -> None:
        """Handle connection dialog result."""
        if host:
            self._connect(host)
        else:
            self.exit()

    @work(exclusive=True)
    async def _connect(self, host: Host) -> None:
        """Connect to the remote host."""
        status_bar = self.query_one("#status-bar", StatusBar)
        status_bar.show_message(f"Connecting to {host}...")

        self.sftp = SFTPClient(host)
        try:
            await self.sftp.connect()
            self.host_cache.add(host)

            # Add connection to the pool for multi-server support
            self.connection_pool.add_connection(host.host_key, self.sftp)

            # Set up the file browser - try last directory, fall back to home
            file_browser = self.query_one("#file-browser", FileBrowser)
            file_browser.set_sftp_client(self.sftp)

            # Store current host for directory tracking
            self._current_host = host

            # Try last directory first, fall back to home dir
            start_dir = host.last_directory
            if start_dir:
                try:
                    # Verify the directory still exists
                    await self.sftp.list_dir(start_dir)
                except Exception:
                    start_dir = None

            if not start_dir:
                start_dir = await self.sftp.get_pwd()

            file_browser.load_directory(start_dir)

            # Set up the transfer manager with connection pool and queue cache
            settings = self.settings_manager.settings
            self.transfer_manager = TransferManager(
                sftp_client=self.sftp,
                settings=settings,
                on_progress=self._on_transfer_progress,
                on_status_change=self._on_transfer_status_change,
                connection_pool=self.connection_pool,
                queue_cache=self.queue_cache,
            )

            # Set up the transfer list
            transfer_list = self.query_one("#transfer-list", TransferList)
            transfer_list.set_queue(self.transfer_manager.queue)

            # Set up file browser queue reference for status indicators
            file_browser.set_transfer_queue(self.transfer_manager.queue)

            # Refresh display to show any persisted transfers
            transfer_list.refresh_display()

            # Start the transfer manager
            self._transfer_task = asyncio.create_task(self.transfer_manager.start())

            # Update status bar
            status_bar.set_connection(str(host), True)
            self.title = f"Queued - {host}"

            # Show message if we loaded persisted transfers
            persisted_count = len([t for t in self.transfer_manager.queue.transfers])
            if persisted_count > 0:
                status_bar.show_message(f"Loaded {persisted_count} transfer(s) from previous session")

            # Start auto-refresh if enabled
            if settings.auto_refresh_interval > 0:
                self._start_auto_refresh(settings.auto_refresh_interval)

        except SFTPError as e:
            self.push_screen(
                ErrorModal(str(e)),
                lambda _: self._show_connection_dialog()
            )

    def _start_auto_refresh(self, interval: int) -> None:
        """Start auto-refresh timer."""

        async def refresh_loop():
            while True:
                await asyncio.sleep(interval)
                file_browser = self.query_one("#file-browser", FileBrowser)
                file_browser.action_refresh()

        self._refresh_timer = asyncio.create_task(refresh_loop())

    def _on_transfer_progress(self, transfer: Transfer) -> None:
        """Handle transfer progress updates."""
        transfer_list = self.query_one("#transfer-list", TransferList)
        transfer_list.update_transfer(transfer)

        # Update status bar speed
        if self.transfer_manager:
            status_bar = self.query_one("#status-bar", StatusBar)
            status_bar.set_speeds(self.transfer_manager.total_speed)

    def _on_transfer_status_change(self, transfer: Transfer) -> None:
        """Handle transfer status changes."""
        transfer_list = self.query_one("#transfer-list", TransferList)
        transfer_list.refresh_display()

    def on_file_browser_file_selected(self, event: FileBrowser.FileSelected) -> None:
        """Handle file selection - queue for download."""
        self._queue_downloads(event.files)

    def on_file_browser_directory_changed(self, event: FileBrowser.DirectoryChanged) -> None:
        """Save current directory to host cache for session restore."""
        if self._current_host:
            self._current_host.last_directory = event.path
            self.host_cache.add(self._current_host)

    @work(exclusive=True)
    async def _queue_downloads(self, files: list[RemoteFile]) -> None:
        """Queue files for download."""
        status_bar = self.query_one("#status-bar", StatusBar)

        if not self.transfer_manager:
            status_bar.show_message("Not connected", error=True)
            return

        count = 0
        skipped = 0
        cancelled = 0

        for f in files:
            if f.is_dir:
                # Recursively get all files in directory
                dir_files = await self._list_dir_recursive(f.path)
                for df in dir_files:
                    result = await self._add_download_with_exists_check(df)
                    if result == "added":
                        count += 1
                    elif result == "skipped":
                        skipped += 1
                    else:  # cancelled
                        cancelled += 1
            else:
                result = await self._add_download_with_exists_check(f)
                if result == "added":
                    count += 1
                elif result == "skipped":
                    skipped += 1
                else:  # cancelled
                    cancelled += 1

        if count > 0 or skipped > 0 or cancelled > 0:
            parts = []
            if count > 0:
                parts.append(f"Queued {count}")
            if skipped > 0:
                parts.append(f"{skipped} already queued")
            if cancelled > 0:
                parts.append(f"{cancelled} cancelled")
            status_bar.show_message(", ".join(parts))

        transfer_list = self.query_one("#transfer-list", TransferList)
        transfer_list.refresh_display()

        # Refresh file browser to show queue indicators
        file_browser = self.query_one("#file-browser", FileBrowser)
        file_browser.refresh_directory()

    async def _add_download_with_exists_check(self, f: RemoteFile) -> str:
        """Add a file to download queue, checking if local file exists.

        Returns:
            "added" - file was added to queue
            "skipped" - file was already in queue
            "cancelled" - user cancelled the action
        """
        status_bar = self.query_one("#status-bar", StatusBar)

        # Check if local file already exists
        local_path = self._compute_local_path(f)
        if local_path.exists():
            local_size = local_path.stat().st_size

            # Show modal and wait for user decision
            result = await self.push_screen_wait(
                FileExistsModal(f.name, local_size, f.size)
            )

            if result == "continue":
                # Resume - add to queue, existing partial file will be used
                pass
            elif result == "replace":
                # Delete existing file and download fresh
                try:
                    local_path.unlink()
                except OSError as e:
                    status_bar.show_message(f"Error deleting file: {e}", error=True)
                    return "cancelled"
            else:
                # Cancel - skip this file
                return "cancelled"

        # Add to queue
        try:
            transfer = self.transfer_manager.add_download(f, host=self._current_host)
            if transfer:
                return "added"
            else:
                return "skipped"  # Already in queue
        except Exception as e:
            status_bar.show_message(f"Error: {e}", error=True)
            return "cancelled"

    async def _list_dir_recursive(self, path: str) -> list[RemoteFile]:
        """Recursively list all files in a directory."""
        all_files = []
        entries = await self.sftp.list_dir(path)
        for entry in entries:
            if entry.is_dir:
                all_files.extend(await self._list_dir_recursive(entry.path))
            else:
                all_files.append(entry)
        return all_files

    def _compute_local_path(self, remote_file: RemoteFile) -> Path:
        """Compute local path for a remote file (mirrors add_download logic)."""
        download_dir = Path(self.settings_manager.settings.download_dir).expanduser()
        safe_name = Path(remote_file.name).name
        return (download_dir / safe_name).resolve()

    def on_transfer_list_transfer_action(self, event: TransferList.TransferAction) -> None:
        """Handle transfer actions."""
        if not self.transfer_manager:
            return

        if event.action == "pause":
            self.transfer_manager.pause_transfer(event.transfer_id)
        elif event.action == "resume":
            self.transfer_manager.resume_transfer(event.transfer_id)
        elif event.action == "remove":
            self.transfer_manager.remove_transfer(event.transfer_id)

        transfer_list = self.query_one("#transfer-list", TransferList)
        transfer_list.refresh_display()

    def action_help(self) -> None:
        """Show help screen."""
        self.push_screen(HelpScreen())

    def action_download(self) -> None:
        """Queue selected files for download."""
        file_browser = self.query_one("#file-browser", FileBrowser)
        files = file_browser.queue_selected()
        if files:
            self._queue_downloads(files)

    def action_upload(self) -> None:
        """Upload action - not yet implemented."""
        status_bar = self.query_one("#status-bar", StatusBar)
        status_bar.show_message("Upload: Select local file (not yet implemented)")

    def action_stop_all(self) -> None:
        """Stop all queue processing - no new downloads will start."""
        if not self.transfer_manager:
            return

        count = self.transfer_manager.stop_queue()
        status_bar = self.query_one("#status-bar", StatusBar)
        if count > 0:
            status_bar.show_message(f"Queue stopped, paused {count} download(s)")
        else:
            status_bar.show_message("Queue stopped")

        transfer_list = self.query_one("#transfer-list", TransferList)
        transfer_list.refresh_display()

    def action_resume_all(self) -> None:
        """Resume queue processing - downloads will start from queue."""
        if not self.transfer_manager:
            return

        count = self.transfer_manager.resume_queue()
        status_bar = self.query_one("#status-bar", StatusBar)
        if count > 0:
            status_bar.show_message(f"Queue resumed, {count} download(s) queued")
        else:
            status_bar.show_message("Queue resumed")

        transfer_list = self.query_one("#transfer-list", TransferList)
        transfer_list.refresh_display()

    def action_options(self) -> None:
        """Open download directory settings."""
        current = self.settings_manager.settings.download_dir
        self.push_screen(DownloadDirModal(current), self._on_download_dir_result)

    def _on_download_dir_result(self, path: Optional[str]) -> None:
        """Handle download directory change."""
        if path:
            # Expand and validate path
            expanded = Path(path).expanduser()
            if not expanded.exists():
                try:
                    expanded.mkdir(parents=True, exist_ok=True)
                except OSError as e:
                    status_bar = self.query_one("#status-bar", StatusBar)
                    status_bar.show_message(f"Invalid path: {e}", error=True)
                    return

            # Update settings
            self.settings_manager.update(download_dir=path)

            # Update status bar
            status_bar = self.query_one("#status-bar", StatusBar)
            status_bar.set_download_dir(path)
            status_bar.show_message(f"Download dir: {path}")

    def action_toggle_transfers(self) -> None:
        """Toggle transfer list visibility."""
        transfer_list = self.query_one("#transfer-list", TransferList)
        transfer_list.toggle_class("hidden")

    async def action_quit(self) -> None:
        """Quit the application - pause all transfers and persist queue."""
        # Clean up refresh timer
        if self._refresh_timer:
            self._refresh_timer.cancel()

        # Pause all active transfers for resumable state
        if self.transfer_manager:
            self.transfer_manager.pause_all()

            # Stop transfer manager and wait for tasks to complete
            await self.transfer_manager.stop()

            # Persist final queue state
            self.transfer_manager._persist_queue()

        if self._transfer_task:
            self._transfer_task.cancel()
            try:
                await self._transfer_task
            except asyncio.CancelledError:
                pass

        # Give asyncssh a moment to clean up before disconnecting
        await asyncio.sleep(0.1)

        # Disconnect using pool if available, otherwise single connection
        if hasattr(self, 'connection_pool') and self.connection_pool:
            await self.connection_pool.disconnect_all()
        elif self.sftp:
            await self.sftp.disconnect()

        self.exit()
