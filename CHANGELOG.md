# Changelog

All notable changes to this project will be documented in this file.

The format is based on [Keep a Changelog](https://keepachangelog.com/en/1.1.0/),
and this project adheres to [Semantic Versioning](https://semver.org/spec/v2.0.0.html).

## [Unreleased]

### Added
- Initial release of Queued TUI SFTP download manager
- Dual-pane interface with file browser and transfer queue
- Concurrent downloads with configurable limits (1-5)
- Resume support for interrupted transfers
- Checksum verification using .sfv and .md5 files
- Recent hosts cache for quick reconnection
- Queue persistence across app restarts
- Individual transfer pause/resume
- Queue stop/resume with proper state management
- Directory queue indicators in file browser
- Auto-reconnect on connection loss
- SFTP download optimizations (pipelining, optimized ciphers)
- Vim-style page navigation (Ctrl+f/b)
