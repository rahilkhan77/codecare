import subprocess
from types import SimpleNamespace

from codecare.services.sandbox import FORBIDDEN_HOST_EXEC, INSTALL_COMMANDS, apply_files, verify_patch


def test_sandbox_does_not_run_on_host(tmp_path, monkeypatch):
    monkeypatch.setattr("codecare.services.sandbox.docker_available", lambda: False)
    result = verify_patch({"app.py": "print('hi')"})
    assert result["status"] == "inconclusive"
    assert "Docker" in result["log"]
    assert FORBIDDEN_HOST_EXEC in result["log"]


def test_sandbox_missing_image(monkeypatch):
    monkeypatch.setattr("codecare.services.sandbox.docker_available", lambda: True)
    monkeypatch.setattr("codecare.services.sandbox.docker_image_present", lambda image: False)
    result = verify_patch({"app.py": "print('hi')"})
    assert result["status"] == "inconclusive"
    assert "image" in result["log"].lower()


def test_path_traversal_rejected(tmp_path):
    try:
        apply_files(tmp_path, {"../escape.py": "x"})
        assert False, "should have refused traversal"
    except ValueError:
        pass


def test_absolute_path_traversal_rejected(tmp_path):
    try:
        apply_files(tmp_path, {"/etc/passwd": "x"})
        assert False, "should have refused absolute path"
    except ValueError:
        pass


def test_malicious_dependency_scripts_are_not_in_install_argv():
    argv = INSTALL_COMMANDS["npm-ci"]
    assert "--ignore-scripts" in argv
    assert "install" not in argv  # npm ci only
    assert INSTALL_COMMANDS["pip-wheels"][0:3] == ["python", "-m", "pip"]
    assert "--only-binary=:all:" in INSTALL_COMMANDS["pip-wheels"]


def test_secret_leakage_redacted_from_sandbox_log(monkeypatch):
    monkeypatch.setattr("codecare.services.sandbox.docker_available", lambda: True)
    monkeypatch.setattr("codecare.services.sandbox.docker_image_present", lambda image: True)

    def fake_run(argv, timeout=0):
        return SimpleNamespace(
            returncode=0,
            stdout="1 passed\napi_key=supersecretvalue\n",
            stderr="",
        )

    monkeypatch.setattr("codecare.services.sandbox.run_docker", fake_run)
    result = verify_patch({"tests/test_ok.py": "def test_ok():\n    assert True\n", "app.py": "x = 1\n"})
    assert "supersecretvalue" not in result["log"]
    assert "[redacted]" in result["log"]
    assert result["status"] == "verified"


def test_sandbox_timeout_is_failed_not_verified(monkeypatch):
    monkeypatch.setattr("codecare.services.sandbox.docker_available", lambda: True)
    monkeypatch.setattr("codecare.services.sandbox.docker_image_present", lambda image: True)

    def fake_run(argv, timeout=0):
        raise subprocess.TimeoutExpired(cmd=argv, timeout=1)

    monkeypatch.setattr("codecare.services.sandbox.run_docker", fake_run)
    result = verify_patch({"tests/test_ok.py": "def test_ok():\n    assert True\n"})
    assert result["status"] in {"failed", "inconclusive"}
    assert result["status"] != "verified"
    assert "time limit" in result["log"].lower() or "exceeded" in result["log"].lower()


def test_compile_only_is_not_verified(monkeypatch):
    monkeypatch.setattr("codecare.services.sandbox.docker_available", lambda: True)
    monkeypatch.setattr("codecare.services.sandbox.docker_image_present", lambda image: True)
    monkeypatch.setattr(
        "codecare.services.sandbox.run_docker",
        lambda argv, timeout: SimpleNamespace(returncode=0, stdout="ok", stderr=""),
    )
    result = verify_patch({"app.py": "print('hi')\n"})
    assert result["status"] == "inconclusive"


def test_empty_pytest_is_not_verified(monkeypatch):
    monkeypatch.setattr("codecare.services.sandbox.docker_available", lambda: True)
    monkeypatch.setattr("codecare.services.sandbox.docker_image_present", lambda image: True)
    monkeypatch.setattr(
        "codecare.services.sandbox.run_docker",
        lambda argv, timeout: SimpleNamespace(returncode=0, stdout="collected 0 items\n", stderr=""),
    )
    result = verify_patch({"tests/test_missing.py": "# empty\n", "app.py": "x=1\n"})
    assert result["status"] == "inconclusive"


def test_pr_requires_verified_run(client):
    from tests.conftest import register

    register(client)
    response = client.post("/api/v1/fixes/00000000-0000-0000-0000-000000000000/pull-request")
    assert response.status_code == 404
