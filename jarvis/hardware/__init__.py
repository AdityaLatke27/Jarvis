"""Hardware interface (Phase 6 placeholder).

Isolated on purpose: nothing here is registered as an assistant tool and
no physical device is controlled yet. See docs/HARDWARE.md.
"""

from .interface import DeviceStatus, HardwareInterface, HardwareUnavailable, NullHardware
from .protocol import Command, Response, decode_response, encode_command

__all__ = ["DeviceStatus", "HardwareInterface", "HardwareUnavailable", "NullHardware",
           "Command", "Response", "decode_response", "encode_command"]
