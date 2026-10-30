"""Which reviews a task's change needs (ROADMAP 4b), decided by fixed rules, not by an AI.

A change is security-sensitive when the planner marked it so, when it touches files whose
names say auth, sessions, secrets, crypto or permissions, or when the lines it adds to code
(not docs or lockfiles) handle credentials, build SQL from strings, run shell commands,
evaluate code, deserialise untrusted data, use weak hashes or build file paths from input.
Every rule works line by line, so a big diff stays fast.
"""

from __future__ import annotations

import re
from pathlib import PurePosixPath

_SENSITIVE_WORDS = {"auth", "authn", "authz", "oauth", "oauth2", "login", "logout", "session", "password",
                    "passwords", "passwd", "secret", "secrets", "crypto", "cryptography", "permission",
                    "permissions", "acl", "rbac", "jwt", "csrf", "credential", "credentials", "token", "tokens"}
_LOCKFILES = {"package-lock.json", "yarn.lock", "pnpm-lock.yaml", "poetry.lock", "pipfile.lock", "cargo.lock",
              "go.sum", "composer.lock", "gemfile.lock"}
_DOC_SUFFIXES = {".md", ".rst", ".txt", ".adoc"}
MAX_ADDED_CHARS = 200_000

_SQL_SHAPE = re.compile(r"(?i)\b(select\s+\S.*?\s+from|insert\s+into|update\s+\w+\s+set|delete\s+from)\b")
_INTERPOLATION = re.compile(r"""f["'][^"'\n]*\{|\$\{|["']\s*(\+|%)\s*\w|\w\s*\+\s*["']|\.format\(""")
_RULES = (
    ("handles a password, token, secret or API key",
     re.compile(r"(?i)\b(password|passwd|api[_-]?key|access[_-]?token|refresh[_-]?token|private[_-]?key|"
                r"client[_-]?secret|secret[_-]?key)\b")),
    ("runs a shell command",
     re.compile(r"""shell\s*=\s*True|os\.(system|popen)\s*\(|child_process|Runtime\.getRuntime\(\)\.exec|"""
                r"""["']sh["']\s*,\s*["']-c["']|["']/bin/(ba)?sh["']""")),
    ("evaluates code (eval/exec)", re.compile(r"(?<![\w.])(eval|exec)\s*\(")),
    ("deserialises untrusted data", re.compile(r"pickle\.loads?\s*\(|yaml\.load\s*\((?![^)\n]*SafeLoader)")),
    ("uses a weak hash (MD5/SHA-1)", re.compile(r"hashlib\.(md5|sha1)\s*\(|(?<![\w.])(md5|sha1)\s*\(")),
    ("builds file paths from input",
     re.compile(r"\b(open|os\.path\.join|Path|send_file|send_from_directory)\s*\([^)\n]*"
                r"\b(request|params|args|input|query|filename)\b")),
)


def _sensitive_path(path: str) -> bool:
    p = PurePosixPath(path.lower())
    if p.name.startswith(".env"):
        return True
    return any(word in _SENSITIVE_WORDS for word in re.split(r"[/_.\-]+", str(p)) if word)


def _code_file(path: str) -> bool:
    p = PurePosixPath(path.lower())
    return (p.name not in _LOCKFILES and p.suffix not in _DOC_SUFFIXES
            and not any(part in ("docs", "doc") for part in p.parts[:-1]))


def _added_code_lines(paths: list[str], diff: str) -> list[str]:
    """Added lines of code files; a diff without file headers counts for all `paths`."""
    lines, current, has_headers, size = [], None, False, 0
    for line in (diff or "").splitlines():
        if line.startswith("+++ "):
            has_headers = True
            current = line[4:].strip()
            current = current[2:] if current.startswith("b/") else current
            continue
        if line.startswith("+") and size < MAX_ADDED_CHARS:
            in_code = _code_file(current) if has_headers and current else any(_code_file(p) for p in paths)
            if in_code:
                lines.append(line[1:])
                size += len(line)
    return lines


def security_reasons(paths: list[str], diff: str, flagged: bool = False) -> list[str]:
    """Why a change needs a security-focused review; empty when it doesn't."""
    reasons = ["marked security-sensitive by the planner"] if flagged else []
    touched = [p for p in paths if _sensitive_path(p)]
    if touched:
        reasons.append("touches " + ", ".join(touched[:5]) + (" …" if len(touched) > 5 else ""))
    added = _added_code_lines(paths, diff)
    if any(_SQL_SHAPE.search(l) and _INTERPOLATION.search(l) for l in added):
        reasons.append("builds SQL from strings")
    for why, pattern in _RULES:
        if any(pattern.search(l) for l in added):
            reasons.append(why)
    return reasons


SECURITY_CHECKLIST = """\
This change is security-sensitive ({reasons}). Review it as a security reviewer as well:
- authentication and authorization: every new path checks who the caller is and what they may do;
- input validation: everything from users, requests, files or other services is validated;
- injection: no SQL, shell, template or code built from input; parameterised queries only;
- secrets: none in code, config, tests or logs; read from the environment or a secret store;
- path traversal: file paths built from input stay inside their directory;
- crypto: standard libraries, no home-made schemes, no MD5/SHA-1 for security, no hard-coded keys.
Any real security finding means review_fail, even if everything else is right."""
