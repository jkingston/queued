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
        Binding("enter", "toggle_pause", "Pause/Resume", key_display="↵/p"),
        Binding("p", "toggle_pause", "Pause/Resume", show=False),
        Binding("space", "toggle_queue", "Stop/Start All"),
        Binding("x", "remove", "Remove", key_display="x/Del"),
        Binding("delete", "remove", "Remove", show=False),
        Binding("j", "cursor_down", show=False),
        Binding("k", "cursor_up", show=False),
        Binding("shift+up", "move_up", "Move Up", key_display="⇧↑/K"),
        Binding("shift+down", "move_down", "Move Down", key_display="⇧↓/J"),
        Binding("K", "move_up", show=False),
        Binding("J", "move_down", show=False),
        Binding("ctrl+f", "page_down", show=False),
        Binding("ctrl+b", "page_up", show=False),
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
        """Refresh the transfer list display using in-place updates."""
        if not self.queue:
            return

        table = self.query_one("#transfer-table", DataTable)
        current_keys = {key.value for key in table.rows}
        new_keys = {t.id for t in self.queue.transfers}

        # Remove rows that no longer exist
        for key in current_keys - new_keys:
            table.remove_row(key)

        # Update or add rows
        for transfer in self.queue.transfers:
            row_data = self._build_row_data(transfer)
            if transfer.id in current_keys:
                # Update existing row cells
                for col_idx, value in enumerate(row_data):
                    col_key = list(table.columns.keys())[col_idx]
                    table.update_cell(transfer.id, col_key, value)
            else:
                # Add new row
                table.add_row(*row_data, key=transfer.id)

        self._update_status()

    def _build_row_data(self, transfer: Transfer) -> tuple:
        """Build row data tuple for a transfer."""
        return (
            transfer.filename,
            self._make_progress_bar(transfer),
            transfer.speed_human if transfer.status == TransferStatus.TRANSFERRING else "",
            transfer.eta or "" if transfer.status == TransferStatus.TRANSFERRING else "",
            self._format_status(transfer),
        )

    def _make_progress_bar(self, transfer: Transfer) -> str:
        """Create a text-based progress bar with size info."""
        percent = transfer.progress
        width = 8
        filled = int(width * percent / 100)
        empty = width - filled
        bar = "█" * filled + "░" * empty

        sizes = self._format_size_pair(transfer.bytes_transferred, transfer.size)
        return f"{sizes} [{bar}] {percent:3.0f}%"

    def _format_size_pair(self, downloaded: int, total: int) -> str:
        """Format downloaded/total with same unit for alignment."""
        units = [("TB", 1024**4), ("GB", 1024**3), ("MB", 1024**2), ("KB", 1024)]

        for unit, divisor in units:
            if total >= divisor:
                dl = downloaded / divisor
                tot = total / divisor
                return f"{dl:5.1f}/{tot:5.1f} {unit}"

        # Bytes
        return f"{downloaded:5d}/{total:5d} B"

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

    def on_data_table_row_selected(self, event: DataTable.RowSelected) -> None:
        """Handle row selection (Enter key) - toggle pause/resume."""
        self.action_toggle_pause()

    def action_toggle_pause(self) -> None:
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
            if transfer.status in (TransferStatus.TRANSFERRING, TransferStatus.QUEUED):
                self.post_message(self.TransferAction(transfer_id, "pause"))
            elif transfer.status in (TransferStatus.PAUSED, TransferStatus.STOPPED):
                self.post_message(self.TransferAction(transfer_id, "resume"))

    def action_toggle_queue(self) -> None:
        """Stop or resume all queue processing."""
        self.post_message(self.TransferAction("", "toggle_queue"))

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

    def action_cursor_down(self) -> None:
        """Move cursor down in transfer list."""
        table = self.query_one("#transfer-table", DataTable)
        table.action_cursor_down()

    def action_cursor_up(self) -> None:
        """Move cursor up in transfer list."""
        table = self.query_one("#transfer-table", DataTable)
        table.action_cursor_up()

    def action_page_down(self) -> None:
        """Move down one page in transfer list."""
        table = self.query_one("#transfer-table", DataTable)
        table.action_page_down()

    def action_page_up(self) -> None:
        """Move up one page in transfer list."""
        table = self.query_one("#transfer-table", DataTable)
        table.action_page_up()

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
