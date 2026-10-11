"""The app's rotating log file (#211).

Called by the entry point (``webapp.py``), never by a service or router:
configuring logging is a process-wide side effect (ADR-0006). The directory is
passed in, so tests write into a temp dir.

The console-less .exe (#214) has no stdout/stderr at all - both are ``None``,
and a library that writes to them (uvicorn asks ``sys.stdout.isatty()``)
fails. ``redirect_missing_std_streams`` gives them the same rotating file. It
writes through the file handler, not into the file directly: on Windows a
second open handle would make every rotation fail.
"""

from __future__ import annotations

import io
import logging
import os
import sys
import threading
from logging.handlers import RotatingFileHandler

LOG_FILE_NAME = "localbib.log"
LOG_MAX_BYTES = 1024 * 1024
LOG_BACKUPS = 4  # the live file + 4 rotated = ~5 x 1 MB
LOG_FORMAT = "%(asctime)s [%(levelname)s] %(name)s: %(message)s"


def log_dir_for(settings_dir: str) -> str:
    """The log folder: ``logs`` inside the settings folder."""
    return os.path.join(settings_dir, "logs")


def build_file_handler(log_dir: str, *, max_bytes: int = LOG_MAX_BYTES,
                       backups: int = LOG_BACKUPS) -> RotatingFileHandler:
    """A rotating UTF-8 file handler writing ``<log_dir>/localbib.log``."""
    os.makedirs(log_dir, exist_ok=True)
    handler = RotatingFileHandler(
        os.path.join(log_dir, LOG_FILE_NAME),
        maxBytes=max_bytes, backupCount=backups, encoding="utf-8",
    )
    handler.setFormatter(logging.Formatter(LOG_FORMAT))
    return handler


def quiet_url_loggers() -> None:
    """Keep libraries that log every request URL on INFO at WARNING. The file
    takes INFO, and a URL can carry a credential in its query string
    (OpenAlex ``api_key``)."""
    logging.getLogger("httpx").setLevel(logging.WARNING)


def console_handler() -> logging.StreamHandler:
    """The source tree's console handler. WARNING and above only: the console
    stays as it was before the file existed (Python's last-resort handler), the
    file alone takes INFO."""
    handler = logging.StreamHandler()
    handler.setLevel(logging.WARNING)
    return handler


def setup_file_logging(log_dir: str, *, level: int = logging.INFO,
                       logger: logging.Logger | None = None,
                       **handler_kwargs) -> RotatingFileHandler | None:
    """Attach the rotating file to ``logger`` (default: root) and return the
    handler, so the caller can detach it. A folder that cannot be written
    must not stop the app: returns ``None`` then. Idempotent per folder."""
    target = logger if logger is not None else logging.getLogger()
    wanted = os.path.abspath(os.path.join(log_dir, LOG_FILE_NAME))
    for existing in target.handlers:
        if isinstance(existing, RotatingFileHandler) and existing.baseFilename == wanted:
            return existing
    try:
        handler = build_file_handler(log_dir, **handler_kwargs)
    except OSError:
        return None
    handler.setLevel(level)
    target.addHandler(handler)
    if target.level == logging.NOTSET or target.level > level:
        target.setLevel(level)
    return handler


class LogStream(io.TextIOBase):
    """A text stream whose lines become records of ``handler``.

    Stands in for ``sys.stdout``/``sys.stderr`` where there are none. Each
    complete line is one record under the logger name ``name`` (``stdout`` /
    ``stderr``); a partial line waits for its newline or ``flush()``. A write
    that happens while the handler itself is writing (its error path prints to
    stderr) is dropped instead of recursing."""

    def __init__(self, handler: logging.Handler, level: int, name: str) -> None:
        super().__init__()
        self._handler = handler
        self._level = level
        self._name = name
        self._buffer = ""
        self._lock = threading.RLock()
        self._busy = threading.local()

    @property
    def encoding(self) -> str:  # what callers inspect before writing bytes
        return "utf-8"

    def writable(self) -> bool:
        return True

    def isatty(self) -> bool:
        return False

    def write(self, text: str) -> int:
        if not isinstance(text, str):
            text = str(text)
        with self._lock:
            self._buffer += text
            *lines, self._buffer = self._buffer.split("\n")
        for line in lines:
            self._emit(line)
        return len(text)

    def flush(self) -> None:
        with self._lock:
            rest, self._buffer = self._buffer, ""
        if rest:
            self._emit(rest)

    def _emit(self, line: str) -> None:
        line = line.rstrip("\r")
        if not line.strip() or getattr(self._busy, "on", False):
            return
        self._busy.on = True
        try:
            self._handler.handle(logging.makeLogRecord({
                "name": self._name, "levelno": self._level,
                "levelname": logging.getLevelName(self._level), "msg": line,
            }))
        finally:
            self._busy.on = False


def redirect_missing_std_streams(log_dir: str) -> RotatingFileHandler | None:
    """Give a missing ``sys.stdout``/``sys.stderr`` the rotating log file.

    Only a stream that is ``None`` is replaced - a console, a pipe or a
    redirect someone set up keeps working. Sets up the file logging of
    ``log_dir`` on the way (idempotent) and returns its handler. Without a
    writable log folder the streams become a sink instead, so a write still
    cannot fail."""
    handler = setup_file_logging(log_dir)
    for attr, level in (("stdout", logging.INFO), ("stderr", logging.WARNING)):
        if getattr(sys, attr) is not None:
            continue
        stream = (LogStream(handler, level, attr) if handler is not None
                  else open(os.devnull, "w", encoding="utf-8"))
        setattr(sys, attr, stream)
    return handler
