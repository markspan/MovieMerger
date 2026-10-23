"""Minimal XDF 1.0 writer used to build test recordings.

Implements just enough of https://github.com/sccn/xdf/wiki/Specifications
for pyxdf to read the result: file header, stream headers, sample chunks and
stream footers, with explicit per-sample timestamps.
"""

from __future__ import annotations

import struct
from dataclasses import dataclass, field
from pathlib import Path
from xml.sax.saxutils import escape

import numpy as np

_TAG_FILE_HEADER = 1
_TAG_STREAM_HEADER = 2
_TAG_SAMPLES = 3
_TAG_CLOCK_OFFSET = 4
_TAG_STREAM_FOOTER = 6

_NUMERIC_FORMATS = {"float32": "<f4", "double64": "<f8", "int16": "<i2", "int32": "<i4"}


@dataclass
class XdfStream:
    """One stream to write.

    ``values`` is a list of strings for ``channel_format="string"`` streams,
    otherwise an (n_samples, n_channels) array.
    """

    name: str
    type: str
    channel_format: str
    nominal_srate: float
    time_stamps: np.ndarray
    values: object
    desc: dict[str, str] = field(default_factory=dict)

    @property
    def channel_count(self) -> int:
        return 1 if self.channel_format == "string" else int(np.asarray(self.values).shape[1])


def write_xdf(path: Path, streams: list[XdfStream]) -> None:
    """Write ``streams`` to ``path`` as an XDF file."""
    with open(path, "wb") as f:
        f.write(b"XDF:")
        _chunk(f, _TAG_FILE_HEADER, b'<?xml version="1.0"?><info><version>1.0</version></info>')
        for sid, stream in enumerate(streams, start=1):
            _chunk(f, _TAG_STREAM_HEADER, struct.pack("<I", sid) + _header_xml(stream))
        for sid, stream in enumerate(streams, start=1):
            _chunk(f, _TAG_SAMPLES, struct.pack("<I", sid) + _samples(stream))
            # Zero offsets at both ends: all test streams share one clock.
            for collected in (stream.time_stamps[0], stream.time_stamps[-1]):
                _chunk(f, _TAG_CLOCK_OFFSET, struct.pack("<Idd", sid, collected, 0.0))
        for sid, stream in enumerate(streams, start=1):
            ts = stream.time_stamps
            footer = (
                f'<?xml version="1.0"?><info><first_timestamp>{ts[0]}</first_timestamp>'
                f"<last_timestamp>{ts[-1]}</last_timestamp><sample_count>{ts.size}</sample_count></info>"
            )
            _chunk(f, _TAG_STREAM_FOOTER, struct.pack("<I", sid) + footer.encode())


def _header_xml(stream: XdfStream) -> bytes:
    desc = "".join(f"<{k}>{escape(v)}</{k}>" for k, v in stream.desc.items())
    xml = (
        '<?xml version="1.0"?><info>'
        f"<name>{escape(stream.name)}</name><type>{escape(stream.type)}</type>"
        f"<channel_count>{stream.channel_count}</channel_count>"
        f"<nominal_srate>{stream.nominal_srate}</nominal_srate>"
        f"<channel_format>{stream.channel_format}</channel_format>"
        f"<source_id>{escape(stream.name)}</source_id><created_at>0</created_at>"
        f"<uid>{escape(stream.name)}</uid><session_id>test</session_id><hostname>test</hostname>"
        f"<desc>{desc}</desc></info>"
    )
    return xml.encode()


def _samples(stream: XdfStream) -> bytes:
    stamps = np.asarray(stream.time_stamps, dtype=np.float64)
    parts = [_varlen(stamps.size)]
    if stream.channel_format == "string":
        for stamp, text in zip(stamps, stream.values):
            data = str(text).encode()
            parts.append(b"\x08" + struct.pack("<d", stamp) + _varlen(len(data)) + data)
    else:
        values = np.asarray(stream.values).astype(_NUMERIC_FORMATS[stream.channel_format])
        for stamp, row in zip(stamps, values):
            parts.append(b"\x08" + struct.pack("<d", stamp) + row.tobytes())
    return b"".join(parts)


def _varlen(value: int) -> bytes:
    if value < 256:
        return b"\x01" + struct.pack("<B", value)
    if value < 2**32:
        return b"\x04" + struct.pack("<I", value)
    return b"\x08" + struct.pack("<Q", value)


def _chunk(f, tag: int, content: bytes) -> None:
    f.write(_varlen(len(content) + 2) + struct.pack("<H", tag) + content)
