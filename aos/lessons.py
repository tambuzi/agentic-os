"""Lessons learned on a project: small, scored, evidence-backed rules (spec 2026-10-09 §4, §5.3).

One Markdown file per lesson in <data>/lessons/<project>/<id>.md: YAML frontmatter, then the
action as the body. Every number here comes from fixed rules; the learner (an AI run) only
proposes lessons and confirmations through validated calls.
"""

from __future__ import annotations

import re
import secrets as _secrets
from dataclasses import asdict, dataclass, field
from datetime import date
from pathlib import Path

import yaml

from .errors import AosError
from .store import locked, read_text, write_atomic

STATUSES = ("unreviewed", "confirmed", "rejected", "superseded")
DOMAINS = ("testing", "api", "database", "security", "style", "workflow", "tooling", "other")
EVIDENCE_KINDS = ("review_fail", "verify_fail", "retry", "steer", "permission_denied", "block", "human")
TRIGGER_MAX, ACTION_MAX = 120, 300
ACTIVE = ("unreviewed", "confirmed")

_SECRET = re.compile(r"(?i)(api[_-]?key|token|secret|password|passwd|authorization|bearer)\s*[:=]\s*\S{8,}"
                     r"|sk-[A-Za-z0-9_-]{16,}|ghp_[A-Za-z0-9]{20,}|xox[abp]-[A-Za-z0-9-]{10,}|AKIA[0-9A-Z]{16}"
                     r"|-----BEGIN [A-Z ]*PRIVATE KEY")
_CODE_LINE = re.compile(r"^(\s{4,}|\t)\S|^\s*(def|class|function)\s+\w+\s*[(:{]|^\s*from\s+\S+\s+import\s"
                        r"|[{}].*;|;.*[{}]|=>")
_DANGEROUS = re.compile(r"(?i)git\s+push|--no-verify|core\.hookspath|\bsudo\b|rm\s+-\w*[rf]"
                        r"|(ignore|disregard|forget|override)\s+(\w+\s+){0,3}(previous|prior|above|earlier|all)\s+"
                        r"(\w+\s+){0,2}(instructions|rules|messages|prompts?|guidance)"
                        r"|(ignore|disregard)\s+(the\s+|all\s+|your\s+)?(instructions|rules|system prompt)")


class LessonError(AosError):
    """A lesson operation was refused; the message is shown to whoever asked (the learner)."""


@dataclass
class Lesson:
    id: str
    project: str
    trigger: str
    action: str
    domain: str
    confidence: float
    status: str = "unreviewed"
    evidence: list = field(default_factory=list)
    same_as: list = field(default_factory=list)
    general_rule: str | None = None
    promotion: str | None = None
    promoted_to: str | None = None
    superseded_by: str | None = None
    created: str = ""
    last_seen: str = ""
    note: str | None = None

    @property
    def ref(self) -> str:
        return f"{self.project}/{self.id}"

    def effective_confidence(self, today: date | None = None) -> float:
        """Confidence after fading: -0.05 per full 30 days unseen, never for confirmed lessons."""
        if self.status == "confirmed":
            return self.confidence
        days = ((today or date.today()) - date.fromisoformat(self.last_seen)).days
        return round(max(0.0, self.confidence - 0.05 * max(0, days // 30)), 2)


# -- validation --------------------------------------------------------------------
def validate_text(text: str, limit: int, what: str) -> str:
    t = (text or "").strip()
    if not t:
        raise LessonError(f"{what} is empty")
    if len(t) > limit:
        raise LessonError(f"{what} is {len(t)} characters; keep it under {limit}")
    if "```" in t or any(_CODE_LINE.search(line) for line in t.splitlines()):
        raise LessonError(f"{what} looks like code; describe the pattern in words instead")
    if _SECRET.search(t):
        raise LessonError(f"{what} looks like it contains a secret; never put secrets in lessons")
    if _DANGEROUS.search(t):
        raise LessonError(f"{what} asks for something board workers must never do")
    if "\n" in t:
        t = " ".join(line.strip() for line in t.splitlines() if line.strip())
    return t


def _check_evidence(evidence: list[dict]) -> list[dict]:
    if not evidence:
        raise LessonError("evidence is required: the board events this lesson comes from")
    for e in evidence:
        if e.get("kind") not in EVIDENCE_KINDS:
            raise LessonError(f"unknown evidence kind {e.get('kind')!r}")
    return [dict(e) for e in evidence]


def _slug(text: str) -> str:
    s = re.sub(r"[^a-z0-9]+", "-", text.lower()).strip("-")
    s = re.sub(r"^when-", "", s)
    return (s[:40].strip("-") or "lesson")


# -- the store ---------------------------------------------------------------------
def _dir(data: Path, project: str) -> Path:
    return Path(data) / "lessons" / project


def _parse(path: Path, project: str) -> Lesson:
    text = path.read_text(encoding="utf-8")
    m = re.match(r"^---\n(.*?)\n---\n?(.*)$", text, re.S)
    if not m:
        raise ValueError("no frontmatter")
    meta = yaml.safe_load(m.group(1))
    if not isinstance(meta, dict):
        raise ValueError("frontmatter is not a mapping")
    known = {f for f in Lesson.__dataclass_fields__} - {"action", "project", "id"}
    kw = {k: v for k, v in meta.items() if k in known}
    for k in ("created", "last_seen"):
        kw[k] = str(kw.get(k) or "")
    lesson = Lesson(id=path.stem, project=project, action=m.group(2).strip(), **kw)
    if lesson.status not in STATUSES or not lesson.last_seen:
        raise ValueError("missing or invalid status/last_seen")
    if not isinstance(lesson.trigger, str) or not lesson.trigger.strip():
        raise ValueError("trigger must be text")
    if not isinstance(lesson.evidence, list) or not all(isinstance(e, dict) for e in lesson.evidence):
        raise ValueError("evidence must be a list of mappings")
    if not isinstance(lesson.same_as, list) or not all(isinstance(s, str) for s in lesson.same_as):
        raise ValueError("same_as must be a list of <project>/<id>")
    for name in ("general_rule", "promotion", "promoted_to", "superseded_by", "note"):
        if getattr(lesson, name) is not None and not isinstance(getattr(lesson, name), str):
            raise ValueError(f"{name} must be text")
    date.fromisoformat(lesson.last_seen)
    lesson.confidence = float(lesson.confidence)
    return lesson


def _render(lesson: Lesson) -> str:
    meta = {k: v for k, v in asdict(lesson).items() if k not in ("action",)}
    return "---\n" + yaml.safe_dump(meta, sort_keys=False, allow_unicode=True) + "---\n" + lesson.action + "\n"


class LessonStore:
    def __init__(self, data: str | Path, project: str):
        self.data = Path(data)
        self.project = project
        self.dir = _dir(self.data, project)

    # reads ------------------------------------------------------------------------
    def list(self, include_inactive: bool = True) -> tuple[list[Lesson], list[str]]:
        found, warnings = [], []
        for p in sorted(self.dir.glob("*.md")) if self.dir.is_dir() else []:
            try:
                lesson = _parse(p, self.project)
            except Exception as e:  # a hand edit gone wrong must never break context or the board
                warnings.append(f"lesson file {p} skipped: {e}")
                continue
            if include_inactive or lesson.status in ACTIVE:
                found.append(lesson)
        return found, warnings

    def get(self, lid: str) -> Lesson:
        p = self.dir / f"{lid}.md"
        if not re.fullmatch(r"[a-z0-9-]+", lid or "") or not p.is_file():
            raise LessonError(f"no lesson {self.project}/{lid}")
        try:
            return _parse(p, self.project)
        except Exception as e:
            raise LessonError(f"lesson {self.project}/{lid} is unreadable: {e}")

    def ranked(self, limit: int, min_conf: float, today: date | None = None) -> list[Lesson]:
        today = today or date.today()
        active = [l for l in self.list(include_inactive=False)[0] if l.effective_confidence(today) >= min_conf]
        active.sort(key=lambda l: (l.status != "confirmed", -l.effective_confidence(today), _neg_date(l.last_seen)))
        return active[:limit]

    # writes -----------------------------------------------------------------------
    def _locked(self):
        return locked(self.dir / "store")

    def _save(self, lesson: Lesson) -> Lesson:
        lesson.confidence = round(lesson.confidence, 2)
        write_atomic(self.dir / f"{lesson.id}.md", _render(lesson))
        return lesson

    def _check_links(self, same_as, general_rule) -> tuple[list[str], str | None]:
        same_as = list(dict.fromkeys(same_as or []))
        for ref in same_as:
            project = ref.split("/", 1)[0]
            if project == self.project:
                raise LessonError(f"same_as must point to another project's lesson, not {ref}")
            other = find(self.data, ref)
            if other is None:
                raise LessonError(f"same_as: no lesson {ref}")
            if other.status != "confirmed":
                raise LessonError(f"same_as: {ref} is not confirmed")
        if same_as and not general_rule:
            raise LessonError("same_as needs a general_rule: the rule without project names")
        rule = validate_text(general_rule, ACTION_MAX, "general_rule") if general_rule else None
        return same_as, rule

    def new(self, trigger: str, action: str, domain: str, evidence: list[dict], same_as=(), general_rule=None,
            today: date | None = None) -> Lesson:
        today = today or date.today()
        trigger = validate_text(trigger, TRIGGER_MAX, "trigger")
        action = validate_text(action, ACTION_MAX, "action")
        if domain not in DOMAINS:
            raise LessonError(f"domain must be one of {', '.join(DOMAINS)}")
        evidence = _check_evidence(evidence)
        same_as, rule = self._check_links(same_as, general_rule)
        kinds = {e["kind"] for e in evidence}
        conf = 0.6 if "steer" in kinds or {"review_fail", "retry"} <= kinds else 0.5
        with self._locked():
            for l in self.list()[0]:
                if l.status == "rejected" and _similar(l.trigger, trigger):
                    raise LessonError(f"a lesson like this was rejected before ({l.ref}); don't re-create it")
            lid = f"{_slug(trigger)}-{_secrets.token_hex(2)}"
            lesson = Lesson(id=lid, project=self.project, trigger=trigger, action=action, domain=domain,
                            confidence=conf, evidence=evidence, same_as=same_as, general_rule=rule,
                            created=today.isoformat(), last_seen=today.isoformat())
            return self._save(lesson)

    def _update(self, lid: str, fn) -> Lesson:
        with self._locked():
            lesson = self.get(lid)
            fn(lesson)
            return self._save(lesson)

    def confirm(self, lid: str, evidence: list[dict], same_as=(), general_rule=None,
                today: date | None = None) -> Lesson:
        today = today or date.today()
        evidence = _check_evidence(evidence)
        links, rule = self._check_links(same_as, general_rule) if same_as else ([], None)

        def apply(l: Lesson):
            _refuse_inactive(l)
            seen = {(e.get("task"), ev) for e in l.evidence for ev in e.get("events") or []}
            if all((e.get("task"), ev) in seen for e in evidence for ev in e.get("events") or []):
                raise LessonError(f"{l.ref} already has this evidence; confirm only with new events")
            l.confidence = min(0.9, l.confidence + 0.1)
            l.last_seen = today.isoformat()
            l.evidence = [*l.evidence, *evidence]
            if links:
                l.same_as = list(dict.fromkeys([*l.same_as, *links]))
                l.general_rule = rule
        return self._update(lid, apply)

    def contradict(self, lid: str, evidence: list[dict], why: str, today: date | None = None) -> Lesson:
        today = today or date.today()
        evidence = _check_evidence(evidence)
        why = validate_text(why, ACTION_MAX, "why")

        def apply(l: Lesson):
            _refuse_inactive(l)
            l.confidence = max(0.0, l.confidence - 0.2)
            l.evidence = [*l.evidence, *evidence]
            if round(l.confidence, 2) < 0.3:
                l.status = "rejected"
                l.note = f"rejected automatically on {today.isoformat()}: {why}"
        return self._update(lid, apply)

    def decide(self, lid: str, confirm: bool, today: date | None = None) -> Lesson:
        """The user's decision: confirm (>= 0.8) or reject (final)."""
        today = today or date.today()

        def apply(l: Lesson):
            if l.status == "rejected" and confirm:
                raise LessonError(f"{l.ref} was rejected; rejection is final")
            if confirm:
                l.status, l.confidence = "confirmed", max(l.confidence, 0.8)
                l.last_seen = today.isoformat()
            else:
                l.status = "rejected"
                l.note = f"rejected by the user on {today.isoformat()}"
        return self._update(lid, apply)

    def supersede(self, lid: str, by: str) -> Lesson:
        def apply(l: Lesson):
            l.status, l.superseded_by = "superseded", by
        return self._update(lid, apply)

    def set_fields(self, lid: str, **fields) -> Lesson:
        allowed = {"promotion", "promoted_to"}
        if set(fields) - allowed:
            raise LessonError(f"only {', '.join(sorted(allowed))} can be set directly")

        def apply(l: Lesson):
            for k, v in fields.items():
                setattr(l, k, v)
        return self._update(lid, apply)


def _refuse_inactive(l: Lesson) -> None:
    if l.status == "rejected":
        raise LessonError(f"{l.ref} was rejected; it can't be confirmed or contradicted")
    if l.status == "superseded":
        raise LessonError(f"{l.ref} was superseded by {l.superseded_by}")


def _similar(a: str, b: str) -> bool:
    words = lambda s: set(re.findall(r"[a-z0-9]+", s.lower())) - {"when", "a", "an", "the", "to", "in", "on"}
    wa, wb = words(a), words(b)
    return bool(wa and wb) and len(wa & wb) / len(wa | wb) >= 0.8


def _neg_date(iso: str) -> int:
    return -date.fromisoformat(iso).toordinal()


# -- across projects ---------------------------------------------------------------
def all_projects(data: str | Path) -> list[str]:
    root = Path(data) / "lessons"
    return sorted(p.name for p in root.iterdir() if p.is_dir() and not p.name.startswith(".")) if root.is_dir() else []


def find(data: str | Path, ref: str) -> Lesson | None:
    project, _, lid = (ref or "").partition("/")
    if not project or not lid:
        return None
    try:
        return LessonStore(data, project).get(lid)
    except LessonError:
        return None


def groups(data: str | Path) -> list[list[Lesson]]:
    """Lessons connected by same_as (in either direction); only groups of two or more."""
    every = {l.ref: l for p in all_projects(data) for l in LessonStore(data, p).list()[0]}
    links: dict[str, set[str]] = {ref: set() for ref in every}
    for ref, l in every.items():
        for other in l.same_as:
            if other in every:
                links[ref].add(other)
                links[other].add(ref)
    seen, out = set(), []
    for ref in sorted(every):
        if ref in seen or not links[ref]:
            continue
        stack, comp = [ref], []
        while stack:
            r = stack.pop()
            if r in seen:
                continue
            seen.add(r)
            comp.append(every[r])
            stack.extend(links[r] - seen)
        out.append(sorted(comp, key=lambda l: l.ref))
    return out


def _group_key(group: list[Lesson]) -> str:
    return "|".join(sorted(l.ref for l in group))


def declined(data: str | Path) -> set[str]:
    p = Path(data) / "lessons" / ".declined"
    return {line.strip() for line in read_text(p).splitlines() if line.strip()}


def decline(data: str | Path, group: list[Lesson]) -> None:
    p = Path(data) / "lessons" / ".declined"
    with locked(p):
        keys = declined(data) | {_group_key(group)}
        write_atomic(p, "\n".join(sorted(keys)) + "\n")


def promotion_candidates(data: str | Path) -> list[list[Lesson]]:
    out, no = [], [set(key.split("|")) for key in declined(data)]
    for g in groups(data):
        confirmed = {l.project for l in g if l.status == "confirmed"}
        if len(confirmed) < 2 or any(key <= {l.ref for l in g} for key in no):  # a declined group that grew
            continue
        if any(l.promoted_to or l.promotion for l in g):
            continue
        out.append(g)
    return out


def render_line(lesson: Lesson, today: date | None = None) -> str:
    mark = " ✓" if lesson.status == "confirmed" else ""
    return f"- [{lesson.effective_confidence(today):.1f}{mark}] {lesson.trigger}: {lesson.action}"


def _group_rule(group: list[Lesson]) -> str:
    with_rule = sorted((l for l in group if l.general_rule), key=lambda l: l.last_seen)
    if with_rule:
        return with_rule[-1].general_rule
    best = max(group, key=lambda l: (l.confidence, l.last_seen))
    text = f"{best.trigger}: {best.action}"
    for slug in sorted({l.project for l in group}, key=len, reverse=True):
        text = re.sub(rf"\b{re.escape(slug)}\b", "the project", text)
    return text


def stage_promotions(aos) -> list[str]:
    """One inbox proposal (org memory) per lesson group confirmed in 2+ projects; the lessons
    remember it (`promotion`) so it is proposed once. Applied → promoted_to: org; rejected →
    declined for good (see AOS.apply_proposal / reject_proposal)."""
    from . import inbox
    staged = []
    # the dispatcher and a CLI confirm may stage at once: candidates are read under the lock
    with locked(Path(aos.data) / "lessons" / ".promotions"):
        for group in promotion_candidates(aos.data):
            refs = [l.ref for l in group]
            projects = sorted({l.project for l in group if l.status == "confirmed"})
            pid = inbox.stage(aos.data, "memory", "add", {"scope": "org", "text": _group_rule(group), "lessons": refs},
                              source="learning", reason=f"lesson confirmed in {len(projects)} projects "
                                                        f"({', '.join(projects)}): {', '.join(refs)}")
            for l in group:
                LessonStore(aos.data, l.project).set_fields(l.id, promotion=pid)
            staged.append(pid)
    return staged


def settle_promotion(data: str | Path, refs: list[str], applied: bool) -> None:
    """The user decided an org promotion in the inbox."""
    group = [l for l in (find(data, r) for r in refs) if l is not None]
    for l in group:
        fields = {"promotion": None, **({"promoted_to": "org"} if applied else {})}
        LessonStore(data, l.project).set_fields(l.id, **fields)
    if not applied and group:
        decline(data, group)
