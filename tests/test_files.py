import json
import zipfile

import pytest

from jarvis.tools.file_tools import SafeFiles, build_file_tools
from jarvis.tools.registry import ToolError, ToolRegistry


@pytest.fixture
def files(tmp_path):
    return SafeFiles([tmp_path / "workspace"])


@pytest.fixture
def registry(files):
    registry = ToolRegistry()
    registry.register_all(build_file_tools(files))
    return registry


@pytest.mark.parametrize("path", [
    "../secret.txt",
    "..\\..\\Windows\\system32\\drivers\\etc\\hosts",
    "C:\\Windows\\win.ini",
    "notes/../../escape.txt",
    "%USERPROFILE%\\Desktop\\x.txt",
    "~/x.txt",
])
def test_path_traversal_blocked(files, path):
    with pytest.raises(ToolError, match="outside the allowed folders"):
        files.resolve(path)


def test_reserved_and_empty_names(files):
    with pytest.raises(ToolError):
        files.resolve("CON.txt")
    with pytest.raises(ToolError):
        files.resolve("  ")


def test_create_read_and_list(files, registry):
    result = registry.execute("write_file", json.dumps({"path": "notes/todo.md", "content": "# Todo\n- test"}))
    assert result.ok and '"overwritten": false' in result.content
    assert "- test" in files.read_file("notes/todo.md")["text"]
    assert {"name": "notes/", "size": None} in files.list_files()["entries"]


def test_overwrite_needs_flag_and_approval(files, registry):
    files.write_file("a.txt", "old text")
    no_flag = registry.execute("write_file", json.dumps({"path": "a.txt", "content": "new"}),
                               approver=lambda t, p: True)
    assert not no_flag.ok and "already exists" in no_flag.content
    args = json.dumps({"path": "a.txt", "content": "new text", "overwrite": True})
    assert registry.execute("write_file", args).status == "denied"
    assert files.read_file("a.txt")["text"] == "old text"
    previews = []
    assert registry.execute("write_file", args, approver=lambda t, p: previews.append(p) or True).ok
    assert files.read_file("a.txt")["text"] == "new text"
    assert "-old text" in previews[0] and "+new text" in previews[0]


def test_delete_needs_approval(files, registry):
    files.write_file("gone.txt", "bye")
    assert registry.execute("delete_file", '{"path": "gone.txt"}').status == "denied"
    assert registry.execute("delete_file", '{"path": "gone.txt"}', approver=lambda t, p: True).ok
    assert not (files.roots[0] / "gone.txt").exists()


def test_unsupported_write_extension(files):
    with pytest.raises(ToolError, match="not allowed"):
        files.write_file("run.exe", "MZ")


def test_search_and_docx(files):
    files.write_file("python_notes.md", "Decorators wrap functions.")
    files.write_file("shopping.txt", "milk eggs")
    docx = files.roots[0] / "report.docx"
    with zipfile.ZipFile(docx, "w") as archive:
        archive.writestr("word/document.xml",
                         '<w:document xmlns:w="http://schemas.openxmlformats.org/wordprocessingml/2006/main">'
                         "<w:body><w:p><w:r><w:t>Quarterly decorators report</w:t></w:r></w:p></w:body></w:document>")
    results = files.search_documents("decorators")["results"]
    assert {r["path"].split("\\")[-1].split("/")[-1] for r in results} == {"python_notes.md", "report.docx"}
    assert "Quarterly" in files.read_file("report.docx")["text"]


def test_read_truncates(files):
    files.write_file("big.txt", "a" * 5000)
    result = files.read_file("big.txt", max_chars=1000)
    assert result["truncated"] and len(result["text"]) == 1000
