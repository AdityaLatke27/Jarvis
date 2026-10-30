"""Open allowlisted applications and websites.

The model can only choose a *name* from config/apps.json; it never
supplies a command line, so it cannot run arbitrary programs.
"""

import json
import os
import shutil
import subprocess
import time
import webbrowser
from urllib.parse import quote_plus

from ..config import PROJECT_ROOT
from .registry import Tool, ToolError

APPS_FILE = PROJECT_ROOT / "config" / "apps.json"


def load_allowlist(path=APPS_FILE):
    try:
        data = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError) as error:
        raise RuntimeError(f"Could not read {path}: {error}") from error
    apps = {name.lower(): list(command) for name, command in data.get("apps", {}).items()}
    sites = {name.lower(): url for name, url in data.get("websites", {}).items()
             if str(url).startswith("https://")}
    engines = {name.lower(): url for name, url in data.get("search_engines", {}).items()
               if str(url).startswith("https://")}
    return apps, sites, engines


class AppLauncher:
    def __init__(self, allowlist=None, popen=subprocess.Popen, startfile=None, open_url=webbrowser.open):
        self.apps, self.sites, self.engines = allowlist or load_allowlist()
        self.popen = popen
        self.startfile = startfile or getattr(os, "startfile", None)
        self.open_url = open_url

    def _lookup(self, table, name, kind):
        key = (name or "").strip().lower()
        if key not in table:
            raise ToolError(f"'{name}' is not an allowed {kind}. Allowed: {', '.join(sorted(table))}.")
        return key, table[key]

    def open_app(self, name):
        key, command = self._lookup(self.apps, name, "application")
        program, args = command[0], command[1:]
        resolved = shutil.which(program)
        if resolved and resolved.lower().endswith(".exe"):
            try:
                process = self.popen([resolved, *args], close_fds=True)
            except OSError as error:
                raise ToolError(f"Could not start {key}: {error}") from error
            time.sleep(0.4)
            code = process.poll()
            if code not in (None, 0):
                raise ToolError(f"{key} exited immediately with code {code}.")
            return {"opened": True, "app": key, "pid": process.pid}
        if self.startfile is None:
            raise ToolError(f"Could not find '{program}' on this computer.")
        # Windows resolves App Paths entries and .cmd launchers (e.g. VS Code's "code").
        try:
            if args:
                self.startfile(resolved or program, arguments=subprocess.list2cmdline(args))
            else:
                self.startfile(resolved or program)
        except OSError as error:
            raise ToolError(f"Windows could not start {key}: {error.strerror or error}") from error
        return {"opened": True, "app": key,
                "note": "Windows accepted the launch request; it may take a moment to appear."}

    def open_website(self, name):
        key, url = self._lookup(self.sites, name, "website")
        if not self.open_url(url):
            raise ToolError("No web browser accepted the request.")
        return {"opened": True, "website": key, "url": url}

    def search_web(self, query, engine="google"):
        query = query.strip()
        if not query:
            raise ToolError("query is required.")
        key, base = self._lookup(self.engines, engine, "search engine")
        url = base + quote_plus(query)
        if not self.open_url(url):
            raise ToolError("No web browser accepted the request.")
        return {"opened": True, "engine": key, "url": url}


def build_app_tools(launcher=None):
    launcher = launcher or AppLauncher()
    name_schema = {"type": "string", "minLength": 1, "maxLength": 60}
    return [
        Tool("open_app",
             f"Open an allowlisted desktop application. Allowed names: {', '.join(sorted(launcher.apps))}.",
             {"type": "object", "properties": {"name": name_schema}, "required": ["name"],
              "additionalProperties": False},
             launcher.open_app, timeout=10),
        Tool("open_website",
             f"Open an allowlisted website in the browser. Allowed names: {', '.join(sorted(launcher.sites))}.",
             {"type": "object", "properties": {"name": name_schema}, "required": ["name"],
              "additionalProperties": False},
             launcher.open_website, timeout=10),
        Tool("search_web",
             "Open a web search in the browser. You do not see the results; the user does.",
             {"type": "object", "properties": {
                 "query": {"type": "string", "minLength": 1, "maxLength": 300},
                 "engine": {"type": "string", "enum": sorted(launcher.engines)},
             }, "required": ["query"], "additionalProperties": False},
             launcher.search_web, timeout=10),
    ]
