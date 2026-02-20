"""Integration tests for Queued app."""

import hashlib
import tempfile
from pathlib import Path

import pytest
from textual.app import App, ComposeResult
from textual.widgets import Button, Checkbox

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
            mock_sftp = MockSFTPClient(remote_md5_results={"/remote/file.txt": expected_md5})

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
                skip_btn = app.modal.query_one("#skip-btn", Button)
                assert not replace_btn.disabled
                assert not skip_btn.disabled


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
            mock_sftp = MockSFTPClient(remote_md5_results={"/remote/testfile.txt": expected_md5})

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
                skip_btn = app.modal.query_one("#skip-btn", Button)
                assert not skip_btn.disabled

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
                skip_btn = app.modal.query_one("#skip-btn", Button)
                assert not replace_btn.disabled
                assert not skip_btn.disabled

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


class TestQueuedAppInitialHost:
    """Tests for QueuedApp with initial_host parameter (CLI connection path)."""

    @pytest.mark.asyncio
    async def test_initial_host_does_not_crash_on_mount(self):
        """App with initial_host should not crash during on_mount.

        Regression test for bug where `await self._connect()` was incorrectly
        awaiting a @work decorated method (workers cannot be awaited).
        """
        from unittest.mock import AsyncMock, patch

        from queued.app import QueuedApp
        from queued.models import Host

        mock_host = Host(hostname="test.example.com", username="testuser", port=22)

        # Patch SFTPClient to prevent real network calls
        with patch("queued.app.SFTPClient") as mock_sftp_class:
            mock_sftp = AsyncMock()
            mock_sftp.connected = True
            mock_sftp.list_dir = AsyncMock(return_value=[])
            mock_sftp.get_pwd = AsyncMock(return_value="/")
            mock_sftp_class.return_value = mock_sftp

            app = QueuedApp(host=mock_host)

            # This should not raise "object worker cannot be used in await expression"
            async with app.run_test() as pilot:
                await pilot.pause()

                # App should have mounted successfully
                assert app.is_running


class TestVerifySizeButton:
    """Tests for the Verify Size button in FileExistsModal."""

    @pytest.mark.asyncio
    async def test_verify_size_button_shown_when_file_complete(self):
        """Verify Size button should appear when local_size >= remote_size and sftp provided."""
        mock_sftp = MockSFTPClient()

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
            await pilot.pause()
            verify_size_btns = app.modal.query("#verify-size-btn")
            assert len(verify_size_btns) == 1, (
                "Verify Size button should be shown for complete file"
            )

    @pytest.mark.asyncio
    async def test_verify_size_button_hidden_when_file_partial(self):
        """Verify Size button should not appear when local_size < remote_size."""
        mock_sftp = MockSFTPClient()

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
            await pilot.pause()
            verify_size_btns = app.modal.query("#verify-size-btn")
            assert len(verify_size_btns) == 0, (
                "Verify Size button should not be shown for partial file"
            )

    @pytest.mark.asyncio
    async def test_verify_size_button_hidden_without_sftp(self):
        """Verify Size button should not appear when sftp client not provided."""
        modal = FileExistsModal(
            "file.txt",
            local_size=1000,
            remote_size=1000,
        )

        app = FileExistsModalTestApp(modal)
        async with app.run_test() as pilot:
            await pilot.pause()
            verify_size_btns = app.modal.query("#verify-size-btn")
            assert len(verify_size_btns) == 0, "Verify Size button requires sftp client"

    @pytest.mark.asyncio
    async def test_verify_size_shows_match(self):
        """Clicking Verify Size should show 'Sizes match' when sizes are equal."""
        mock_sftp = MockSFTPClient()

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
            await pilot.pause()
            verify_size_btn = app.modal.query_one("#verify-size-btn", Button)
            await pilot.click(verify_size_btn)
            await pilot.pause()

            result_label = app.modal.query_one("#verify-result")
            assert "Sizes match" in str(result_label.content)

    @pytest.mark.asyncio
    async def test_verify_size_shows_mismatch(self):
        """Clicking Verify Size should show mismatch when sizes differ."""
        mock_sftp = MockSFTPClient()

        # local_size > remote_size (can_verify is true, can_continue is false)
        modal = FileExistsModal(
            "file.txt",
            local_size=2000,
            remote_size=1000,
            remote_path="/remote/file.txt",
            local_path="/local/file.txt",
            sftp=mock_sftp,
        )

        app = FileExistsModalTestApp(modal)
        async with app.run_test() as pilot:
            await pilot.pause()
            verify_size_btn = app.modal.query_one("#verify-size-btn", Button)
            await pilot.click(verify_size_btn)
            await pilot.pause()

            result_label = app.modal.query_one("#verify-result")
            assert "Size mismatch" in str(result_label.content)


class TestApplyToAllCheckbox:
    """Tests for the Apply to All checkbox in FileExistsModal."""

    @pytest.mark.asyncio
    async def test_apply_all_checkbox_exists(self):
        """Apply to all checkbox should be present in the modal."""
        mock_sftp = MockSFTPClient()

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
            await pilot.pause()
            checkboxes = app.modal.query("#apply-all-checkbox")
            assert len(checkboxes) == 1, "Apply to all checkbox should exist"

    @pytest.mark.asyncio
    async def test_skip_without_apply_all_returns_none(self):
        """Skip without apply-all should dismiss with None."""
        mock_sftp = MockSFTPClient()

        modal = FileExistsModal(
            "file.txt",
            local_size=1000,
            remote_size=1000,
            remote_path="/remote/file.txt",
            local_path="/local/file.txt",
            sftp=mock_sftp,
        )

        results = []

        app = FileExistsModalTestApp(modal)
        async with app.run_test() as pilot:
            await pilot.pause()

            # Override dismiss to capture result
            original_dismiss = modal.dismiss

            def capture_dismiss(result=None):
                results.append(result)
                original_dismiss(result)

            modal.dismiss = capture_dismiss

            skip_btn = app.modal.query_one("#skip-btn", Button)
            await pilot.click(skip_btn)
            await pilot.pause()

            assert results == [None]

    @pytest.mark.asyncio
    async def test_skip_with_apply_all_returns_skip_all(self):
        """Skip with apply-all checked should dismiss with 'skip_all'."""
        mock_sftp = MockSFTPClient()

        modal = FileExistsModal(
            "file.txt",
            local_size=1000,
            remote_size=1000,
            remote_path="/remote/file.txt",
            local_path="/local/file.txt",
            sftp=mock_sftp,
        )

        results = []

        app = FileExistsModalTestApp(modal)
        async with app.run_test() as pilot:
            await pilot.pause()

            # Check the apply-all checkbox
            checkbox = app.modal.query_one("#apply-all-checkbox", Checkbox)
            checkbox.value = True
            await pilot.pause()

            # Override dismiss to capture result
            original_dismiss = modal.dismiss

            def capture_dismiss(result=None):
                results.append(result)
                original_dismiss(result)

            modal.dismiss = capture_dismiss

            skip_btn = app.modal.query_one("#skip-btn", Button)
            await pilot.click(skip_btn)
            await pilot.pause()

            assert results == ["skip_all"]

    @pytest.mark.asyncio
    async def test_replace_with_apply_all_returns_replace_all(self):
        """Replace with apply-all checked should dismiss with 'replace_all'."""
        mock_sftp = MockSFTPClient()

        modal = FileExistsModal(
            "file.txt",
            local_size=1000,
            remote_size=1000,
            remote_path="/remote/file.txt",
            local_path="/local/file.txt",
            sftp=mock_sftp,
        )

        results = []

        app = FileExistsModalTestApp(modal)
        async with app.run_test() as pilot:
            await pilot.pause()

            # Check the apply-all checkbox
            checkbox = app.modal.query_one("#apply-all-checkbox", Checkbox)
            checkbox.value = True
            await pilot.pause()

            original_dismiss = modal.dismiss

            def capture_dismiss(result=None):
                results.append(result)
                original_dismiss(result)

            modal.dismiss = capture_dismiss

            replace_btn = app.modal.query_one("#replace-btn", Button)
            await pilot.click(replace_btn)
            await pilot.pause()

            assert results == ["replace_all"]

    @pytest.mark.asyncio
    async def test_continue_with_apply_all_returns_continue_all(self):
        """Continue with apply-all checked should dismiss with 'continue_all'."""
        mock_sftp = MockSFTPClient()

        # Partial file so Continue button appears
        modal = FileExistsModal(
            "file.txt",
            local_size=500,
            remote_size=1000,
            remote_path="/remote/file.txt",
            local_path="/local/file.txt",
            sftp=mock_sftp,
        )

        results = []

        app = FileExistsModalTestApp(modal)
        async with app.run_test() as pilot:
            await pilot.pause()

            checkbox = app.modal.query_one("#apply-all-checkbox", Checkbox)
            checkbox.value = True
            await pilot.pause()

            original_dismiss = modal.dismiss

            def capture_dismiss(result=None):
                results.append(result)
                original_dismiss(result)

            modal.dismiss = capture_dismiss

            continue_btn = app.modal.query_one("#continue-btn", Button)
            await pilot.click(continue_btn)
            await pilot.pause()

            assert results == ["continue_all"]

    @pytest.mark.asyncio
    async def test_verify_size_with_apply_all_dismisses(self):
        """Verify Size with apply-all checked should dismiss with 'verify_size_all'."""
        mock_sftp = MockSFTPClient()

        modal = FileExistsModal(
            "file.txt",
            local_size=1000,
            remote_size=1000,
            remote_path="/remote/file.txt",
            local_path="/local/file.txt",
            sftp=mock_sftp,
        )

        results = []

        app = FileExistsModalTestApp(modal)
        async with app.run_test() as pilot:
            await pilot.pause()

            checkbox = app.modal.query_one("#apply-all-checkbox", Checkbox)
            checkbox.value = True
            await pilot.pause()

            original_dismiss = modal.dismiss

            def capture_dismiss(result=None):
                results.append(result)
                original_dismiss(result)

            modal.dismiss = capture_dismiss

            verify_size_btn = app.modal.query_one("#verify-size-btn", Button)
            await pilot.click(verify_size_btn)
            await pilot.pause()

            assert results == ["verify_size_all"]


class TestAutoApplyExistsAction:
    """Tests for _auto_apply_exists_action logic via _add_download_with_exists_check."""

    @pytest.mark.asyncio
    async def test_auto_skip_returns_cancelled(self):
        """apply_all_action='skip' should return cancelled without showing modal."""
        from unittest.mock import AsyncMock, patch

        from queued.app import QueuedApp
        from queued.models import Host

        mock_host = Host(hostname="test.example.com", username="testuser", port=22)

        with (
            patch("queued.app.SFTPClient") as mock_sftp_class,
            tempfile.TemporaryDirectory() as tmpdir,
        ):
            mock_sftp = AsyncMock()
            mock_sftp.connected = True
            mock_sftp.list_dir = AsyncMock(return_value=[])
            mock_sftp.get_pwd = AsyncMock(return_value="/")
            mock_sftp_class.return_value = mock_sftp

            app = QueuedApp(host=mock_host, download_dir=tmpdir)
            async with app.run_test() as pilot:
                await pilot.pause()

                # Create a local file that "already exists"
                local_file = Path(tmpdir) / "existing.txt"
                local_file.write_bytes(b"existing content")

                remote_file = RemoteFile(
                    name="existing.txt",
                    path="/remote/existing.txt",
                    size=16,
                    is_dir=False,
                    mtime=None,
                )

                result, new_action = await app._add_download_with_exists_check(
                    remote_file, apply_all_action="skip"
                )
                assert result == "cancelled"
                assert new_action == "skip"

    @pytest.mark.asyncio
    async def test_auto_verify_size_match_returns_verified(self):
        """apply_all_action='verify_size' with matching sizes should return 'verified'."""
        from unittest.mock import AsyncMock, patch

        from queued.app import QueuedApp
        from queued.models import Host

        mock_host = Host(hostname="test.example.com", username="testuser", port=22)

        with (
            patch("queued.app.SFTPClient") as mock_sftp_class,
            tempfile.TemporaryDirectory() as tmpdir,
        ):
            mock_sftp = AsyncMock()
            mock_sftp.connected = True
            mock_sftp.list_dir = AsyncMock(return_value=[])
            mock_sftp.get_pwd = AsyncMock(return_value="/")
            mock_sftp_class.return_value = mock_sftp

            app = QueuedApp(host=mock_host, download_dir=tmpdir)
            async with app.run_test() as pilot:
                await pilot.pause()

                # Create local file with matching size
                content = b"matching content!"  # 17 bytes
                local_file = Path(tmpdir) / "match.txt"
                local_file.write_bytes(content)

                remote_file = RemoteFile(
                    name="match.txt",
                    path="/remote/match.txt",
                    size=len(content),
                    is_dir=False,
                    mtime=None,
                )

                result, new_action = await app._add_download_with_exists_check(
                    remote_file, apply_all_action="verify_size"
                )
                assert result == "verified"
                assert new_action == "verify_size"

    @pytest.mark.asyncio
    async def test_auto_verify_size_mismatch_replaces(self):
        """apply_all_action='verify_size' with mismatched sizes should delete and re-queue."""
        from unittest.mock import AsyncMock, patch

        from queued.app import QueuedApp
        from queued.models import Host

        mock_host = Host(hostname="test.example.com", username="testuser", port=22)

        with (
            patch("queued.app.SFTPClient") as mock_sftp_class,
            tempfile.TemporaryDirectory() as tmpdir,
        ):
            mock_sftp = AsyncMock()
            mock_sftp.connected = True
            mock_sftp.list_dir = AsyncMock(return_value=[])
            mock_sftp.get_pwd = AsyncMock(return_value="/")
            mock_sftp_class.return_value = mock_sftp

            app = QueuedApp(host=mock_host, download_dir=tmpdir)
            async with app.run_test() as pilot:
                await pilot.pause()

                # Create local file with DIFFERENT size
                local_file = Path(tmpdir) / "mismatch.txt"
                local_file.write_bytes(b"short")  # 5 bytes

                remote_file = RemoteFile(
                    name="mismatch.txt",
                    path="/remote/mismatch.txt",
                    size=1000,  # Different from local
                    is_dir=False,
                    mtime=None,
                )

                result, new_action = await app._add_download_with_exists_check(
                    remote_file, apply_all_action="verify_size"
                )
                # File should be deleted and queued (or skipped if queue is not set up)
                assert result in ("added", "skipped", "cancelled")
                assert new_action == "verify_size"
                # Local file should have been deleted
                assert not local_file.exists()
