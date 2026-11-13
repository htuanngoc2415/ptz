"""Thread-safe console/file logging for existing subsystem event messages."""
import builtins
import logging
import re
import sys
from datetime import datetime
from pathlib import Path

logger = logging.getLogger('ptz.events')
logger.setLevel(logging.INFO)
logger.propagate = False


def setup_logging(directory):
    if logger.handlers:
        return next(Path(h.baseFilename) for h in logger.handlers if isinstance(h, logging.FileHandler))
    directory = Path(directory)
    directory.mkdir(parents=True, exist_ok=True)
    path = directory / f"ptz_{datetime.now():%Y%m%d_%H%M%S}.log"
    formatter = logging.Formatter('%(asctime)s.%(msecs)03d [%(levelname)s] %(message)s', datefmt='%Y-%m-%d %H:%M:%S')
    file_handler = logging.FileHandler(path, encoding='utf-8')
    # Runtime diagnostics are written only to the session log file.
    # Keep the terminal clean while the camera is idle.
    file_handler.setFormatter(formatter)
    logger.addHandler(file_handler)
    metrics = logging.getLogger('ptz.metrics')
    metrics.setLevel(logging.INFO)
    metrics.propagate = False
    metrics_handler = logging.FileHandler(path.with_suffix('.jsonl'), encoding='utf8')
    metrics_handler.setFormatter(logging.Formatter('%(message)s'))
    metrics.addHandler(metrics_handler)
    logger.info('[STARTUP] log_file=%s', path)
    return path


def redact(message):
    message = re.sub(r'(rtsps?://|https?://)[^\s/@]+:[^\s/@]+@', r'\1[redacted]@', str(message))
    return re.sub(r'([?&](?:pwd|password|token)=)[^&\s]+', r'\1[redacted]', message, flags=re.I)


def event_print(*values, sep=' ', end='\n', file=None, flush=False):
    values = tuple(redact(value) for value in values)
    if not logger.handlers:
        builtins.print(*values, sep=sep, end=end, file=file, flush=flush)
        return
    # Startup failures must remain visible even when routine diagnostics are
    # file-only. Use already-redacted values for both outputs.
    if file is sys.stderr:
        builtins.print(*values, sep=sep, end=end, file=file, flush=True)
    message = sep.join(str(value) for value in values)
    for line in message.splitlines():
        if line.strip():
            if not line.startswith('['):
                line = '[APP] ' + line
            upper = line.upper()
            level = logging.ERROR if file is sys.stderr or "ERROR:" in upper or " ERROR " in upper or "[ERROR]" in upper or "EXCEPTION" in upper else (logging.WARNING if "WARNING" in upper or "WARN" in upper else logging.INFO)
            logger.log(level, line)
