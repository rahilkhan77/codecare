from __future__ import annotations

import re
import shutil
import subprocess
import tempfile
from pathlib import Path

from codecare.config import get_settings
from codecare.security import redact

FORBIDDEN_HOST_EXEC = "Customer repository code is never executed on the API host."
SITE_PACKAGES = ".codecare-site-packages"

RUNTIME_COMMANDS = {
    "pytest": ["python", "-m", "pytest", "-q", "--maxfail=5", "-o", "cache_dir=/tmp/pytest-cache"],
    "tsc": ["tsc", "--noEmit", "--pretty", "false"],
}

INSTALL_COMMANDS = {
    "pip-wheels": [
        "python",
        "-m",
        "pip",
        "install",
        "--no-input",
        "--disable-pip-version-check",
        "--only-binary=:all:",
        f"--target={SITE_PACKAGES}",
        "-r",
        "requirements.txt",
    ],
    "npm-ci": ["npm", "ci", "--ignore-scripts", "--no-audit", "--no-fund"],
}

NO_TESTS = re.compile(r"(collected 0 items|no tests (ran|collected)|no tests were found)", re.I)
PASSED_TESTS = re.compile(r"\b([1-9]\d*) passed\b")


def docker_available() -> bool:
    return shutil.which("docker") is not None


def docker_image_present(image: str) -> bool:
    try:
        completed = subprocess.run(
            ["docker", "image", "inspect", image],
            capture_output=True,
            text=True,
            timeout=15,
            check=False,
        )
    except (FileNotFoundError, subprocess.TimeoutExpired):
        return False
    return completed.returncode == 0


def apply_files(root: Path, files: dict[str, str]) -> None:
    root = root.resolve()
    for rel, content in files.items():
        normalized = rel.replace("\\", "/")
        if normalized.startswith("/") or normalized.startswith("~") or (len(normalized) > 1 and normalized[1] == ":"):
            raise ValueError("Refusing path traversal in proposed patch")
        cleaned = normalized.lstrip("/")
        if not cleaned or ".." in cleaned.split("/"):
            raise ValueError("Refusing path traversal in proposed patch")
        path = (root / cleaned).resolve()
        if root not in path.parents and path != root:
            raise ValueError("Refusing path traversal in proposed patch")
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(content, encoding="utf-8")


def patch_applied(root: Path, files: dict[str, str]) -> bool:
    if not files:
        return False
    for rel, content in files.items():
        path = root / rel.replace("\\", "/").lstrip("/")
        if not path.is_file() or path.read_text(encoding="utf-8") != content:
            return False
    return True


def requirements_pinned(text: str) -> bool:
    lines = [line.strip() for line in text.splitlines() if line.strip() and not line.startswith("#")]
    if not lines:
        return False
    for line in lines:
        if line.startswith("-"):
            continue
        if "==" not in line and "--hash=" not in line:
            return False
    return True


def requirements_require_hashes(text: str) -> bool:
    return "--hash=" in text


def detect_install(root: Path) -> tuple[list[str], str]:
    names = {path.name.lower() for path in root.iterdir()} if root.exists() else set()
    commands: list[str] = []
    notes: list[str] = []
    if "package.json" in names:
        if "package-lock.json" in names or "npm-shrinkwrap.json" in names:
            commands.append("npm-ci")
        else:
            notes.append("Skipped npm ci because no package-lock.json was present.")
    req = root / "requirements.txt"
    if req.is_file():
        text = req.read_text(encoding="utf-8", errors="replace")
        if requirements_pinned(text):
            commands.append("pip-wheels")
        else:
            notes.append("Skipped pip install because requirements.txt is not pinned.")
    return commands, " ".join(notes)


def detect_runtime(root: Path) -> tuple[list[str] | None, str]:
    if not root.exists():
        return None, "Workspace is missing."
    names = {path.name.lower() for path in root.iterdir()}
    has_pytest = "pytest.ini" in names or "conftest.py" in names or (root / "tests").is_dir()
    if has_pytest:
        return ["pytest"], ""
    if "tsconfig.json" in names:
        return ["tsc"], "Typecheck is not a substitute for tests."
    if "package.json" in names:
        return None, "No allowlisted test runner (pytest). Customer npm test scripts are not executed."
    return None, "No tests were found. compile-only success is not verification."


def _docker_argv(
    *,
    work: Path,
    argv: list[str],
    network: str,
    user: str,
    extra_env: list[str] | None = None,
) -> list[str]:
    settings = get_settings()
    cmd = [
        "docker",
        "run",
        "--rm",
        "--network",
        network,
        "--memory",
        settings.sandbox_memory,
        "--memory-swap",
        settings.sandbox_memory,
        "--cpus",
        str(settings.sandbox_cpus),
        "--pids-limit",
        str(settings.sandbox_pids_limit),
        "--ulimit",
        "nofile=256:256",
        "--cap-drop",
        "ALL",
        "--security-opt",
        "no-new-privileges",
        "--tmpfs",
        "/tmp:rw,noexec,nosuid,size=64m",
        "-v",
        f"{work}:/work:rw",
        "-w",
        "/work",
        "--user",
        user,
        "-e",
        "PYTHONDONTWRITEBYTECODE=1",
        "-e",
        f"PYTHONPATH=/work/{SITE_PACKAGES}",
        "-e",
        "NPM_CONFIG_IGNORE_SCRIPTS=true",
        "-e",
        "PIP_DISABLE_PIP_VERSION_CHECK=1",
    ]
    for env in extra_env or []:
        cmd.extend(["-e", env])
    if network == "none":
        cmd.extend(["--read-only"])
    cmd.append(settings.sandbox_image)
    cmd.extend(argv)
    return cmd


def run_docker(argv: list[str], timeout: int) -> subprocess.CompletedProcess[str]:
    return subprocess.run(argv, capture_output=True, text=True, timeout=timeout, check=False)


def _result(status: str, log: str, passed: int | None = None, failed: int | None = None, **extra) -> dict:
    payload = {
        "status": status,
        "log": redact(log)[-8000:],
        "tests_passed": passed,
        "tests_failed": failed,
    }
    payload.update(extra)
    return payload


def classify_pytest(output: str, returncode: int) -> tuple[str, int, int]:
    if returncode != 0:
        failed_match = re.search(r"\b([1-9]\d*) failed\b", output)
        failed = int(failed_match.group(1)) if failed_match else 1
        return "failed", 0, failed
    if NO_TESTS.search(output):
        return "inconclusive", 0, 0
    match = PASSED_TESTS.search(output)
    if match:
        return "verified", int(match.group(1)), 0
    return "inconclusive", 0, 0


def verify_patch(files: dict[str, str], commands: list[str] | None = None, work_dir: Path | None = None) -> dict:
    settings = get_settings()
    if not docker_available():
        return _result("inconclusive", "Docker is not available. Verification did not run. " + FORBIDDEN_HOST_EXEC)
    if not docker_image_present(settings.sandbox_image):
        return _result(
            "inconclusive",
            f"Sandbox image '{settings.sandbox_image}' is not present. Build it before verification. " + FORBIDDEN_HOST_EXEC,
        )
    if not files:
        return _result("inconclusive", "No patch files were produced, so nothing was executed.")

    created = work_dir is None
    work = Path(tempfile.mkdtemp(prefix="codecare-sandbox-")) if created else work_dir
    logs: list[str] = []
    try:
        apply_files(work, files)
        if not patch_applied(work, files):
            return _result("failed", "Proposed patch was not present on disk after apply.")
        logs.append(f"Applied patch files: {', '.join(files)}")

        install, install_notes = detect_install(work)
        if install_notes:
            logs.append(install_notes)

        for key in install:
            argv = INSTALL_COMMANDS.get(key)
            if not argv:
                return _result("inconclusive", f"Refusing unlisted install command '{key}'.")
            pip_argv = list(argv)
            if key == "pip-wheels":
                req = (work / "requirements.txt").read_text(encoding="utf-8", errors="replace")
                if requirements_require_hashes(req) and "--require-hashes" not in pip_argv:
                    pip_argv.insert(-2, "--require-hashes")
            docker_cmd = _docker_argv(work=work, argv=pip_argv, network="bridge", user="0")
            try:
                completed = run_docker(docker_cmd, settings.sandbox_install_timeout_seconds)
            except FileNotFoundError:
                return _result("inconclusive", "Docker CLI is not installed. " + FORBIDDEN_HOST_EXEC)
            except subprocess.TimeoutExpired:
                return _result("inconclusive", "Dependency install exceeded the time limit.", failed=1)
            logs.append(f"$ {' '.join(pip_argv)} (exit {completed.returncode})\n{completed.stdout or ''}\n{completed.stderr or ''}")
            if completed.returncode != 0:
                return _result(
                    "inconclusive",
                    "\n".join(logs) + "\nDependency installation failed. Verification did not run tests.",
                )

        keys, runtime_note = (commands, "") if commands is not None else detect_runtime(work)
        if keys is None:
            return _result("inconclusive", "\n".join(logs + [runtime_note, FORBIDDEN_HOST_EXEC]))
        if keys == ["tsc"] and "npm-ci" not in install and (work / "package.json").exists():
            return _result(
                "inconclusive",
                "\n".join(logs + ["TypeScript/Next.js verification needs npm ci from a lockfile."]),
            )

        meaningful = False
        tests_passed = 0
        tests_failed = 0
        for key in keys:
            argv = RUNTIME_COMMANDS.get(key)
            if not argv:
                return _result("inconclusive", f"Refusing unlisted verification command '{key}'.")
            docker_cmd = _docker_argv(work=work, argv=argv, network="none", user="65534:65534")
            try:
                completed = run_docker(docker_cmd, settings.sandbox_timeout_seconds)
            except FileNotFoundError:
                return _result("inconclusive", "Docker CLI is not installed. " + FORBIDDEN_HOST_EXEC)
            except subprocess.TimeoutExpired:
                return _result(
                    "failed",
                    "\n".join(logs) + "\nSandbox exceeded the time limit and was terminated.",
                    passed=tests_passed,
                    failed=tests_failed + 1,
                )
            output = f"{completed.stdout or ''}\n{completed.stderr or ''}"
            logs.append(f"$ {' '.join(argv)} (exit {completed.returncode})\n{output}")
            if key == "pytest":
                status, passed, failed = classify_pytest(output, completed.returncode)
                tests_passed += passed
                tests_failed += failed
                if status == "verified":
                    meaningful = True
                elif status == "failed":
                    return _result("failed", "\n".join(logs), passed=tests_passed, failed=tests_failed)
                else:
                    return _result("inconclusive", "\n".join(logs + ["Pytest collected no tests."]), passed=0, failed=0)
            else:
                if completed.returncode != 0:
                    return _result("failed", "\n".join(logs), passed=tests_passed, failed=tests_failed + 1)
                logs.append(runtime_note or "Command succeeded but is not a test run.")

        if not meaningful:
            return _result(
                "inconclusive",
                "\n".join(logs + ["No meaningful tests passed, so the patch is not verified."]),
                passed=tests_passed,
                failed=tests_failed,
            )
        return _result("verified", "\n".join(logs), passed=tests_passed, failed=tests_failed, patch_applied=True)
    finally:
        if created:
            shutil.rmtree(work, ignore_errors=True)


def detect_commands(root: Path) -> list[str] | None:
    keys, _ = detect_runtime(root)
    return keys
