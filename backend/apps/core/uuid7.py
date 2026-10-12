"""UUIDv7 generator (time-ordered) — Python 3.12 has no built-in uuid7."""
import os
import time
import uuid

_last_ts = 0
_seq = 0


def uuid7() -> uuid.UUID:
    global _last_ts, _seq
    ts = time.time_ns() // 1_000_000  # ms
    if ts == _last_ts:
        _seq = (_seq + 1) & 0xFFF
    else:
        _last_ts = ts
        _seq = int.from_bytes(os.urandom(2), "big") & 0xFFF
    rand = int.from_bytes(os.urandom(8), "big") & 0x3FFFFFFFFFFFFFFF
    value = (ts << 80) | (0x7 << 76) | (_seq << 64) | (0b10 << 62) | rand
    return uuid.UUID(int=value)
