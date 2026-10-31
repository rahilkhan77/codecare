from pathlib import Path

import pytest

from codecare.services.sandbox import detect_commands, verify_patch
from codecare.services.scanner import scan_local_directory

FIXTURE = Path(__file__).resolve().parents[1] / "samples" / "maintenance-fixture"


@pytest.mark.asyncio
async def test_fixture_scan_finds_reproducible_bugs():
    result = await scan_local_directory(FIXTURE, network=False)
    files = {item.get("file_path") for item in result["findings"]}
    categories = {item["category"] for item in result["findings"]}
    assert "Python" in result["languages"]
    assert any(item["file_path"] == "app.py" and "eval" in item["title"].lower() for item in result["findings"])
    assert any(item["file_path"] == "src/risky.js" for item in result["findings"])
    assert "broken_build" in categories
    assert "app.py" in files
    assert not any(".env.example" in (item.get("file_path") or "") for item in result["findings"])


def test_node_without_lockfile_or_modules_is_inconclusive(tmp_path, monkeypatch):
    monkeypatch.setattr("codecare.services.sandbox.docker_available", lambda: True)
    monkeypatch.setattr("codecare.services.sandbox.docker_image_present", lambda image: True)
    (tmp_path / "package.json").write_text('{"name":"demo","dependencies":{"express":"4.17.1"}}', encoding="utf-8")
    assert detect_commands(tmp_path) is None
    result = verify_patch({"package.json": '{"name":"demo","dependencies":{"express":"4.17.1"}}'}, work_dir=tmp_path)
    assert result["status"] == "inconclusive"
    assert "test" in result["log"].lower() or "lockfile" in result["log"].lower() or "npm" in result["log"].lower()


def test_fixture_detects_pytest():
    assert detect_commands(FIXTURE) == ["pytest"]
