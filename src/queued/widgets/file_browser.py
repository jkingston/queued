"""Remote file browser widget."""

from pathlib import PurePosixPath

from textual import work
from textual.app import ComposeResult
from textual.binding import Binding
from textual.containers import Vertical
from textual.message import Message
from textual.widgets import DataTable, Label, Static

from queued.models import RemoteFile, TransferQueue, TransferStatus
from queued.sftp import SFTPClient, SFTPError, is_connection_error


class FileBrowser(Static):
    """Remote file browser with selection support."""

    BINDINGS = [
        Binding("enter", "select", "Open/Queue", key_display="↵/l"),
        Binding("l", "select", "Open/Queue", show=False),
        Binding("space", "toggle_select", "Select"),
        Binding("backspace", "go_up", "Up Dir", key_display="←/h"),
        Binding("h", "go_up", "Up Dir", show=False),
        Binding("r", "refresh", "Refresh"),
        Binding("a", "select_all", "Sel All"),
        Binding("escape", "clear_selection", "Clear", key_display="Esc"),
        Binding("d", "download", "Download"),
        Binding("j", "cursor_down", show=False),
        Binding("k", "cursor_up", show=False),
        Binding("ctrl+f", "page_down", show=False),
        Binding("ctrl+b", "page_up", show=False),
    ]

    DEFAULT_CSS = """
    FileBrowser {
        height: 100%;
        border: solid $primary;
    }

    FileBrowser > Vertical {
        height: 100%;
    }

    FileBrowser #path-label {
        height: 1;
        background: $primary;
        color: $text;
        padding: 0 1;
    }

    FileBrowser #file-table {
        height: 1fr;
    }

    FileBrowser #status-label {
        height: 1;
        background: $surface;
        color: $text-muted;
        padding: 0 1;
    }
    """

    class FileSelected(Message):
        """File selected for action."""

        def __init__(self, files: list[RemoteFile]) -> None:
            super().__init__()
            self.files = files

    class DirectoryChanged(Message):
        """Directory changed."""

        def __init__(self, path: str) -> None:
            super().__init__()
            self.path = path

    class DownloadRequested(Message):
        """Download requested for files."""

        def __init__(self, files: list[RemoteFile]) -> None:
            super().__init__()
            self.files = files

    class ConnectionLost(Message):
        """Connection to server was lost."""

        pass

    def __init__(
        self,
        sftp_client: SFTPClient | None = None,
        id: str | None = None,
    ) -> None:
        super().__init__(id=id)
        self.sftp = sftp_client
        self.current_path = "/"
        self._files: list[RemoteFile] = []
        self._selected: set[str] = set()  # Set of selected file paths
        self._loading = False
        self._transfer_queue: TransferQueue | None = None

    def compose(self) -> ComposeResult:
        with Vertical():
            yield Label("Remote Files", id="path-label")
            yield DataTable(id="file-table", cursor_type="row")
            yield Label("", id="status-label")

    def on_mount(self) -> None:
        """Set up the data table."""
        table = self.query_one("#file-table", DataTable)
        table.add_columns("", "Name", "Size", "Modified")
        table.cursor_type = "row"

    def set_sftp_client(self, sftp: SFTPClient) -> None:
        """Set the SFTP client (caller should call load_directory)."""
        self.sftp = sftp

    def set_transfer_queue(self, queue: TransferQueue) -> None:
        """Set the transfer queue for displaying queue status indicators."""
        self._transfer_queue = queue

    @work(exclusive=True)
    async def load_directory(self, path: str) -> None:
        """Load directory contents."""
        if not self.sftp or not self.sftp.connected:
            self.post_message(self.ConnectionLost())
            return

        self._loading = True
        self._update_status("Loading...")

        try:
            # Normalize path
            path = str(PurePosixPath(path))
            self._files = await self.sftp.list_dir(path)

            # Only clear selections when navigating to a different directory
            if path != self.current_path:
                self._selected.clear()

            self.current_path = path
            self._update_table()
            self._update_path_label()
            self.post_message(self.DirectoryChanged(path))
        except SFTPError as e:
            self._update_status(f"Error: {e}")
        except Exception as e:
            if is_connection_error(e):
                self.post_message(self.ConnectionLost())
            else:
                self._update_status(f"Error: {e}")
        finally:
            self._loading = False

    def refresh_directory(self) -> None:
        """Refresh current directory, preserving selections."""
        self.load_directory(self.current_path)

    def _update_table(self) -> None:
        """Update the data table with current files using in-place updates."""
        table = self.query_one("#file-table", DataTable)
        current_keys = {key.value for key in table.rows}

        # Build new key set (including ".." if not at root)
        new_keys = {f.path for f in self._files}
        if self.current_path != "/":
            new_keys.add("..")

        # Remove rows that no longer exist
        for key in current_keys - new_keys:
            table.remove_row(key)

        # Add ".." entry if needed (static content, no update needed)
        if self.current_path != "/" and ".." not in current_keys:
            table.add_row("", "..", "<DIR>", "", key="..")

        # Update or add file rows
        for f in self._files:
            row_data = self._build_file_row(f)
            if f.path in current_keys:
                # Update existing row cells
                for col_idx, value in enumerate(row_data):
                    col_key = list(table.columns.keys())[col_idx]
                    table.update_cell(f.path, col_key, value)
            else:
                # Add new row
                table.add_row(*row_data, key=f.path)

        self._update_status(f"{len(self._files)} items")

    def _build_file_row(self, f: RemoteFile) -> tuple:
        """Build row data tuple for a file."""
        icon = self._get_file_icon(f)
        name = f"[bold blue]{f.name}/[/]" if f.is_dir else f.name
        size = f.size_human
        mtime = f.mtime.strftime("%Y-%m-%d %H:%M") if f.mtime else ""
        return (icon, name, size, mtime)

    def _get_file_icon(self, f: RemoteFile) -> str:
        """Get icon for file based on queue status and selection."""
        if self._transfer_queue:
            if f.is_dir:
                if self._transfer_queue.has_queued_in_directory(f.path):
                    return "[dim]◌[/]"  # Has queued files
            else:
                transfer = self._transfer_queue.get_by_remote_path(f.path)
                terminal_statuses = (TransferStatus.COMPLETED, TransferStatus.FAILED)
                if transfer and transfer.status not in terminal_statuses:
                    if transfer.status == TransferStatus.TRANSFERRING:
                        return "[green]↓[/]"  # Downloading
                    elif transfer.status == TransferStatus.PAUSED:
                        return "[yellow]⏸[/]"  # Paused
                    elif transfer.status == TransferStatus.STOPPED:
                        return "[dim yellow]⏹[/]"  # Stopped (queue stopped)
                    else:  # QUEUED or other pending states
                        return "[dim]◌[/]"  # Queued
        if f.path in self._selected:
            return "[cyan]>>[/]"
        return ""

    def _update_path_label(self) -> None:
        """Update the path label."""
        label = self.query_one("#path-label", Label)
        label.update(f" Remote: {self.current_path}")

    def _update_status(self, text: str) -> None:
        """Update status label."""
        label = self.query_one("#status-label", Label)
        selected_count = len(self._selected)
        if selected_count > 0:
            label.update(f"{text} | {selected_count} selected")
        else:
            label.update(text)

    def on_data_table_row_selected(self, event: DataTable.RowSelected) -> None:
        """Handle row selection (Enter key)."""
        if event.row_key is None:
            return

        key = str(event.row_key.value)

        if key == "..":
            self.action_go_up()
        else:
            # Find the file
            for f in self._files:
                if f.path == key:
                    if f.is_dir:
                        self.load_directory(f.path)
                    else:
                        # Single file selected - add to selection and emit
                        if f.path not in self._selected:
                            self._selected.add(f.path)
                        self._emit_selection()
                    break

    def action_select(self) -> None:
        """Select action - triggers row selection."""
        table = self.query_one("#file-table", DataTable)
        if table.cursor_row is not None:
            table.action_select_cursor()

    def action_toggle_select(self) -> None:
        """Toggle selection of current file."""
        table = self.query_one("#file-table", DataTable)
        if table.cursor_row is None:
            return

        row_key = table.get_row_at(table.cursor_row)
        if row_key is None:
            return

        # Get the key from the table
        keys = list(table.rows.keys())
        if table.cursor_row >= len(keys):
            return

        key = str(keys[table.cursor_row].value)
        if key == "..":
            return

        # Save cursor position before table rebuild
        saved_cursor = table.cursor_row

        # Toggle selection
        if key in self._selected:
            self._selected.remove(key)
        else:
            self._selected.add(key)

        self._update_table()

        # Restore cursor position and move down
        table.move_cursor(row=saved_cursor)
        table.action_cursor_down()

    def action_go_up(self) -> None:
        """Go to parent directory."""
        if self.current_path == "/":
            return
        parent = str(PurePosixPath(self.current_path).parent)
        self.load_directory(parent)

    def action_refresh(self) -> None:
        """Refresh current directory."""
        self.load_directory(self.current_path)

    def action_select_all(self) -> None:
        """Select all files (not directories)."""
        for f in self._files:
            if not f.is_dir:
                self._selected.add(f.path)
        self._update_table()

    def action_clear_selection(self) -> None:
        """Clear all selections."""
        self._selected.clear()
        self._update_table()

    def action_cursor_down(self) -> None:
        """Move cursor down in file list."""
        table = self.query_one("#file-table", DataTable)
        table.action_cursor_down()

    def action_cursor_up(self) -> None:
        """Move cursor up in file list."""
        table = self.query_one("#file-table", DataTable)
        table.action_cursor_up()

    def action_page_down(self) -> None:
        """Move down one page in file list."""
        table = self.query_one("#file-table", DataTable)
        table.action_page_down()

    def action_page_up(self) -> None:
        """Move up one page in file list."""
        table = self.query_one("#file-table", DataTable)
        table.action_page_up()

    def _emit_selection(self) -> None:
        """Emit FileSelected message with current selection."""
        selected_files = [f for f in self._files if f.path in self._selected]
        if selected_files:
            self.post_message(self.FileSelected(selected_files))
            self._selected.clear()
            self._update_table()

    def get_selected_files(self) -> list[RemoteFile]:
        """Get currently selected files."""
        return [f for f in self._files if f.path in self._selected]

    def queue_selected(self) -> list[RemoteFile]:
        """Get selected files (or file under cursor if none selected)."""
        if self._selected:
            files = self.get_selected_files()
            self._selected.clear()
            self._update_table()
            return files
        else:
            # Nothing selected - return file under cursor
            table = self.query_one("#file-table", DataTable)
            if table.cursor_row is None:
                return []

            keys = list(table.rows.keys())
            if table.cursor_row >= len(keys):
                return []

            key = str(keys[table.cursor_row].value)
            if key == "..":
                return []

            for f in self._files:
                if f.path == key:
                    return [f]
            return []

    def action_download(self) -> None:
        """Queue selected files for download."""
        files = self.queue_selected()
        if files:
            self.post_message(self.DownloadRequested(files))
