from __future__ import annotations

import ast
import hashlib
import json
import re
from collections import Counter
from pathlib import Path
from typing import Any

import httpx

from codecare.security import redact
from codecare.services import github as gh

LANGUAGE_BY_EXT = {
    ".py": "Python",
    ".js": "JavaScript",
    ".ts": "TypeScript",
    ".tsx": "TypeScript",
    ".jsx": "JavaScript",
    ".mjs": "JavaScript",
    ".cjs": "JavaScript",
}

SKIP_DIRS = {"node_modules", ".next", "dist", "build", "coverage", "vendor", ".git", "__pycache__", ".venv", "venv"}
SKIP_FILES = {".env.example", ".env.sample", ".env.template"}
SECRET_HINT = re.compile(r"(?i)(aws_secret_access_key|private_key|xox[baprs]-)['\"]?\s*[:=]\s*['\"][^'\"]{12,}")
DYNAMIC_JS = re.compile(r"\beval\s*\(|\bnew\s+Function\s*\(")
PY_STRING = re.compile(r"('''[\s\S]*?'''|\"\"\"[\s\S]*?\"\"\"|'(?:\\.|[^'\\])*'|\"(?:\\.|[^\"\\])*\")")
JS_STRING = re.compile(r"(`(?:\\.|[^`\\])*`|'(?:\\.|[^'\\])*'|\"(?:\\.|[^\"\\])*\")")


def code_without_literals(content: str, *, javascript: bool = False) -> str:
    """Drop comments and string literals so a mention of eval() is not treated as a call."""
    without_comments = re.sub(r"(?m)//.*$" if javascript else r"(?m)#.*$", "", content)
    return (JS_STRING if javascript else PY_STRING).sub("''", without_comments)
KNOWN_PY = {"flask": "flask", "django": "django", "fastapi": "fastapi", "requests": "requests", "numpy": "numpy"}
KNOWN_JS = {"react": "react", "next": "next", "express": "express", "lodash": "lodash", "axios": "axios"}
PY_IMPORT = re.compile(r"^\s*(?:import|from)\s+([a-zA-Z0-9_]+)", re.M)
JS_IMPORT = re.compile(r"""(?:from\s+['"]([a-zA-Z0-9@/_-]+)['"]|require\(\s*['"]([a-zA-Z0-9@/_-]+)['"]\s*\))""")


def fingerprint(*parts: str) -> str:
    return hashlib.sha256("|".join(parts).encode()).hexdigest()[:32]


def skip_path(path: str) -> bool:
    parts = path.replace("\\", "/").split("/")
    name = parts[-1].lower() if parts else ""
    if name in SKIP_FILES:
        return True
    return any(part in SKIP_DIRS for part in parts)


def detect_languages(paths: list[str]) -> list[str]:
    counts: Counter[str] = Counter()
    for path in paths:
        if skip_path(path):
            continue
        lower = path.lower()
        for ext, lang in LANGUAGE_BY_EXT.items():
            if lower.endswith(ext):
                counts[lang] += 1
    return [name for name, _ in counts.most_common(5)]


def detect_frameworks(paths: list[str], package_json: dict | None) -> list[str]:
    names = {Path(p).name.lower() for p in paths}
    deps = {}
    if package_json:
        deps.update(package_json.get("dependencies") or {})
        deps.update(package_json.get("devDependencies") or {})
    found = []
    if "next.config.js" in names or "next.config.mjs" in names or "next.config.ts" in names or "next" in deps:
        found.append("Next.js")
    if "react" in deps:
        found.append("React")
    if "express" in deps:
        found.append("Express")
    if any(p.endswith(".ts") or p.endswith(".tsx") for p in paths if not skip_path(p)):
        found.append("TypeScript")
    if any(p.endswith(".py") for p in paths if not skip_path(p)):
        found.append("Python")
    if "package.json" in names:
        found.append("Node.js")
    return list(dict.fromkeys(found))


def parse_requirements(text: str) -> list[tuple[str, str]]:
    pkgs = []
    for line in text.splitlines():
        line = line.strip()
        if not line or line.startswith("#") or line.startswith("-"):
            continue
        name = re.split(r"[=<>!~\[]", line, maxsplit=1)[0].strip()
        version = ""
        if "==" in line:
            version = line.split("==", 1)[1].split("#")[0].strip()
        if name:
            pkgs.append((name, version))
    return pkgs


def parse_package_json(text: str) -> tuple[dict | None, list[tuple[str, str]]]:
    try:
        data = json.loads(text)
    except json.JSONDecodeError:
        return None, []
    deps = {}
    deps.update(data.get("dependencies") or {})
    deps.update(data.get("devDependencies") or {})
    packages = []
    for name, version in deps.items():
        raw = str(version)
        if raw.startswith(("file:", "workspace:", "link:", "http")):
            continue
        packages.append((name, raw.lstrip("^~>=<")))
    return data, packages


def python_syntax_findings(path: str, source: str) -> list[dict]:
    try:
        ast.parse(source)
        return []
    except SyntaxError as exc:
        return [
            {
                "title": f"Python syntax error in {path}",
                "severity": "high",
                "category": "broken_build",
                "evidence": f"Line {exc.lineno}: {exc.msg}",
                "file_path": path,
            }
        ]


def static_findings(files: dict[str, str]) -> list[dict]:
    findings: list[dict] = []
    paths = list(files)
    pkg_raw = files.get("package.json") or next((files[p] for p in paths if p.endswith("package.json")), "")
    pkg_data, npm_pkgs = parse_package_json(pkg_raw) if pkg_raw else (None, [])
    npm_names = {name for name, _ in npm_pkgs}
    req_raw = files.get("requirements.txt") or ""
    py_pkgs = {name for name, _ in parse_requirements(req_raw)} if req_raw else set()

    has_lock = any(Path(p).name in {"package-lock.json", "pnpm-lock.yaml", "yarn.lock"} for p in paths)
    if pkg_data is None and pkg_raw:
        findings.append(
            {
                "title": "package.json is not valid JSON",
                "severity": "high",
                "category": "broken_build",
                "evidence": "The manifest could not be parsed, so installs and builds will fail.",
                "file_path": "package.json",
            }
        )
    if pkg_data and not has_lock:
        findings.append(
            {
                "title": "Node manifest has no lockfile",
                "severity": "medium",
                "category": "broken_build",
                "evidence": "package.json is present without package-lock.json, pnpm-lock.yaml, or yarn.lock.",
                "file_path": "package.json",
            }
        )

    for path in paths:
        lower = path.lower()
        name = Path(path).name.lower()
        if name == ".env" or name.endswith(".pem") or name in {"id_rsa", "credentials.json"}:
            findings.append(
                {
                    "title": f"Sensitive file committed: {path}",
                    "severity": "high",
                    "category": "static_analysis",
                    "evidence": "A secrets-bearing filename is in the tree. Contents were not stored.",
                    "file_path": path,
                }
            )

    for path, content in files.items():
        if path.endswith(".py"):
            findings.extend(python_syntax_findings(path, content))
            if "eval(" in code_without_literals(content):
                findings.append(
                    {
                        "title": f"Use of eval() in {path}",
                        "severity": "medium",
                        "category": "static_analysis",
                        "evidence": "eval() executes a string as code. The occurrence was recorded without storing secrets.",
                        "file_path": path,
                    }
                )
            for match in PY_IMPORT.finditer(content):
                mod = match.group(1)
                pkg = KNOWN_PY.get(mod)
                if pkg and py_pkgs and pkg not in py_pkgs and path.endswith(".py"):
                    findings.append(
                        {
                            "title": f"Import '{mod}' is not listed in requirements.txt",
                            "severity": "medium",
                            "category": "missing_dependency",
                            "evidence": f"{path} imports {mod}, which maps to PyPI package '{pkg}'.",
                            "file_path": path,
                        }
                    )
        if path.endswith((".js", ".mjs", ".cjs", ".ts", ".tsx", ".jsx")):
            if DYNAMIC_JS.search(code_without_literals(content, javascript=True)):
                findings.append(
                    {
                        "title": f"Dynamic code execution in {path}",
                        "severity": "medium",
                        "category": "static_analysis",
                        "evidence": "eval() or new Function() was found.",
                        "file_path": path,
                    }
                )
            for match in JS_IMPORT.finditer(content):
                spec = match.group(1) or match.group(2) or ""
                root = spec.split("/")[0]
                if root.startswith("."):
                    continue
                pkg = KNOWN_JS.get(root)
                if pkg and npm_names and pkg not in npm_names:
                    findings.append(
                        {
                            "title": f"Import '{root}' is not listed in package.json",
                            "severity": "medium",
                            "category": "missing_dependency",
                            "evidence": f"{path} imports {root}.",
                            "file_path": path,
                        }
                    )
        if SECRET_HINT.search(content):
            findings.append(
                {
                    "title": f"Possible hardcoded secret in {path}",
                    "severity": "high",
                    "category": "static_analysis",
                    "evidence": "A high-confidence secret pattern was detected. The value was not stored.",
                    "file_path": path,
                }
            )
    return findings


async def query_osv(packages: list[tuple[str, str, str]]) -> list[dict]:
    if not packages:
        return []
    queries = []
    for ecosystem, name, version in packages[:80]:
        item: dict[str, Any] = {"package": {"name": name, "ecosystem": ecosystem}}
        if version:
            item["version"] = version
        queries.append(item)
    async with httpx.AsyncClient(timeout=30) as client:
        response = await client.post("https://api.osv.dev/v1/querybatch", json={"queries": queries})
        if response.status_code >= 400:
            return []
        results = response.json().get("results") or []
    findings = []
    for query, result in zip(queries, results):
        for vuln in result.get("vulns") or []:
            findings.append(
                {
                    "title": f"Vulnerability in {query['package']['name']}: {vuln.get('id')}",
                    "severity": "high",
                    "category": "dependency_vulnerability",
                    "evidence": redact(vuln.get("summary") or vuln.get("id") or ""),
                    "file_path": "requirements.txt" if query["package"]["ecosystem"] == "PyPI" else "package.json",
                }
            )
    return findings


async def outdated_pypi(name: str, current: str) -> dict | None:
    if not current:
        return None
    try:
        async with httpx.AsyncClient(timeout=15) as client:
            response = await client.get(f"https://pypi.org/pypi/{name}/json")
            if response.status_code >= 400:
                return None
            latest = response.json()["info"]["version"]
    except Exception:
        return None
    if latest and latest != current:
        return {
            "title": f"Outdated package {name} ({current} → {latest})",
            "severity": "low",
            "category": "outdated_package",
            "evidence": f"{name}=={current}; latest on PyPI is {latest}",
            "file_path": "requirements.txt",
        }
    return None


async def outdated_npm(name: str, current: str) -> dict | None:
    if not current:
        return None
    try:
        async with httpx.AsyncClient(timeout=15) as client:
            response = await client.get(f"https://registry.npmjs.org/{name}/latest")
            if response.status_code >= 400:
                return None
            latest = response.json().get("version")
    except Exception:
        return None
    if latest and latest != current:
        return {
            "title": f"Outdated package {name} ({current} → {latest})",
            "severity": "low",
            "category": "outdated_package",
            "evidence": f"{name}@{current}; latest on npm is {latest}",
            "file_path": "package.json",
        }
    return None


def uniquify(full_name: str, findings: list[dict]) -> list[dict]:
    unique = {}
    for item in findings:
        key = fingerprint(full_name, item["category"], item["title"], item.get("file_path", ""))
        item["fingerprint"] = key
        unique[key] = item
    return list(unique.values())


def read_local_files(root: Path) -> dict[str, str]:
    files: dict[str, str] = {}
    for path in root.rglob("*"):
        if not path.is_file():
            continue
        rel = path.relative_to(root).as_posix()
        if skip_path(rel):
            continue
        if path.stat().st_size > 200_000:
            continue
        try:
            files[rel] = path.read_text(encoding="utf-8")
        except UnicodeDecodeError:
            continue
    return files


async def enrich_manifests(files: dict[str, str]) -> list[dict]:
    extra: list[dict] = []
    req = files.get("requirements.txt")
    if req:
        packages = parse_requirements(req)
        extra.extend(await query_osv([("PyPI", n, v) for n, v in packages if n]))
        for name, version in packages[:10]:
            stale = await outdated_pypi(name, version)
            if stale:
                extra.append(stale)
    pkg = files.get("package.json")
    if pkg:
        _data, packages = parse_package_json(pkg)
        extra.extend(await query_osv([("npm", n, v) for n, v in packages]))
        for name, version in packages[:10]:
            stale = await outdated_npm(name, version)
            if stale:
                extra.append(stale)
    return extra


async def scan_local_directory(root: Path, *, network: bool = False) -> dict:
    files = read_local_files(root)
    return await scan_files(files, full_name=str(root.name), network=network)


async def scan_files(files: dict[str, str], *, full_name: str = "local/fixture", network: bool = False) -> dict:
    paths = [p for p in files if not skip_path(p)]
    pkg_data, _ = parse_package_json(files.get("package.json") or "") if files.get("package.json") else (None, [])
    findings = static_findings({p: files[p] for p in paths})
    if network:
        findings.extend(await enrich_manifests(files))
    return {
        "commit_sha": "",
        "languages": detect_languages(paths),
        "frameworks": detect_frameworks(paths, pkg_data),
        "file_count": len(paths),
        "findings": uniquify(full_name, findings),
        "default_branch": "local",
    }


async def scan_repository(token: str, repo: dict) -> dict:
    full_name = repo["full_name"]
    branch = repo.get("default_branch") or "main"
    meta = await gh.get_repo(token, full_name)
    default_branch = meta.get("default_branch") or branch
    ref = await gh._request("GET", f"{gh.GITHUB_API}/repos/{full_name}/branches/{default_branch}", token)
    commit_sha = ((ref.get("commit") or {}).get("sha")) or ""
    tree = await gh.get_tree(token, full_name, default_branch)
    paths = [item["path"] for item in tree.get("tree") or [] if item.get("type") == "blob" and not skip_path(item["path"])]
    files: dict[str, str] = {}
    interesting = [
        p
        for p in paths
        if Path(p).name in {"package.json", "package-lock.json", "pnpm-lock.yaml", "yarn.lock", "requirements.txt", "pyproject.toml", "next.config.js", "next.config.mjs", "tsconfig.json"}
        or p.endswith((".py", ".js", ".ts", ".tsx", ".jsx", ".mjs"))
    ][:40]
    for path in interesting:
        content = await gh.get_file(token, full_name, path, default_branch)
        if content:
            files[path] = content
    result = await scan_files(files, full_name=full_name, network=True)
    result["commit_sha"] = commit_sha
    result["file_count"] = len(paths)
    result["default_branch"] = default_branch
    for run in await gh.list_workflow_runs(token, full_name):
        if run.get("conclusion") == "failure" and run.get("head_branch") in {default_branch, run.get("head_branch")}:
            if run.get("event") in {"push", "workflow_dispatch", "schedule", "pull_request"} or True:
                result["findings"].append(
                    {
                        "title": f"GitHub workflow failed: {run.get('name')}",
                        "severity": "high",
                        "category": "workflow_failure",
                        "evidence": f"Run {run.get('id')} on {run.get('head_branch')} concluded failure.",
                        "file_path": ".github/workflows",
                        "fingerprint": fingerprint(full_name, "workflow_failure", str(run.get("name")), str(run.get("id"))),
                    }
                )
    result["findings"] = uniquify(full_name, result["findings"])
    return result
