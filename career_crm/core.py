from __future__ import annotations

import csv
import fcntl
import html
import io
import json
import os
import re
import shutil
import stat
import tempfile
import uuid
from contextlib import contextmanager
from dataclasses import dataclass
from datetime import date, datetime, timedelta
from pathlib import Path
from typing import Dict, Iterable, List, Mapping, Optional, Sequence, Tuple
from urllib.parse import urlparse


SCHEMAS: Dict[str, List[str]] = {
    "companies": [
        "company_id", "name", "target_status", "industry", "location",
        "careers_url", "notes", "created_at", "updated_at", "archived",
    ],
    "people": [
        "person_id", "name", "company_id", "company_name", "title", "location",
        "profile_url", "email", "keywords", "connection_points", "status", "last_contact",
        "next_action", "next_action_date", "notes", "source_text", "created_at",
        "updated_at", "archived",
    ],
    "jobs": [
        "job_id", "title", "company_id", "company_name", "location", "source_url",
        "status", "description", "deadline", "salary_range", "fit_score", "fit_reasons",
        "pursuit_decision", "pursuit_decided_at", "next_action", "next_action_date",
        "notes", "source_text", "created_at", "updated_at", "archived",
    ],
    "interactions": [
        "interaction_id", "person_id", "job_id", "type", "date", "notes", "result",
        "created_at",
    ],
    "tasks": [
        "task_id", "type", "due_date", "status", "priority", "person_id", "job_id",
        "title", "note", "created_at", "completed_at",
    ],
}

PERSON_STATUSES = {
    "to_research", "ready_to_reach_out", "reached_out", "replied",
    "chat_scheduled", "relationship_active", "paused", "closed",
}
JOB_STATUSES = {
    "saved", "researching", "ready_to_apply", "applied", "interviewing",
    "offer", "accepted", "rejected", "withdrawn", "closed",
}
TASK_STATUSES = {"open", "done", "dismissed"}
PRIORITIES = {"low", "medium", "high"}
PURSUIT_DECISIONS = {"undecided", "pursue", "maybe", "pass"}
INTERACTION_TYPES = {"outreach", "reply", "coffee_chat", "application", "interview", "note"}
OPERATION_JOURNAL = ".career-operation.json"

TARGET_MD = """---
title: Career Target
tags: [career, strategy, bizops, frontier-ai]
status: active
updated: {today}
---

# Career target

## Statement

Pursue Strategy and Business Operations roles at frontier AI companies in the San Francisco Bay Area or Seattle, using eight years of experience and an advanced degree to help teams make high-impact decisions and scale new products or businesses.

## Candidate profile

- Eight years of professional experience
- Advanced degree holder

## Role areas

- Strategy and Operations
- Business Operations
- Strategic Initiatives
- Product Strategy
- Go-to-Market Strategy and Operations
- Chief of Staff

## Target seniority

- Senior
- Lead
- Manager
- Experienced individual contributor

## Locations

- San Francisco Bay Area
- Seattle metropolitan area

## Keywords

- strategy
- business operations
- bizops
- strategic initiatives
- product strategy
- go-to-market
- GTM
- chief of staff
- frontier AI
- foundation models
- AI agents
- developer platform
- enterprise AI
- commercialization
- scaling
"""

OUTREACH_PROFILE_MD = """---
title: Outreach Profile
tags: [career, networking, outreach]
status: active
updated: {today}
---

# Outreach profile

## Professional background

- Add only facts you are comfortable using in professional outreach.

## Education

- Add schools, degrees, and fields that a recipient could recognize.

## Career story

- Add employers, internships, career changes, and relevant experiences.

## Interests and communities

- Add interests or communities that may create a genuine conversation topic.

## Outreach rules

- Use only real overlap supported by the saved profile and a public source.
- Never infer another person's identity from their name, photo, or appearance.
- Prefer a specific observation or useful question over generic praise.
"""

OUTREACH_PLAYBOOK_MD = """---
title: Outreach Playbook
tags: [career, networking, outreach, workflow]
status: active
updated: {today}
---

# Outreach playbook

## Message stages

### 1. Connection request

- Make the note relatable with one natural, verifiable observation.
- Use a specific compliment about a career choice, project, or professional focus.
- Keep the request small: ask to connect only.
- Do not ask for a chat, referral, role information, resume review, or application help yet.
- Preferred structure: “Hi [Name], I came across your profile and noticed [shared or relevant point]! I found your [journey or work] very interesting. I’d love to connect if possible!”
- Keep shared background natural: “noticed you also went to Columbia Business School” is preferred to “I also have Columbia ties.” Use “also” only when the overlap is verified.

### 2. After acceptance

- Thank the person for accepting.
- Mention what you are learning about or researching.
- Ask whether they would be open to a casual 15–20 minute conversation about their work, team, or career path.
- Ask one answerable, non-generic question connected to their background.

### 3. After a team conversation

- Thank the person and mention one specific thing you learned.
- Explain how the conversation affected your interest.
- Ask whether it would be convenient to send a resume or continue the conversation.

## Personalization rules

- Prefer natural observations such as “noticed you also went to Columbia Business School” over explicit statements such as “I also have Columbia ties.”
- Use a shared school, city, employer, or field as context, then let the observation carry the connection.
- Never infer protected identity traits.
- Avoid generic questions such as “What is your day-to-day like?” Ask about a specific transition, product, market, team, or decision visible in the person's public work.
- Compliments should be concrete and sincere, never exaggerated.

## Workflow checklist

1. Research the person and company using public professional sources.
2. Save two or three grounded connection points and one conversation angle.
3. Draft the connection request and chat message separately.
4. After a chat is scheduled, send a calendar invite and confirmation email.
5. Create a reminder for one day before the chat.
6. After the chat, record one learning and draft the follow-up.

## Confirmation email template

> Hi [Name], thanks again for making time. Confirming our chat for [DATE] at [TIME] [TIME ZONE]. We’ll use [LINK]. I’m looking forward to learning more about [SPECIFIC TOPIC].

## Day-before reminder template

> Hi [Name], looking forward to our chat tomorrow at [TIME] [TIME ZONE]. Here’s the link again: [LINK]. See you then!
"""


def now_iso() -> str:
    return datetime.now().replace(microsecond=0).isoformat()


def today_iso() -> str:
    return date.today().isoformat()


def make_id(prefix: str) -> str:
    return f"{prefix}_{uuid.uuid4().hex[:10]}"


def normalize_bool(value: str) -> str:
    return "true" if str(value).strip().lower() in {"1", "true", "yes", "y"} else "false"


def ensure_data_dir(data_dir: Path, overwrite_target: bool = False) -> None:
    data_dir.mkdir(parents=True, exist_ok=True)
    # A prior process may have stopped between multi-file replacements. Any mutating
    # command recovers the exact pre-operation snapshot before continuing.
    with data_write_lock(data_dir):
        pass
    for name, fields in SCHEMAS.items():
        path = data_dir / f"{name}.csv"
        if not path.exists():
            with path.open("w", newline="", encoding="utf-8") as handle:
                csv.DictWriter(handle, fieldnames=fields).writeheader()
        else:
            with path.open(newline="", encoding="utf-8-sig") as handle:
                reader = csv.DictReader(handle)
                actual = reader.fieldnames or []
                rows = list(reader)
            missing = [field for field in fields if field not in actual]
            extra = [field for field in actual if field not in fields]
            if missing and not extra:
                for row in rows:
                    for field in missing:
                        row[field] = "undecided" if name == "jobs" and field == "pursuit_decision" else ""
                    if name == "jobs" and "salary_range" in missing and not row.get("salary_range"):
                        salary_match = re.search(r"(?:range|rate):\s*(\$[\d,.]+\s*(?:-|–)\s*\$[\d,.]+)", row.get("notes", ""), re.I)
                        if salary_match:
                            row["salary_range"] = salary_match.group(1)
                write_rows(data_dir, name, rows)
    target = data_dir / "target.md"
    if overwrite_target or not target.exists():
        target.write_text(TARGET_MD.format(today=today_iso()), encoding="utf-8")
    outreach_profile = data_dir / "outreach_profile.md"
    if not outreach_profile.exists():
        outreach_profile.write_text(OUTREACH_PROFILE_MD.format(today=today_iso()), encoding="utf-8")
    outreach_playbook = data_dir / "outreach_playbook.md"
    if not outreach_playbook.exists():
        outreach_playbook.write_text(OUTREACH_PLAYBOOK_MD.format(today=today_iso()), encoding="utf-8")


def read_rows(data_dir: Path, name: str) -> List[Dict[str, str]]:
    path = data_dir / f"{name}.csv"
    if not path.exists():
        return []
    with path.open(newline="", encoding="utf-8-sig") as handle:
        return [dict(row) for row in csv.DictReader(handle)]


def backup_file(path: Path) -> Optional[Path]:
    if not path.exists() or path.stat().st_size == 0:
        return None
    backup_dir = path.parent / "backups"
    backup_dir.mkdir(exist_ok=True)
    stamp = datetime.now().strftime("%Y%m%d_%H%M%S_%f")
    destination = backup_dir / f"{path.stem}_{stamp}{path.suffix}"
    shutil.copy2(path, destination)
    return destination


def backup_operation(data_dir: Path, names: Sequence[str], label: str) -> Path:
    stamp = datetime.now().strftime("%Y%m%d_%H%M%S_%f")
    safe_label = re.sub(r"[^A-Za-z0-9_.-]+", "_", label).strip("_") or "operation"
    destination = data_dir / "backups" / f"{stamp}_{safe_label}"
    destination.mkdir(parents=True, exist_ok=False)
    for name in names:
        source = data_dir / f"{name}.csv"
        if not source.exists():
            raise ValueError(f"Cannot back up missing file: {source}")
        backup_path = destination / source.name
        shutil.copy2(source, backup_path)
        with backup_path.open("rb") as handle:
            os.fsync(handle.fileno())
    fsync_directory(destination)
    fsync_directory(destination.parent)
    return destination


def fsync_directory(path: Path) -> None:
    descriptor = os.open(path, os.O_RDONLY)
    try:
        os.fsync(descriptor)
    finally:
        os.close(descriptor)


def operation_journal_path(data_dir: Path) -> Path:
    return data_dir / OPERATION_JOURNAL


def write_operation_journal(data_dir: Path, backup_dir: Path, names: Sequence[str]) -> Path:
    resolved_data = data_dir.resolve()
    resolved_backup = backup_dir.resolve()
    try:
        relative_backup = resolved_backup.relative_to(resolved_data)
    except ValueError as exc:
        raise ValueError("Operation backup must be inside the career data directory.") from exc
    payload = {
        "version": 1,
        "operation": "replace_csv_set",
        "backup_directory": relative_backup.as_posix(),
        "files": [f"{name}.csv" for name in names],
    }
    journal = operation_journal_path(data_dir)
    replace_bytes_atomic(
        journal,
        (json.dumps(payload, indent=2, sort_keys=True) + "\n").encode("utf-8"),
    )
    fsync_directory(data_dir)
    return journal


def clear_operation_journal(data_dir: Path) -> None:
    journal = operation_journal_path(data_dir)
    if journal.exists():
        journal.unlink()
        fsync_directory(data_dir)


def recover_pending_operation(data_dir: Path) -> Optional[Path]:
    journal = operation_journal_path(data_dir)
    if not journal.exists():
        return None
    try:
        payload = json.loads(journal.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError) as exc:
        raise ValueError(f"Cannot read interrupted-operation journal {journal}: {exc}") from exc
    if payload.get("version") != 1 or payload.get("operation") != "replace_csv_set":
        raise ValueError(f"Unsupported interrupted-operation journal: {journal}")

    filenames = payload.get("files")
    if not isinstance(filenames, list) or not filenames:
        raise ValueError(f"Interrupted-operation journal has no files: {journal}")
    allowed = {f"{name}.csv" for name in SCHEMAS}
    if any(not isinstance(name, str) or name not in allowed for name in filenames):
        raise ValueError(f"Interrupted-operation journal lists unsupported files: {journal}")
    if len(set(filenames)) != len(filenames):
        raise ValueError(f"Interrupted-operation journal lists duplicate files: {journal}")

    backup_value = payload.get("backup_directory")
    if not isinstance(backup_value, str) or not backup_value:
        raise ValueError(f"Interrupted-operation journal has no backup directory: {journal}")
    backup_root = (data_dir / "backups").resolve()
    backup_dir = (data_dir / backup_value).resolve()
    try:
        relative_to_root = backup_dir.relative_to(backup_root)
    except ValueError as exc:
        raise ValueError(f"Interrupted-operation backup is outside {backup_root}: {backup_dir}") from exc
    if not relative_to_root.parts:
        raise ValueError(f"Interrupted-operation journal must name a specific backup directory: {journal}")

    originals: Dict[str, bytes] = {}
    for filename in filenames:
        backup_path = backup_dir / filename
        if not backup_path.is_file():
            raise ValueError(f"Interrupted-operation backup is missing: {backup_path}")
        originals[filename] = backup_path.read_bytes()

    staged: Dict[str, Path] = {}
    try:
        for filename in filenames:
            staged[filename] = stage_bytes(data_dir / filename, originals[filename])
        for filename in filenames:
            os.replace(staged[filename], data_dir / filename)
        fsync_directory(data_dir)
        clear_operation_journal(data_dir)
    finally:
        for path in staged.values():
            path.unlink(missing_ok=True)
    return backup_dir


@contextmanager
def data_write_lock(data_dir: Path):
    data_dir.mkdir(parents=True, exist_ok=True)
    lock_path = data_dir / ".career-write.lock"
    with lock_path.open("a+") as handle:
        fcntl.flock(handle.fileno(), fcntl.LOCK_EX)
        try:
            recover_pending_operation(data_dir)
            yield
        finally:
            fcntl.flock(handle.fileno(), fcntl.LOCK_UN)


def rows_to_csv_bytes(name: str, rows: Sequence[Mapping[str, object]]) -> bytes:
    buffer = io.StringIO(newline="")
    writer = csv.DictWriter(buffer, fieldnames=SCHEMAS[name], extrasaction="ignore")
    writer.writeheader()
    for raw in rows:
        writer.writerow({field: raw.get(field, "") for field in SCHEMAS[name]})
    return buffer.getvalue().encode("utf-8")


def stage_bytes(path: Path, raw: bytes) -> Path:
    path.parent.mkdir(parents=True, exist_ok=True)
    descriptor, temporary_name = tempfile.mkstemp(prefix=f".{path.name}.", suffix=".tmp", dir=path.parent)
    temporary = Path(temporary_name)
    try:
        with os.fdopen(descriptor, "wb") as handle:
            handle.write(raw)
            handle.flush()
            os.fsync(handle.fileno())
        mode = stat.S_IMODE(path.stat().st_mode) if path.exists() else 0o644
        temporary.chmod(mode)
        return temporary
    except Exception:
        temporary.unlink(missing_ok=True)
        raise


def replace_bytes_atomic(path: Path, raw: bytes) -> None:
    temporary = stage_bytes(path, raw)
    try:
        os.replace(temporary, path)
    finally:
        temporary.unlink(missing_ok=True)


def _write_rows_unlocked(
    data_dir: Path,
    name: str,
    rows: Sequence[Mapping[str, object]],
    backup: bool = True,
) -> None:
    path = data_dir / f"{name}.csv"
    if backup:
        backup_file(path)
    replace_bytes_atomic(path, rows_to_csv_bytes(name, rows))


def write_rows(data_dir: Path, name: str, rows: Sequence[Mapping[str, object]], backup: bool = True) -> None:
    with data_write_lock(data_dir):
        _write_rows_unlocked(data_dir, name, rows, backup=backup)


def append_row(data_dir: Path, name: str, raw: Mapping[str, object]) -> Dict[str, str]:
    with data_write_lock(data_dir):
        rows = read_rows(data_dir, name)
        row = {field: str(raw.get(field, "") or "") for field in SCHEMAS[name]}
        rows.append(row)
        _write_rows_unlocked(data_dir, name, rows)
        return row


def update_row(data_dir: Path, name: str, id_field: str, record_id: str, changes: Mapping[str, object]) -> Dict[str, str]:
    with data_write_lock(data_dir):
        rows = read_rows(data_dir, name)
        result: Optional[Dict[str, str]] = None
        for row in rows:
            if row.get(id_field) == record_id:
                for key, value in changes.items():
                    if key in SCHEMAS[name]:
                        row[key] = str(value or "")
                if "updated_at" in row:
                    row["updated_at"] = now_iso()
                result = row
                break
        if result is None:
            raise ValueError(f"No {name[:-1]} found with ID {record_id}")
        _write_rows_unlocked(data_dir, name, rows)
        return result


def find_row(rows: Sequence[Mapping[str, str]], id_field: str, record_id: str) -> Optional[Mapping[str, str]]:
    return next((row for row in rows if row.get(id_field) == record_id), None)


def parse_date(value: str) -> Optional[date]:
    if not value:
        return None
    try:
        return date.fromisoformat(value[:10])
    except ValueError:
        return None


def validate(data_dir: Path) -> List[str]:
    errors: List[str] = []
    ids: Dict[str, set] = {}
    required = {
        "companies": ["company_id", "name", "target_status"],
        "people": ["person_id", "name", "status"],
        "jobs": ["job_id", "title", "company_name", "status"],
        "interactions": ["interaction_id", "type", "date"],
        "tasks": ["task_id", "type", "due_date", "status", "title"],
    }
    id_fields = {
        "companies": "company_id", "people": "person_id", "jobs": "job_id",
        "interactions": "interaction_id", "tasks": "task_id",
    }
    for name, expected in SCHEMAS.items():
        path = data_dir / f"{name}.csv"
        if not path.exists():
            errors.append(f"{path}: file is missing; run `career init`.")
            continue
        with path.open(newline="", encoding="utf-8-sig") as handle:
            reader = csv.DictReader(handle)
            actual = reader.fieldnames or []
            missing_columns = [field for field in expected if field not in actual]
            extra_columns = [field for field in actual if field not in expected]
            if missing_columns:
                errors.append(f"{path}: missing columns: {', '.join(missing_columns)}")
            if extra_columns:
                errors.append(
                    f"{path}: unsupported columns: {', '.join(extra_columns)}; "
                    "remove them or add them to the documented schema before running a write command."
                )
            rows = list(reader)
        id_field = id_fields[name]
        ids[name] = {row.get(id_field, "") for row in rows if row.get(id_field)}
        seen = set()
        for line, row in enumerate(rows, start=2):
            for field in required[name]:
                if not row.get(field, "").strip():
                    errors.append(f"{path}:{line}: `{field}` is required.")
            record_id = row.get(id_field, "")
            if record_id and record_id in seen:
                errors.append(f"{path}:{line}: duplicate `{id_field}` value `{record_id}`.")
            seen.add(record_id)
            if name == "people" and row.get("status") not in PERSON_STATUSES:
                errors.append(f"{path}:{line}: unknown person status `{row.get('status')}`.")
            if name == "jobs" and row.get("status") not in JOB_STATUSES:
                errors.append(f"{path}:{line}: unknown job status `{row.get('status')}`.")
            if name == "jobs" and (row.get("pursuit_decision") or "undecided") not in PURSUIT_DECISIONS:
                errors.append(f"{path}:{line}: unknown pursuit decision `{row.get('pursuit_decision')}`.")
            if name == "tasks":
                if row.get("status") not in TASK_STATUSES:
                    errors.append(f"{path}:{line}: unknown task status `{row.get('status')}`.")
                if row.get("priority") and row.get("priority") not in PRIORITIES:
                    errors.append(f"{path}:{line}: unknown priority `{row.get('priority')}`.")
            for field in ("due_date", "deadline", "last_contact", "next_action_date", "date"):
                if field in row and row.get(field) and parse_date(row[field]) is None:
                    errors.append(f"{path}:{line}: `{field}` must use YYYY-MM-DD.")
    people = ids.get("people", set())
    jobs = ids.get("jobs", set())
    for name in ("tasks", "interactions"):
        for line, row in enumerate(read_rows(data_dir, name), start=2):
            if row.get("person_id") and row["person_id"] not in people:
                errors.append(f"{name}.csv:{line}: person `{row['person_id']}` does not exist.")
            if row.get("job_id") and row["job_id"] not in jobs:
                errors.append(f"{name}.csv:{line}: job `{row['job_id']}` does not exist.")
    if not (data_dir / "target.md").exists():
        errors.append(f"{data_dir / 'target.md'}: file is missing; run `career init`.")
    return errors


FIELD_ALIASES = {
    "person": {
        "name": "name", "person": "name", "contact": "name", "title": "title",
        "role": "title", "company": "company_name", "organization": "company_name",
        "location": "location", "linkedin": "profile_url", "profile": "profile_url",
        "url": "profile_url", "email": "email", "keywords": "keywords", "notes": "notes",
    },
    "job": {
        "job": "title", "job title": "title", "title": "title", "role": "title",
        "company": "company_name", "organization": "company_name", "location": "location",
        "url": "source_url", "link": "source_url", "apply": "source_url",
        "description": "description", "deadline": "deadline", "notes": "notes",
    },
}


def split_blocks(text: str) -> List[str]:
    text = text.replace("\r\n", "\n").strip()
    blocks = [block.strip() for block in re.split(r"\n\s*\n|\n(?=(?:[-*]\s+)?(?:Name|Person|Contact|Job|Job title)\s*:)", text, flags=re.I) if block.strip()]
    if len(blocks) == 1:
        bullet_lines = [re.sub(r"^[-*]\s+", "", line).strip() for line in text.splitlines() if line.strip()]
        if len(bullet_lines) > 1 and all(":" not in line for line in bullet_lines):
            return bullet_lines
    return blocks


def classify_block(block: str, requested: str = "auto") -> str:
    if requested in {"person", "job"}:
        return requested
    lower = block.lower()
    job_signals = (
        "job title:", "description:", "apply:", "jobs.lever.co", "greenhouse.io",
        "salary:", "responsibilities", "qualifications", "employment type:",
        "job code:", "apply to this job",
    )
    person_signals = ("name:", "person:", "contact:", "linkedin.com/in/", "email:")
    job_score = sum(signal in lower for signal in job_signals)
    person_score = sum(signal in lower for signal in person_signals)
    return "job" if job_score > person_score else "person"


def looks_like_job_page(text: str) -> bool:
    lower = text.lower()
    signals = (
        "responsibilities", "qualifications", "employment type:", "job code:",
        "apply to this job", "minimum qualifications", "preferred qualifications",
    )
    return sum(signal in lower for signal in signals) >= 4


def value_after_label(text: str, label: str) -> str:
    match = re.search(rf"(?im)^{re.escape(label)}\s*:\s*(?:\n\s*)?([^\n]+)", text)
    return match.group(1).strip() if match else ""


def extract_job_page(text: str) -> Tuple[Dict[str, str], List[str]]:
    record: Dict[str, str] = {"status": "saved", "source_text": text.strip()}
    title_match = re.search(r"(?im)^([^\n]{5,180})\n\s*Location\s*:\s*$", text)
    if title_match:
        record["title"] = title_match.group(1).strip()
    record["location"] = value_after_label(text, "Location")
    employment_type = value_after_label(text, "Employment Type")
    job_code = value_after_label(text, "Job Code")
    company_match = re.search(r"(?im)^About\s+([A-Z][^\n]{1,80})$", text)
    if company_match:
        record["company_name"] = company_match.group(1).strip()
    urls = re.findall(r"https?://[^\s)>\]]+", text)
    job_url = next(
        (url.rstrip(".,'") for url in urls if any(token in url.lower() for token in ("/job", "/career", "jobs.", "careers."))),
        "",
    )
    if job_url:
        record["source_url"] = job_url
    responsibilities_match = re.search(
        r"(?is)\nResponsibilities\s*\n(.*?)(?=\nQualifications\s*\n)", text
    )
    qualifications_match = re.search(
        r"(?is)\nQualifications\s*\n(.*?)(?=\nJob Information\s*\n|\nAbout\s+[A-Z])", text
    )
    description_parts = []
    if responsibilities_match:
        description_parts.append("Responsibilities\n" + responsibilities_match.group(1).strip())
    if qualifications_match:
        description_parts.append("Qualifications\n" + qualifications_match.group(1).strip())
    if description_parts:
        record["description"] = "\n\n".join(description_parts)
    note_parts = []
    if employment_type:
        note_parts.append(f"Employment type: {employment_type}")
    if job_code:
        note_parts.append(f"Job code: {job_code}")
    if "rolling basis" in text.lower():
        note_parts.append("Applications reviewed on a rolling basis; apply early")
    pay_match = re.search(r"(?i)(?:hourly rate range|base salary range|compensation[^\n]*)[^$]{0,160}(\$[\d,.]+\s*-\s*\$[\d,.]+)", text)
    if pay_match:
        salary_range = pay_match.group(1).rstrip(".,")
        pay_label = "Listed annual base range" if "base salary range" in text.lower() or "compensation description (annually)" in text.lower() else "Listed hourly range"
        note_parts.append(f"{pay_label}: {salary_range}")
        record["salary_range"] = salary_range
    if note_parts:
        record["notes"] = " | ".join(note_parts)
    uncertain = [field for field in ("title", "company_name") if not record.get(field)]
    if not record.get("source_url"):
        uncertain.append("source_url")
    return record, uncertain


def extract_labeled(block: str, kind: str) -> Tuple[Dict[str, str], List[str]]:
    aliases = FIELD_ALIASES[kind]
    record: Dict[str, str] = {}
    uncertain: List[str] = []
    leftovers: List[str] = []
    for raw_line in block.splitlines():
        line = re.sub(r"^[-*]\s+", "", raw_line.strip())
        match = re.match(r"^([^:]{2,30}):\s*(.*)$", line)
        if match:
            label = re.sub(r"\s+", " ", match.group(1).strip().lower())
            value = match.group(2).strip()
            field = aliases.get(label)
            if field:
                record[field] = value
            else:
                leftovers.append(line)
        else:
            leftovers.append(line)
    urls = re.findall(r"https?://[^\s)>\]]+", block)
    emails = re.findall(r"[\w.+-]+@[\w.-]+\.[A-Za-z]{2,}", block)
    if kind == "person":
        if emails and not record.get("email"):
            record["email"] = emails[0]
        linkedin = next((url for url in urls if "linkedin.com" in url), None)
        if linkedin and not record.get("profile_url"):
            record["profile_url"] = linkedin.rstrip(".,'")
        if not record.get("name") and leftovers:
            first = leftovers.pop(0)
            at_match = re.match(r"^(.+?)\s+[—–-]?\s*(.+?)\s+at\s+(.+)$", first, re.I)
            if at_match:
                record.update(name=at_match.group(1).strip(), title=at_match.group(2).strip(), company_name=at_match.group(3).strip())
            else:
                record["name"] = re.sub(r"\s*https?://.*$", "", first).strip(" -–—")
                uncertain.extend(["title", "company_name"])
        record.setdefault("status", "to_research")
    else:
        if urls and not record.get("source_url"):
            record["source_url"] = urls[0].rstrip(".,'")
        if not record.get("title") and leftovers:
            first = leftovers.pop(0)
            at_match = re.match(r"^(.+?)\s+(?:at|@|—|–|-|\|)\s+(.+)$", first, re.I)
            if at_match:
                record["title"] = at_match.group(1).strip()
                record["company_name"] = at_match.group(2).strip()
            else:
                record["title"] = first.strip(" -–—")
                uncertain.append("company_name")
        record.setdefault("status", "saved")
    if leftovers:
        note = " | ".join(leftovers)
        record["notes"] = " | ".join(filter(None, [record.get("notes", ""), note]))
    record["source_text"] = block
    required = ["name"] if kind == "person" else ["title", "company_name"]
    uncertain.extend(field for field in required if not record.get(field))
    return record, sorted(set(uncertain))


def detect_duplicates(data_dir: Path, kind: str, record: Mapping[str, str]) -> List[Dict[str, str]]:
    name = "people" if kind == "person" else "jobs"
    rows = read_rows(data_dir, name)
    duplicates = []
    for row in rows:
        if kind == "person":
            same_url = record.get("profile_url") and row.get("profile_url") == record.get("profile_url")
            same_email = record.get("email") and row.get("email", "").lower() == record.get("email", "").lower()
            same_name_company = (
                row.get("name", "").lower() == record.get("name", "").lower()
                and row.get("company_name", "").lower() == record.get("company_name", "").lower()
            )
            if same_url or same_email or same_name_company:
                duplicates.append(row)
        else:
            same_url = record.get("source_url") and row.get("source_url") == record.get("source_url")
            same_title_company = (
                row.get("title", "").lower() == record.get("title", "").lower()
                and row.get("company_name", "").lower() == record.get("company_name", "").lower()
            )
            if same_url or same_title_company:
                duplicates.append(row)
    return duplicates


def preview_import(data_dir: Path, text: str, requested: str = "auto") -> Dict[str, object]:
    items = []
    blocks = [text.strip()] if looks_like_job_page(text) and requested in {"auto", "job"} else split_blocks(text)
    for block in blocks:
        kind = classify_block(block, requested)
        record, uncertain = extract_job_page(block) if kind == "job" and looks_like_job_page(block) else extract_labeled(block, kind)
        duplicates = detect_duplicates(data_dir, kind, record)
        items.append({
            "kind": kind,
            "record": record,
            "uncertain_fields": uncertain,
            "duplicate_ids": [row.get(f"{kind}_id", "") for row in duplicates],
        })
    return {"created_at": now_iso(), "items": items}


def approve_import(data_dir: Path, payload: Mapping[str, object], allow_duplicates: bool = False) -> Tuple[int, List[str]]:
    saved = 0
    skipped: List[str] = []
    for index, raw_item in enumerate(payload.get("items", []), start=1):
        item = dict(raw_item)  # type: ignore[arg-type]
        kind = str(item.get("kind", ""))
        record = dict(item.get("record", {}))
        duplicates = item.get("duplicate_ids", [])
        if duplicates and not allow_duplicates:
            skipped.append(f"item {index}: possible duplicate of {', '.join(duplicates)}")
            continue
        if kind == "person":
            if not record.get("name"):
                skipped.append(f"item {index}: person name is missing")
                continue
            record.update(person_id=make_id("per"), created_at=now_iso(), updated_at=now_iso(), archived="false")
            append_row(data_dir, "people", record)
        elif kind == "job":
            if not record.get("title") or not record.get("company_name"):
                skipped.append(f"item {index}: job title or company is missing")
                continue
            company_id = ensure_company(data_dir, str(record.get("company_name", "")), str(record.get("location", "")))
            record["company_id"] = company_id
            record.update(job_id=make_id("job"), created_at=now_iso(), updated_at=now_iso(), archived="false")
            record.setdefault("pursuit_decision", "undecided")
            append_row(data_dir, "jobs", record)
        else:
            skipped.append(f"item {index}: unknown type `{kind}`")
            continue
        saved += 1
    return saved, skipped


def add_person(data_dir: Path, values: Mapping[str, str]) -> Dict[str, str]:
    if not values.get("name"):
        raise ValueError("Person name is required.")
    status = values.get("status", "to_research")
    if status not in PERSON_STATUSES:
        raise ValueError(f"Unknown person status: {status}")
    row = dict(values)
    if not row.get("company_id") and row.get("company_name"):
        row["company_id"] = ensure_company(data_dir, str(row.get("company_name", "")), str(row.get("location", "")))
    row.update(person_id=make_id("per"), status=status, created_at=now_iso(), updated_at=now_iso(), archived="false")
    return append_row(data_dir, "people", row)


def ensure_company(data_dir: Path, company_name: str, location: str = "") -> str:
    company_name = company_name.strip()
    if not company_name:
        return ""
    companies = read_rows(data_dir, "companies")
    existing = next((row for row in companies if row.get("name", "").strip().lower() == company_name.lower()), None)
    if existing:
        return existing.get("company_id", "")
    row = append_row(data_dir, "companies", {
        "company_id": make_id("com"),
        "name": company_name,
        "target_status": "watch",
        "location": location,
        "notes": "Created automatically from a job import; review target status.",
        "created_at": now_iso(),
        "updated_at": now_iso(),
        "archived": "false",
    })
    return row["company_id"]


def add_job(data_dir: Path, values: Mapping[str, str]) -> Dict[str, str]:
    if not values.get("title") or not values.get("company_name"):
        raise ValueError("Job title and company are required.")
    status = values.get("status", "saved")
    if status not in JOB_STATUSES:
        raise ValueError(f"Unknown job status: {status}")
    row = dict(values)
    if not row.get("company_id"):
        row["company_id"] = ensure_company(data_dir, str(row.get("company_name", "")), str(row.get("location", "")))
    row.update(job_id=make_id("job"), status=status, pursuit_decision=values.get("pursuit_decision", "undecided"), created_at=now_iso(), updated_at=now_iso(), archived="false")
    return append_row(data_dir, "jobs", row)


def set_job_status(data_dir: Path, job_id: str, status: str) -> Dict[str, str]:
    if status not in JOB_STATUSES:
        raise ValueError(f"Job status must be one of: {', '.join(sorted(JOB_STATUSES))}")
    return update_row(data_dir, "jobs", "job_id", job_id, {"status": status})


def set_pursuit_decision(data_dir: Path, job_id: str, decision: str) -> Dict[str, str]:
    if decision not in PURSUIT_DECISIONS:
        raise ValueError(f"Decision must be one of: {', '.join(sorted(PURSUIT_DECISIONS))}")
    return update_row(data_dir, "jobs", "job_id", job_id, {
        "pursuit_decision": decision,
        "pursuit_decided_at": "" if decision == "undecided" else now_iso(),
    })


def set_person_status(data_dir: Path, person_id: str, status: str) -> Dict[str, str]:
    if status not in PERSON_STATUSES:
        raise ValueError(f"Person status must be one of: {', '.join(sorted(PERSON_STATUSES))}")
    people = read_rows(data_dir, "people")
    person = find_row(people, "person_id", person_id)
    if not person:
        raise ValueError(f"No person found with ID {person_id}")
    changes: Dict[str, str] = {"status": status}
    task: Optional[Dict[str, str]] = None
    interactions = read_rows(data_dir, "interactions")
    has_outreach = any(row.get("person_id") == person_id and row.get("type") == "outreach" for row in interactions)
    has_reply = any(row.get("person_id") == person_id and row.get("type") == "reply" for row in interactions)
    if status in {"reached_out", "replied"} and not has_outreach:
        append_row(data_dir, "interactions", {
            "interaction_id": make_id("int"), "person_id": person_id, "job_id": "",
            "type": "outreach", "date": today_iso(),
            "notes": "Reached out; exact message text not recorded.", "result": "",
            "created_at": now_iso(),
        })
    if status == "replied" and not has_reply:
        append_row(data_dir, "interactions", {
            "interaction_id": make_id("int"), "person_id": person_id, "job_id": "",
            "type": "reply", "date": today_iso(),
            "notes": "Reply received; exact message text not recorded.", "result": "",
            "created_at": now_iso(),
        })
    if status == "reached_out" and (person.get("status") != "reached_out" or not person.get("last_contact") or not person.get("next_action_date")):
        contact_date = today_iso()
        due = (date.fromisoformat(contact_date) + timedelta(days=7)).isoformat()
        changes.update({
            "last_contact": contact_date,
            "next_action": "Follow up",
            "next_action_date": due,
        })
        tasks = read_rows(data_dir, "tasks")
        open_follow_up = next(
            (row for row in tasks if row.get("person_id") == person_id and row.get("type") == "follow_up" and row.get("status") == "open"),
            None,
        )
        if not open_follow_up:
            task = add_task(data_dir, {
                "type": "follow_up", "due_date": due, "person_id": person_id,
                "title": f"Follow up with {person.get('name', person_id)}", "priority": "medium",
                "note": "Status was marked reached out; exact sent message is not recorded in the dashboard.",
            })
    return update_row(data_dir, "people", "person_id", person_id, changes)


def add_task(data_dir: Path, values: Mapping[str, str]) -> Dict[str, str]:
    if not values.get("title") or not values.get("due_date"):
        raise ValueError("Task title and due date are required.")
    if parse_date(values["due_date"]) is None:
        raise ValueError("Task due date must use YYYY-MM-DD.")
    row = dict(values)
    row.update(
        task_id=make_id("tsk"), status=values.get("status", "open"),
        priority=values.get("priority", "medium"),
        created_at=values.get("created_at") or now_iso(),
        completed_at="",
    )
    return append_row(data_dir, "tasks", row)


def log_event(
    data_dir: Path,
    event_type: str,
    person_id: str = "",
    job_id: str = "",
    event_date: str = "",
    notes: str = "",
    follow_up_days: int = 7,
) -> Tuple[Dict[str, str], Optional[Dict[str, str]]]:
    if not person_id and not job_id:
        raise ValueError("An event needs a person ID or job ID.")
    people = read_rows(data_dir, "people")
    jobs = read_rows(data_dir, "jobs")
    if person_id and not find_row(people, "person_id", person_id):
        raise ValueError(f"No person found with ID {person_id}")
    if job_id and not find_row(jobs, "job_id", job_id):
        raise ValueError(f"No job found with ID {job_id}")
    if event_type == "coffee_chat" and not person_id:
        raise ValueError("A coffee chat needs a person ID.")
    if event_type == "coffee_chat" and not event_date:
        raise ValueError("Coffee chat date is required and must use YYYY-MM-DD.")
    event_date = event_date or today_iso()
    if re.fullmatch(r"\d{4}-\d{2}-\d{2}", event_date) is None:
        raise ValueError("Event date must use YYYY-MM-DD.")
    parsed_event_date = parse_date(event_date)
    if parsed_event_date is None:
        raise ValueError("Event date must use YYYY-MM-DD.")
    interaction = append_row(data_dir, "interactions", {
        "interaction_id": make_id("int"), "person_id": person_id, "job_id": job_id,
        "type": event_type, "date": event_date, "notes": notes, "result": "",
        "created_at": now_iso(),
    })
    task: Optional[Dict[str, str]] = None
    if event_type == "outreach" and person_id:
        due = (parsed_event_date + timedelta(days=follow_up_days)).isoformat()
        update_row(data_dir, "people", "person_id", person_id, {
            "status": "reached_out", "last_contact": event_date,
            "next_action": "Follow up", "next_action_date": due,
        })
        person = find_row(people, "person_id", person_id) or {}
        task = add_task(data_dir, {
            "type": "follow_up", "due_date": due, "person_id": person_id,
            "title": f"Follow up with {person.get('name', person_id)}", "priority": "medium",
        })
    elif event_type == "reply" and person_id:
        update_row(data_dir, "people", "person_id", person_id, {
            "status": "replied", "last_contact": event_date,
        })
    elif event_type == "coffee_chat" and person_id:
        update_row(data_dir, "people", "person_id", person_id, {
            "status": "chat_scheduled", "next_action": "Prepare for coffee chat",
            "next_action_date": event_date,
        })
        person = find_row(people, "person_id", person_id) or {}
        task = add_task(data_dir, {
            "type": "coffee_chat", "due_date": event_date, "person_id": person_id,
            "title": f"Send calendar invite and confirmation email for {person.get('name', person_id)}", "priority": "high",
            "note": "Confirm the time, meeting link, and agenda before the chat.",
            "created_at": interaction["created_at"],
        })
        reminder_due = (parsed_event_date - timedelta(days=1)).isoformat()
        add_task(data_dir, {
            "type": "chat_reminder", "due_date": reminder_due, "person_id": person_id,
            "title": f"Remind {person.get('name', person_id)} about the coffee chat", "priority": "medium",
            "note": "Send a brief confirmation and any updated meeting details one day before.",
            "created_at": interaction["created_at"],
        })
    elif event_type == "application" and job_id:
        update_row(data_dir, "jobs", "job_id", job_id, {
            "status": "applied", "next_action": "Check application status",
            "next_action_date": (parsed_event_date + timedelta(days=14)).isoformat(),
        })
    elif event_type == "interview" and job_id:
        update_row(data_dir, "jobs", "job_id", job_id, {"status": "interviewing"})
    return interaction, task


def plan_interaction_deletion(
    data_dir: Path,
    interaction_id: str,
    restore_status: str = "",
    restore_next_action: Optional[str] = None,
    restore_next_action_date: Optional[str] = None,
) -> Dict[str, object]:
    interactions = read_rows(data_dir, "interactions")
    matches = [row for row in interactions if row.get("interaction_id") == interaction_id]
    if len(matches) != 1:
        raise ValueError(f"Expected one interaction with ID {interaction_id}; found {len(matches)}.")
    interaction = matches[0]
    if interaction.get("type") != "coffee_chat":
        raise ValueError("Self-service deletion currently supports only coffee_chat interactions.")
    chat_date = parse_date(interaction.get("date", ""))
    if chat_date is None:
        raise ValueError("The coffee chat has an invalid date and cannot be deleted safely.")

    people = read_rows(data_dir, "people")
    person = find_row(people, "person_id", interaction.get("person_id", ""))
    if not person:
        raise ValueError(f"No person found for interaction {interaction_id}.")
    if restore_status and restore_status not in PERSON_STATUSES:
        raise ValueError(f"Restore status must be one of: {', '.join(sorted(PERSON_STATUSES))}")
    if (restore_next_action is not None or restore_next_action_date is not None) and not restore_status:
        raise ValueError("Use --restore-status when supplying restore next-action fields.")
    if restore_next_action_date:
        if re.fullmatch(r"\d{4}-\d{2}-\d{2}", restore_next_action_date) is None or parse_date(restore_next_action_date) is None:
            raise ValueError("Restore next-action date must use YYYY-MM-DD.")

    created_at = interaction.get("created_at", "")
    if not created_at:
        raise ValueError("The coffee chat has no creation timestamp and cannot be matched to generated tasks safely.")

    person_name = person.get("name", "") or interaction.get("person_id", "")
    expected_specs = {
        "coffee_chat": {
            "due_date": chat_date.isoformat(),
            "title": f"Send calendar invite and confirmation email for {person_name}",
            "note": "Confirm the time, meeting link, and agenda before the chat.",
        },
        "chat_reminder": {
            "due_date": (chat_date - timedelta(days=1)).isoformat(),
            "title": f"Remind {person_name} about the coffee chat",
            "note": "Send a brief confirmation and any updated meeting details one day before.",
        },
    }
    tasks = read_rows(data_dir, "tasks")
    selected_tasks: List[Dict[str, str]] = []
    warnings: List[str] = []
    blockers: List[str] = []

    for task_type, spec in expected_specs.items():
        exact = [
            row for row in tasks
            if row.get("person_id") == interaction.get("person_id")
            and row.get("type") == task_type
            and row.get("due_date") == spec["due_date"]
            and row.get("created_at") == created_at
            and row.get("title") == spec["title"]
            and row.get("note") == spec["note"]
        ]
        if len(exact) == 1:
            selected_tasks.append(exact[0])
        elif not exact:
            blockers.append(
                f"No exact generated {task_type} task matches this interaction; deletion is blocked."
            )
        else:
            blockers.append(
                f"Multiple exact generated {task_type} tasks match this interaction; deletion is blocked."
            )

    selected_by_id = {row.get("task_id", ""): row for row in selected_tasks}
    selected_tasks = [selected_by_id[key] for key in sorted(selected_by_id)]

    remaining_chats = [
        row for row in interactions
        if row.get("interaction_id") != interaction_id
        and row.get("person_id") == interaction.get("person_id")
        and row.get("type") == "coffee_chat"
        and parse_date(row.get("date", "")) is not None
    ]
    remaining_chats.sort(key=lambda row: (parse_date(row.get("date", "")), row.get("created_at", "")))
    upcoming = [row for row in remaining_chats if parse_date(row.get("date", "")) >= date.today()]

    person_changes: Dict[str, str] = {}
    deletion_owns_person_schedule = (
        person.get("status") == "chat_scheduled"
        and person.get("next_action") == "Prepare for coffee chat"
        and parse_date(person.get("next_action_date", "")) == chat_date
    )
    if deletion_owns_person_schedule:
        if upcoming:
            person_changes = {
                "status": "chat_scheduled",
                "next_action": "Prepare for coffee chat",
                "next_action_date": upcoming[0]["date"][:10],
            }
            if restore_status:
                blockers.append(
                    "Restore fields are allowed only when deleting the person's current and last upcoming chat."
                )
        elif restore_status:
            person_changes = {
                "status": restore_status,
                "next_action": restore_next_action or "",
                "next_action_date": restore_next_action_date or "",
            }
        else:
            blockers.append(
                "This is the person's current and last upcoming coffee chat. "
                "Supply --restore-status and, if needed, --restore-next-action/--restore-next-action-date."
            )
    elif restore_status:
        blockers.append("Restore fields are allowed only when deleting the person's current and last upcoming chat.")

    return {
        "interaction": {
            "interaction_id": interaction.get("interaction_id", ""),
            "person_id": interaction.get("person_id", ""),
            "person_name": person.get("name", ""),
            "type": interaction.get("type", ""),
            "date": chat_date.isoformat(),
            "notes": interaction.get("notes", ""),
            "created_at": interaction.get("created_at", ""),
        },
        "tasks": [
            {
                "task_id": row.get("task_id", ""),
                "type": row.get("type", ""),
                "due_date": row.get("due_date", ""),
                "status": row.get("status", ""),
            }
            for row in selected_tasks
        ],
        "person_changes": person_changes,
        "remaining_coffee_chats": [
            {"interaction_id": row.get("interaction_id", ""), "date": row.get("date", "")[:10]}
            for row in remaining_chats
        ],
        "warnings": warnings,
        "blockers": blockers,
        "deleted": False,
    }


def delete_interaction(
    data_dir: Path,
    interaction_id: str,
    confirm: bool = False,
    restore_status: str = "",
    restore_next_action: Optional[str] = None,
    restore_next_action_date: Optional[str] = None,
) -> Dict[str, object]:
    plan_args = {
        "restore_status": restore_status,
        "restore_next_action": restore_next_action,
        "restore_next_action_date": restore_next_action_date,
    }
    if not confirm:
        return plan_interaction_deletion(data_dir, interaction_id, **plan_args)

    with data_write_lock(data_dir):
        # Recompute under the writer lock so confirmation never relies on a stale preview.
        plan = plan_interaction_deletion(data_dir, interaction_id, **plan_args)
        blockers = list(plan.get("blockers", []))
        if blockers:
            raise ValueError("Deletion blocked:\n- " + "\n- ".join(str(item) for item in blockers))
        existing_errors = validate(data_dir)
        if existing_errors:
            raise ValueError("Fix current data errors before deleting:\n- " + "\n- ".join(existing_errors))

        interaction_rows = [
            row for row in read_rows(data_dir, "interactions")
            if row.get("interaction_id") != interaction_id
        ]
        task_ids_to_delete = {str(row.get("task_id", "")) for row in plan.get("tasks", [])}
        if len(task_ids_to_delete) != 2:
            raise ValueError("Deletion requires exactly two generated tasks; no files were changed.")
        task_rows = [
            row for row in read_rows(data_dir, "tasks")
            if row.get("task_id") not in task_ids_to_delete
        ]

        person_changes = dict(plan.get("person_changes", {}))
        person_rows = read_rows(data_dir, "people")
        if person_changes:
            person_id = str(plan["interaction"]["person_id"])
            changed = False
            for row in person_rows:
                if row.get("person_id") == person_id:
                    row.update({key: str(value or "") for key, value in person_changes.items()})
                    row["updated_at"] = now_iso()
                    changed = True
                    break
            if not changed:
                raise ValueError("Person data changed before confirmation; no files were changed.")

        changed_tables = ["interactions", "tasks"]
        if person_changes:
            changed_tables.append("people")
        rows_by_table = {
            "interactions": interaction_rows,
            "tasks": task_rows,
            "people": person_rows,
        }
        original_bytes = {
            name: (data_dir / f"{name}.csv").read_bytes()
            for name in changed_tables
        }
        replacement_bytes = {
            name: rows_to_csv_bytes(name, rows_by_table[name])
            for name in changed_tables
        }
        backup_dir = backup_operation(data_dir, changed_tables, f"delete_{interaction_id}")
        staged: Dict[str, Path] = {}
        journal_written = False
        try:
            write_operation_journal(data_dir, backup_dir, changed_tables)
            journal_written = True
            for name in changed_tables:
                staged[name] = stage_bytes(data_dir / f"{name}.csv", replacement_bytes[name])
            for name in changed_tables:
                os.replace(staged[name], data_dir / f"{name}.csv")
            errors = validate(data_dir)
            if errors:
                raise ValueError("Deletion produced invalid data:\n- " + "\n- ".join(errors))
            fsync_directory(data_dir)
            clear_operation_journal(data_dir)
        except Exception as exc:
            rollback_errors: List[str] = []
            for name in changed_tables:
                try:
                    replace_bytes_atomic(data_dir / f"{name}.csv", original_bytes[name])
                except Exception as rollback_exc:  # pragma: no cover - catastrophic filesystem failure
                    rollback_errors.append(f"{name}.csv: {rollback_exc}")
            if rollback_errors:
                raise RuntimeError(
                    f"Deletion failed ({exc}) and rollback was incomplete: " + "; ".join(rollback_errors)
                ) from exc
            fsync_directory(data_dir)
            if journal_written:
                clear_operation_journal(data_dir)
            raise
        finally:
            for path in staged.values():
                path.unlink(missing_ok=True)

        result = dict(plan)
        result["deleted"] = True
        result["backup_directory"] = str(backup_dir)
        return result


def complete_task(data_dir: Path, task_id: str, dismissed: bool = False) -> Dict[str, str]:
    return update_row(data_dir, "tasks", "task_id", task_id, {
        "status": "dismissed" if dismissed else "done",
        "completed_at": now_iso() if not dismissed else "",
    })


def target_summary(target_path: Path) -> Dict[str, object]:
    text = target_path.read_text(encoding="utf-8") if target_path.exists() else ""
    sections: Dict[str, List[str]] = {}
    current = ""
    for line in text.splitlines():
        if line.startswith("## "):
            current = line[3:].strip().lower()
            sections[current] = []
        elif current and line.strip() and not line.startswith("---"):
            sections[current].append(line.strip())
    statement = " ".join(line for line in sections.get("statement", []) if not line.startswith("- "))
    def bullets(key: str) -> List[str]:
        return [line[2:].strip() for line in sections.get(key, []) if line.startswith("- ")]
    return {
        "statement": statement,
        "roles": bullets("role areas"),
        "seniority": bullets("target seniority"),
        "locations": bullets("locations"),
        "keywords": bullets("keywords"),
    }


def metrics(data_dir: Path) -> Dict[str, object]:
    interactions = read_rows(data_dir, "interactions")
    jobs = read_rows(data_dir, "jobs")
    tasks = read_rows(data_dir, "tasks")
    outreach = sum(row.get("type") == "outreach" for row in interactions)
    replies = sum(row.get("type") == "reply" for row in interactions)
    chats = sum(row.get("type") == "coffee_chat" for row in interactions)
    applied = sum(row.get("status") in {"applied", "interviewing", "offer", "accepted", "rejected"} for row in jobs)
    interviews = sum(row.get("status") in {"interviewing", "offer", "accepted"} for row in jobs)
    due_tasks = [row for row in tasks if row.get("status") == "open" and parse_date(row.get("due_date", "")) and parse_date(row["due_date"]) <= date.today()]
    done_tasks = [row for row in tasks if row.get("status") == "done"]
    return {
        "outreach": outreach,
        "replies": replies,
        "reply_rate": round(replies / outreach * 100) if outreach else 0,
        "chats": chats,
        "applications": applied,
        "interviews": interviews,
        "interview_rate": round(interviews / applied * 100) if applied else 0,
        "due_tasks": len(due_tasks),
        "done_tasks": len(done_tasks),
    }


def seed_demo(data_dir: Path) -> None:
    ensure_data_dir(data_dir, overwrite_target=True)
    for name in SCHEMAS:
        write_rows(data_dir, name, [], backup=False)
    stamp = now_iso()
    companies = [
        {"company_id": "com_northstar", "name": "Northstar AI", "target_status": "priority", "industry": "Foundation models", "location": "San Francisco, CA", "careers_url": "https://example.com/northstar", "notes": "Synthetic demo company", "created_at": stamp, "updated_at": stamp, "archived": "false"},
        {"company_id": "com_cascade", "name": "Cascade Intelligence", "target_status": "priority", "industry": "AI agents", "location": "Seattle, WA", "careers_url": "https://example.com/cascade", "notes": "Synthetic demo company", "created_at": stamp, "updated_at": stamp, "archived": "false"},
        {"company_id": "com_lantern", "name": "Lantern Compute", "target_status": "watch", "industry": "AI infrastructure", "location": "Palo Alto, CA", "careers_url": "https://example.com/lantern", "notes": "Synthetic demo company", "created_at": stamp, "updated_at": stamp, "archived": "false"},
    ]
    write_rows(data_dir, "companies", companies, backup=False)
    people = [
        {"person_id": "per_maya", "name": "Maya Chen", "company_id": "com_northstar", "company_name": "Northstar AI", "title": "Director, Strategy", "location": "San Francisco, CA", "profile_url": "https://example.com/maya", "email": "", "keywords": "strategy;foundation models", "status": "reached_out", "last_contact": (date.today() - timedelta(days=6)).isoformat(), "next_action": "Follow up", "next_action_date": (date.today() + timedelta(days=1)).isoformat(), "notes": "Met at an AI policy event", "source_text": "Synthetic demo record", "created_at": stamp, "updated_at": stamp, "archived": "false"},
        {"person_id": "per_jordan", "name": "Jordan Lee", "company_id": "com_cascade", "company_name": "Cascade Intelligence", "title": "BizOps Lead", "location": "Seattle, WA", "profile_url": "https://example.com/jordan", "email": "", "keywords": "bizops;agents", "status": "chat_scheduled", "last_contact": (date.today() - timedelta(days=2)).isoformat(), "next_action": "Prepare for coffee chat", "next_action_date": (date.today() + timedelta(days=2)).isoformat(), "notes": "Ask about launch operations", "source_text": "Synthetic demo record", "created_at": stamp, "updated_at": stamp, "archived": "false"},
        {"person_id": "per_samira", "name": "Samira Patel", "company_id": "com_lantern", "company_name": "Lantern Compute", "title": "Product Strategy Manager", "location": "Palo Alto, CA", "profile_url": "https://example.com/samira", "email": "", "keywords": "product strategy;infrastructure", "status": "ready_to_reach_out", "last_contact": "", "next_action": "Send introduction", "next_action_date": today_iso(), "notes": "Alumni connection", "source_text": "Synthetic demo record", "created_at": stamp, "updated_at": stamp, "archived": "false"},
    ]
    write_rows(data_dir, "people", people, backup=False)
    jobs = [
        {"job_id": "job_strategy", "title": "Senior Strategy & Operations Lead", "company_id": "com_northstar", "company_name": "Northstar AI", "location": "San Francisco, CA", "source_url": "https://example.com/job/strategy", "status": "ready_to_apply", "description": "Lead cross-functional strategy for new model products.", "deadline": (date.today() + timedelta(days=7)).isoformat(), "fit_score": "92", "fit_reasons": "Function;location;seniority;frontier AI", "next_action": "Tailor resume", "next_action_date": today_iso(), "notes": "Strong match", "source_text": "Synthetic demo record", "created_at": stamp, "updated_at": stamp, "archived": "false"},
        {"job_id": "job_bizops", "title": "Business Operations Manager", "company_id": "com_cascade", "company_name": "Cascade Intelligence", "location": "Seattle, WA", "source_url": "https://example.com/job/bizops", "status": "applied", "description": "Build operating systems for an enterprise AI agent team.", "deadline": "", "fit_score": "88", "fit_reasons": "Function;location;AI agents", "next_action": "Check application status", "next_action_date": (date.today() + timedelta(days=5)).isoformat(), "notes": "Applied with referral", "source_text": "Synthetic demo record", "created_at": stamp, "updated_at": stamp, "archived": "false"},
        {"job_id": "job_gtm", "title": "GTM Strategy Lead", "company_id": "com_lantern", "company_name": "Lantern Compute", "location": "Palo Alto, CA", "source_url": "https://example.com/job/gtm", "status": "researching", "description": "Shape go-to-market choices for AI infrastructure products.", "deadline": "", "fit_score": "76", "fit_reasons": "Location;strategy;AI infrastructure", "next_action": "Find hiring manager", "next_action_date": (date.today() + timedelta(days=3)).isoformat(), "notes": "Check scope", "source_text": "Synthetic demo record", "created_at": stamp, "updated_at": stamp, "archived": "false"},
    ]
    write_rows(data_dir, "jobs", jobs, backup=False)
    interactions = [
        {"interaction_id": "int_outreach", "person_id": "per_maya", "job_id": "job_strategy", "type": "outreach", "date": (date.today() - timedelta(days=6)).isoformat(), "notes": "Asked about the strategy team", "result": "", "created_at": stamp},
        {"interaction_id": "int_reply", "person_id": "per_jordan", "job_id": "job_bizops", "type": "reply", "date": (date.today() - timedelta(days=2)).isoformat(), "notes": "Agreed to chat", "result": "coffee chat scheduled", "created_at": stamp},
        {"interaction_id": "int_coffee_chat", "person_id": "per_jordan", "job_id": "job_bizops", "type": "coffee_chat", "date": (date.today() + timedelta(days=2)).isoformat(), "notes": "10:00 AM PT · Video call", "result": "", "created_at": stamp},
        {"interaction_id": "int_application", "person_id": "per_jordan", "job_id": "job_bizops", "type": "application", "date": (date.today() - timedelta(days=1)).isoformat(), "notes": "Submitted with referral", "result": "", "created_at": stamp},
    ]
    write_rows(data_dir, "interactions", interactions, backup=False)
    tasks = [
        {"task_id": "tsk_resume", "type": "application", "due_date": today_iso(), "status": "open", "priority": "high", "person_id": "", "job_id": "job_strategy", "title": "Tailor resume for Northstar AI", "note": "Focus on scaling and cross-functional decisions", "created_at": stamp, "completed_at": ""},
        {"task_id": "tsk_intro", "type": "outreach", "due_date": today_iso(), "status": "open", "priority": "high", "person_id": "per_samira", "job_id": "job_gtm", "title": "Send introduction to Samira", "note": "Mention shared alumni group", "created_at": stamp, "completed_at": ""},
        {"task_id": "tsk_followup", "type": "follow_up", "due_date": (date.today() + timedelta(days=1)).isoformat(), "status": "open", "priority": "medium", "person_id": "per_maya", "job_id": "job_strategy", "title": "Follow up with Maya", "note": "Ask one focused question", "created_at": stamp, "completed_at": ""},
        {"task_id": "tsk_chat", "type": "coffee_chat", "due_date": (date.today() + timedelta(days=2)).isoformat(), "status": "open", "priority": "high", "person_id": "per_jordan", "job_id": "job_bizops", "title": "Coffee chat with Jordan", "note": "Prepare three questions", "created_at": stamp, "completed_at": ""},
    ]
    write_rows(data_dir, "tasks", tasks, backup=False)


def esc(value: object) -> str:
    return html.escape(str(value or ""), quote=True)


def generate_dashboard(data_dir: Path, output: Path, demo: bool = False) -> Path:
    errors = validate(data_dir)
    if errors:
        raise ValueError("Cannot build dashboard:\n" + "\n".join(f"- {error}" for error in errors))
    target = target_summary(data_dir / "target.md")
    people = [row for row in read_rows(data_dir, "people") if normalize_bool(row.get("archived", "false")) == "false"]
    jobs = [row for row in read_rows(data_dir, "jobs") if normalize_bool(row.get("archived", "false")) == "false"]
    tasks = read_rows(data_dir, "tasks")
    interactions = read_rows(data_dir, "interactions")
    m = metrics(data_dir)
    person_names = {row["person_id"]: row["name"] for row in people}
    job_names = {row["job_id"]: f"{row['title']} · {row['company_name']}" for row in jobs}
    today = date.today()
    open_tasks = [row for row in tasks if row.get("status") == "open"]
    open_tasks.sort(key=lambda row: (row.get("due_date", "9999"), {"high": 0, "medium": 1, "low": 2}.get(row.get("priority", ""), 3)))
    reports_dir = data_dir / "agent_outputs"
    reports = sorted(reports_dir.glob("*.md"), key=lambda path: path.stat().st_mtime, reverse=True)[:8] if reports_dir.exists() else []

    def task_group(predicate) -> str:
        selected = [row for row in open_tasks if predicate(parse_date(row.get("due_date", "")))]
        if not selected:
            return '<div class="empty"><strong>Nothing waiting on you.</strong><span>Your attention is clear for now.</span></div>'
        cards = []
        for row in selected:
            context = person_names.get(row.get("person_id", "")) or job_names.get(row.get("job_id", "")) or "General"
            due = parse_date(row.get("due_date", ""))
            if due is None:
                due_label, urgency = "No due date", "later"
            elif due < today:
                days = (today - due).days
                due_label, urgency = f"{days} day{'s' if days != 1 else ''} overdue", "overdue"
            elif due == today:
                due_label, urgency = "Due today", "today"
            else:
                days = (due - today).days
                due_label, urgency = f"In {days} day{'s' if days != 1 else ''}", "upcoming"
            target_view = "people" if row.get("person_id") else ("jobs" if row.get("job_id") else "today")
            record_query = person_names.get(row.get("person_id", "")) or (
                job_names.get(row.get("job_id", ""), "").split(" · ")[0]
            )
            cards.append(f'''<article class="task-card" data-search="{esc((row.get('title','') + ' ' + context).lower())}">
              <div class="task-marker urgency-{urgency}" aria-hidden="true"></div><div class="task-copy"><div class="task-topline"><span class="eyebrow">{esc(row.get('type','').replace('_',' '))}</span><span class="due-label urgency-{urgency}">{esc(due_label)}</span></div>
              <h3>{esc(row.get('title'))}</h3><p>{esc(context)}</p><p class="task-note">{esc(row.get('note'))}</p></div>
              <button class="task-record-link" type="button" data-open-view="{target_view}" data-query="{esc(record_query)}">Open record <span aria-hidden="true">→</span></button>
            </article>''')
        return "".join(cards)

    def report_cards() -> str:
        if not reports:
            return '<div class="empty">No agent reports yet. Run an agent command, then rebuild the dashboard.</div>'
        cards = []
        for path in reports:
            text = path.read_text(encoding="utf-8", errors="replace")
            title_match = re.search(r"^title:\s*[\"']?(.*?)[\"']?\s*$", text, re.M)
            title = title_match.group(1) if title_match else path.stem.replace("_", " ")
            updated_match = re.search(r"^updated:\s*(.*?)\s*$", text, re.M)
            updated = updated_match.group(1) if updated_match else datetime.fromtimestamp(path.stat().st_mtime).date().isoformat()
            cards.append(
                f'<article class="agent-report"><div class="eyebrow">Human review required · {esc(updated)}</div>'
                f'<h3>{esc(title)}</h3><p class="muted">{esc(path.name)}</p></article>'
            )
        return "".join(cards)

    metric_cards = "".join(
        f'<article class="metric"><span>{esc(label)}</span><strong data-metric="{esc(key)}">{esc(value)}</strong><small>{esc(note)}</small></article>'
        for key, label, value, note in [
            ("outreach", "Outreach", m["outreach"], "messages logged"),
            ("reply_rate", "Reply rate", f'{m["reply_rate"]}%', "replies ÷ outreach"),
            ("chats", "Coffee chats", m["chats"], "scheduled or held"),
            ("applications", "Applications", m["applications"], "submitted"),
            ("interview_rate", "Interview rate", f'{m["interview_rate"]}%', "interviews ÷ applications"),
        ]
    )
    def connection_point_list(row: Mapping[str, str]) -> str:
        points = [point.strip() for point in re.split(r"[;|\n]+", row.get("connection_points", "")) if point.strip()]
        if not points:
            return '<p class="muted">No connection points saved yet.</p>'
        return "<ul>" + "".join(f"<li>{esc(point)}</li>" for point in points) + "</ul>"

    def sent_messages_for(person_id: str) -> str:
        sent = [row for row in interactions if row.get("person_id") == person_id and row.get("type") == "outreach" and row.get("notes")]
        sent.sort(key=lambda row: (row.get("date", ""), row.get("created_at", "")), reverse=True)
        if not sent:
            return '<p class="muted">No sent message yet.</p>'
        return "".join(
            f'''<article class="person-message"><small>{esc(row.get('date'))} · {esc(job_names.get(row.get('job_id', ''), 'No linked job'))}</small><p>{esc(row.get('notes'))}</p></article>'''
            for row in sent
        )

    def draft_messages_for(person_id: str) -> str:
        drafts = [
            row for row in interactions
            if row.get("person_id") == person_id
            and row.get("type") == "note"
            and row.get("result") == "message_draft"
            and row.get("notes")
        ]
        drafts.sort(key=lambda row: (row.get("date", ""), row.get("created_at", "")), reverse=True)
        if not drafts:
            return '<p class="muted">No draft message yet.</p>'
        return "".join(
            f'''<article class="person-message"><small>Draft · {esc(row.get('date'))}</small><p>{esc(row.get('notes'))}</p></article>'''
            for row in drafts
        )

    def display_date(value: str) -> str:
        parsed = parse_date(value)
        return f"{parsed.strftime('%b')} {parsed.day}, {parsed.year}" if parsed else value

    def coffee_chats_for(person_id: str) -> List[Dict[str, str]]:
        chats = []
        for row in interactions:
            parsed = parse_date(row.get("date", ""))
            if row.get("person_id") != person_id or row.get("type") != "coffee_chat" or parsed is None:
                continue
            normalized = dict(row)
            normalized["date"] = parsed.isoformat()
            chats.append(normalized)
        chats.sort(key=lambda row: (row.get("date", ""), row.get("created_at", "")))
        return chats

    def primary_coffee_chat(chats: List[Dict[str, str]]) -> Optional[Dict[str, str]]:
        upcoming = [row for row in chats if date.fromisoformat(row["date"]) >= today]
        return upcoming[0] if upcoming else (chats[-1] if chats else None)

    def coffee_chat_cell(row: Mapping[str, str], chats: List[Dict[str, str]]) -> str:
        primary = primary_coffee_chat(chats)
        if primary is None:
            empty_label = "Date not logged" if row.get("status") == "chat_scheduled" else "Not scheduled"
            return f'<span class="muted">{empty_label}</span>'
        chat_date = primary["date"]
        parsed = date.fromisoformat(chat_date)
        timing = "Today" if parsed == today else ("Upcoming" if parsed > today else "Most recent")
        more = f" · +{len(chats) - 1} more" if len(chats) > 1 else ""
        notes = f'<small>{esc(primary.get("notes"))}</small>' if primary.get("notes") else ""
        return f'<time datetime="{esc(chat_date)}">{esc(display_date(chat_date))}</time><small>{timing}{more}</small>{notes}'

    def coffee_chat_history(row: Mapping[str, str], chats: List[Dict[str, str]]) -> str:
        if not chats:
            message = (
                "Chat status is scheduled, but no date is logged."
                if row.get("status") == "chat_scheduled"
                else "No coffee chat scheduled."
            )
            return f'<p class="muted">{message}</p>'
        items = []
        upcoming = [chat for chat in chats if date.fromisoformat(chat["date"]) >= today]
        past = [chat for chat in chats if date.fromisoformat(chat["date"]) < today]
        for chat in upcoming + list(reversed(past)):
            chat_date = chat["date"]
            parsed = date.fromisoformat(chat_date)
            timing = "Today" if parsed == today else ("Upcoming" if parsed > today else "Past")
            notes = f' · {esc(chat.get("notes"))}' if chat.get("notes") else ""
            items.append(
                f'<li><time datetime="{esc(chat_date)}">{esc(display_date(chat_date))}</time>'
                f'<small>{timing}{notes}</small></li>'
            )
        return '<ul class="coffee-chat-list">' + "".join(items) + "</ul>"

    person_status_labels = {
        "to_research": "To research", "ready_to_reach_out": "Ready to reach out",
        "reached_out": "Reached out", "replied": "Replied", "chat_scheduled": "Chat scheduled",
        "relationship_active": "Relationship active", "paused": "Paused", "closed": "Closed",
    }
    def person_rows() -> str:
        def person_status_select(row: Mapping[str, str]) -> str:
            current = row.get("status") or "to_research"
            options = "".join(
                f'<option value="{value}"{" selected" if value == current else ""}>{label}</option>'
                for value, label in person_status_labels.items()
            )
            quick_action = ""
            if current in {"to_research", "ready_to_reach_out"}:
                quick_action = f'<button class="mark-reached-out" type="button" data-person-id="{esc(row["person_id"])}">Mark reached out</button>'
            return f'<div class="person-status-control"><select class="person-status-select status-{esc(current)}" data-person-id="{esc(row["person_id"])}" data-previous="{esc(current)}" aria-label="Status for {esc(row["name"])}">{options}</select>{quick_action}</div>'
        rows = []
        for row in people:
            detail_id = f"person-detail-{row['person_id']}"
            chats = coffee_chats_for(row["person_id"])
            primary_chat = primary_coffee_chat(chats)
            chat_search = " ".join(
                value
                for chat in chats
                for value in (chat.get("date", ""), display_date(chat.get("date", "")), chat.get("notes", ""))
                if value
            )
            search_text = (" ".join(row.values()) + " " + chat_search).lower()
            chat_date = primary_chat["date"] if primary_chat else ""
            if primary_chat is None:
                chat_sort = "2"
            elif date.fromisoformat(chat_date) >= today:
                chat_sort = f"0-{chat_date}"
            else:
                reverse_ordinal = 9_999_999 - date.fromisoformat(chat_date).toordinal()
                chat_sort = f"1-{reverse_ordinal:07d}"
            profile_link = f'<a href="{esc(row.get("profile_url"))}" target="_blank" rel="noreferrer">Open public profile</a>' if row.get("profile_url") else ""
            rows.append(f'''<tr class="person-main-row" data-search="{esc(search_text)}" data-detail-id="{esc(detail_id)}" data-name="{esc(row['name'])}" data-company="{esc(row['company_name'])}" data-status="{esc(row.get('status') or 'to_research')}" data-coffee-chat="{esc(chat_date)}" data-coffee-chat-sort="{esc(chat_sort)}" data-next-action="{esc(row.get('next_action_date') or '9999-12-31')}"><td><button class="person-toggle" type="button" data-target="{esc(detail_id)}" aria-expanded="false"><span>{esc(row['name'])}</span><span class="toggle-icon" aria-hidden="true">⌄</span></button><small>{esc(row['person_id'])}</small></td><td>{esc(row['title'])}</td><td>{esc(row['company_name'])}</td><td>{esc(row.get('location') or 'Not saved')}</td><td>{person_status_select(row)}</td><td class="coffee-chat-cell">{coffee_chat_cell(row, chats)}</td><td>{esc(row['next_action'])}<small>{esc(row['next_action_date'])}</small></td></tr>''')
            rows.append(f'''<tr id="{esc(detail_id)}" class="person-detail-row" hidden><td colspan="7"><div class="person-detail-grid"><section><div class="eyebrow">Connection points</div>{connection_point_list(row)}{f'<p class="profile-link">{profile_link}</p>' if profile_link else ''}</section><section><div class="eyebrow">Coffee chat schedule</div>{coffee_chat_history(row, chats)}</section><section><div class="eyebrow">Draft messages</div>{draft_messages_for(row['person_id'])}<div class="eyebrow message-section-label">Actual messages sent</div>{sent_messages_for(row['person_id'])}</section></div></td></tr>''')
        return "".join(rows)

    people_rows = person_rows()
    person_status_filter_options = '<option value="">All statuses</option>' + "".join(
        f'<option value="{value}">{label}</option>' for value, label in person_status_labels.items()
    )
    def decision_select(row: Mapping[str, str]) -> str:
        current = row.get("pursuit_decision") or "undecided"
        labels = {"undecided": "Undecided", "pursue": "Pursue", "maybe": "Maybe", "pass": "Pass"}
        options = "".join(
            f'<option value="{value}"{" selected" if value == current else ""}>{label}</option>'
            for value, label in labels.items()
        )
        return f'<select class="decision-select decision-{esc(current)}" data-job-id="{esc(row["job_id"])}" aria-label="Pursuit decision for {esc(row["title"])}">{options}</select><small>{esc(row.get("pursuit_decided_at", ""))}</small>'

    job_status_order = [
        "saved", "researching", "ready_to_apply", "applied", "interviewing",
        "offer", "accepted", "rejected", "withdrawn", "closed",
    ]
    job_status_labels = {value: value.replace("_", " ").title() for value in job_status_order}
    job_status_filter_options = '<option value="">All statuses</option>' + "".join(
        f'<option value="{value}">{label}</option>' for value, label in job_status_labels.items()
    )
    def job_status_select(row: Mapping[str, str]) -> str:
        current = row.get("status") or "saved"
        options = "".join(
            f'<option value="{value}"{" selected" if value == current else ""}>{label}</option>'
            for value, label in job_status_labels.items()
        )
        return f'<select class="job-status-select status-{esc(current)}" data-job-id="{esc(row["job_id"])}" data-previous="{esc(current)}" aria-label="Status for {esc(row["title"])}">{options}</select>'

    def job_fit_details(row: Mapping[str, str]) -> str:
        score = f'{esc(row.get("fit_score"))}%' if row.get("fit_score") else "Not scored"
        reasons = [reason.strip() for reason in row.get("fit_reasons", "").split(";") if reason.strip()]
        body = (
            '<ul>' + "".join(f'<li>{esc(reason)}</li>' for reason in reasons) + '</ul>'
            if reasons
            else '<p class="muted">No fit details saved.</p>'
        )
        return f'<details class="job-fit-details"><summary>{score}</summary><div class="job-fit-body">{body}</div></details>'

    jobs_rows = "".join(f'''<tr data-search="{esc(' '.join(row.values()).lower())}" data-title="{esc(row['title'])}" data-company="{esc(row['company_name'])}" data-status="{esc(row.get('status') or 'saved')}" data-fit="{esc(row.get('fit_score') or '0')}" data-next-action="{esc(row.get('next_action_date') or '9999-12-31')}"><td><strong>{esc(row['title'])}</strong><small>{esc(row['job_id'])}</small></td><td>{esc(row['company_name'])}</td><td>{esc(row['location'])}</td><td>{esc(row.get('salary_range') or 'Not listed')}</td><td>{job_status_select(row)}</td><td>{job_fit_details(row)}</td><td>{decision_select(row)}</td><td>{esc(row['next_action'])}<small>{esc(row['next_action_date'])}</small></td></tr>''' for row in jobs)
    message_interactions = [
        row for row in interactions
        if (
            row.get("type") in {"outreach", "reply"}
            or (row.get("type") == "note" and row.get("result") == "message_draft")
        ) and (row.get("notes") or row.get("result"))
    ]
    message_interactions.sort(key=lambda row: (row.get("date", ""), row.get("created_at", "")), reverse=True)
    message_cards = "".join(
        f'''<article class="message-card" data-search="{esc(' '.join(row.values()).lower())}"><div class="message-meta"><div><span class="pill">{'Sent' if row.get('type') == 'outreach' else ('Reply' if row.get('type') == 'reply' else 'Draft')}</span><strong>{esc(person_names.get(row.get('person_id', ''), 'Unknown person'))}</strong></div><time>{esc(row.get('date'))}</time></div><p class="message-text">{esc(row.get('notes') if row.get('result') == 'message_draft' else (row.get('result') or row.get('notes')))}</p><small>{esc(job_names.get(row.get('job_id', ''), 'No linked job'))} · {esc(row.get('interaction_id'))}</small></article>'''
        for row in message_interactions
    )
    companies = [row for row in read_rows(data_dir, "companies") if normalize_bool(row.get("archived", "false")) == "false"]
    person_options = '<option value="">Select a person</option>' + "".join(
        f'<option value="{esc(row["person_id"])}">{esc(row["name"])} · {esc(row["company_name"])}</option>' for row in people
    )
    job_options = '<option value="">Select a job</option>' + "".join(
        f'<option value="{esc(row["job_id"])}">{esc(row["title"])} · {esc(row["company_name"])}</option>' for row in jobs
    )
    company_options = '<option value="">Select a company</option>' + "".join(
        f'<option value="{esc(row["company_id"])}">{esc(row["name"])}</option>' for row in companies
    )
    roles = " · ".join(target.get("roles", []))
    locations = " · ".join(target.get("locations", []))
    demo_badge = '<span class="demo-badge">Synthetic demo data</span>' if demo else '<span class="private-badge">Private local data</span>'
    demo_note = (
        '<div class="demo-note"><strong>You are viewing the public demo.</strong> '
        'The names and companies are fictional. Filters, sorting, and record details work here; changes reset when the page reloads.</div>'
        if demo else ""
    )
    overdue_count = sum(1 for row in open_tasks if (parse_date(row.get("due_date", "")) or date.max) < today)
    today_count = sum(1 for row in open_tasks if parse_date(row.get("due_date", "")) == today)
    upcoming_count = sum(
        1 for row in open_tasks
        if (due := parse_date(row.get("due_date", ""))) is not None and today < due <= today + timedelta(days=14)
    )
    active_job_stages = ["saved", "researching", "ready_to_apply", "applied", "interviewing", "offer"]
    pipeline_counts = {stage: sum(row.get("status") == stage for row in jobs) for stage in active_job_stages}
    pipeline_labels = {
        "saved": "Saved", "researching": "Researching", "ready_to_apply": "Ready",
        "applied": "Applied", "interviewing": "Interviewing", "offer": "Offer",
    }
    pipeline_html = "".join(
        f'''<button class="pipeline-step" type="button" data-pipeline-status="{stage}">
          <span class="pipeline-count">{pipeline_counts[stage]}</span><span>{pipeline_labels[stage]}</span>
        </button>'''
        for stage in active_job_stages
    )
    upcoming_chats = []
    for interaction in interactions:
        chat_date = parse_date(interaction.get("date", ""))
        if interaction.get("type") == "coffee_chat" and chat_date is not None and chat_date >= today:
            upcoming_chats.append((chat_date, interaction))
    upcoming_chats.sort(key=lambda pair: pair[0])
    if upcoming_chats:
        chat_date, chat = upcoming_chats[0]
        chat_person = person_names.get(chat.get("person_id", ""), "Someone in your network")
        chat_detail = chat.get("notes") or "Details not added yet"
        next_chat_html = f'''<article class="next-chat-card">
          <div class="calendar-tile"><span>{chat_date.strftime('%b')}</span><strong>{chat_date.day}</strong></div>
          <div><div class="eyebrow">Next conversation</div><h3>{esc(chat_person)}</h3><p>{esc(chat_detail)}</p></div>
          <button type="button" data-open-view="people" data-query="{esc(chat_person)}">View contact <span aria-hidden="true">→</span></button>
        </article>'''
    else:
        next_chat_html = '<div class="empty compact"><strong>No conversations scheduled.</strong><span>Use People to choose the next relationship to build.</span></div>'
    build_time = datetime.now().strftime("%B %d, %Y at %I:%M %p")
    briefing_day = datetime.now().strftime("%A")
    output.parent.mkdir(parents=True, exist_ok=True)
    page = f'''<!doctype html>
<html lang="en"><head><meta charset="utf-8"><meta name="viewport" content="width=device-width,initial-scale=1"><meta name="description" content="A private, local-first CRM for a thoughtful job search.">
<title>Career Connection Manager</title>
<style>
:root{{--ink:#19231e;--muted:#65736b;--paper:#f7f6f1;--card:#fff;--line:#dde2dc;--green:#174d3a;--mint:#d9eee3;--lime:#d8ef82;--orange:#f2a65a;--shadow:0 14px 40px rgba(21,48,37,.08)}}
*{{box-sizing:border-box}}body{{margin:0;background:var(--paper);color:var(--ink);font:15px/1.5 Inter,ui-sans-serif,system-ui,-apple-system,sans-serif}}button,input,select,textarea{{font:inherit}}.shell{{display:grid;grid-template-columns:230px 1fr;min-height:100vh}}aside{{background:#123d30;color:#fff;padding:30px 20px;position:sticky;top:0;height:100vh}}.brand{{font-size:18px;font-weight:800;line-height:1.2;margin-bottom:44px}}.brand i{{display:inline-block;width:12px;height:12px;background:var(--lime);border-radius:50%;margin-right:8px}}nav button{{display:block;width:100%;border:0;background:transparent;color:#c7dbd3;text-align:left;padding:11px 12px;margin:4px 0;border-radius:9px;cursor:pointer}}nav button.active,nav button:hover{{background:rgba(255,255,255,.11);color:#fff}}.aside-foot{{position:absolute;bottom:24px;left:20px;right:20px;color:#a8c1b7;font-size:12px}}main{{padding:34px 4vw 70px;max-width:1440px;width:100%}}header{{display:flex;justify-content:space-between;gap:20px;align-items:flex-start;margin-bottom:24px}}h1{{font-family:Georgia,serif;font-size:36px;margin:0 0 6px;letter-spacing:-.8px}}h2{{font:700 21px Georgia,serif;margin:0}}h3{{font-size:15px;margin:2px 0 3px}}p{{margin:0}}.muted,small{{color:var(--muted)}}small{{display:block;margin-top:3px}}.demo-badge,.private-badge{{display:inline-block;padding:6px 10px;border-radius:99px;font-size:12px;font-weight:700}}.demo-badge{{background:#fff0d5;color:#7d4a11}}.private-badge{{background:var(--mint);color:var(--green)}}.target{{background:var(--green);color:#fff;border-radius:18px;padding:27px 30px;box-shadow:var(--shadow);position:relative;overflow:hidden}}.target:after{{content:'';position:absolute;width:180px;height:180px;border-radius:50%;background:var(--lime);right:-80px;top:-90px;opacity:.9}}.target .eyebrow{{color:#b9d8cb}}.target p{{font:21px/1.45 Georgia,serif;max-width:850px;margin:7px 0 16px}}.target-meta{{font-size:12px;color:#cce0d8}}.metrics{{display:grid;grid-template-columns:repeat(5,1fr);gap:12px;margin:20px 0}}.metric{{background:var(--card);border:1px solid var(--line);border-radius:14px;padding:17px}}.metric span,.metric small{{font-size:11px;color:var(--muted)}}.metric strong{{display:block;font:28px Georgia,serif;margin:5px 0}}.panel{{background:var(--card);border:1px solid var(--line);border-radius:16px;padding:22px;box-shadow:0 4px 20px rgba(21,48,37,.04);margin-top:16px}}.panel-head{{display:flex;align-items:center;justify-content:space-between;margin-bottom:14px}}.count{{background:var(--mint);color:var(--green);border-radius:99px;padding:3px 9px;font-size:12px}}.task-card{{display:grid;grid-template-columns:22px 1fr;gap:12px;padding:13px 0;border-top:1px solid var(--line)}}.task-card:first-child{{border-top:0}}.task-check{{width:18px;height:18px;border:2px solid #92a49b;border-radius:5px;margin-top:3px}}.eyebrow{{font-size:10px;letter-spacing:.09em;text-transform:uppercase;color:var(--muted);font-weight:800}}.empty{{border:1px dashed #bdc8c2;border-radius:10px;padding:24px;text-align:center;color:var(--muted)}}.grid-2,.agent-grid,.form-grid{{display:grid;grid-template-columns:1fr 1fr;gap:16px}}.view{{display:none}}.view.active{{display:block}}.toolbar{{display:flex;gap:10px;margin:14px 0}}.search{{width:min(420px,100%);padding:10px 13px;border:1px solid var(--line);border-radius:10px;background:#fff}}table{{width:100%;border-collapse:collapse}}th{{font-size:10px;letter-spacing:.08em;text-transform:uppercase;color:var(--muted);text-align:left;padding:10px}}td{{padding:13px 10px;border-top:1px solid var(--line);vertical-align:top}}td strong{{display:block}}.pill{{display:inline-block;background:#edf3ef;color:#345a49;border-radius:99px;padding:3px 8px;font-size:11px;white-space:nowrap}}.table-wrap{{overflow:auto}}.decision-select,.job-status-select{{border:1px solid var(--line);border-radius:8px;padding:6px 8px;font-weight:700;background:#fff}}.job-status-select{{max-width:150px}}.job-fit-details{{min-width:120px}}.job-fit-details summary{{color:var(--green);font-weight:800;cursor:pointer;white-space:nowrap}}.job-fit-body{{margin-top:8px;padding:9px 10px;background:#f6faf7;border:1px solid var(--line);border-radius:8px;min-width:180px}}.job-fit-body ul{{margin:0;padding-left:18px}}.job-fit-body li{{margin:3px 0}}.decision-pursue{{background:#dff3e7;color:#155b37}}.decision-maybe{{background:#fff2d6;color:#7a4b0a}}.decision-pass{{background:#f5e2e2;color:#7f2929}}.message-card{{background:#fff;border:1px solid var(--line);border-radius:14px;padding:18px;margin-top:12px}}.message-meta{{display:flex;justify-content:space-between;gap:14px;align-items:flex-start}}.message-meta strong{{display:inline;margin-left:8px}}.message-meta time{{color:var(--muted);font-size:12px}}.message-text{{white-space:pre-wrap;font-size:16px;line-height:1.6;margin:14px 0}}.agent-card{{background:var(--card);border:1px solid var(--line);border-radius:16px;padding:20px;margin-top:16px}}.agent-card code{{display:block;background:#edf3ef;border-radius:8px;padding:9px 10px;margin-top:13px;white-space:nowrap;overflow:auto;font-size:11px}}.agent-report{{padding:13px 0;border-top:1px solid var(--line)}}.agent-report:first-child{{border-top:0}}.safety-note{{background:#fff4df;border-left:4px solid var(--orange);padding:13px 15px;border-radius:8px;margin-top:18px}}.agent-form label{{display:block;font-size:12px;font-weight:700;margin:10px 0 4px}}.agent-form select,.agent-form input,.agent-form textarea{{width:100%;border:1px solid var(--line);border-radius:8px;padding:9px;background:#fff}}.agent-form textarea{{min-height:130px;resize:vertical}}.agent-actions{{display:flex;gap:10px;margin-top:14px}}.primary,.secondary{{border:0;border-radius:9px;padding:10px 15px;font-weight:700;cursor:pointer}}.primary{{background:var(--green);color:#fff}}.secondary{{background:var(--mint);color:var(--green)}}.agent-result{{display:none;white-space:pre-wrap;background:#16251e;color:#edf7f1;padding:18px;border-radius:10px;max-height:520px;overflow:auto;margin-top:16px}}.agent-result.show{{display:block}}.footer{{font-size:12px;color:var(--muted);margin-top:30px}}@media(max-width:900px){{.shell{{display:block}}aside{{height:auto;position:relative;padding:16px}}.brand{{margin:0 0 12px}}nav{{display:flex;overflow:auto}}nav button{{white-space:nowrap}}.aside-foot{{display:none}}main{{padding:24px 16px}}.metrics{{grid-template-columns:repeat(2,1fr)}}.grid-2,.agent-grid,.form-grid{{grid-template-columns:1fr}}header{{display:block}}header span{{margin-top:10px}}}}@media(max-width:520px){{.metrics{{grid-template-columns:1fr 1fr}}h1{{font-size:29px}}.target{{padding:22px}}.target p{{font-size:18px}}}}
.person-toggle{{display:flex;align-items:center;gap:7px;border:0;background:transparent;color:var(--green);font-weight:800;padding:0;cursor:pointer;text-align:left}}.person-toggle:hover span:first-child,.person-toggle:focus-visible span:first-child{{text-decoration:underline}}.toggle-icon{{transition:transform .18s ease}}.person-toggle[aria-expanded="true"] .toggle-icon{{transform:rotate(180deg)}}.person-detail-row[hidden]{{display:none}}.person-detail-row td{{background:#f6faf7;padding:18px 20px 22px}}.person-detail-grid{{display:grid;grid-template-columns:minmax(0,1fr) minmax(0,1.4fr);gap:28px}}.person-detail-grid ul{{margin:10px 0 0;padding-left:20px}}.person-detail-grid li{{margin:6px 0}}.person-message{{background:#fff;border:1px solid var(--line);border-radius:10px;padding:12px;margin-top:9px}}.person-message p{{margin-top:7px;white-space:pre-wrap}}.message-section-label{{margin-top:20px}}.profile-link{{margin-top:12px}}.profile-link a{{color:var(--green);font-weight:700}}.person-status-control{{display:flex;flex-direction:column;align-items:flex-start;gap:6px;min-width:142px}}.person-status-select{{border:1px solid var(--line);border-radius:8px;padding:6px 8px;font-weight:700;max-width:100%}}.mark-reached-out{{border:0;border-radius:7px;padding:5px 8px;background:var(--mint);color:var(--green);font-size:11px;font-weight:800;cursor:pointer;white-space:nowrap}}.mark-reached-out:hover{{background:#c5e7d6}}.mark-reached-out:disabled{{opacity:.6;cursor:wait}}.status-to_research{{background:#edf1f3;color:#4d5a61;border-color:#cbd5da}}.status-ready_to_reach_out{{background:#fff2d6;color:#7a4b0a;border-color:#efd597}}.status-reached_out{{background:#dff3e7;color:#155b37;border-color:#a8d9b9}}.status-replied{{background:#dfefff;color:#174d7d;border-color:#a9cbea}}.status-chat_scheduled{{background:#e9e1ff;color:#573b91;border-color:#c9b8ed}}.status-relationship_active{{background:#d9eee3;color:#174d3a;border-color:#9fcbb0}}.status-paused{{background:#f7e8d8;color:#875321;border-color:#e6c6a0}}.status-closed,.status-rejected,.status-withdrawn{{background:#f5e2e2;color:#7f2929;border-color:#e1b7b7}}.status-saved{{background:#edf1f3;color:#4d5a61}}.status-researching{{background:#e7effa;color:#285b8c}}.status-ready_to_apply{{background:#fff2d6;color:#7a4b0a}}.status-applied{{background:#dff3e7;color:#155b37}}.status-interviewing{{background:#e9e1ff;color:#573b91}}.status-offer,.status-accepted{{background:#d9eee3;color:#174d3a}}.pill.status-to_research,.pill.status-ready_to_reach_out,.pill.status-reached_out,.pill.status-replied,.pill.status-chat_scheduled,.pill.status-relationship_active,.pill.status-paused,.pill.status-closed,.pill.status-rejected,.pill.status-withdrawn,.pill.status-saved,.pill.status-researching,.pill.status-ready_to_apply,.pill.status-applied,.pill.status-interviewing,.pill.status-offer,.pill.status-accepted{{border:1px solid currentColor}}@media(max-width:900px){{.person-detail-grid{{grid-template-columns:1fr}}}}
    .toolbar{{flex-wrap:wrap}}.coffee-chat-cell{{min-width:150px}}.coffee-chat-cell time,.coffee-chat-list time{{font-weight:700}}.coffee-chat-list{{margin-top:10px}}.person-detail-grid{{grid-template-columns:minmax(0,1fr) minmax(180px,.75fr) minmax(0,1.4fr)}}@media(max-width:900px){{.person-detail-grid{{grid-template-columns:1fr}}}}
/* Product polish */
:root{{--ink:#20201d;--muted:#716f68;--paper:#f4f1e9;--card:#fffdf8;--line:#e2ded3;--green:#153f35;--green-2:#24594b;--mint:#dceadf;--lime:#d7e88b;--rust:#bd583b;--orange:#e49a53;--shadow:0 18px 50px rgba(41,39,29,.07);--serif:Iowan Old Style,Palatino Linotype,Book Antiqua,Georgia,serif}}
body{{background:var(--paper);font-size:14px;line-height:1.55}}.shell{{grid-template-columns:248px minmax(0,1fr)}}aside{{background:linear-gradient(165deg,#153f35 0%,#102f29 75%);padding:30px 22px}}.brand{{display:flex;align-items:flex-start;gap:10px;font-family:var(--serif);font-size:19px;letter-spacing:-.2px;margin-bottom:46px}}.brand i{{flex:0 0 auto;margin:5px 0 0;width:11px;height:11px;box-shadow:0 0 0 5px rgba(215,232,139,.12)}}nav button{{display:flex;align-items:center;gap:10px;padding:11px 12px;margin:5px 0;font-weight:650;transition:background .16s ease,transform .16s ease}}nav button:hover{{transform:translateX(2px)}}.nav-icon{{width:18px;text-align:center;color:#a9c7bb}}.nav-total{{margin-left:auto;min-width:22px;padding:1px 6px;border-radius:99px;background:rgba(255,255,255,.1);font-size:11px;text-align:center}}.privacy-dot{{display:inline-block;width:7px;height:7px;border-radius:50%;background:var(--lime);margin-right:6px}}.aside-foot span:last-child{{opacity:.72}}main{{padding:42px clamp(24px,4vw,64px) 72px;max-width:1560px}}header{{align-items:center;margin-bottom:25px}}header h1{{font:500 clamp(34px,4vw,52px)/1.06 var(--serif);letter-spacing:-1.7px;max-width:760px}}header p{{font-size:15px;max-width:680px}}.page-kicker{{color:var(--green-2);margin-bottom:7px}}.demo-badge,.private-badge{{border:1px solid currentColor;padding:7px 11px;background:transparent}}.demo-note{{display:flex;gap:6px;align-items:center;background:#fff8e7;border:1px solid #ead9af;border-radius:11px;padding:10px 13px;color:#6f5427;margin:-8px 0 20px;font-size:12px}}.target{{display:flex;justify-content:space-between;align-items:end;gap:30px;background:linear-gradient(125deg,#17483b,#0f352c);border-radius:22px;padding:28px 31px}}.target:after{{width:240px;height:240px;right:-135px;top:-150px;opacity:.8}}.target-copy{{position:relative;z-index:1}}.target p{{font:500 20px/1.45 var(--serif);max-width:780px;margin:7px 0 18px}}.target-meta{{display:flex;flex-wrap:wrap;gap:7px}}.target-meta span{{padding:5px 9px;border:1px solid rgba(255,255,255,.2);border-radius:99px;color:#d8e8e1;font-size:11px}}.target-stats{{position:relative;z-index:1;display:flex;gap:12px;min-width:235px}}.target-stats div{{flex:1;border-left:1px solid rgba(255,255,255,.22);padding-left:16px}}.target-stats strong,.target-stats span{{display:block}}.target-stats strong{{font:31px var(--serif)}}.target-stats span{{font-size:11px;color:#c6d9d1;white-space:nowrap}}.today-layout{{display:grid;grid-template-columns:minmax(0,1.35fr) minmax(320px,.8fr);gap:18px;align-items:start}}.panel{{border-radius:17px;padding:21px 22px;box-shadow:0 6px 28px rgba(41,39,29,.035);margin-top:18px}}.panel h2{{font:600 22px var(--serif);letter-spacing:-.3px}}.panel-head{{align-items:flex-start;margin-bottom:10px}}.text-button{{border:0;background:transparent;color:var(--green-2);font-size:12px;font-weight:800;cursor:pointer;padding:5px}}.text-button:hover{{text-decoration:underline}}.queue-legend{{display:flex;gap:8px;font-size:11px;color:var(--muted)}}.queue-legend span{{padding:4px 8px;border-radius:99px;background:#f1efe8}}.queue-legend .legend-overdue{{background:#f7e4df;color:#923b27}}.task-card{{position:relative;grid-template-columns:6px minmax(0,1fr) auto;gap:14px;align-items:center;padding:16px 0}}.task-marker{{width:5px;height:40px;border-radius:99px;background:#a9b4ae}}.task-marker.urgency-overdue{{background:var(--rust)}}.task-marker.urgency-today{{background:var(--orange)}}.task-marker.urgency-upcoming{{background:#7fa395}}.task-topline{{display:flex;gap:8px;align-items:center;margin-bottom:2px}}.due-label{{display:inline-block;border-radius:99px;padding:2px 7px;background:#eef1ed;color:#55675f;font-size:10px;font-weight:750}}.due-label.urgency-overdue{{background:#f7e4df;color:#923b27}}.due-label.urgency-today{{background:#fff0d9;color:#8b5319}}.task-copy h3{{font-size:15px;margin:2px 0 1px}}.task-copy>p{{color:#51514d;font-size:12px}}.task-copy .task-note{{color:var(--muted);font-size:11px;margin-top:3px}}.task-record-link,.next-chat-card button{{border:0;background:transparent;color:var(--green-2);font-size:11px;font-weight:800;cursor:pointer;white-space:nowrap;padding:8px}}.task-record-link:hover,.next-chat-card button:hover{{text-decoration:underline}}.pipeline{{display:grid;grid-template-columns:repeat(3,1fr);gap:8px}}.pipeline-step{{border:1px solid var(--line);border-radius:11px;background:#faf8f2;color:var(--muted);padding:10px 7px;text-align:left;cursor:pointer;transition:border-color .15s ease,background .15s ease,transform .15s ease}}.pipeline-step:hover{{border-color:#91ad9f;background:#f2f7f3;transform:translateY(-1px)}}.pipeline-step span{{display:block;font-size:10px}}.pipeline-step .pipeline-count{{font:23px var(--serif);color:var(--ink);margin-bottom:2px}}.next-chat-card{{display:grid;grid-template-columns:50px 1fr auto;gap:13px;align-items:center;padding-top:4px}}.calendar-tile{{border:1px solid var(--line);border-radius:10px;overflow:hidden;text-align:center}}.calendar-tile span{{display:block;background:var(--green);color:#fff;font-size:9px;text-transform:uppercase;letter-spacing:.08em;padding:3px}}.calendar-tile strong{{display:block;font:23px var(--serif);padding:3px}}.next-chat-card h3{{font-size:15px}}.next-chat-card p{{font-size:11px;color:var(--muted)}}.signal-row{{display:grid;grid-template-columns:repeat(3,1fr);gap:8px;padding-top:5px}}.signal-row div{{border-left:1px solid var(--line);padding-left:12px}}.signal-row div:first-child{{border-left:0;padding-left:0}}.signal-row strong,.signal-row span{{display:block}}.signal-row strong{{font:25px var(--serif)}}.signal-row span{{font-size:10px;color:var(--muted)}}.empty strong,.empty span{{display:block}}.empty span{{font-size:12px;margin-top:3px}}.empty.compact{{padding:18px}}.view>h1{{font:500 42px/1.1 var(--serif);letter-spacing:-1.2px}}.toolbar{{position:sticky;top:0;z-index:4;background:rgba(244,241,233,.92);backdrop-filter:blur(8px);padding:12px 0;margin:7px 0 0}}.toolbar input,.toolbar select{{min-height:42px;border:1px solid var(--line);border-radius:10px;background:var(--card);padding:9px 12px;color:var(--ink)}}.table-wrap{{padding:5px 12px 12px;margin-top:0}}thead{{position:sticky;top:65px;background:var(--card);z-index:2}}tbody tr[data-search]{{transition:background .15s ease}}tbody tr[data-search]:hover{{background:#faf8f2}}th{{padding:13px 10px}}td{{padding:15px 10px}}.message-card{{box-shadow:0 5px 20px rgba(41,39,29,.025)}}.toast{{position:fixed;right:24px;bottom:24px;z-index:20;max-width:340px;padding:12px 15px;border-radius:10px;background:#183e34;color:#fff;box-shadow:var(--shadow);opacity:0;transform:translateY(12px);pointer-events:none;transition:.2s ease}}.toast.show{{opacity:1;transform:translateY(0)}}button:focus-visible,input:focus-visible,select:focus-visible,textarea:focus-visible,summary:focus-visible{{outline:3px solid rgba(54,113,91,.28);outline-offset:2px}}
@media(max-width:1100px){{.today-layout{{grid-template-columns:1fr}}.target-stats{{min-width:210px}}}}
@media(max-width:900px){{.shell{{display:block}}aside{{position:sticky;top:0;z-index:10;padding:12px 16px;height:auto;box-shadow:0 6px 20px rgba(0,0,0,.12)}}.brand{{display:none}}nav{{gap:3px}}nav button{{justify-content:center;margin:0;padding:9px 10px}}.nav-icon,.nav-total{{display:none}}main{{padding:28px 16px 60px}}header h1{{font-size:38px}}.target{{align-items:start}}.target-stats{{display:none}}.toolbar{{top:50px}}thead{{top:115px}}}}
@media(max-width:600px){{header{{display:block}}header h1{{font-size:34px}}header>span{{margin-top:12px}}.demo-note{{align-items:flex-start;display:block}}.target{{padding:23px 21px}}.target p{{font-size:18px}}.target-meta span{{white-space:normal}}.today-layout{{display:block}}.task-card{{grid-template-columns:5px minmax(0,1fr)}}.task-record-link{{grid-column:2;text-align:left;padding:3px 0}}.next-chat-card{{grid-template-columns:46px 1fr}}.next-chat-card button{{grid-column:2;text-align:left;padding:2px 0}}.pipeline{{grid-template-columns:repeat(2,1fr)}}.view>h1{{font-size:34px}}nav button{{font-size:12px}}.metrics{{grid-template-columns:1fr 1fr}}}}
</style></head><body data-demo="{str(demo).lower()}"><div id="toast" class="toast" role="status" aria-live="polite"></div><div class="shell"><aside><div class="brand"><i></i><span>Career Connection<br>Manager</span></div><nav aria-label="Main navigation"><button class="active" data-view="today"><span class="nav-icon">⌂</span>Today</button><button data-view="people"><span class="nav-icon">○</span>People <span class="nav-total">{len(people)}</span></button><button data-view="jobs"><span class="nav-icon">◇</span>Jobs <span class="nav-total">{len(jobs)}</span></button><button data-view="messages"><span class="nav-icon">↗</span>Messages</button><button data-view="agents"><span class="nav-icon">✦</span>Agent Center</button><button data-view="metrics"><span class="nav-icon">⌁</span>Progress</button></nav><div class="aside-foot"><span class="privacy-dot"></span>Local-first · Human-approved<br><span>Built {esc(build_time)}</span></div></aside><main>
<header><div><div class="eyebrow page-kicker">{briefing_day} briefing</div><h1>Here’s what needs your attention.</h1><p class="muted">A calm view of the relationships, roles, and follow-ups moving your search forward.</p></div>{demo_badge}</header>{demo_note}
<section id="today" class="view active"><article class="target"><div class="target-copy"><div class="eyebrow">Active search</div><p>{esc(target.get('statement'))}</p><div class="target-meta"><span>{esc(roles)}</span><span>{esc(locations)}</span></div></div><div class="target-stats"><div><strong>{overdue_count + today_count}</strong><span>need attention</span></div><div><strong>{upcoming_count}</strong><span>coming up</span></div></div></article><div class="today-layout"><div><section class="panel action-panel"><div class="panel-head"><div><div class="eyebrow">Your queue</div><h2>Do these next</h2></div><div class="queue-legend"><span class="legend-overdue">{overdue_count} overdue</span><span>{today_count} today</span></div></div>{task_group(lambda due: due is not None and due <= today)}</section><section class="panel"><div class="panel-head"><div><div class="eyebrow">After that</div><h2>Coming up</h2></div><span class="count">14 days</span></div>{task_group(lambda due: due is not None and today < due <= today + timedelta(days=14))}</section></div><div><section class="panel pipeline-panel"><div class="panel-head"><div><div class="eyebrow">Roles in motion</div><h2>Job pipeline</h2></div><button class="text-button" type="button" data-open-view="jobs">See all</button></div><div class="pipeline">{pipeline_html}</div></section><section class="panel"><div class="panel-head"><div><div class="eyebrow">On the calendar</div><h2>Upcoming chat</h2></div></div>{next_chat_html}</section><section class="panel signal-panel"><div class="panel-head"><div><div class="eyebrow">Search pulse</div><h2>Progress</h2></div><button class="text-button" type="button" data-open-view="metrics">Details</button></div><div class="signal-row"><div><strong data-metric="outreach">{m['outreach']}</strong><span>outreach</span></div><div><strong>{m['reply_rate']}%</strong><span>reply rate</span></div><div><strong data-metric="applications">{m['applications']}</strong><span>applications</span></div></div></section></div></div></section>
<section id="people" class="view"><h1>People</h1><p class="muted">Relationships connected to your search.</p><div class="toolbar"><input class="search" data-table="people-table" placeholder="Search name, company, title, location, chat date…"><select class="table-filter" data-table="people-table" data-field="status" aria-label="Filter people by status">{person_status_filter_options}</select><select class="table-sort" data-table="people-table" aria-label="Sort people"><option value="name">Sort: name</option><option value="company">Sort: company</option><option value="status">Sort: status</option><option value="coffeeChatSort">Sort: coffee chat</option><option value="nextAction">Sort: next action</option></select></div><section class="panel table-wrap"><table id="people-table"><thead><tr><th>Person</th><th>Role</th><th>Company</th><th>Location</th><th>Status</th><th>Coffee chat</th><th>Next action</th></tr></thead><tbody>{people_rows or '<tr><td colspan="7" class="empty">No people yet. Run career person add.</td></tr>'}</tbody></table></section></section>
<section id="jobs" class="view"><h1>Jobs</h1><p class="muted">Roles moving through your pipeline.</p><div class="toolbar"><input class="search" data-table="jobs-table" placeholder="Search role, company, location, decision…"><select class="table-filter" data-table="jobs-table" data-field="status" aria-label="Filter jobs by status">{job_status_filter_options}</select><select class="table-sort" data-table="jobs-table" aria-label="Sort jobs"><option value="title">Sort: title</option><option value="company">Sort: company</option><option value="status">Sort: status</option><option value="fit">Sort: highest fit</option><option value="nextAction">Sort: next action</option></select></div><section class="panel table-wrap"><table id="jobs-table"><thead><tr><th>Role</th><th>Company</th><th>Location</th><th>Salary</th><th>Status</th><th>Fit</th><th>Pursue?</th><th>Next action</th></tr></thead><tbody>{jobs_rows or '<tr><td colspan="8" class="empty">No jobs yet. Run career job add.</td></tr>'}</tbody></table></section></section>
<section id="messages" class="view"><h1>Messages</h1><p class="muted">Drafts, sent outreach, and replies—clearly labeled and kept separate.</p><div class="toolbar"><input class="search" data-table="messages-list" placeholder="Search person, job, or message…"></div><section id="messages-list">{message_cards or '<div class="empty">No messages yet. Add a draft or log outreach to see it here.</div>'}</section></section>
<section id="agents" class="view"><h1>Agent Center</h1><p class="muted">Run five assistants and review their work before using it.</p><section class="panel"><div class="panel-head"><h2>Run an agent</h2><span class="count">local only</span></div><form id="agent-form" class="agent-form"><div class="form-grid"><div><label for="agent-action">Agent</label><select id="agent-action"><option value="recruiter-outreach">Recruiter outreach researcher</option><option value="message">Message writer</option><option value="resume">JD and resume reviewer</option><option value="person-research">Person researcher</option><option value="company-research">Company and team researcher</option></select></div><div><label for="agent-channel">Message channel</label><select id="agent-channel"><option value="linkedin">LinkedIn</option><option value="email">Email</option></select></div><div><label for="agent-stage">Message stage</label><select id="agent-stage"><option value="auto">Auto</option><option value="connection_request">Connection request</option><option value="after_acceptance">After acceptance</option><option value="after_team_chat">After team conversation</option><option value="chat_confirmation">Chat confirmation email</option><option value="chat_reminder">Chat reminder</option><option value="relationship_follow_up">Relationship follow-up</option></select></div><div><label for="agent-person">Person (optional for recruiter search)</label><select id="agent-person">{person_options}</select></div><div><label for="agent-job">Job</label><select id="agent-job">{job_options}</select></div><div><label for="agent-company">Company</label><select id="agent-company">{company_options}</select></div><div><label for="agent-goal">Goal or research purpose</label><input id="agent-goal" value="find a suitable recruiter or team member and prepare personalized outreach"></div></div><label for="agent-resume">Resume text for the resume agent</label><textarea id="agent-resume" placeholder="Paste your resume text here. It is used only when you choose the resume agent."></textarea><div class="agent-actions"><button type="submit" class="primary" data-dry="false">Run agent</button><button type="submit" class="secondary" data-dry="true">Preview prompt</button></div><p class="muted" style="margin-top:10px">Use <code>./career serve</code> to enable these buttons. A static file cannot call the local agent service.</p><pre id="agent-result" class="agent-result"></pre></form></section><div class="agent-grid"><article class="agent-card"><div class="eyebrow">Research + Draft</div><h2>Recruiter outreach agent</h2><p>Finds relevant public contacts, proves genuine overlap, and drafts connection, introduction, and follow-up messages.</p><code>./career agent recruiter-outreach --job JOB_ID [--person PERSON_ID]</code></article><article class="agent-card"><div class="eyebrow">Draft</div><h2>Message agent</h2><p>Uses your approved style, outreach stage, saved facts, and past interactions.</p><code>./career agent message --person PERSON_ID --stage connection_request</code></article><article class="agent-card"><div class="eyebrow">Tailor</div><h2>Resume agent</h2><p>Compares a JD with your resume and suggests truthful changes.</p><code>./career agent resume --job JOB_ID --resume resume.md</code></article><article class="agent-card"><div class="eyebrow">Research</div><h2>Person agent</h2><p>Finds public professional work, verifies identity, and suggests conversation angles.</p><code>./career agent person-research --person PERSON_ID</code></article><article class="agent-card"><div class="eyebrow">Research</div><h2>Company agent</h2><p>Studies the company, strategy, relevant team, and likely role challenges.</p><code>./career agent company-research --company COMPANY_ID --job JOB_ID</code></article></div><div class="safety-note"><strong>You stay in control.</strong> Agents do not send messages, submit applications, overwrite resumes, or save research as verified facts. The recruiter agent does not infer personal identity from names or photos.</div><section class="panel"><div class="panel-head"><h2>Recent reports</h2><span class="count">{len(reports)}</span></div>{report_cards()}</section></section>
<section id="metrics" class="view"><h1>Progress</h1><p class="muted">Simple measures of action and response.</p><div class="metrics">{metric_cards}</div><section class="panel"><h2>Metric definitions</h2><p style="margin-top:12px"><strong>Reply rate</strong> = replies logged ÷ outreach events logged.</p><p style="margin-top:8px"><strong>Interview rate</strong> = jobs at interview or later ÷ submitted applications.</p><p style="margin-top:8px"><strong>Date range</strong> = all local records in this dataset.</p></section></section>
<div class="footer">Generated from CSV and Markdown · No automatic messages or applications · IDs are shown for terminal commands</div></main></div>
<script>document.querySelectorAll('.person-toggle').forEach(button=>button.addEventListener('click',()=>{{const detail=document.getElementById(button.dataset.target);const opening=detail.hidden;detail.hidden=!opening;button.setAttribute('aria-expanded',opening?'true':'false')}}));document.querySelectorAll('[data-table="people-table"]').forEach(input=>input.addEventListener('input',()=>{{document.querySelectorAll('.person-main-row').forEach(row=>{{if(row.style.display==='none'){{const detail=document.getElementById(row.dataset.detailId);detail.hidden=true;row.querySelector('.person-toggle').setAttribute('aria-expanded','false')}}}})}}));document.querySelectorAll('nav button').forEach(b=>b.addEventListener('click',()=>{{document.querySelectorAll('nav button,.view').forEach(x=>x.classList.remove('active'));b.classList.add('active');document.getElementById(b.dataset.view).classList.add('active')}}));document.querySelectorAll('.search').forEach(input=>input.addEventListener('input',()=>{{const q=input.value.toLowerCase();document.getElementById(input.dataset.table).querySelectorAll('[data-search]').forEach(row=>row.style.display=(row.dataset.search||row.innerText.toLowerCase()).includes(q)?'':'none')}}));document.querySelectorAll('.decision-select').forEach(select=>{{select.dataset.previous=select.value;select.addEventListener('change',async()=>{{const previous=select.dataset.previous;select.disabled=true;try{{const response=await fetch('/api/job-decision',{{method:'POST',headers:{{'Content-Type':'application/json'}},body:JSON.stringify({{job_id:select.dataset.jobId,decision:select.value}})}});const data=await response.json();if(!response.ok||!data.ok)throw new Error(data.error||'Could not save decision');select.dataset.previous=select.value;select.className='decision-select decision-'+select.value}}catch(error){{select.value=previous;alert('Decision was not saved. Start the dashboard with ./career serve.')}}finally{{select.disabled=false}}}})}});let dryRun=false;document.querySelectorAll('#agent-form button').forEach(button=>button.addEventListener('click',()=>dryRun=button.dataset.dry==='true'));document.getElementById('agent-form').addEventListener('submit',async event=>{{event.preventDefault();const result=document.getElementById('agent-result');result.classList.add('show');result.textContent=dryRun?'Building preview…':'Running agent…';const payload={{action:document.getElementById('agent-action').value,channel:document.getElementById('agent-channel').value,stage:document.getElementById('agent-stage').value,person_id:document.getElementById('agent-person').value,job_id:document.getElementById('agent-job').value,company_id:document.getElementById('agent-company').value,goal:document.getElementById('agent-goal').value,purpose:document.getElementById('agent-goal').value,resume_text:document.getElementById('agent-resume').value,dry_run:dryRun}};try{{const response=await fetch('/api/agent',{{method:'POST',headers:{{'Content-Type':'application/json'}},body:JSON.stringify(payload)}});const data=await response.json();if(!response.ok||!data.ok)throw new Error(data.error||'Agent request failed');result.textContent=data.result+(data.report_path?'\\n\\nSaved: '+data.report_path:'')}}catch(error){{result.textContent='Could not run the agent. Start the local dashboard with ./career serve.\\n\\n'+error.message}}}});</script></body></html>'''
    status_script = """<script>
const isDemo = document.body.dataset.demo === 'true';
let toastTimer;
function showToast(message) {
  const toast = document.getElementById('toast');
  toast.textContent = message;
  toast.classList.add('show');
  clearTimeout(toastTimer);
  toastTimer = setTimeout(() => toast.classList.remove('show'), 2800);
}
function activateView(viewId, query = '', status = '') {
  const target = document.getElementById(viewId);
  if (!target) return;
  document.querySelectorAll('nav button,.view').forEach(element => element.classList.remove('active'));
  document.querySelector(`nav button[data-view="${viewId}"]`)?.classList.add('active');
  target.classList.add('active');
  history.replaceState(null, '', `#${viewId}`);
  if (viewId === 'people' || viewId === 'jobs') {
    const tableId = `${viewId}-table`;
    const search = document.querySelector(`.search[data-table="${tableId}"]`);
    const filter = document.querySelector(`.table-filter[data-table="${tableId}"]`);
    if (search && query) search.value = query;
    if (filter && status) filter.value = status;
    applyTableFilters(tableId);
  }
  window.scrollTo({top: 0, behavior: 'smooth'});
}
document.querySelectorAll('[data-open-view]').forEach(button => button.addEventListener('click', () => {
  activateView(button.dataset.openView, button.dataset.query || '');
}));
document.querySelectorAll('.pipeline-step').forEach(button => button.addEventListener('click', () => {
  activateView('jobs', '', button.dataset.pipelineStatus);
}));
document.querySelectorAll('nav button').forEach(button => button.addEventListener('click', () => {
  history.replaceState(null, '', `#${button.dataset.view}`);
}));
const initialView = location.hash.slice(1);
if (initialView && document.getElementById(initialView)?.classList.contains('view')) activateView(initialView);
if (isDemo) {
  document.addEventListener('change', event => {
    const control = event.target.closest('.job-status-select,.person-status-select,.decision-select');
    if (!control) return;
    event.stopImmediatePropagation();
    if (control.classList.contains('decision-select')) {
      control.className = `decision-select decision-${control.value}`;
    } else if (control.classList.contains('job-status-select')) {
      control.className = `job-status-select status-${control.value}`;
      control.closest('tr').dataset.status = control.value;
      applyTableFilters('jobs-table');
    } else {
      control.className = `person-status-select status-${control.value}`;
      control.closest('tr').dataset.status = control.value;
      applyTableFilters('people-table');
    }
    showToast('Updated for this demo. Reload the page to reset it.');
  }, true);
  document.addEventListener('click', event => {
    const button = event.target.closest('.mark-reached-out');
    if (!button) return;
    event.stopImmediatePropagation();
    const row = button.closest('tr');
    const select = row.querySelector('.person-status-select');
    select.value = 'reached_out';
    select.className = 'person-status-select status-reached_out';
    row.dataset.status = 'reached_out';
    button.remove();
    showToast('Marked reached out for this demo. Nothing was sent.');
  }, true);
}
async function saveJobStatus(jobId, status, select) {
  const previous = select.dataset.previous;
  select.disabled = true;
  try {
    const response = await fetch('/api/job-status', {
      method: 'POST', headers: {'Content-Type': 'application/json'},
      body: JSON.stringify({job_id: jobId, status})
    });
    const data = await response.json();
    if (!response.ok || !data.ok) throw new Error(data.error || 'Could not save job status');
    select.value = data.status;
    select.dataset.previous = data.status;
    select.className = 'job-status-select status-' + data.status;
    select.closest('tr').dataset.status = data.status;
    document.querySelectorAll('[data-metric="applications"]').forEach(el => el.textContent = data.metrics.applications);
    document.querySelectorAll('[data-metric="interview_rate"]').forEach(el => el.textContent = data.metrics.interview_rate + '%');
    applyTableFilters('jobs-table');
  } catch (error) {
    select.value = previous;
    alert('Job status was not saved. Open the dashboard at http://127.0.0.1:8765 after running ./career serve.');
  } finally {
    select.disabled = false;
  }
}
document.querySelectorAll('.job-status-select').forEach(select => select.addEventListener('change', () => {
  saveJobStatus(select.dataset.jobId, select.value, select);
}));
async function savePersonStatus(personId, status, control, previous) {
  control.disabled = true;
  try {
    const response = await fetch('/api/person-status', {
      method: 'POST', headers: {'Content-Type': 'application/json'},
      body: JSON.stringify({person_id: personId, status})
    });
    const data = await response.json();
    if (!response.ok || !data.ok) throw new Error(data.error || 'Could not save person status');
    const row = control.closest('.person-main-row');
    const select = row.querySelector('.person-status-select');
    select.value = data.status;
    select.dataset.previous = data.status;
    select.className = 'person-status-select status-' + data.status;
    row.dataset.status = data.status;
    const nextActionCell = row.lastElementChild;
    nextActionCell.textContent = data.next_action || '';
    const nextActionDate = document.createElement('small');
    nextActionDate.textContent = data.next_action_date || '';
    nextActionCell.appendChild(nextActionDate);
    if (data.status === 'reached_out') row.querySelector('.mark-reached-out')?.remove();
    document.querySelectorAll('[data-metric="outreach"]').forEach(el => el.textContent = data.metrics.outreach);
    document.querySelectorAll('[data-metric="reply_rate"]').forEach(el => el.textContent = data.metrics.reply_rate + '%');
    applyTableFilters('people-table');
  } catch (error) {
    if (previous) control.value = previous;
    alert('Status was not saved. Open the dashboard at http://127.0.0.1:8765 after running ./career serve.');
  } finally {
    control.disabled = false;
  }
}
document.querySelectorAll('.person-status-select').forEach(select => select.addEventListener('change', () => {
  savePersonStatus(select.dataset.personId, select.value, select, select.dataset.previous);
}));
document.querySelectorAll('.mark-reached-out').forEach(button => button.addEventListener('click', () => {
  savePersonStatus(button.dataset.personId, 'reached_out', button);
}));
function applyTableFilters(tableId) {
  const table = document.getElementById(tableId);
  const query = (document.querySelector(`.search[data-table="${tableId}"]`)?.value || '').toLowerCase();
  const filter = document.querySelector(`.table-filter[data-table="${tableId}"]`);
  const field = filter?.dataset.field;
  const selected = filter?.value || '';
  table.querySelectorAll('tbody > tr[data-search]').forEach(row => {
    const matchesSearch = (row.dataset.search || '').includes(query);
    const matchesFilter = !selected || row.dataset[field] === selected;
    row.style.display = matchesSearch && matchesFilter ? '' : 'none';
    if (row.style.display === 'none' && row.dataset.detailId) {
      const detail = document.getElementById(row.dataset.detailId);
      detail.hidden = true;
      row.querySelector('.person-toggle').setAttribute('aria-expanded', 'false');
    }
  });
}
function sortTable(tableId, field) {
  const table = document.getElementById(tableId);
  const body = table.querySelector('tbody');
  const rows = Array.from(body.querySelectorAll(':scope > tr[data-search]'));
  rows.sort((a, b) => {
    const left = a.dataset[field] || '';
    const right = b.dataset[field] || '';
    const comparison = field === 'fit'
      ? Number(right) - Number(left)
      : left.localeCompare(right, undefined, {numeric: true, sensitivity: 'base'});
    return comparison || (a.dataset.name || '').localeCompare(b.dataset.name || '', undefined, {sensitivity: 'base'});
  });
  rows.forEach(row => {
    body.appendChild(row);
    if (row.dataset.detailId) body.appendChild(document.getElementById(row.dataset.detailId));
  });
}
document.querySelectorAll('.table-filter').forEach(filter => filter.addEventListener('change', () => applyTableFilters(filter.dataset.table)));
document.querySelectorAll('.table-sort').forEach(sort => sort.addEventListener('change', () => sortTable(sort.dataset.table, sort.value)));
document.querySelectorAll('.search').forEach(search => search.addEventListener('input', () => applyTableFilters(search.dataset.table)));
</script>"""
    page = page.replace("</script></body>", "</script>" + status_script + "</body>")
    output.write_text(page, encoding="utf-8")
    return output
