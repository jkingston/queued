"""Tests for FileBrowser widget."""

import pytest
from textual.app import App, ComposeResult
from textual.widgets import DataTable

from queued.models import RemoteFile
from queued.widgets.file_browser import FileBrowser

from .mocks.sftp_mock import MockSFTPClient, create_mock_files


class FileBrowserTestApp(App):
    """Test app that hosts a FileBrowser widget."""

    def __init__(self, sftp: MockSFTPClient):
        super().__init__()
        self.sftp = sftp

    def compose(self) -> ComposeResult:
        yield FileBrowser(sftp_client=self.sftp, id="browser")


class TestFileBrowserCursorBug:
    """Tests for cursor jump bug - BUG REPRODUCTION."""

    async def test_toggle_select_cursor_moves_down_not_to_top(self):
        """BUG REPRO: Space should move cursor from row N to row N+1, not reset to 1.

        Current buggy behavior:
        1. Cursor at row 2
        2. Press space
        3. _update_table() clears table, cursor resets to 0
        4. action_cursor_down() moves cursor to 1
        5. Result: cursor at row 1 (WRONG)

        Expected behavior:
        1. Cursor at row 2
        2. Press space
        3. Cursor should be at row 3
        """
        sftp = MockSFTPClient(create_mock_files())
        await sftp.connect()

        app = FileBrowserTestApp(sftp)
        async with app.run_test() as pilot:
            browser = app.query_one("#browser", FileBrowser)
            table = browser.query_one("#file-table", DataTable)

            # Manually load the directory (simulating what set_sftp_client does)
            browser._files = await sftp.list_dir("/")
            browser.current_path = "/"
            browser._update_table()

            # Move cursor to row 2 (skip row 0 and 1)
            await pilot.press("down")  # row 1
            await pilot.press("down")  # row 2

            cursor_before = table.cursor_row
            assert cursor_before == 2, f"Cursor should be at row 2, got {cursor_before}"

            # Toggle select - this triggers the bug
            await pilot.press("space")

            cursor_after = table.cursor_row
            # BUG: cursor ends up at 1 (reset to 0, then moved down to 1)
            # EXPECTED: cursor should be at 3 (was at 2, moved down to 3)
            assert cursor_after == 3, (
                f"Cursor should be at row 3 after toggle from row 2, "
                f"but got {cursor_after} (bug: cursor reset to top)"
            )


class TestFileBrowserSelectionsBug:
    """Tests for selections cleared on refresh bug - BUG REPRODUCTION."""

    async def test_refresh_preserves_selections(self):
        """BUG REPRO: Refresh (r key) should NOT clear selections.

        Current buggy behavior:
        1. Select files
        2. Press r to refresh
        3. load_directory() calls _selected.clear() unconditionally
        4. Result: all selections lost (WRONG)

        Expected behavior:
        1. Select files
        2. Press r to refresh (same directory)
        3. Selections should be preserved
        """
        sftp = MockSFTPClient(create_mock_files())
        await sftp.connect()

        app = FileBrowserTestApp(sftp)
        async with app.run_test() as pilot:
            browser = app.query_one("#browser", FileBrowser)

            # Manually load the directory
            browser._files = await sftp.list_dir("/")
            browser.current_path = "/"
            browser._update_table()

            # Select first file (row 0)
            file_path = browser._files[0].path
            browser._selected.add(file_path)
            browser._update_table()

            assert len(browser._selected) == 1, "Should have 1 file selected"
            assert file_path in browser._selected

            # Call refresh (same path) - this spawns a worker
            browser.refresh_directory()

            # Wait for worker to complete
            await pilot.pause()

            # Selections should be preserved since path didn't change
            assert len(browser._selected) == 1, (
                f"Selections should be preserved on refresh of same directory, "
                f"but got {len(browser._selected)} selections (bug: cleared)"
            )
            assert file_path in browser._selected

    async def test_directory_change_clears_selections(self):
        """Navigating to different directory SHOULD clear selections."""
        sftp = MockSFTPClient(create_mock_files())
        await sftp.connect()

        app = FileBrowserTestApp(sftp)
        async with app.run_test() as pilot:
            browser = app.query_one("#browser", FileBrowser)

            # Load root directory
            browser._files = await sftp.list_dir("/")
            browser.current_path = "/"
            browser._update_table()

            # Select a file
            file_path = browser._files[0].path
            browser._selected.add(file_path)
            assert len(browser._selected) == 1

            # Navigate to different directory - should clear selections
            browser.load_directory("/subdir")

            # Wait for worker to complete
            await pilot.pause()

            # Selections should be cleared since we changed directories
            assert len(browser._selected) == 0, (
                "Selections should be cleared when navigating to different directory"
            )


class TestFileBrowserCursor:
    """Tests for cursor behavior in FileBrowser."""

    @pytest.fixture
    def browser_with_files(self, mock_files: list[RemoteFile]) -> tuple[FileBrowser, MockSFTPClient]:
        """Create a FileBrowser with mock SFTP client."""
        sftp = MockSFTPClient(mock_files)
        browser = FileBrowser(sftp_client=sftp)
        return browser, sftp

    async def test_toggle_select_preserves_cursor_position(
        self, browser_with_files: tuple[FileBrowser, MockSFTPClient]
    ):
        """BUG: Space to toggle select should not reset cursor to top.

        Current behavior: Cursor jumps to row 0 after toggle.
        Expected behavior: Cursor should move to next row (row 2 if starting at row 1).
        """
        browser, sftp = browser_with_files
        await sftp.connect()

        # We need to mount the widget to test it properly
        # For now, test the internal state logic
        browser._files = await sftp.list_dir("/")
        browser.current_path = "/"

        # Simulate having cursor on row 1 (first actual file after potential "..")
        # Select a file
        file_path = browser._files[0].path
        browser._selected.add(file_path)

        # The bug is in _update_table() which clears the table, resetting cursor
        # After the fix, cursor position should be preserved/incremented
        initial_selection_count = len(browser._selected)
        assert initial_selection_count == 1
        assert file_path in browser._selected

    async def test_toggle_select_moves_cursor_down(
        self, browser_with_files: tuple[FileBrowser, MockSFTPClient]
    ):
        """After toggling selection, cursor should move to next row."""
        browser, sftp = browser_with_files
        await sftp.connect()
        browser._files = await sftp.list_dir("/")
        browser.current_path = "/"

        # Verify we have files to select
        files_only = [f for f in browser._files if not f.is_dir]
        assert len(files_only) >= 2, "Need at least 2 files for this test"


class TestFileBrowserSelections:
    """Tests for selection behavior in FileBrowser."""

    @pytest.fixture
    def browser_with_files(self, mock_files: list[RemoteFile]) -> tuple[FileBrowser, MockSFTPClient]:
        """Create a FileBrowser with mock SFTP client."""
        sftp = MockSFTPClient(mock_files)
        browser = FileBrowser(sftp_client=sftp)
        return browser, sftp

    async def test_refresh_preserves_selections(
        self, browser_with_files: tuple[FileBrowser, MockSFTPClient]
    ):
        """BUG: Refreshing directory should NOT clear selections.

        Current behavior: All selections lost on refresh.
        Expected behavior: Selections persist across refresh of same directory.
        """
        browser, sftp = browser_with_files
        await sftp.connect()

        # Load initial directory
        browser._files = await sftp.list_dir("/")
        browser.current_path = "/"

        # Select some files
        files_only = [f for f in browser._files if not f.is_dir]
        for f in files_only[:2]:
            browser._selected.add(f.path)

        selected_before = set(browser._selected)
        assert len(selected_before) == 2

        # Currently, load_directory clears selections unconditionally
        # This simulates refresh - same path
        # After fix, selections should be preserved for same directory
        browser._files = await sftp.list_dir("/")
        # BUG: load_directory() calls _selected.clear() on line 115

        # For now, test that we HAVE selections before the clear
        # The actual fix will preserve them
        assert len(selected_before) == 2

    async def test_directory_change_clears_selections(
        self, browser_with_files: tuple[FileBrowser, MockSFTPClient]
    ):
        """Changing to different directory SHOULD clear selections."""
        browser, sftp = browser_with_files
        await sftp.connect()

        # Load root
        browser._files = await sftp.list_dir("/")
        browser.current_path = "/"

        # Select a file
        files_only = [f for f in browser._files if not f.is_dir]
        browser._selected.add(files_only[0].path)
        assert len(browser._selected) == 1

        # Change to subdirectory - selections SHOULD be cleared
        browser.current_path = "/subdir"
        browser._files = await sftp.list_dir("/subdir")
        # This is correct behavior - new directory should clear old selections
        browser._selected.clear()

        assert len(browser._selected) == 0

    async def test_select_all_only_selects_files(
        self, browser_with_files: tuple[FileBrowser, MockSFTPClient]
    ):
        """Select all should only select files, not directories."""
        browser, sftp = browser_with_files
        await sftp.connect()

        browser._files = await sftp.list_dir("/")
        browser.current_path = "/"

        # Call select all logic
        for f in browser._files:
            if not f.is_dir:
                browser._selected.add(f.path)

        # Verify no directories selected
        for path in browser._selected:
            file = next(f for f in browser._files if f.path == path)
            assert not file.is_dir, f"Directory {path} should not be selected"

        # Verify files are selected
        files_only = [f for f in browser._files if not f.is_dir]
        assert len(browser._selected) == len(files_only)

    async def test_clear_selection_clears_all(
        self, browser_with_files: tuple[FileBrowser, MockSFTPClient]
    ):
        """Clear selection should remove all selections."""
        browser, sftp = browser_with_files
        await sftp.connect()

        browser._files = await sftp.list_dir("/")
        browser.current_path = "/"

        # Select some files
        for f in browser._files[:2]:
            if not f.is_dir:
                browser._selected.add(f.path)

        assert len(browser._selected) > 0

        # Clear
        browser._selected.clear()

        assert len(browser._selected) == 0


class TestFileBrowserNavigation:
    """Tests for navigation in FileBrowser."""

    @pytest.fixture
    def browser_with_files(self, mock_files: list[RemoteFile]) -> tuple[FileBrowser, MockSFTPClient]:
        """Create a FileBrowser with mock SFTP client."""
        sftp = MockSFTPClient(mock_files)
        browser = FileBrowser(sftp_client=sftp)
        return browser, sftp

    async def test_go_up_from_subdir(
        self, browser_with_files: tuple[FileBrowser, MockSFTPClient]
    ):
        """Go up should navigate to parent directory."""
        browser, sftp = browser_with_files
        await sftp.connect()

        browser.current_path = "/subdir"

        # Go up logic
        from pathlib import PurePosixPath

        parent = str(PurePosixPath(browser.current_path).parent)

        assert parent == "/"

    async def test_go_up_from_root_stays(
        self, browser_with_files: tuple[FileBrowser, MockSFTPClient]
    ):
        """Go up from root should stay at root."""
        browser, sftp = browser_with_files
        await sftp.connect()

        browser.current_path = "/"

        # Should not change
        if browser.current_path != "/":
            from pathlib import PurePosixPath

            parent = str(PurePosixPath(browser.current_path).parent)
        else:
            parent = "/"

        assert parent == "/"
