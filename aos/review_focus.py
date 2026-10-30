"""Which reviews a task's change needs (ROADMAP 4b), decided by fixed rules, not by an AI.

A change is security-sensitive when the planner marked it so, when it touches files whose
paths name auth, sessions, secrets, crypto or permissions, or when its added lines handle
secrets, build SQL from strings, run shell commands, evaluate code or build paths from input.
"""

from __future__ import annotations

import re

_PATH = re.compile(r"(?i)(^|[/_.-])(auth\w*|login|logout|session\w*|token\w*|password\w*|passwd|secret\w*|"
                   r"crypt\w*|permission\w*|acl|rbac|oauth\w*|jwt|csrf|\.env[\w.-]*|credentials?)([/_.-]|$)")
_CONTENT = (
    ("handles a password, token, secret or API key",
     re.compile(r"(?i)\b(password|passwd|secret|api[_-]?key|access[_-]?token|private[_-]?key)\b")),
    ("builds SQL from strings",
     re.compile(r"(?i)(execute|query|raw)\s*\(\s*(f[\"']|[\"'][^\"']*[\"']\s*(%|\+|\.format))[^)]*\b(select|insert|update|delete)\b"
                r"|\b(select|insert|update|delete)\b[^\"']*[\"']\s*(%|\+|\.format)")),
    ("runs a shell command", re.compile(r"shell\s*=\s*True|os\.system\s*\(|child_process|Runtime\.getRuntime\(\)\.exec")),
    ("evaluates code (eval/exec)", re.compile(r"(?<![\w.])(eval|exec)\s*\(")),
    ("deserialises untrusted data", re.compile(r"pickle\.loads?\s*\(|yaml\.load\s*\((?![^)]*SafeLoader)")),
    ("uses crypto or hashing", re.compile(r"(?i)\b(hashlib|bcrypt|hmac|Crypto\.|cryptography|md5|sha1)\b")),
    ("builds file paths from input", re.compile(r"(?i)(open|os\.path\.join|Path)\s*\([^)]*(request|params|args|input|user)")),
)


def security_reasons(paths: list[str], diff: str, flagged: bool = False) -> list[str]:
    """Why a change needs a security-focused review; empty when it doesn't."""
    reasons = ["marked security-sensitive by the planner"] if flagged else []
    touched = [p for p in paths if _PATH.search(p)]
    if touched:
        reasons.append("touches " + ", ".join(touched[:5]) + (" …" if len(touched) > 5 else ""))
    added = "\n".join(line[1:] for line in (diff or "").splitlines()
                      if line.startswith("+") and not line.startswith("+++"))
    reasons += [why for why, pattern in _CONTENT if pattern.search(added)]
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
