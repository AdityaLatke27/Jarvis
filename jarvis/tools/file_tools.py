"""File and document tools restricted to configured folders.

Safety rules:
- Every path is resolved (following "..", symlinks and junctions) and
  must stay inside one of JARVIS_ALLOWED_DIRS, or it is refused.
- Creating a new file needs no approval; overwriting or deleting a file
  shows a preview and requires the user's approval.
- Only text-like files can be written.
"""

import difflib
import os
import re
import zipfile
from pathlib import Path
from xml.etree import ElementTree

from .registry import Tool, ToolError

WRITABLE_EXTENSIONS = {".txt", ".md", ".csv", ".json", ".log", ".py", ".html", ".css", ".js", ".xml", ".yaml",
                       ".yml", ".ini", ".toml"}
TEXT_EXTENSIONS = WRITABLE_EXTENSIONS | {".bat", ".ps1", ".sql", ".c", ".cpp", ".h", ".java", ".ino"}
DOCUMENT_EXTENSIONS = TEXT_EXTENSIONS | {".pdf", ".docx"}
MAX_WRITE_BYTES = 1_000_000
MAX_READ_BYTES = 20_000_000
MAX_SCAN_FILES = 3000
WINDOWS_RESERVED = re.compile(r"^(con|prn|aux|nul|com\d|lpt\d)(\..*)?$", re.I)


class SafeFiles:
    def __init__(self, allowed_dirs):
        self.roots = [Path(d).resolve() for d in allowed_dirs]
        if not self.roots:
            raise ValueError("At least one allowed directory is required.")
        for root in self.roots:
            root.mkdir(parents=True, exist_ok=True)

    # ------------------------------------------------------------ paths
    def resolve(self, path):
        """Return an absolute path inside an allowed folder, or raise ToolError."""
        raw = (path or "").strip().strip('"')
        if not raw:
            raise ToolError("path is required.")
        if "\x00" in raw:
            raise ToolError("Invalid path.")
        candidate = Path(os.path.expandvars(os.path.expanduser(raw)))
        if not candidate.is_absolute():
            candidate = self.roots[0] / candidate
        resolved = candidate.resolve()
        if any(WINDOWS_RESERVED.match(part) for part in resolved.parts[1:]):
            raise ToolError("That file name is reserved by Windows.")
        for root in self.roots:
            if resolved == root or resolved.is_relative_to(root):
                return resolved
        raise ToolError(f"Access denied: '{path}' is outside the allowed folders "
                        f"({'; '.join(str(r) for r in self.roots)}).")

    # ------------------------------------------------------------ actions
    def list_files(self, path=""):
        folder = self.resolve(path) if path else self.roots[0]
        if not folder.is_dir():
            raise ToolError(f"'{path}' is not a folder.")
        entries = []
        for item in sorted(folder.iterdir(), key=lambda p: (not p.is_dir(), p.name.lower()))[:200]:
            entries.append({"name": item.name + ("/" if item.is_dir() else ""),
                            "size": item.stat().st_size if item.is_file() else None})
        return {"folder": str(folder), "entries": entries,
                "allowed_folders": [str(r) for r in self.roots]}

    def read_file(self, path, max_chars=12000):
        target = self.resolve(path)
        if not target.is_file():
            raise ToolError(f"File not found: {path}")
        return {"path": str(target), **extract_text(target, max_chars)}

    def _check_writable(self, target, content):
        if target.suffix.lower() not in WRITABLE_EXTENSIONS:
            raise ToolError(f"Writing '{target.suffix or 'no extension'}' files is not allowed. "
                            f"Allowed: {', '.join(sorted(WRITABLE_EXTENSIONS))}.")
        if len(content.encode("utf-8")) > MAX_WRITE_BYTES:
            raise ToolError("Content is too large (max 1 MB).")
        if target.is_dir():
            raise ToolError("That path is a folder.")

    def write_preview(self, args):
        """Approval text for overwrites; None for brand-new files."""
        target = self.resolve(args["path"])
        content = args["content"]
        self._check_writable(target, content)
        if not target.exists():
            return None
        if not args.get("overwrite"):
            raise ToolError(f"'{target.name}' already exists. Ask the user whether to overwrite it, then call "
                            "again with overwrite=true.")
        old = target.read_text(encoding="utf-8", errors="replace")
        diff = list(difflib.unified_diff(old.splitlines(), content.splitlines(), "current", "new", lineterm="", n=1))
        shown = "\n".join(diff[:40]) + ("\n  ..." if len(diff) > 40 else "")
        return f"Overwrite {target} ({len(old)} -> {len(content)} characters)?\n{shown or '(no textual change)'}"

    def write_file(self, path, content, overwrite=False):
        target = self.resolve(path)
        self._check_writable(target, content)
        existed = target.exists()
        if existed and not overwrite:
            raise ToolError(f"'{target.name}' already exists and overwrite was not requested.")
        target.parent.mkdir(parents=True, exist_ok=True)
        target.write_text(content, encoding="utf-8")
        written = target.read_text(encoding="utf-8")
        if written != content:
            raise ToolError("Verification failed: the file content differs from what was written.")
        return {"written": True, "path": str(target), "overwritten": existed, "characters": len(content)}

    def delete_preview(self, args):
        target = self.resolve(args["path"])
        if not target.is_file():
            raise ToolError(f"File not found: {args['path']}")
        return f"Permanently delete {target} ({target.stat().st_size} bytes)?"

    def delete_file(self, path):
        target = self.resolve(path)
        if not target.is_file():
            raise ToolError(f"File not found: {path}")
        target.unlink()
        if target.exists():
            raise ToolError("The file still exists after deletion.")
        return {"deleted": True, "path": str(target)}

    def search_documents(self, query, max_results=10):
        words = [w for w in re.findall(r"\w+", query.lower()) if len(w) > 1]
        if not words:
            raise ToolError("query needs at least one word.")
        results = []
        scanned = 0
        for root in self.roots:
            for folder, dirs, files in os.walk(root):
                dirs[:] = [d for d in dirs if not d.startswith(".") and d not in {"node_modules", "__pycache__"}]
                for name in files:
                    path = Path(folder) / name
                    if path.suffix.lower() not in DOCUMENT_EXTENSIONS:
                        continue
                    scanned += 1
                    if scanned > MAX_SCAN_FILES:
                        break
                    hit = _match(path, words)
                    if hit:
                        results.append(hit)
        results.sort(key=lambda r: -r["score"])
        return {"query": query, "results": results[:max_results], "files_scanned": min(scanned, MAX_SCAN_FILES)}


def _match(path, words):
    name_hits = sum(word in path.name.lower() for word in words)
    try:
        if path.stat().st_size > MAX_READ_BYTES:
            return None
        text = extract_text(path, 200_000)["text"]
    except (OSError, ToolError):
        text = ""
    lower = text.lower()
    content_hits = sum(word in lower for word in words)
    score = name_hits * 2 + content_hits
    if score == 0:
        return None
    snippet = ""
    for word in words:
        index = lower.find(word)
        if index >= 0:
            snippet = text[max(0, index - 80): index + 120].replace("\n", " ").strip()
            break
    return {"path": str(path), "score": score, "snippet": snippet}


def extract_text(path, max_chars=12000):
    """Text of a supported document, cut to max_chars."""
    suffix = path.suffix.lower()
    if path.stat().st_size > MAX_READ_BYTES:
        raise ToolError("File is too large to read (max 20 MB).")
    if suffix == ".pdf":
        try:
            from pypdf import PdfReader
            reader = PdfReader(str(path))
            pages = []
            for page in reader.pages[:50]:
                pages.append(page.extract_text() or "")
                if sum(map(len, pages)) > max_chars:
                    break
            text = "\n".join(pages)
        except Exception as error:
            raise ToolError(f"Could not read PDF: {type(error).__name__}") from error
    elif suffix == ".docx":
        try:
            with zipfile.ZipFile(path) as archive:
                root = ElementTree.fromstring(archive.read("word/document.xml"))
            ns = "{http://schemas.openxmlformats.org/wordprocessingml/2006/main}"
            text = "\n".join("".join(t.text or "" for t in p.iter(f"{ns}t")) for p in root.iter(f"{ns}p"))
        except (zipfile.BadZipFile, KeyError, ElementTree.ParseError) as error:
            raise ToolError(f"Could not read Word document: {type(error).__name__}") from error
    elif suffix in TEXT_EXTENSIONS:
        text = path.read_text(encoding="utf-8", errors="replace")
    else:
        raise ToolError(f"Unsupported file type '{suffix}'. Supported: {', '.join(sorted(DOCUMENT_EXTENSIONS))}.")
    truncated = len(text) > max_chars
    return {"text": text[:max_chars], "characters": len(text), "truncated": truncated}


def build_file_tools(files):
    path = {"type": "string", "minLength": 1, "maxLength": 500,
            "description": "Path inside an allowed folder; relative paths start in the first allowed folder."}
    folders = "; ".join(str(r) for r in files.roots)
    return [
        Tool("list_files", f"List files in an allowed folder ({folders}).",
             {"type": "object", "properties": {"path": {"type": "string", "maxLength": 500}},
              "required": [], "additionalProperties": False},
             files.list_files),
        Tool("read_file",
             "Read a text, Markdown, CSV, code, PDF or .docx file from an allowed folder. Use it to answer "
             "questions about or summarize a document.",
             {"type": "object", "properties": {
                 "path": path, "max_chars": {"type": "integer", "minimum": 500, "maximum": 30000}},
              "required": ["path"], "additionalProperties": False},
             files.read_file),
        Tool("write_file",
             "Create a text file in an allowed folder. Overwriting an existing file requires overwrite=true "
             "and the user's approval.",
             {"type": "object", "properties": {
                 "path": path, "content": {"type": "string", "maxLength": 200000},
                 "overwrite": {"type": "boolean"}},
              "required": ["path", "content"], "additionalProperties": False},
             files.write_file, approval=files.write_preview),
        Tool("delete_file", "Delete one file in an allowed folder. Always requires the user's approval.",
             {"type": "object", "properties": {"path": path}, "required": ["path"], "additionalProperties": False},
             files.delete_file, approval=files.delete_preview),
        Tool("search_documents",
             "Search file names and contents of documents in the allowed folders by keywords.",
             {"type": "object", "properties": {
                 "query": {"type": "string", "minLength": 1, "maxLength": 200},
                 "max_results": {"type": "integer", "minimum": 1, "maximum": 25}},
              "required": ["query"], "additionalProperties": False},
             files.search_documents, timeout=30),
    ]
