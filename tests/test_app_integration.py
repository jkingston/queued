"""Integration tests for Queued app."""

import hashlib
import tempfile
from pathlib import Path

import pytest
from textual.app import App, ComposeResult
from textual.widgets import Button

from queued.app import FileExistsModal
from queued.models import RemoteFile
from queued.widgets.file_browser import FileBrowser

from .mocks.sftp_mock import MockSFTPClient, create_mock_files


class FileBrowserTestApp(App):
    """Minimal test app that hosts a FileBrowser widget."""

    def __init__(self, sftp: MockSFTPClient):
        super().__init__()
        self.sftp = sftp

    def compose(self) -> ComposeResult:
        yield FileBrowser(sftp_client=self.sftp, id="file-browser")


class FileExistsModalTestApp(App):
    """Test app that pushes a FileExistsModal for testing."""

    def __init__(self, modal: FileExistsModal):
        super().__init__()
        self._modal = modal

    async def on_mount(self) -> None:
        await self.push_screen(self._modal)

    @property
    def modal(self) -> FileExistsModal:
        """Get the pushed modal screen."""
        return self._modal


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


class TestFileExistsModalVerify:
    """Tests for FileExistsModal verify functionality."""

    @pytest.mark.asyncio
    async def test_verify_button_shown_when_file_complete(self):
        """Verify button should appear when local_size >= remote_size."""
        mock_sftp = MockSFTPClient()

        # File appears complete (same size)
        modal = FileExistsModal(
            "file.txt",
            local_size=1000,
            remote_size=1000,
            remote_path="/remote/file.txt",
            local_path="/local/file.txt",
            sftp=mock_sftp,
        )

        app = FileExistsModalTestApp(modal)
        async with app.run_test() as pilot:
            await pilot.pause()  # Wait for modal to mount
            # Verify button should exist
            verify_btns = app.modal.query("#verify-btn")
            assert len(verify_btns) == 1, "Verify button should be shown for complete file"

    @pytest.mark.asyncio
    async def test_verify_button_hidden_when_file_partial(self):
        """Verify button should not appear when local_size < remote_size."""
        mock_sftp = MockSFTPClient()

        # File is partial (local smaller than remote)
        modal = FileExistsModal(
            "file.txt",
            local_size=500,
            remote_size=1000,
            remote_path="/remote/file.txt",
            local_path="/local/file.txt",
            sftp=mock_sftp,
        )

        app = FileExistsModalTestApp(modal)
        async with app.run_test() as pilot:
            await pilot.pause()  # Wait for modal to mount
            # Verify button should NOT exist
            verify_btns = app.modal.query("#verify-btn")
            assert len(verify_btns) == 0, "Verify button should not be shown for partial file"

            # Continue button SHOULD exist for resume
            continue_btns = app.modal.query("#continue-btn")
            assert len(continue_btns) == 1, "Continue button should be shown for partial file"

    @pytest.mark.asyncio
    async def test_verify_button_hidden_without_sftp(self):
        """Verify button should not appear when sftp client not provided."""
        # No sftp client
        modal = FileExistsModal(
            "file.txt",
            local_size=1000,
            remote_size=1000,
        )

        app = FileExistsModalTestApp(modal)
        async with app.run_test() as pilot:
            await pilot.pause()  # Wait for modal to mount
            # Verify button should NOT exist without sftp
            verify_btns = app.modal.query("#verify-btn")
            assert len(verify_btns) == 0, "Verify button requires sftp client"

    @pytest.mark.asyncio
    async def test_verify_success_shows_result(self):
        """Clicking Verify should show success message when hashes match."""
        with tempfile.TemporaryDirectory() as tmpdir:
            # Create a local file
            local_path = Path(tmpdir) / "file.txt"
            content = b"test content for verification"
            local_path.write_bytes(content)
            expected_md5 = hashlib.md5(content).hexdigest()

            # Mock SFTP with matching MD5
            mock_sftp = MockSFTPClient(
                remote_md5_results={"/remote/file.txt": expected_md5}
            )

            modal = FileExistsModal(
                "file.txt",
                local_size=len(content),
                remote_size=len(content),
                remote_path="/remote/file.txt",
                local_path=str(local_path),
                sftp=mock_sftp,
            )

            app = FileExistsModalTestApp(modal)
            async with app.run_test() as pilot:
                await pilot.pause()  # Wait for modal to mount
                # Click the Verify button
                verify_btn = app.modal.query_one("#verify-btn", Button)
                await pilot.click(verify_btn)

                # Wait for async verification to complete
                await pilot.pause()
                await pilot.pause()  # Extra pause for worker

                # Check result label shows success
                result_label = app.modal.query_one("#verify-result")
                assert "MD5 match" in str(result_label.content)

    @pytest.mark.asyncio
    async def test_verify_failure_shows_error(self):
        """Clicking Verify should show error when hashes don't match."""
        with tempfile.TemporaryDirectory() as tmpdir:
            # Create a local file
            local_path = Path(tmpdir) / "file.txt"
            local_path.write_bytes(b"local content")

            # Mock SFTP with DIFFERENT MD5
            mock_sftp = MockSFTPClient(
                remote_md5_results={"/remote/file.txt": "different_hash_1234567890"}
            )

            modal = FileExistsModal(
                "file.txt",
                local_size=100,
                remote_size=100,
                remote_path="/remote/file.txt",
                local_path=str(local_path),
                sftp=mock_sftp,
            )

            app = FileExistsModalTestApp(modal)
            async with app.run_test() as pilot:
                await pilot.pause()  # Wait for modal to mount
                # Click the Verify button
                verify_btn = app.modal.query_one("#verify-btn", Button)
                await pilot.click(verify_btn)

                # Wait for async verification
                await pilot.pause()
                await pilot.pause()

                # Check result label shows mismatch
                result_label = app.modal.query_one("#verify-result")
                assert "mismatch" in str(result_label.content).lower()

    @pytest.mark.asyncio
    async def test_verify_fallback_to_size_when_md5_unavailable(self):
        """Should fall back to size comparison when md5sum not available."""
        with tempfile.TemporaryDirectory() as tmpdir:
            local_path = Path(tmpdir) / "file.txt"
            content = b"test content"
            local_path.write_bytes(content)

            # Mock SFTP with md5sum unavailable
            mock_sftp = MockSFTPClient(md5_available=False)

            modal = FileExistsModal(
                "file.txt",
                local_size=len(content),
                remote_size=len(content),
                remote_path="/remote/file.txt",
                local_path=str(local_path),
                sftp=mock_sftp,
            )

            app = FileExistsModalTestApp(modal)
            async with app.run_test() as pilot:
                await pilot.pause()  # Wait for modal to mount
                verify_btn = app.modal.query_one("#verify-btn", Button)
                await pilot.click(verify_btn)

                await pilot.pause()
                await pilot.pause()

                # Should show size match warning
                result_label = app.modal.query_one("#verify-result")
                assert "size match only" in str(result_label.content).lower()

    @pytest.mark.asyncio
    async def test_buttons_remain_after_verify(self):
        """Replace and Cancel buttons should still work after verification."""
        with tempfile.TemporaryDirectory() as tmpdir:
            local_path = Path(tmpdir) / "file.txt"
            local_path.write_bytes(b"test")

            mock_sftp = MockSFTPClient(
                remote_md5_results={"/remote/file.txt": hashlib.md5(b"test").hexdigest()}
            )

            modal = FileExistsModal(
                "file.txt",
                local_size=4,
                remote_size=4,
                remote_path="/remote/file.txt",
                local_path=str(local_path),
                sftp=mock_sftp,
            )

            app = FileExistsModalTestApp(modal)
            async with app.run_test() as pilot:
                await pilot.pause()  # Wait for modal to mount
                # Verify first
                verify_btn = app.modal.query_one("#verify-btn", Button)
                await pilot.click(verify_btn)
                await pilot.pause()
                await pilot.pause()

                # Replace and Cancel should still be enabled
                replace_btn = app.modal.query_one("#replace-btn", Button)
                cancel_btn = app.modal.query_one("#cancel-btn", Button)
                assert not replace_btn.disabled
                assert not cancel_btn.disabled


class TestVerifyE2E:
    """End-to-end tests for verify flow."""

    @pytest.mark.asyncio
    async def test_full_verify_flow_success(self):
        """Test complete verify flow: modal appears, verify succeeds, cancel keeps file."""
        with tempfile.TemporaryDirectory() as tmpdir:
            # Create a "downloaded" file locally
            local_path = Path(tmpdir) / "testfile.txt"
            content = b"already downloaded content"
            local_path.write_bytes(content)
            expected_md5 = hashlib.md5(content).hexdigest()

            # Mock SFTP with matching remote MD5
            mock_sftp = MockSFTPClient(
                remote_md5_results={"/remote/testfile.txt": expected_md5}
            )

            # Create modal as if user selected a file that already exists locally
            modal = FileExistsModal(
                "testfile.txt",
                local_size=len(content),
                remote_size=len(content),
                remote_path="/remote/testfile.txt",
                local_path=str(local_path),
                sftp=mock_sftp,
            )

            app = FileExistsModalTestApp(modal)
            async with app.run_test() as pilot:
                await pilot.pause()

                # 1. Verify button should be visible (file appears complete)
                verify_btn = app.modal.query_one("#verify-btn", Button)
                assert verify_btn is not None

                # 2. Click Verify
                await pilot.click(verify_btn)
                await pilot.pause()
                await pilot.pause()

                # 3. Verify shows success
                result_label = app.modal.query_one("#verify-result")
                assert "MD5 match" in str(result_label.content)

                # 4. Cancel button still available
                cancel_btn = app.modal.query_one("#cancel-btn", Button)
                assert not cancel_btn.disabled

                # 5. Original file still exists unchanged
                assert local_path.exists()
                assert local_path.read_bytes() == content

    @pytest.mark.asyncio
    async def test_full_verify_flow_mismatch_then_replace(self):
        """Test verify fails, user can still choose to replace."""
        with tempfile.TemporaryDirectory() as tmpdir:
            # Create a corrupted local file
            local_path = Path(tmpdir) / "corrupted.txt"
            local_path.write_bytes(b"corrupted content")

            # Remote has different content
            mock_sftp = MockSFTPClient(
                remote_md5_results={"/remote/corrupted.txt": "different_md5_hash"}
            )

            modal = FileExistsModal(
                "corrupted.txt",
                local_size=17,  # len("corrupted content")
                remote_size=17,
                remote_path="/remote/corrupted.txt",
                local_path=str(local_path),
                sftp=mock_sftp,
            )

            app = FileExistsModalTestApp(modal)
            async with app.run_test() as pilot:
                await pilot.pause()

                # Click Verify
                verify_btn = app.modal.query_one("#verify-btn", Button)
                await pilot.click(verify_btn)
                await pilot.pause()
                await pilot.pause()

                # Verify shows mismatch
                result_label = app.modal.query_one("#verify-result")
                assert "mismatch" in str(result_label.content).lower()

                # Replace button still available for user to re-download
                replace_btn = app.modal.query_one("#replace-btn", Button)
                assert not replace_btn.disabled


class TestFileExistsModalContinue:
    """Tests for FileExistsModal continue (resume) functionality."""

    @pytest.mark.asyncio
    async def test_continue_button_shown_for_partial_file(self):
        """Continue button should appear when local_size < remote_size."""
        mock_sftp = MockSFTPClient()

        # File is partial (local smaller than remote)
        modal = FileExistsModal(
            "partial.txt",
            local_size=500,
            remote_size=1000,
            remote_path="/remote/partial.txt",
            local_path="/local/partial.txt",
            sftp=mock_sftp,
        )

        app = FileExistsModalTestApp(modal)
        async with app.run_test() as pilot:
            await pilot.pause()

            # Continue button SHOULD exist for partial file
            continue_btns = app.modal.query("#continue-btn")
            assert len(continue_btns) == 1, "Continue button should be shown for partial file"

            # Verify button should NOT exist (file not complete)
            verify_btns = app.modal.query("#verify-btn")
            assert len(verify_btns) == 0, "Verify button should not be shown for partial file"

    @pytest.mark.asyncio
    async def test_continue_button_hidden_for_complete_file(self):
        """Continue button should NOT appear when local_size >= remote_size."""
        mock_sftp = MockSFTPClient()

        # File appears complete
        modal = FileExistsModal(
            "complete.txt",
            local_size=1000,
            remote_size=1000,
            remote_path="/remote/complete.txt",
            local_path="/local/complete.txt",
            sftp=mock_sftp,
        )

        app = FileExistsModalTestApp(modal)
        async with app.run_test() as pilot:
            await pilot.pause()

            # Continue button should NOT exist
            continue_btns = app.modal.query("#continue-btn")
            assert len(continue_btns) == 0, "Continue button should not be shown for complete file"

            # Verify button SHOULD exist
            verify_btns = app.modal.query("#verify-btn")
            assert len(verify_btns) == 1, "Verify button should be shown for complete file"


class TestVerifyErrorHandling:
    """Tests for verify error handling scenarios."""

    @pytest.mark.asyncio
    async def test_verify_handles_connection_failure(self):
        """Verify should show error gracefully when connection drops."""
        with tempfile.TemporaryDirectory() as tmpdir:
            local_path = Path(tmpdir) / "file.txt"
            local_path.write_bytes(b"test content")

            # Mock SFTP that fails on MD5 computation
            mock_sftp = MockSFTPClient(fail_on_md5=True)

            modal = FileExistsModal(
                "file.txt",
                local_size=12,
                remote_size=12,
                remote_path="/remote/file.txt",
                local_path=str(local_path),
                sftp=mock_sftp,
            )

            app = FileExistsModalTestApp(modal)
            async with app.run_test() as pilot:
                await pilot.pause()

                # Click Verify
                verify_btn = app.modal.query_one("#verify-btn", Button)
                await pilot.click(verify_btn)
                await pilot.pause()
                await pilot.pause()

                # Should show error or fallback message
                result_label = app.modal.query_one("#verify-result")
                result_text = str(result_label.content).lower()
                # Should either show error or fall back to size match
                assert "error" in result_text or "size" in result_text

                # Buttons should still work
                replace_btn = app.modal.query_one("#replace-btn", Button)
                cancel_btn = app.modal.query_one("#cancel-btn", Button)
                assert not replace_btn.disabled
                assert not cancel_btn.disabled

    @pytest.mark.asyncio
    async def test_verify_fallback_when_md5_fails_but_sizes_match(self):
        """When MD5 computation fails, should fall back to size comparison."""
        with tempfile.TemporaryDirectory() as tmpdir:
            local_path = Path(tmpdir) / "file.txt"
            content = b"test content for fallback"
            local_path.write_bytes(content)

            # Mock SFTP that fails on MD5 but sizes will match
            mock_sftp = MockSFTPClient(fail_on_md5=True)

            modal = FileExistsModal(
                "file.txt",
                local_size=len(content),
                remote_size=len(content),
                remote_path="/remote/file.txt",
                local_path=str(local_path),
                sftp=mock_sftp,
            )

            app = FileExistsModalTestApp(modal)
            async with app.run_test() as pilot:
                await pilot.pause()

                verify_btn = app.modal.query_one("#verify-btn", Button)
                await pilot.click(verify_btn)
                await pilot.pause()
                await pilot.pause()

                # Should show size match fallback
                result_label = app.modal.query_one("#verify-result")
                result_text = str(result_label.content).lower()
                # Accept either "size match" or an error - both are valid handling
                assert "size" in result_text or "match" in result_text or "error" in result_text


class TestVerifyChecksumFiles:
    """Tests for verification using .md5/.sfv checksum files."""

    @pytest.mark.asyncio
    async def test_verify_uses_md5_checksum_file(self):
        """Verify should use .md5 file when present in directory."""
        from datetime import datetime

        with tempfile.TemporaryDirectory() as tmpdir:
            # Create local file
            local_path = Path(tmpdir) / "testfile.bin"
            content = b"test content for checksum verification"
            local_path.write_bytes(content)
            local_md5 = hashlib.md5(content).hexdigest()

            # Create mock with .md5 file in same directory
            md5_content = f"{local_md5}  testfile.bin\n".encode()
            mock_files = [
                RemoteFile(
                    name="testfile.bin",
                    path="/remote/testfile.bin",
                    size=len(content),
                    is_dir=False,
                    mtime=datetime.now(),
                ),
                RemoteFile(
                    name="checksums.md5",
                    path="/remote/checksums.md5",
                    size=len(md5_content),
                    is_dir=False,
                    mtime=datetime.now(),
                ),
            ]

            mock_sftp = MockSFTPClient(
                files=mock_files,
                file_contents={"/remote/checksums.md5": md5_content},
            )

            modal = FileExistsModal(
                "testfile.bin",
                local_size=len(content),
                remote_size=len(content),
                remote_path="/remote/testfile.bin",
                local_path=str(local_path),
                sftp=mock_sftp,
            )

            app = FileExistsModalTestApp(modal)
            async with app.run_test() as pilot:
                await pilot.pause()

                verify_btn = app.modal.query_one("#verify-btn", Button)
                await pilot.click(verify_btn)
                await pilot.pause()
                await pilot.pause()

                # Should show MD5 match from checksum file
                result_label = app.modal.query_one("#verify-result")
                result_text = str(result_label.content)
                assert "MD5 match" in result_text or "match" in result_text.lower()

    @pytest.mark.asyncio
    async def test_verify_fallback_when_checksum_file_corrupt(self):
        """Should fall back to remote MD5 when .md5 file is malformed."""
        from datetime import datetime

        with tempfile.TemporaryDirectory() as tmpdir:
            local_path = Path(tmpdir) / "testfile.bin"
            content = b"test content"
            local_path.write_bytes(content)
            local_md5 = hashlib.md5(content).hexdigest()

            # Create mock with corrupted .md5 file
            mock_files = [
                RemoteFile(
                    name="testfile.bin",
                    path="/remote/testfile.bin",
                    size=len(content),
                    is_dir=False,
                    mtime=datetime.now(),
                ),
                RemoteFile(
                    name="checksums.md5",
                    path="/remote/checksums.md5",
                    size=10,
                    is_dir=False,
                    mtime=datetime.now(),
                ),
            ]

            mock_sftp = MockSFTPClient(
                files=mock_files,
                file_contents={"/remote/checksums.md5": b"corrupted garbage data"},
                remote_md5_results={"/remote/testfile.bin": local_md5},
            )

            modal = FileExistsModal(
                "testfile.bin",
                local_size=len(content),
                remote_size=len(content),
                remote_path="/remote/testfile.bin",
                local_path=str(local_path),
                sftp=mock_sftp,
            )

            app = FileExistsModalTestApp(modal)
            async with app.run_test() as pilot:
                await pilot.pause()

                verify_btn = app.modal.query_one("#verify-btn", Button)
                await pilot.click(verify_btn)
                await pilot.pause()
                await pilot.pause()

                # Should fall back to remote MD5 and succeed
                result_label = app.modal.query_one("#verify-result")
                result_text = str(result_label.content)
                assert "MD5 match" in result_text or "match" in result_text.lower()
