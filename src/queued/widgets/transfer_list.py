"""Transfer queue list widget with progress display."""

import time

from textual.app import ComposeResult
from textual.binding import Binding
from textual.containers import Vertical
from textual.message import Message
from textual.widgets import DataTable, Label, Static

from queued.models import Transfer, TransferQueue, TransferStatus

# Minimum interval between UI refreshes (100ms = 10 updates/sec max)
MIN_REFRESH_INTERVAL = 0.1


class TransferList(Static):
    """Transfer queue display with progress bars."""

    BINDINGS = [
        Binding("p", "pause", "Pause/Resume", show=True),
        Binding("x", "remove", "Remove", show=True),
        Binding("up", "move_up", "Move Up", show=False),
        Binding("down", "move_down", "Move Down", show=False),
    ]

    DEFAULT_CSS = """
    TransferList {
        height: 100%;
        border: solid $secondary;
    }

    TransferList > Vertical {
        height: 100%;
    }

    TransferList #transfers-label {
        height: 1;
        background: $secondary;
        color: $text;
        padding: 0 1;
    }

    TransferList #transfer-table {
        height: 1fr;
    }

    TransferList #transfers-status {
        height: 1;
        background: $surface;
        color: $text-muted;
        padding: 0 1;
    }
    """

    class TransferAction(Message):
        """Transfer action requested."""

        def __init__(self, transfer_id: str, action: str) -> None:
            super().__init__()
            self.transfer_id = transfer_id
            self.action = action

    def __init__(self, id: str | None = None) -> None:
        super().__init__(id=id)
        self.queue: TransferQueue | None = None
        self._last_refresh_time: float = 0.0
        self._refresh_pending: bool = False

    def compose(self) -> ComposeResult:
        with Vertical():
            yield Label(" Transfers", id="transfers-label")
            yield DataTable(id="transfer-table", cursor_type="row")
            yield Label("", id="transfers-status")

    def on_mount(self) -> None:
        """Set up the data table."""
        table = self.query_one("#transfer-table", DataTable)
        table.add_columns("File", "Progress", "Speed", "ETA", "Status")
        table.cursor_type = "row"

    def set_queue(self, queue: TransferQueue) -> None:
        """Set the transfer queue to display."""
        self.queue = queue
        self.refresh_display()

    def refresh_display(self) -> None:
        """Refresh the transfer list display."""
        if not self.queue:
            return

        table = self.query_one("#transfer-table", DataTable)
        table.clear()

        for transfer in self.queue.transfers:
            # Filename (auto-sizes to available width)
            filename = transfer.filename

            # Progress bar as text
            progress = self._make_progress_bar(transfer.progress)

            # Speed (separate from ETA)
            speed = transfer.speed_human if transfer.status == TransferStatus.TRANSFERRING else ""

            # ETA as separate column
            eta = transfer.eta or "" if transfer.status == TransferStatus.TRANSFERRING else ""

            # Status
            status = self._format_status(transfer)

            table.add_row(
                filename,
                progress,
                speed,
                eta,
                status,
                key=transfer.id,
            )

        self._update_status()

    def _make_progress_bar(self, percent: float) -> str:
        """Create a text-based progress bar."""
        width = 8
        filled = int(width * percent / 100)
        empty = width - filled
        bar = "█" * filled + "░" * empty
        return f"[{bar}] {percent:3.0f}%"

    def _format_status(self, transfer: Transfer) -> str:
        """Format transfer status for display."""
        status_colors = {
            TransferStatus.QUEUED: "[dim]",
            TransferStatus.CONNECTING: "[yellow]",
            TransferStatus.TRANSFERRING: "[green]",
            TransferStatus.PAUSED: "[yellow]",
            TransferStatus.STOPPED: "[dim yellow]",
            TransferStatus.COMPLETED: "[green]",
            TransferStatus.FAILED: "[red]",
            TransferStatus.VERIFYING: "[cyan]",
        }

        color = status_colors.get(transfer.status, "")
        status_text = transfer.status.value.capitalize()

        if transfer.status == TransferStatus.FAILED and transfer.error:
            status_text = f"Failed: {transfer.error[:15]}"
        elif transfer.status == TransferStatus.COMPLETED:
            status_text = "✓ Complete"
        elif transfer.status == TransferStatus.PAUSED:
            status_text = "⏸ Paused"
        elif transfer.status == TransferStatus.STOPPED:
            status_text = "⏹ Stopped"

        return f"{color}{status_text}[/]"

    def _update_status(self) -> None:
        """Update the status bar."""
        if not self.queue:
            return

        label = self.query_one("#transfers-status", Label)

        active = self.queue.active_count
        queued = len([t for t in self.queue.transfers if t.status == TransferStatus.QUEUED])
        paused = len([t for t in self.queue.transfers if t.status == TransferStatus.PAUSED])
        completed = len([t for t in self.queue.transfers if t.status == TransferStatus.COMPLETED])

        parts = [f"Active: {active}"]
        if queued > 0:
            parts.append(f"Queued: {queued}")
        if paused > 0:
            parts.append(f"Paused: {paused}")
        if completed > 0:
            parts.append(f"Done: {completed}")

        label.update(" | ".join(parts))

    def action_pause(self) -> None:
        """Pause or resume the selected transfer."""
        table = self.query_one("#transfer-table", DataTable)
        if table.cursor_row is None or not self.queue:
            return

        keys = list(table.rows.keys())
        if table.cursor_row >= len(keys):
            return

        transfer_id = str(keys[table.cursor_row].value)
        transfer = self.queue.get_by_id(transfer_id)

        if transfer:
            if transfer.status == TransferStatus.TRANSFERRING:
                self.post_message(self.TransferAction(transfer_id, "pause"))
            elif transfer.status == TransferStatus.PAUSED:
                self.post_message(self.TransferAction(transfer_id, "resume"))

    def action_remove(self) -> None:
        """Remove the selected transfer."""
        table = self.query_one("#transfer-table", DataTable)
        if table.cursor_row is None or not self.queue:
            return

        keys = list(table.rows.keys())
        if table.cursor_row >= len(keys):
            return

        transfer_id = str(keys[table.cursor_row].value)
        self.post_message(self.TransferAction(transfer_id, "remove"))

    def action_move_up(self) -> None:
        """Move selected transfer up in queue."""
        table = self.query_one("#transfer-table", DataTable)
        if table.cursor_row is None or not self.queue:
            return

        keys = list(table.rows.keys())
        if table.cursor_row >= len(keys):
            return

        transfer_id = str(keys[table.cursor_row].value)
        if self.queue.move_up(transfer_id):
            self.refresh_display()
            table.move_cursor(row=max(0, table.cursor_row - 1))

    def action_move_down(self) -> None:
        """Move selected transfer down in queue."""
        table = self.query_one("#transfer-table", DataTable)
        if table.cursor_row is None or not self.queue:
            return

        keys = list(table.rows.keys())
        if table.cursor_row >= len(keys):
            return

        transfer_id = str(keys[table.cursor_row].value)
        if self.queue.move_down(transfer_id):
            self.refresh_display()
            table.move_cursor(row=min(len(self.queue.transfers) - 1, table.cursor_row + 1))

    def update_transfer(self, transfer: Transfer) -> None:
        """Update a single transfer's display with debouncing.

        Limits refreshes to max 10 per second to prevent UI lag.
        """
        now = time.monotonic()
        elapsed = now - self._last_refresh_time

        if elapsed >= MIN_REFRESH_INTERVAL:
            # Enough time has passed, refresh immediately
            self._last_refresh_time = now
            self._refresh_pending = False
            self.refresh_display()
        elif not self._refresh_pending:
            # Schedule a delayed refresh
            self._refresh_pending = True
            delay = MIN_REFRESH_INTERVAL - elapsed
            self.set_timer(delay, self._do_pending_refresh)

    def _do_pending_refresh(self) -> None:
        """Execute pending refresh after debounce delay."""
        if self._refresh_pending:
            self._refresh_pending = False
            self._last_refresh_time = time.monotonic()
            self.refresh_display()
