"""Status bar widget showing connection info and global stats."""

from textual.app import ComposeResult
from textual.widgets import Static


class StatusBar(Static):
    """Bottom status bar with connection info and speed."""

    DEFAULT_CSS = """
    StatusBar {
        height: 1;
        dock: bottom;
        background: $primary;
        color: $text;
        padding: 0 1;
    }

    StatusBar .status-left {
        width: 1fr;
    }

    StatusBar .status-right {
        width: auto;
    }
    """

    def __init__(self, id: str | None = None) -> None:
        super().__init__(id=id)
        self._host: str = ""
        self._connected: bool = False
        self._download_speed: float = 0.0
        self._upload_speed: float = 0.0
        self._download_dir: str = ""

    def compose(self) -> ComposeResult:
        yield Static("", classes="status-left", id="status-left")
        yield Static("", classes="status-right", id="status-right")

    def set_connection(self, host: str, connected: bool) -> None:
        """Update connection status."""
        self._host = host
        self._connected = connected
        self._update()

    def set_speeds(self, download: float, upload: float = 0.0) -> None:
        """Update transfer speeds."""
        self._download_speed = download
        self._upload_speed = upload
        self._update()

    def set_download_dir(self, path: str) -> None:
        """Update download directory display."""
        self._download_dir = path
        self._update()

    def _format_speed(self, speed: float) -> str:
        """Format speed in human-readable format."""
        if speed == 0:
            return "0 B/s"
        units = ["B/s", "KB/s", "MB/s", "GB/s"]
        for unit in units[:-1]:
            if speed < 1024:
                return f"{speed:.1f} {unit}"
            speed /= 1024
        return f"{speed:.1f} {units[-1]}"

    def _update(self) -> None:
        """Update the status bar display."""
        left = self.query_one("#status-left", Static)
        right = self.query_one("#status-right", Static)

        # Left side: connection info + download dir
        if self._connected:
            status = f"[green]●[/] {self._host}"
        else:
            status = "[red]●[/] Disconnected"

        if self._download_dir:
            # Truncate long paths with ellipsis
            dir_display = self._download_dir
            if len(dir_display) > 30:
                dir_display = "..." + dir_display[-27:]
            status += f" → {dir_display}"

        left.update(status)

        # Right side: speeds
        parts = []
        if self._download_speed > 0:
            parts.append(f"↓ {self._format_speed(self._download_speed)}")
        if self._upload_speed > 0:
            parts.append(f"↑ {self._format_speed(self._upload_speed)}")

        right.update(" | ".join(parts) if parts else "")

    def show_message(self, message: str, error: bool = False) -> None:
        """Show a temporary message."""
        left = self.query_one("#status-left", Static)
        color = "red" if error else "yellow"
        left.update(f"[{color}]{message}[/]")
