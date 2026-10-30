# Hardware (Phase 6, planned, not implemented)

JARVIS does **not** control any physical device yet. `jarvis/hardware/` contains only an
isolated interface and protocol definition, so the design can be reviewed and tested
before any wiring exists. Nothing in it is exposed to the AI model.

## Planned design

- **Link:** USB serial between the PC (Python, `pyserial`) and an ESP32, at 115200 baud.
- **Framing:** one JSON object per line, ASCII, at most 512 bytes (`jarvis/hardware/protocol.py`).
  ```
  PC  -> ESP32  {"v":1,"id":7,"cmd":"led.set","args":{"pin":2,"on":true}}
  ESP32 -> PC   {"v":1,"id":7,"ok":true,"data":{}}
                {"v":1,"id":7,"ok":false,"error":"pin not allowed"}
  ```
- **Allowlist:** only `ping`, `status`, `led.set` and `sensor.read` can be encoded. The firmware
  must enforce its own allowlist as well (for example, which pins may be driven).
- **Timeouts:** each command waits at most `HardwareInterface.command_timeout` (2 s). A missing
  reply is reported as "unknown outcome", never as success.
- **Status:** `DeviceStatus(connected, port, firmware, detail)` for the `status` command and the HUD.
- **Interface:** `HardwareInterface.connect/status/send/close`. `NullHardware` is the current
  implementation and refuses everything.

## Steps when implementing

1. Write `SerialHardware(HardwareInterface)` with pyserial: open the port, `ping` handshake, a reader
   thread that matches responses to request ids, and per-command timeouts.
2. Write ESP32 firmware that parses one line, validates `v`, `cmd` and `args`, and replies with the same `id`.
3. Add tests with a fake serial port (loopback) before touching real hardware.
4. Only then register tools such as `device_status` and `set_led`. Any actuator command must need
   user approval, like other actions with physical effects.

## Safety rules for demos

- Low voltage only (3.3 V / 5 V logic, LEDs, small sensors). **No mains voltage** and no relays
  switching mains.
- Use current-limiting resistors on LEDs, and never drive motors directly from GPIO pins.
- The firmware should reach a safe state (outputs off) on reset and when the serial link is lost.
