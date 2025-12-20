"""Entry point for Queued TUI SFTP download manager."""

import logging
import sys
from pathlib import Path

import click

from queued import __version__
from queued.models import Host


def setup_logging(verbose: bool) -> None:
    """Configure logging based on verbosity level."""
    if verbose:
        logging.basicConfig(
            level=logging.DEBUG,
            format="%(asctime)s [%(levelname)s] %(name)s: %(message)s",
            datefmt="%H:%M:%S",
        )
    else:
        # Suppress all logging in non-verbose mode
        logging.basicConfig(level=logging.CRITICAL)


@click.command()
@click.argument("connection", required=False)
@click.option("-p", "--port", default=22, help="SSH port (default: 22)")
@click.option("-i", "--identity", help="Path to SSH private key")
@click.option("-P", "--password", is_flag=True, help="Prompt for password authentication")
@click.option("-d", "--download-dir", help="Download directory (default: ~/Downloads)")
@click.option("-v", "--verbose", is_flag=True, help="Enable debug output")
@click.option("--version", is_flag=True, help="Show version and exit")
def main(
    connection: str | None,
    port: int,
    identity: str | None,
    password: bool,
    download_dir: str | None,
    verbose: bool,
    version: bool,
) -> None:
    """Queued - TUI SFTP download manager.

    CONNECT as user@hostname to connect directly, or run without
    arguments for interactive mode.

    Examples:

        queued user@server.example.com

        queued user@host -p 2222 -i ~/.ssh/server_key

        queued user@host -P  # Prompt for password

        queued  # Interactive mode
    """
    if version:
        click.echo(f"queued {__version__}")
        sys.exit(0)

    # Parse connection string if provided
    host: Host | None = None
    if connection:
        # Expand identity path if provided
        key_path = None
        if identity:
            key_path = str(Path(identity).expanduser())

        host = Host.from_string(connection, port=port, key_path=key_path)

        # If no username, prompt for it
        if not host.username:
            host.username = click.prompt("Username")

        # Prompt for password if -P flag is used
        if password:
            host.password = click.prompt("Password", hide_input=True)

    # Set up logging before starting app
    setup_logging(verbose)

    # Import here to avoid slow startup for --version
    from queued.app import QueuedApp

    app = QueuedApp(host=host, download_dir=download_dir)
    app.run()


if __name__ == "__main__":
    main()
