"""Tests for TransferList widget cursor behavior."""

import uuid

from textual.app import App, ComposeResult
from textual.widgets import DataTable

from queued.models import Transfer, TransferDirection, TransferQueue, TransferStatus
from queued.widgets.transfer_list import TransferList


def create_transfer(remote_path: str, local_path: str, size: int = 1000) -> Transfer:
    """Create a Transfer with required fields."""
    return Transfer(
        id=str(uuid.uuid4()),
        remote_path=remote_path,
        local_path=local_path,
        direction=TransferDirection.DOWNLOAD,
        size=size,
        host_key="user@host:22",
    )


class TransferListTestApp(App):
    """Test app that hosts a TransferList widget."""

    def __init__(self):
        super().__init__()
        self.queue = TransferQueue(max_concurrent=2)

    def compose(self) -> ComposeResult:
        yield TransferList(id="transfers")


class TestTransferListCursor:
    """Tests for cursor preservation in TransferList."""

    async def test_refresh_display_preserves_cursor_position(self):
        """Cursor should stay on same transfer after refresh_display()."""
        app = TransferListTestApp()

        # Add multiple transfers
        for i in range(5):
            transfer = create_transfer(
                remote_path=f"/path/file{i}.txt",
                local_path=f"/tmp/file{i}.txt",
                size=1000 * (i + 1),
            )
            app.queue.transfers.append(transfer)

        async with app.run_test() as pilot:
            transfer_list = app.query_one("#transfers", TransferList)
            transfer_list.set_queue(app.queue)

            table = transfer_list.query_one("#transfer-table", DataTable)

            # Move cursor to row 2
            await pilot.press("down")
            await pilot.press("down")

            # Get the transfer ID at current position
            keys = list(table.rows.keys())
            cursor_before = table.cursor_row
            transfer_id_before = keys[cursor_before].value

            # Call refresh_display (this is what caused the bug)
            transfer_list.refresh_display()

            # Cursor should still be on same row/transfer
            cursor_after = table.cursor_row
            keys_after = list(table.rows.keys())
            transfer_id_after = keys_after[cursor_after].value

            assert cursor_after == cursor_before, (
                f"Cursor jumped from row {cursor_before} to {cursor_after}"
            )
            assert transfer_id_after == transfer_id_before, (
                f"Cursor was on transfer {transfer_id_before}, now on {transfer_id_after}"
            )

    async def test_cursor_preserved_after_multiple_refreshes(self):
        """Cursor should stay stable through multiple refresh cycles."""
        app = TransferListTestApp()

        for i in range(3):
            transfer = create_transfer(
                remote_path=f"/path/file{i}.txt",
                local_path=f"/tmp/file{i}.txt",
            )
            app.queue.transfers.append(transfer)

        async with app.run_test() as pilot:
            transfer_list = app.query_one("#transfers", TransferList)
            transfer_list.set_queue(app.queue)
            table = transfer_list.query_one("#transfer-table", DataTable)

            # Move to last row
            await pilot.press("down")
            await pilot.press("down")

            cursor_before = table.cursor_row
            keys = list(table.rows.keys())
            transfer_id = keys[cursor_before].value

            # Simulate rapid refresh cycles (like during active transfer)
            for _ in range(10):
                transfer_list.refresh_display()

            # Cursor should still be on same transfer
            cursor_after = table.cursor_row
            keys_after = list(table.rows.keys())

            assert cursor_after == cursor_before
            assert keys_after[cursor_after].value == transfer_id

    async def test_cursor_handles_removed_transfer(self):
        """When the selected transfer is removed, cursor should stay in bounds."""
        app = TransferListTestApp()

        transfers = []
        for i in range(3):
            transfer = create_transfer(
                remote_path=f"/path/file{i}.txt",
                local_path=f"/tmp/file{i}.txt",
            )
            app.queue.transfers.append(transfer)
            transfers.append(transfer)

        async with app.run_test() as pilot:
            transfer_list = app.query_one("#transfers", TransferList)
            transfer_list.set_queue(app.queue)
            table = transfer_list.query_one("#transfer-table", DataTable)

            # Move to row 2 (last row, 0-indexed)
            await pilot.press("down")
            await pilot.press("down")
            assert table.cursor_row == 2

            # Remove the transfer at row 2
            app.queue.remove(transfers[2].id)
            transfer_list.refresh_display()

            # Cursor should not exceed new row count
            assert table.cursor_row < len(app.queue.transfers)

    async def test_update_transfer_preserves_cursor(self):
        """update_transfer() with debouncing should preserve cursor."""
        app = TransferListTestApp()

        for i in range(3):
            t = create_transfer(
                remote_path=f"/path/file{i}.txt",
                local_path=f"/tmp/file{i}.txt",
            )
            app.queue.transfers.append(t)

        async with app.run_test() as pilot:
            transfer_list = app.query_one("#transfers", TransferList)
            transfer_list.set_queue(app.queue)
            table = transfer_list.query_one("#transfer-table", DataTable)

            # Move cursor to row 1
            await pilot.press("down")
            cursor_before = table.cursor_row

            # Simulate transfer progress update
            transfer_list.update_transfer(app.queue.transfers[0])
            await pilot.pause()

            # Cursor should be preserved
            assert table.cursor_row == cursor_before


class TestTransferListActions:
    """Tests for transfer list actions."""

    async def test_pause_action_on_transferring(self):
        """Pause action should be available for transferring item."""
        app = TransferListTestApp()

        transfer = create_transfer(
            remote_path="/path/file.txt",
            local_path="/tmp/file.txt",
        )
        transfer.status = TransferStatus.TRANSFERRING
        app.queue.transfers.append(transfer)

        async with app.run_test():
            transfer_list = app.query_one("#transfers", TransferList)
            transfer_list.set_queue(app.queue)

            table = transfer_list.query_one("#transfer-table", DataTable)

            # Verify the transfer is in the table
            assert len(list(table.rows.keys())) == 1
            assert list(table.rows.keys())[0].value == transfer.id

            # Verify cursor is on the transfer
            assert table.cursor_row == 0
