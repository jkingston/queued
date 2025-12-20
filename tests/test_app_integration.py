"""Integration tests for Queued app."""

from textual.app import App, ComposeResult

from queued.widgets.file_browser import FileBrowser

from .mocks.sftp_mock import MockSFTPClient, create_mock_files


class FileBrowserTestApp(App):
    """Minimal test app that hosts a FileBrowser widget."""

    def __init__(self, sftp: MockSFTPClient):
        super().__init__()
        self.sftp = sftp

    def compose(self) -> ComposeResult:
        yield FileBrowser(sftp_client=self.sftp, id="file-browser")


class TestConnectionIntegration:
    """Integration tests for connection flow."""

    async def test_connection_loads_directory(self):
        """After successful connection, file browser should display files."""
        sftp = MockSFTPClient(create_mock_files())
        await sftp.connect()

        app = FileBrowserTestApp(sftp)
        async with app.run_test() as pilot:
            browser = app.query_one("#file-browser", FileBrowser)

            # Simulate what happens during connection:
            # app._connect() calls set_sftp_client then load_directory
            browser.set_sftp_client(sftp)
            browser.load_directory("/")

            # Wait for the @work decorated load_directory to complete
            await pilot.pause()

            assert len(browser._files) > 0, (
                f"File browser should have loaded files after connection, "
                f"but got {len(browser._files)} files"
            )
            assert browser.current_path == "/"

    async def test_connection_sets_path_label(self):
        """After connection, path label should show current directory."""
        sftp = MockSFTPClient(create_mock_files())
        await sftp.connect()

        app = FileBrowserTestApp(sftp)
        async with app.run_test() as pilot:
            browser = app.query_one("#file-browser", FileBrowser)
            browser.set_sftp_client(sftp)
            browser.load_directory("/")

            await pilot.pause()

            # Path should be set to root
            assert browser.current_path == "/"
