"""Abstract hardware interface. A future SerialHardware (pyserial) will
implement it; for now NullHardware refuses every command."""

from abc import ABC, abstractmethod
from dataclasses import dataclass


class HardwareUnavailable(Exception):
    pass


@dataclass
class DeviceStatus:
    connected: bool
    port: str = ""
    firmware: str = ""
    detail: str = ""


class HardwareInterface(ABC):
    command_timeout = 2.0  # seconds to wait for a device response

    @abstractmethod
    def connect(self):
        """Open the connection and verify the device answers 'ping'."""

    @abstractmethod
    def status(self):
        """Return a DeviceStatus."""

    @abstractmethod
    def send(self, command):
        """Send a protocol.Command and return a protocol.Response, or raise on timeout."""

    @abstractmethod
    def close(self):
        """Close the connection. Must be safe to call twice."""


class NullHardware(HardwareInterface):
    def connect(self):
        raise HardwareUnavailable("Hardware integration is not implemented yet.")

    def status(self):
        return DeviceStatus(connected=False, detail="Hardware integration is not implemented yet.")

    def send(self, command):
        raise HardwareUnavailable("Hardware integration is not implemented yet.")

    def close(self):
        pass
