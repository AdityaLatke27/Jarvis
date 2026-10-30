from types import SimpleNamespace

import pytest

from jarvis.hardware import Command, NullHardware, decode_response, encode_command
from jarvis.hardware.interface import HardwareUnavailable
from jarvis.hardware.protocol import ProtocolError
from jarvis.tools.app_tools import AppLauncher, load_allowlist
from jarvis.tools.clipboard_tools import Clipboard
from jarvis.tools.registry import ToolError

ALLOW = ({"notepad": ["notepad.exe"], "vs code": ["code"]},
         {"github": "https://github.com"},
         {"google": "https://www.google.com/search?q="})


def launcher(**kwargs):
    opened = []
    defaults = dict(open_url=lambda url: opened.append(url) or True)
    defaults.update(kwargs)
    return AppLauncher(ALLOW, **defaults), opened


def test_shipped_allowlist_is_valid():
    apps, sites, engines = load_allowlist()
    assert "notepad" in apps and all(url.startswith("https://") for url in sites.values())


def test_only_allowlisted_names():
    app, _ = launcher()
    for bad in ["powershell", "notepad.exe && del *", "cmd /c whoami"]:
        with pytest.raises(ToolError, match="not an allowed"):
            app.open_app(bad)
    with pytest.raises(ToolError, match="not an allowed"):
        app.open_website("https://evil.example")


def test_open_app_reports_real_process(monkeypatch):
    monkeypatch.setattr("jarvis.tools.app_tools.shutil.which", lambda p: "C:\\Windows\\notepad.exe")
    started = []

    def popen(args, **kw):
        started.append(args)
        return SimpleNamespace(pid=42, poll=lambda: None)

    app, _ = launcher(popen=popen)
    assert app.open_app("Notepad") == {"opened": True, "app": "notepad", "pid": 42}
    assert started == [["C:\\Windows\\notepad.exe"]]


def test_open_app_immediate_exit_is_failure(monkeypatch):
    monkeypatch.setattr("jarvis.tools.app_tools.shutil.which", lambda p: "C:\\x\\notepad.exe")
    app, _ = launcher(popen=lambda args, **kw: SimpleNamespace(pid=1, poll=lambda: 1))
    with pytest.raises(ToolError, match="exited immediately"):
        app.open_app("notepad")


def test_website_and_search_urls():
    app, opened = launcher()
    app.open_website("GitHub")
    app.search_web("python & sqlite tips")
    assert opened == ["https://github.com", "https://www.google.com/search?q=python+%26+sqlite+tips"]
    failing, _ = launcher(open_url=lambda url: False)
    with pytest.raises(ToolError, match="browser"):
        failing.open_website("github")


class FakeClip:
    def __init__(self, text=""):
        self.text = text

    def paste(self):
        return self.text

    def copy(self, text):
        self.text = text


def test_clipboard_read_write_and_secret_guard():
    clip = Clipboard(FakeClip("hello world"))
    assert clip.read()["text"] == "hello world"
    assert clip.write("copied")["copied"]
    with pytest.raises(ToolError, match="password"):
        Clipboard(FakeClip("password: hunter2")).read()


def test_hardware_protocol_and_null_device():
    line = encode_command(Command(7, "led.set", {"pin": 2, "on": True}))
    assert line.endswith(b"\n") and b'"cmd":"led.set"' in line
    with pytest.raises(ProtocolError):
        encode_command(Command(1, "relay.mains_on"))
    response = decode_response(b'{"v":1,"id":7,"ok":true,"data":{"temp":24.5}}\n')
    assert response.ok and response.data["temp"] == 24.5
    for bad in [b"not json", b'{"v":2,"id":1,"ok":true}', b'{"v":1,"id":"x","ok":true}']:
        with pytest.raises(ProtocolError):
            decode_response(bad)
    device = NullHardware()
    assert not device.status().connected
    with pytest.raises(HardwareUnavailable):
        device.send(Command(1, "ping"))
