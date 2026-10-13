"""
memoria-mcp -- MCP server for persistent context and proactive awareness.

Gives any Claude instance (Cowork, Claude Desktop, Claude Code) structured
access to a shared, file-based memory system: current state, deadlines,
projects, logs, follow-ups, and proactive suggestions.

Instead of manually reading files and parsing them, Claude calls high-level
tools like get_context() and gets back an actionable summary.

Design notes (the behaviours that matter):
- Memory files may be written in Hungarian or English: the parsers accept
  both sets of markers (section names, TODO words, "done"/"closed" words).
- Dates are compared as CALENDAR days (today=0, tomorrow=+1, yesterday=-1),
  never as datetime differences, so a date of "today" is not overdue at 2 a.m.
- get_context() truncates long session highlights to a pointer, because the
  field is loaded verbatim at every session start; the log files themselves
  are never shortened, only the loaded view.
- get_deadlines() is a WHITELIST: only the deadline section of the context
  file is a deadline source. Full ISO dates elsewhere are references. Future
  dates found outside the section are NOT dropped silently: they come back in
  a separate "might be in the wrong place" advisory block.
- Filters never drop silently: whatever a heuristic suppresses that still
  carries a future date is reported back, so the limit says it was a limit.
- get_open_markers() sweeps the live memory areas for open markers
  (TODO / TEENDŐ / ⚑ / checkboxes). Its output is a SUSPICION list: closing an
  item still needs evidence.
"""

import os
import re
import json
import shutil
import sqlite3
import sys
import time
import glob as glob_module
from datetime import datetime, timedelta
from pathlib import Path

from mcp.server.fastmcp import FastMCP

# --- Configuration ---

# The memory folder: MEMORIA_ROOT (recommended: a folder OUTSIDE the repository), or, as a fallback, the
# git-ignored memoria/ folder next to this server's folder.
MEMORIA_ROOT = os.environ.get("MEMORIA_ROOT") or os.path.join(
    os.path.dirname(os.path.dirname(os.path.abspath(__file__))), "memoria")
if not os.environ.get("MEMORIA_ROOT"):
    print(f"memoria-mcp: MEMORIA_ROOT is not set, using the fallback {MEMORIA_ROOT}", file=sys.stderr)

# Optional memoria.config.json in MEMORIA_ROOT (see memoria.config.example.json); env vars win over it.
_CONFIG = {}
try:
    with open(os.path.join(MEMORIA_ROOT, "memoria.config.json"), encoding="utf-8") as _f:
        _CONFIG = json.load(_f)
except (OSError, ValueError):
    _CONFIG = {}


def _cfg(key, env_key, default):
    """Config value: env var > memoria.config.json > default."""
    return os.environ.get(env_key) or _CONFIG.get(key) or default


# The living context model: who the owner is, what is going on, what is urgent (with the deadline section).
CONTEXT_FILE = _cfg("context_file", "MEMORIA_CONTEXT_FILE", "kontextus/helyzet.md")
# Cross-session patterns and lessons ("mintázatok" = patterns), read on demand by get_patterns.
PATTERNS_FILE = _cfg("patterns_file", "MEMORIA_PATTERNS_FILE", "kontextus/mintazatok.md")
# How the owner is called in the tool output (e.g. "the owner", or a first name).
OWNER_NAME = _cfg("owner_name", "MEMORIA_OWNER_NAME", "the owner")

# --- Helpers ---


def _safe_path(rel_path: str) -> str:
    """Resolve a relative path and ensure it stays within MEMORIA_ROOT."""
    full = os.path.realpath(os.path.join(MEMORIA_ROOT, rel_path))
    root = os.path.realpath(MEMORIA_ROOT)
    try:
        inside = os.path.commonpath([os.path.normcase(full), os.path.normcase(root)]) == os.path.normcase(root)
    except ValueError:   # different drives on Windows
        inside = False
    if not inside:
        raise ValueError(f"Path escapes memory root: {rel_path}")
    return full


def _read_file(rel_path: str) -> str:
    """Read a file relative to MEMORIA_ROOT."""
    full = _safe_path(rel_path)
    if not os.path.isfile(full):
        raise FileNotFoundError(f"Not found: {rel_path}")
    with open(full, "r", encoding="utf-8") as f:
        return f.read()


def _write_file(rel_path: str, content: str) -> str:
    """Atomic write to a path relative to MEMORIA_ROOT.

    Temp file + fsync + os.replace: a partial write can never leave the target
    half-written or NUL-padded. fsync() guarantees the data is on disk before
    the target name is replaced.
    """
    full = _safe_path(rel_path)
    os.makedirs(os.path.dirname(full), exist_ok=True)
    dir_name = os.path.dirname(full)
    base_name = os.path.basename(full)
    tmp_path = os.path.join(dir_name, f".{base_name}.tmp")
    try:
        with open(tmp_path, "w", encoding="utf-8") as f:
            f.write(content)
            f.flush()
            os.fsync(f.fileno())
        os.replace(tmp_path, full)
    except Exception:
        if os.path.exists(tmp_path):
            try:
                os.remove(tmp_path)
            except OSError:
                pass
        raise
    return full


def _append_file(rel_path: str, content: str) -> str:
    """Append content to a file relative to MEMORIA_ROOT."""
    full = _safe_path(rel_path)
    os.makedirs(os.path.dirname(full), exist_ok=True)
    with open(full, "a", encoding="utf-8") as f:
        f.write(content)
    return full


def _parse_frontmatter(text: str) -> tuple[dict, str]:
    """Parse YAML-ish frontmatter from markdown. Returns (meta, body)."""
    if not text.startswith("---"):
        return {}, text
    parts = text.split("---", 2)
    if len(parts) < 3:
        return {}, text
    meta = {}
    for line in parts[1].strip().splitlines():
        if ":" in line:
            key, val = line.split(":", 1)
            meta[key.strip()] = val.strip()
    return meta, parts[2].strip()


def _strip_frontmatter(text: str) -> str:
    """Remove YAML frontmatter from markdown, returning only the body."""
    _, body = _parse_frontmatter(text)
    return body


def _list_files(subdir: str = "", pattern: str = "*.md") -> list[str]:
    """List files in a subdirectory of MEMORIA_ROOT."""
    root = Path(MEMORIA_ROOT) / subdir
    if not root.exists():
        return []
    return sorted(
        str(p.relative_to(MEMORIA_ROOT))
        for p in root.glob(pattern)
    )


def _today() -> str:
    return datetime.now().strftime("%Y-%m-%d")


def _days_until(date_str: str) -> int | None:
    """Calculate days from today until a date string (YYYY-MM-DD)."""
    try:
        target = datetime.strptime(date_str, "%Y-%m-%d")
        # Calendar-day difference (not datetime): otherwise a date of TODAY would
        # show up as -1/overdue in the small hours, because midnight has already
        # "passed". Date-level comparison: today = 0.
        return (target.date() - datetime.now().date()).days
    except (ValueError, TypeError):
        return None


# "Completed" markers, matched on WORD BOUNDARIES. A plain substring match once
# found "kész" (done) inside "elkészíti" (will prepare) and silently dropped a
# real deadline. Hungarian agglutination makes substring matching produce false
# positives, so we match whole words only. Hungarian first, English after.
_COMPLETED_WORD_MARKERS = (
    "leadva",
    "le van adva",
    "k\u00e9sz",
    "teljes\u00edtve",
    "befejezve",
    "elk\u00e9sz\u00fclt",
    "megoldva",
    "lez\u00e1rva",
    "done",
    "completed",
    "finished",
    "submitted",
    "resolved",
    "closed",
)
# \b also behaves correctly on derived Hungarian forms: in "elkészíti" the "kész"
# is preceded by 'l' (a word character), so there is no word boundary -> no match.
_COMPLETED_WORD_RE = re.compile(
    r"(?<![\w\u00c0-\u024f])(?:" + "|".join(_COMPLETED_WORD_MARKERS)
    + r")(?![\w\u00c0-\u024f])",
    re.IGNORECASE,
)


def _is_completed_line(line: str) -> bool:
    """Check if a line describes something already completed.

    ⚠️ The caller must not just drop a True: if the line carries a FUTURE ISO
    date, the suppression has to be REPORTED (see `_scan_deadline_dates` ->
    `suppressed`). A silently vanished deadline is exactly the failure this
    guards against.
    """
    if "\u2705" in line:
        return True
    return bool(_COMPLETED_WORD_RE.search(line))


def _is_table_line(line: str) -> bool:
    """Check if a line is part of a markdown table."""
    stripped = line.strip()
    return stripped.startswith("|") or (
        stripped.startswith("|-") or stripped.startswith("| -")
    )


def _is_metadata_line(line: str) -> bool:
    """Check if a line is metadata/frontmatter rather than content."""
    stripped = line.strip().lower()
    return (
        stripped.startswith("updated:")
        or stripped.startswith("created:")
        or stripped.startswith("source:")
        or stripped.startswith("type:")
        or stripped.startswith("next_update:")
    )


def _parse_date(date_str: str) -> datetime | None:
    """Parse a YYYY-MM-DD date string, returning None on failure."""
    try:
        return datetime.strptime(date_str, "%Y-%m-%d")
    except (ValueError, TypeError):
        return None


def _is_completion_date_line(line: str) -> bool:
    """Check if a line contains a completion/reference date rather than a deadline.

    Lines like 'Telegram-MCP: LIVE and TESTED (2026-04-02)' contain dates
    that mark when something was completed, not when something is due.
    These should not be flagged as stale.
    """
    lower = line.lower()
    completion_markers = [
        "live", "tesztelve", "deployed", "létrehozva", "implementálva",
        "megírva", "published", "released", "aktiválva", "migrálva",
        # Extension against false positives (status/confirmation lines):
        "frissítve", "frissítés", "megerősítette", "megerősítve",
        "megerősítés", "jelezte", "datált", "napirend-segéd",
        "snapshot", "státusz frissítve", "feldolgozva", "kezdődött",
        "leadva", "elkészült", "elkészítve", "lement", "lezárva",
        # English equivalents (substring match, so only clearly past-tense words):
        "tested", "created", "implemented", "activated", "migrated",
        "status updated", "processed", "submitted", "completed",
    ]
    return any(marker in lower for marker in completion_markers)


def _is_period_start_date(line: str, date_str: str) -> bool:
    """Check if a date is followed by a period-start suffix (-tól/-től = "from").

    These dates mark the beginning of a time period rather than a deadline.
    Example: 'Current schedule (2026-05-04-től until the exam)' -- the date
    here is when a period starts, not when something is due.
    """
    idx = line.find(date_str)
    if idx == -1:
        return False
    suffix = line[idx + len(date_str):idx + len(date_str) + 5].lower()
    return suffix.startswith(("-tól", "-től", "tól", "től"))


def _is_filename_date_reference(line: str, date_str: str) -> bool:
    """Check if the date appears inside a markdown filename reference.

    Examples that should be filtered:
    - `projektek/market_survey_2026-05.md`
    - [link](naplo/2026-05-22_session.md)
    These dates mark when a file was created/saved, not deadlines.

    Heuristic: the date is followed (within 60 chars) by a known file extension,
    AND preceded (within 50 chars) by a backtick / slash / underscore -- typical
    filename context.
    """
    idx = line.find(date_str)
    if idx == -1:
        return False
    right = line[idx:idx + 60].lower()
    if not any(ext in right for ext in (".md", ".py", ".json", ".txt", ".docx")):
        return False
    left = line[max(0, idx - 50):idx]
    return "`" in left or "/" in left or "_" in left[-30:]


def _is_suffixed_past_date(line: str, date_str: str) -> bool:
    """Check if the date is followed by a Hungarian past-tense temporal suffix.

    Examples that should be filtered:
    - '2026-05-21-en megerositette' (-en/-an = "on", confirmed)
    - '2026-04-12-kor leadta'       (-kor = "at", submitted)
    - '2026-05-13-an erkezett'      (-an = "on", arrived)
    These mark when something happened, not when something is due.
    """
    idx = line.find(date_str)
    if idx == -1:
        return False
    suffix = line[idx + len(date_str):idx + len(date_str) + 6].lower()
    past_suffixes = ("-én", "-án", "-on", "-en", "-ön", "-kor", "-ban", "-ben")
    return suffix.startswith(past_suffixes)


def _is_parenthetical_reference(line: str, date_str: str) -> bool:
    """Check if the date is inside an unclosed-paren reference context.

    Examples that should be filtered:
    - 'foo.md (2026-05-19, dated market snapshot)'  -> paren + comma
    - 'owner 2026-05-21): "..."'                    -> paren + closer
    - 'project (2026-05-22 - description)'          -> paren + em-dash
    Heuristic: within 60 chars to the left there is an unmatched '(', AND the
    date is followed by one of: comma, em-dash (' --'), closing paren, ' -'.
    """
    idx = line.find(date_str)
    if idx == -1:
        return False
    left = line[max(0, idx - 60):idx]
    open_count = left.count("(") - left.count(")")
    if open_count <= 0:
        return False
    right = line[idx + len(date_str):idx + len(date_str) + 5]
    return (right.startswith(",")
            or right.startswith(" —")
            or right.startswith(" -")
            or right.startswith(")")
            or right.startswith("):"))


# Words that identify the deadline section header (Hungarian first, then English).
DEADLINE_SECTION_MARKERS = ("hatarido", "hat\u00e1rid\u0151", "deadline")


def _extract_deadline_section(body: str) -> tuple:
    """Split the context file into (deadline-section, everything-else).

    Convention: a real, future deadline lives ONLY in the deadline section
    ("SURGOS / KOZELGO HATARIDOK" or "URGENT / UPCOMING DEADLINES"), written
    with a full ISO date (year-month-day). In any other section a full ISO
    date is a REFERENCE -- when something happened -- not a deadline.

    The parser enforces this (whitelist) instead of relying on the author's
    discipline. Returns (deadline_body, other_body). If the section is not
    found, returns (full body, "") -- i.e. the old behaviour.
    """
    lines = body.splitlines()
    start = None
    for i, line in enumerate(lines):
        if line.startswith("## "):
            low = line.lower()
            if any(m in low for m in DEADLINE_SECTION_MARKERS):
                start = i
                break
    if start is None:
        return body, ""
    end = len(lines)
    for j in range(start + 1, len(lines)):
        if lines[j].startswith("## "):
            end = j
            break
    return "\n".join(lines[start:end]), "\n".join(lines[:start] + lines[end:])


# The head anchor is not a strict regex but "what may stand BEFORE the date".
# Allowed at the start of the line: whitespace, bullet markers (- * • #),
# emphasis (** __ _ *) and DECORATION (★, ⚠, ✅, emoji, arrows) --
# anything that is not a word. FORBIDDEN: letters, digits and an opening
# bracket/quote (that is already a prose reference: "_(since 2026-09-11 ...)").
# Without the decoration allowance a real deadline bullet like `- **★ 2099-03-15: ...`
# would not count as head-position and would be demoted to a reference.
_HEAD_FORBIDDEN_RE = re.compile(
    "[0-9A-Za-z\u00c0-\u024f\u0370-\u03ff\u0400-\u04ff(\\[{\u201e\u201c\u2018\"\']"
)


def _is_head_date(line: str, date_str: str) -> bool:
    """Is the date at the HEAD of the line (directly after bullet marker and bold)?

    This is the structural signal behind the `seen_dates` dedup exception. By
    the context file's convention a REAL deadline looks like
    `- **YYYY-MM-DD: ...**`; any other ISO date in the line is a prose
    reference (cross-reference, reasoning, history). So the POSITION is a
    declaration, not a guess -- which is why lexical similarity (`_fuzzy_match`),
    unreliable on long prose, does not decide here ("what can be declared,
    should not be guessed").
    """
    idx = line.find(date_str)
    if idx < 0:
        return False
    return not _HEAD_FORBIDDEN_RE.search(line[:idx])


def _scan_deadline_dates(text: str, seen_dates: set, min_days: int,
                         suppressed: list | None = None,
                         allow_head_duplicates: bool = False,
                         require_head: bool = False,
                         body_refs: list | None = None) -> list:
    """Collect date-bearing lines from `text` that survive the non-deadline filters.

    Shared engine for the deadline section (min_days=-7: recently overdue
    items are still interesting) and the advisory outside it (min_days=1:
    future only).

    `suppressed`: if a list is passed, lines dropped as "completed" that carry
    a FUTURE ISO date are added to it, so the caller CAN REPORT the
    suppression. Without this, a real deadline silently vanishes when its prose
    happens to contain a completed-word.

    `allow_head_duplicates`: TWO SEPARATE DEADLINES CAN FALL ON THE SAME DAY.
    The `seen_dates` dedup exists so a date mentioned several times in prose
    comes back once; but it deduplicates by DATE, globally and silently, so a
    second deadline bullet on an already-seen day used to vanish without trace.
    ➜ If the date is at the HEAD of the line (`_is_head_date`), it is a
    separate deadline bullet, so we LET IT THROUGH; if it is in the line's
    prose, the dedup stays (that really is repetition). Only enabled when
    scanning the deadline section: outside it, repeating an already reported
    deadline is noise.
    """
    date_pattern = re.compile(r"(\d{4}-\d{2}-\d{2})")
    found = []

    for line in text.splitlines():
        if _is_table_line(line):
            continue
        if _is_metadata_line(line):
            continue
        if _is_completed_line(line):
            if suppressed is not None:
                for date_str in date_pattern.findall(line):
                    days = _days_until(date_str)
                    if days is not None and days >= 1:
                        clean = line.strip().lstrip("-\u2022*# ").replace("**", "")
                        suppressed.append({
                            "date": date_str,
                            "days_remaining": days,
                            "description": clean[:400],
                        })
                        break
            continue
        if _is_completion_date_line(line):
            continue

        line_emitted: set = set()
        for date_str in date_pattern.findall(line):
            if date_str in line_emitted:
                continue  # the same date twice WITHIN one line -- that is repetition
            if date_str in seen_dates:
                # Exception: if the date is at the HEAD of the line, this is a
                # separate deadline bullet on the same day, not a repeated mention.
                if not (allow_head_duplicates and _is_head_date(line, date_str)):
                    continue
            # POSITION ANCHOR inside the deadline section. The whitelist narrows
            # to the SECTION, but inside it every ISO date used to stay a
            # deadline -- even in the BODY of a bullet. One exam bullet once
            # produced three deadline rows (break end, the real exam date,
            # exam-period end). Same principle as the whitelist: the PARSER
            # keeps the convention, not the author.
            # AND THE FILTER IS NOT SILENT: a FUTURE date in the body goes to
            # the `body_refs` advisory block.
            if require_head and not _is_head_date(line, date_str):
                ref_days = _days_until(date_str)
                if body_refs is not None and ref_days is not None and ref_days >= 1:
                    ref_clean = line.strip().lstrip("-\u2022*# ").replace("**", "")
                    body_refs.append({
                        "date": date_str,
                        "days_remaining": ref_days,
                        "description": ref_clean[:400],
                    })
                continue
            if _is_period_start_date(line, date_str):
                continue
            if _is_filename_date_reference(line, date_str):
                continue
            if _is_suffixed_past_date(line, date_str):
                continue
            if _is_parenthetical_reference(line, date_str):
                continue

            days = _days_until(date_str)
            if days is None or days < min_days:
                continue
            seen_dates.add(date_str)
            line_emitted.add(date_str)

            clean_line = line.strip().lstrip("-\u2022*# ")
            clean_line = clean_line.replace("**", "")

            if days < 0:
                urgency = "OVERDUE"
            elif days <= 3:
                urgency = "[!!!] CRITICAL"
            elif days <= 7:
                urgency = "[!!] URGENT"
            elif days <= 14:
                urgency = "[!] UPCOMING"
            else:
                urgency = "LATER"

            found.append({
                "date": date_str,
                "days_remaining": days,
                "urgency": urgency,
                "description": clean_line,
            })

    return found


def _extract_significant_words(text: str) -> set:
    """Extract significant words (3+ chars, no stopwords) from text.

    Used for fuzzy matching between open items and resolved items.
    """
    stopwords = {
        "egy", "van", "nem", "meg", "már", "ami", "aki", "és", "the", "for",
        "ápr", "nap", "kell", "session", "még", "volt", "lesz", "hogy",
        "ezt", "azt", "mint", "csak", "majd", "ide", "oda", "most",
    }
    words = re.findall(r'\w{3,}', text.lower())
    return {w for w in words if w not in stopwords}


def _fuzzy_match(text1: str, text2: str, threshold: float = 0.5) -> bool:
    """Check if two texts are similar enough based on shared significant words.

    Returns True if the overlap ratio (relative to the smaller set) >= threshold.
    Default threshold 0.5 means at least half of the smaller text's words
    must appear in the larger text.
    """
    words1 = _extract_significant_words(text1)
    words2 = _extract_significant_words(text2)
    if not words1 or not words2:
        return False
    overlap = len(words1 & words2)
    smaller = min(len(words1), len(words2))
    return (overlap / smaller) >= threshold


# --- Processed Items Tracker ---
# Tracks which session context_updates have been applied and which open_items
# have been resolved, so suggest_context_updates() doesn't show stale items.

_PROCESSED_FILE = "naplo/_processed.json"


def _load_processed() -> dict:
    """Load the processed items tracker. Returns default structure if not found."""
    try:
        content = _read_file(_PROCESSED_FILE)
        data = json.loads(content)
        # Migrate old format: convert plain strings to dated dicts
        migrated = False
        new_items = []
        for item in data.get("resolved_items", []):
            if isinstance(item, str):
                new_items.append({"text": item, "resolved_on": data.get("last_updated", _today())})
                migrated = True
            else:
                new_items.append(item)
        if migrated:
            data["resolved_items"] = new_items
            _save_processed(data)
        return data
    except (FileNotFoundError, json.JSONDecodeError):
        return {
            "processed_sessions": [],  # List of session filenames whose context_updates are done
            "resolved_items": [],      # List of open_item texts that have been resolved
        }


def _save_processed(data: dict) -> None:
    """Save the processed items tracker."""
    _write_file(_PROCESSED_FILE, json.dumps(data, indent=2, ensure_ascii=False))


# --- Audit Log ---
# SQLite-based audit logging for memoria-mcp file operations (move, delete).
# Single source of truth, best-effort writes, separate DB file under
# <server_dir>/data/.

MEMORIA_AUDIT_DB = os.environ.get(
    "MEMORIA_AUDIT_DB",
    os.path.join(MEMORIA_ROOT, "_audit", "memoria_audit.db"),   # personal text: keep it with the memory, not in the repo
)

_AUDIT_SCHEMA = """
CREATE TABLE IF NOT EXISTS memoria_audit_log (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    timestamp INTEGER NOT NULL,
    operation TEXT NOT NULL,
    src_path TEXT NOT NULL,
    dst_path TEXT,
    file_size_bytes INTEGER,
    first_line TEXT,
    notes TEXT
);
CREATE INDEX IF NOT EXISTS idx_memoria_audit_timestamp ON memoria_audit_log(timestamp);
CREATE INDEX IF NOT EXISTS idx_memoria_audit_operation ON memoria_audit_log(operation);
"""


def _get_audit_db() -> sqlite3.Connection:
    """Open (and initialize if needed) the audit log SQLite database."""
    Path(os.path.dirname(MEMORIA_AUDIT_DB)).mkdir(parents=True, exist_ok=True)
    conn = sqlite3.connect(MEMORIA_AUDIT_DB, timeout=10.0)
    conn.row_factory = sqlite3.Row
    conn.executescript(_AUDIT_SCHEMA)
    return conn


def _log_memoria_audit(
    operation: str,
    src_path: str,
    dst_path: str | None = None,
    file_size_bytes: int | None = None,
    first_line: str | None = None,
    notes: str | None = None,
) -> None:
    """Best-effort audit logging — never raises.

    Each file operation (move, delete) writes a row here for traceability.
    If audit logging fails (DB locked, disk full), the caller's operation
    still succeeds — audit is nice-to-have, not gating.
    """
    try:
        conn = _get_audit_db()
        try:
            conn.execute(
                """INSERT INTO memoria_audit_log
                   (timestamp, operation, src_path, dst_path,
                    file_size_bytes, first_line, notes)
                   VALUES (?, ?, ?, ?, ?, ?, ?)""",
                (int(time.time()), operation, src_path, dst_path,
                 file_size_bytes, first_line, notes),
            )
            conn.commit()
        finally:
            conn.close()
    except Exception:
        pass  # best-effort: never break the caller


def _read_first_line(rel_path: str, max_chars: int = 200) -> str:
    """Read the first up-to-max_chars of a file (for audit logging).

    Used before delete to capture an identifying snippet that can later
    answer 'what was in this file?' when only the audit log remains.
    If the first newline appears before max_chars, returns the first line;
    otherwise returns up to max_chars (no newline split).
    Returns empty string on any error (best-effort).
    """
    try:
        full = _safe_path(rel_path)
        with open(full, "r", encoding="utf-8", errors="replace") as f:
            buf = f.read(max_chars + 1)
            if "\n" in buf:
                first_line = buf.split("\n", 1)[0]
                return first_line[:max_chars]
            return buf[:max_chars]
    except Exception:
        return ""


def _check_archive_blocked(rel_path: str) -> None:
    """Raise ValueError if the path is under archiv/.

    The archiv/ directory is intentionally protected from move/delete:
    archived content is a time-capsule for debug purposes. To restore
    something from archiv/, do it manually in an interactive session.
    """
    normalized = rel_path.replace("\\", "/").lstrip("./")
    parts = [p for p in normalized.split("/") if p]
    if parts and parts[0] == "archiv":
        raise ValueError(
            f"Path is under archiv/ — move/delete blocked for safety: {rel_path}. "
            f"To restore archived content, do it manually in an interactive session."
        )


# --- MCP Server ---

mcp = FastMCP(name="memoria-mcp")




# ---------------------------------------------------------------------------
# MEMORY HYGIENE -- growth and staleness guard.  _MEMORIA_HYGIENE_V49
#
# WHY. The system only had DELIVERY guards (spill stamp, get_patterns paging,
# highlights truncation), no GROWTH guard. Delivery guards HIDE THE SYMPTOM:
# once a big file arrives neatly in parts, nothing signals that it is too
# large, so the pressure to fix it disappears. The workaround ate the alarm.
# The same shape appeared elsewhere: a documented size threshold that nothing
# enforced, and a system index left unrefreshed for months while the tool
# count doubled -- a false map, a silent error.
#
# PRINCIPLE: "the PARSER keeps the convention, not the author" and "THE LIMIT
# SAYS IT WAS A LIMIT". A threshold written in a markdown table is not an
# interface.
#
# WHY NOT A GENERIC SCAN. Flagging every .md over 45 KB would be noise: log and
# project files are large BY NATURE and are NEVER loaded whole. Noisy alarms get
# ignored by routine, and then the real signal is lost with them. Hence a
# NARROW, EXPLICIT watch list: only files that are reloaded at EVERY session
# start, so their size hurts session startup.
# ---------------------------------------------------------------------------

# (relative path, threshold in KB, what to do above it)
_HYGIENE_SIZE = [
    ("_state.md", 20,
     "compress: closed block -> archiv/, leave 1-3 sentences + pointer in its place"),
    (PATTERNS_FILE, 60,
     "archive sections BY HAND: closed, dated case entries -> "
     "archiv/mintazatok_archive/YYYY-MM.md, leave a 1-2 sentence rule + pointer in their place"),
    (CONTEXT_FILE, 30,
     "archive closed life periods"),
    ("_config.md", 20,
     "retire outdated protocols"),
]

# (relative path, max days without update, what to do above it)
_HYGIENE_STALE = [
    ("_index.md", 45,
     "the system map -- if a new tool was born meanwhile, update it"),
]


# --- FAIL-CLOSED principle in the radars ---
# "A check that cannot run is an ERROR with a named reason -- never a silent
# pass." The radars collect the checks that could not run into their own `inc`
# list and print them LOUDLY at the end of the output. The list is LOCAL (not
# module-level), so parallel tool calls never mix.

def _radar_note(inc, hol: str, mit: str, hiba) -> None:
    """Record a check that could NOT run. `inc is None` -> no-op."""
    if inc is None:
        return
    ok = f"{type(hiba).__name__}: {hiba}" if isinstance(hiba, BaseException) else str(hiba)
    inc.append((hol, mit, ok))


def _radar_incident_block(inc, kovetkezmeny: str) -> str:
    """The fail-closed block. Empty list -> empty string (no needless noise)."""
    if not inc:
        return ""
    sorok = [f"\n## ⛔ FAIL-CLOSED: {len(inc)} check(s) could NOT run"]
    sorok.append(
        "_This is NOT a finding and NOT a clean bill either: these files/steps were "
        "LEFT OUT of the count. The summary above DOES NOT APPLY to them._")
    for hol, mit, ok in inc:
        sorok.append(f"- `{hol}` — {mit} → **{ok}**")
    sorok.append(f"\n➜ {kovetkezmeny}")
    return "\n".join(sorok)


def _radar_zold_zarojel(inc) -> str:
    """A warning to put NEXT TO the green summary sentence (the sentence stays intact)."""
    if not inc:
        return ""
    return (f"  ⚠️ _BUT {len(inc)} check(s) could not run — see the "
            f"⛔ FAIL-CLOSED section; the green tick only covers the MEASURED part._")


def _hygiene_rows(inc=None) -> list[str]:
    """Size and staleness violations, as plain sentences. Empty list = all fine."""
    rows = []
    for rel, limit_kb, teendo in _HYGIENE_SIZE:
        try:
            full = _safe_path(rel)
            if not os.path.isfile(full):
                continue
            kb = os.path.getsize(full) / 1024
            if kb > limit_kb:
                rows.append(f"- `{rel}` — **{kb:.0f} KB** (threshold {limit_kb} KB) → {teendo}")
        except Exception as e:
            # FAIL-CLOSED: a measurement that did not run is NOT a green tick, it is a row.
            _radar_note(inc, rel, "size check did not run", e)
            rows.append(f"- ⛔ `{rel}` — the **size check did NOT run** "
                        f"({type(e).__name__}) → check it by hand")
            continue
    today = datetime.now().date()
    for rel, max_days, teendo in _HYGIENE_STALE:
        try:
            full = _safe_path(rel)
            if not os.path.isfile(full):
                continue
            mtime = datetime.fromtimestamp(os.path.getmtime(full)).date()
            kor = (today - mtime).days
            if kor > max_days:
                rows.append(f"- `{rel}` — not updated for **{kor} days** "
                            f"(threshold {max_days} days) → {teendo}")
        except Exception as e:
            # FAIL-CLOSED (see above).
            _radar_note(inc, rel, "staleness check did not run", e)
            rows.append(f"- ⛔ `{rel}` — the **staleness check did NOT run** "
                        f"({type(e).__name__}) → check it by hand")
            continue
    return rows


def _hygiene_block(rovid: bool = False) -> str:
    """Warning block to insert at the top of get_context / get_patterns."""
    rows = _hygiene_rows()
    if not rows:
        return ""
    if rovid:
        return (f"⚠️ **MEMORY HYGIENE: {len(rows)} violation(s)** — details at the "
                f"top of `get_context()`.\n")
    return ("## ⚠️ Memory hygiene\n"
            + "\n".join(rows)
            + "\n\n_Not urgent, but NOT postponable indefinitely either: the "
              "listed files are reloaded at EVERY session start, so their size "
              "hurts session startup. If you don't do it now, put it on a date "
              "with `track_followup` -- don't just log that you saw it. (This "
              "failure mode arose exactly so: several sessions NOTED it and none "
              "fixed it.)_\n")


def _expire_daily_handoff(body: str, today: str = "") -> str:
    """EXPIRE the dated line of the handoff section if it is not from today.

    Does NOT modify the _state.md FILE -- only the text rendered by get_context().
    Why: the morning brief must not reuse yesterday's handoff as if it were
    today's; a missing handoff has to be visible as missing (fail-closed).
    """
    import datetime as _dt
    import re as _re

    # Header words of the handoff section (Hungarian first, then English).
    if not any(_m in body for _m in ("ÁTADÓ A REGGELINEK", "MORNING HANDOFF")):
        return body
    today = today or _dt.date.today().isoformat()

    lines = body.split("\n")
    out = []
    in_sec = False
    found_dated = False
    sec_start = -1
    for idx, ln in enumerate(lines):
        if ln.startswith("## "):
            # end of the section: if we step out and there was no dated line, say so
            if in_sec and not found_dated:
                out.append("- ⚠️ **NO DATED HANDOFF LINE** — the morning brief should treat it "
                           "as missing (not invent it, not work it out itself). [v5.5 guard]")
            in_sec = any(_m in ln for _m in ("ÁTADÓ A REGGELINEK", "MORNING HANDOFF"))
            if in_sec:
                sec_start = idx
                found_dated = False
            out.append(ln)
            continue
        if in_sec:
            if "[v5.5" in ln:          # already expired/guarded line -> unchanged (idempotent)
                found_dated = True
                out.append(ln)
                continue
            m = _re.search(r"(\d{4}-\d{2}-\d{2})", ln)
            if m and ln.lstrip().startswith(("-", "*")):
                found_dated = True
                d = m.group(1)
                if d == today:
                    out.append(ln)
                else:
                    try:
                        age = (_dt.date.fromisoformat(today) - _dt.date.fromisoformat(d)).days
                        age_txt = f"{age} days old"
                    except ValueError:
                        age_txt = "unknown age"
                    out.append(
                        f"- ⚠️ **STALE HANDOFF ({age_txt}, {d}) — THERE IS NO HANDOFF LINE FOR TODAY.** "
                        f"The morning brief should treat it as MISSING: not use it and not work it "
                        f"out itself (fail-closed guard). The original line: "
                        f"`{ln.strip()[:160]}`  [v5.5 guard]"
                    )
                continue
        out.append(ln)
    if in_sec and not found_dated:
        out.append("- ⚠️ **NO DATED HANDOFF LINE** — the morning brief should treat it as missing "
                   "(not invent it, not work it out itself). [v5.5 guard]")
    return "\n".join(out)


@mcp.tool()
def get_context() -> str:
    """Get a complete overview of the owner's current situation.

    This is THE tool to call at the start of any session. Returns a structured
    summary covering:
    - Current date and upcoming deadlines (with urgency)
    - The living context file (situation, projects, what is urgent)
    - The current state file (_state.md)
    - Recent activity (last 3 days of session summaries)
    - Open predictions, if any
    - Memory-hygiene warnings, if any

    No parameters needed -- just call it and you're up to speed.
    """
    sections = []
    today = _today()
    sections.append(f"# Context -- {today}\n")

    # MEMORY HYGIENE. It lives here because EVERY session calls get_context().
    # Note: position does not protect against a SPILL (all-or-nothing: above the
    # client's size limit the WHOLE payload goes to a file). This block only
    # reaches the model if get_context() itself stays inline -- which is exactly
    # why the size of _state.md is the first thing it watches.
    try:
        _hyg = _hygiene_block()
        if _hyg:
            sections.append(_hyg)
    except Exception:
        pass

    # 1. Read the context file (the living context model)
    try:
        helyzet = _read_file(CONTEXT_FILE)
        _, body = _parse_frontmatter(helyzet)
        sections.append(f"## Current situation ({OWNER_NAME})\n" + body)
    except FileNotFoundError:
        sections.append(f"## Current situation ({OWNER_NAME})\n!! {CONTEXT_FILE} not found!")

    # 1b. NOTE: the patterns file is NOT auto-loaded here (it used to be), to keep
    # get_context() within the token limit. Use get_patterns() to load it explicitly.

    # 2. Read current state
    try:
        state = _read_file("_state.md")
        _, body = _parse_frontmatter(state)
        body = _expire_daily_handoff(body)   # expire a stale handoff line
        sections.append("## Current state\n" + body)
    except FileNotFoundError:
        sections.append("## Current state\n!! _state.md not found!")

    # 3. Recent activity (journal entries from the last 3 days).
    #    Morning/Sunday digests are skipped -- they are summaries for the owner,
    #    not for session continuity.
    activity = _get_recent_activity_internal(
        days=3,
        exclude_types={"reggeli", "vasarnapi"},
        max_highlight_chars=600,
    )
    if activity:
        sections.append("## Activity of the last 3 days\n" + activity)

    # 4. Prediction log -- only speaks up if there are open items.
    #    This is the anti-cherry-picking guard: if badly-going predictions could
    #    quietly stay open, the calibration record would flatter itself. So it
    #    lives here, where EVERY session sees it. *The parser keeps the
    #    convention, not the author.*
    try:
        _pd = _load_preds()
        _pop, _pold, _pfut = _pred_open_summary(_pd)
        if _pop:
            _l = [f"## Open predictions: {len(_pop)}"]
            for _p in sorted(_pop, key=lambda x: x.get("created", ""))[:8]:
                _l.append(f"- #{_p['id']} ({_p.get('konfidencia')}%, "
                          f"{_p.get('tipus')}){_pred_horizon_cimke(_p)} — "
                          f"{_p.get('allitas','')}")
            if _pfut:
                _l.append(f"⏳ {len(_pfut)} PENDING (horizon set, not decidable yet) — "
                          "nothing to do with these now.")
            if _pold:
                _l.append(f"⚠️ **{len(_pold)} ABANDONED** — score them "
                          "(`score_prediction`), even if it is uncomfortable: an unscored "
                          "prediction DISTORTS the record. If it is just not due yet, "
                          "give it a horizon: `defer_prediction`.")
            _l.append("_Record: `get_calibration()`._")
            sections.append("\n".join(_l))
    except Exception:
        pass

    return "\n\n".join(sections)


@mcp.tool()
def get_patterns(part: int = 1) -> str:
    """Read the cross-session patterns and lessons file (PATTERNS_FILE).

    Kept out of get_context() to keep that within the token limit.

    Recommended: call this at the start of every session, alongside get_context().
    Typically contains: how the owner likes to work, troubleshooting heuristics,
    and Claude-about-Claude patterns (introspection limits, stale cache,
    procrastinated maintenance, etc.).

    If the file would exceed the client's size limit in one piece, it comes
    back IN PARTS (split at H2 headers), with a section map and a loud
    "this is part i/N" notice -- because a spilled result means the tool ran
    but NOTHING of the content reached the model.

    Args:
        part: Which part you need (from 1). The header gives the section map,
            so you can request the part you need directly.

    Returns:
        The requested part (or the whole file if it fits), without frontmatter.
    """
    try:
        content = _read_file(PATTERNS_FILE)
        _, body = _parse_frontmatter(content)
    except FileNotFoundError:
        return f"!! {PATTERNS_FILE} not found!"

    parts = _split_md_by_h2(body, _PATTERNS_CHUNK_KAR)
    total = len(parts)
    if total == 1:
        out = "## Patterns\n" + body
        _stamp_self_knowledge("get_patterns", len(out),
                              claude_szekcio=bool(_CLAUDE_SECTION_RE.search(body)))
        return out

    # SEVERAL PARTS. Without this the whole file would spill and NOTHING would
    # reach the model.
    i = 1 if not isinstance(part, int) else max(1, min(part, total))
    chunk = parts[i - 1]
    head = [f"## Patterns — **part {i}/{total}** ({len(chunk)//1000} KB · the whole file is "
            f"{len(body)//1000} KB, so it arrives in parts)",
            "",
            # Paging alone does NOT mean everything is fine. Paging is exactly what
            # hides the overgrowth symptom -- so it speaks up here too.
            _hygiene_block(rovid=True),
            "**Section map** (request directly: `get_patterns(part=N)`):",
            _patterns_map(parts), ""]
    foot = []
    if total > 1:
        hatra = [str(x) for x in range(1, total + 1) if x != i]
        foot = ["", "---",
                f"⚠️ **THIS IS NOT THE WHOLE FILE** — this is part {i}/{total}. Remaining: "
                f"{', '.join(hatra)}. · `get_patterns(part=N)`",
                "_It is paged because in one piece the file exceeds the client's size limit "
                "and SPILLS TO A FILE — then the tool runs, but the content does not reach you. "
                "Better to arrive in parts than not at all._"]
    out = "\n".join(head) + chunk + "\n".join(foot)
    _stamp_self_knowledge(f"get_patterns#{i}/{total}", len(out),
                          claude_szekcio=bool(_CLAUDE_SECTION_RE.search(chunk)))
    return out


# Markers of a closed/past line inside the deadline section (case-sensitive;
# Hungarian first, then English).
_DEADLINE_CLOSED_RE = re.compile(
    r"(\u2705|MEGT\u00d6RT\u00c9NT|MEGTORTENT|TELJES\u00cdTVE|TELJESITVE|"
    r"LEZ\u00c1RVA|LEZARVA|K\u00c9SZ\b|KESZ\b|KIFIZETVE|ELMENT|"
    r"HAPPENED|COMPLETED|CLOSED|DONE\b|PAID)")


def _deadline_section_health(deadline_body: str) -> dict:
    """Measure whether the deadline section itself is fresh enough for an
    EMPTY answer to be believable.

    WHY: get_deadlines() once returned ZERO deadlines, SILENTLY -- while the
    section held a dozen dated lines, ALL closed, the newest six weeks old, and
    the calendar had real upcoming items. The empty answer did not mean "nothing
    to do" but "nobody has refilled the section for weeks" -- and the
    difference was visible nowhere. Same principle as the advisory block:
    THE LIMIT SAYS IT WAS A LIMIT.
    """
    date_pattern = re.compile(r"(\d{4}-\d{2}-\d{2})")
    total = closed = 0
    newest = None
    for line in deadline_body.splitlines():
        if _is_table_line(line) or _is_metadata_line(line):
            continue
        hits = date_pattern.findall(line)
        if not hits:
            continue
        total += 1
        if _DEADLINE_CLOSED_RE.search(line):
            closed += 1
        for d in hits:
            if newest is None or d > newest:
                newest = d
    kor = _days_until(newest) if newest else None
    return {"total": total, "closed": closed, "newest": newest, "newest_days": kor}


def _deadline_staleness_note(deadline_body: str) -> str:
    """Loud warning if an EMPTY deadline list is suspicious."""
    h = _deadline_section_health(deadline_body)
    if not h["total"]:
        return ("\n⚠️ FRESHNESS GUARD: there is NOT A SINGLE dated line in the "
                "deadline section.\n   An empty answer here is no proof that there is nothing to do.\n"
                f"   ➜ Check {OWNER_NAME}'s CALENDAR (list_events), and write any real deadline\n"
                "     into the 'URGENT / UPCOMING DEADLINES' section.\n")
    kor = h["newest_days"]
    reg = kor is not None and kor < -14
    mind_lezart = h["closed"] >= h["total"]
    if not (reg or mind_lezart):
        return ""
    reszek = []
    if mind_lezart:
        reszek.append("all of its %d dated lines are ALREADY CLOSED (✅/HAPPENED/COMPLETED)"
                      % h["total"])
    else:
        reszek.append("%d of its dated lines, %d already closed" % (h["total"], h["closed"]))
    if kor is not None and kor < 0:
        reszek.append("its newest date was %d days ago (%s)" % (-kor, h["newest"]))
    return ("\n⚠️ FRESHNESS GUARD: the deadline section MAY BE STALE --\n"
            "   " + ", ".join(reszek) + ".\n"
            "   An \"empty deadline list\" is NOT the same as \"nothing to do\": maybe\n"
            "   nobody has refilled the section.\n"
            f"   ➜ Check {OWNER_NAME}'s CALENDAR (list_events) before saying it is clear.\n")


@mcp.tool()
def get_deadlines() -> str:
    """Get upcoming deadlines sorted by urgency.

    Returns a structured list of deadlines with:
    - Days remaining (negative = overdue!)
    - Subject/project name
    - What's due
    - Urgency level

    SOURCE: only the deadline section of the context file ("URGENT / UPCOMING
    DEADLINES", or the Hungarian "SURGOS / KOZELGO HATARIDOK"). Full ISO dates
    in other sections are REFERENCES (when something happened), not deadlines.
    A FUTURE date outside the section appears in a separate
    "[?] Might be in the wrong place" advisory block, so a real deadline
    written in the wrong place is never lost silently.

    Filters out: frontmatter lines, completed items, table rows, duplicate dates.
    Great for proactive planning and daily check-ins.
    """
    try:
        helyzet = _read_file(CONTEXT_FILE)
    except FileNotFoundError:
        return json.dumps({"error": f"{CONTEXT_FILE} not found"})

    body = _strip_frontmatter(helyzet)

    # WHITELIST -- only the deadline section counts as a deadline source.
    deadline_body, other_body = _extract_deadline_section(body)
    scoped = bool(other_body)

    seen_dates = set()
    suppressed: list = []
    body_refs: list = []
    deadlines = _scan_deadline_dates(deadline_body, seen_dates, min_days=-7,
                                     suppressed=suppressed,
                                     allow_head_duplicates=True,
                                     require_head=True,
                                     body_refs=body_refs)
    # FUTURE dates outside the section do not vanish silently, but they do not
    # mix with the real deadlines either -- they go to a separate advisory block.
    misfiled = (_scan_deadline_dates(other_body, seen_dates, min_days=1)
                if scoped else [])

    deadlines.sort(key=lambda d: d["days_remaining"])
    misfiled.sort(key=lambda d: d["days_remaining"])

    if not deadlines and not misfiled:
        return ("No known upcoming deadline in the context file. "
                "Check the calendar too!\n"
                + _deadline_staleness_note(deadline_body))

    result = "# Deadlines\n\n"
    if deadlines:
        for d in deadlines:
            result += (f"{d['urgency']} | {d['date']} ({d['days_remaining']} days)"
                       f" | {d['description']}\n")
    else:
        result += ("No known upcoming deadline in the deadline section.\n"
                   + _deadline_staleness_note(deadline_body))

    if misfiled:
        result += (
            "\n## [?] Might be in the wrong place -- NOT treated as a deadline\n"
            "I found a future ISO date OUTSIDE the deadline section. By convention\n"
            "every full ISO date there is a reference (when something happened).\n"
            "If one below is a REAL deadline, move it into the 'URGENT / UPCOMING DEADLINES'\n"
            "section -- otherwise get_deadlines will not keep track of it.\n"
        )
        for d in misfiled:
            result += (f"  - {d['date']} ({d['days_remaining']} days)"
                       f" | {d['description']}\n")

    # The position anchor must not be silent either. Future ISO dates in the
    # BODY of the deadline-section bullets are treated as references -- but
    # listed, so a REAL deadline that is merely in the wrong position is not
    # lost silently. Dates already reported as deadlines are not repeated (noise).
    if body_refs:
        dl_dates = {d["date"] for d in deadlines}
        seen_ref = set()
        uniq_refs = []
        for r in body_refs:
            if r["date"] in dl_dates or r["date"] in seen_ref:
                continue
            seen_ref.add(r["date"])
            uniq_refs.append(r)
        if uniq_refs:
            uniq_refs.sort(key=lambda d: d["days_remaining"])
            result += (
                "\n## [?] Future date in the BODY of a deadline-section bullet\n"
                "Treated as a REFERENCE, not a deadline: by convention the deadline\n"
                "stands at the START of the bullet (`- **YYYY-MM-DD: ...`); an ISO date\n"
                "in the body is a cross-reference (history, reasoning, file name, period bound).\n"
                "If one below is a REAL, separate deadline, put it at the start of its OWN bullet --\n"
                "otherwise get_deadlines will not keep track of it.\n"
            )
            for d in uniq_refs:
                result += (f"  - {d['date']} ({d['days_remaining']} days)"
                           f" | {d['description']}\n")

    # The completed-filter must NOT be silent. If a line carrying a future date
    # was dropped as completed, we say so -- otherwise a real deadline quietly
    # vanishes (a word like "elkészíti" once swallowed a real deadline).
    if suppressed:
        suppressed.sort(key=lambda d: d["days_remaining"])
        result += (
            "\n## [~] Filtered out as completed, BUT it carries a future date\n"
            "These lines contain a completed-word (✅ / done / submitted / closed\n"
            "etc.), so they are NOT treated as deadlines. If one of them IS a real\n"
            "deadline, remove the completed-word from it, or split it into two lines.\n"
        )
        for d in suppressed:
            result += (f"  - {d['date']} ({d['days_remaining']} days)"
                       f" | {d['description']}\n")

    return result


@mcp.tool()
def get_state() -> str:
    """Get the current state file (_state.md).

    Returns the full _state.md content including:
    - What's been done
    - What's the next step
    - Whether there's a blocker
    - Open questions

    Use this to continue work from where the last session left off.
    """
    try:
        return _read_file("_state.md")
    except FileNotFoundError:
        return "!! _state.md not found!"


@mcp.tool()
def get_open_threads() -> str:
    """Get a structured overview of currently open threads.

    Aggregates from _state.md (Blocker, Open questions, Active development
    plans sections) and the context file (an "Open Claude question for the
    owner" subsection, if present). Hungarian section names are accepted too
    (Nyitott kérdések, Aktív fejlesztési tervek, ...).

    Categories returned:
    - 🚫 Blocker (from the _state.md "Blocker" section)
    - 🔴 Waiting for the owner's answer (Open questions > Questions needing
      the owner's decision, + the open question from the context file)
    - 🕐 Tech item / passive observation (filters out items marked closed / ✅)
    - 💡 Active development plans (the "To implement" subsection only)

    Complements get_context() (full state) and get_deadlines() (date-driven).
    """
    output = ["# Open threads overview"]

    try:
        state = _read_file("_state.md")
    except FileNotFoundError:
        state = ""
    try:
        helyzet = _read_file(CONTEXT_FILE)
    except FileNotFoundError:
        helyzet = ""

    def _section_l2(text: str, header_substring: str) -> str:
        """Extract a level-2 section by partial header match. Body only (no header line)."""
        pattern = rf"\n##\s+[^\n]*{re.escape(header_substring)}[^\n]*\n(.*?)(?=\n##\s|\Z)"
        m = re.search(pattern, "\n" + text, re.DOTALL)
        return m.group(1).strip() if m else ""

    def _subsection_l3(section_body: str, header_substring: str) -> str:
        """Extract a level-3 subsection by partial header match from a body string."""
        pattern = rf"\n###\s+[^\n]*{re.escape(header_substring)}[^\n]*\n(.*?)(?=\n###\s|\Z)"
        m = re.search(pattern, "\n" + section_body, re.DOTALL)
        return m.group(1).strip() if m else ""

    def _truncate(text: str, limit: int, suffix: str = "...(truncated)") -> str:
        if len(text) <= limit:
            return text
        return text[:limit].rstrip() + " " + suffix

    # 1. Blocker
    output.append("\n## 🚫 Blocker")
    blocker = _section_l2(state, "Blocker")
    if not blocker or "Nincs aktív blocker" in blocker[:200] or "No active blocker" in blocker[:200]:
        output.append("_No active blocker._")
    else:
        output.append(_truncate(blocker, 800))

    # 2. Waiting for the owner's answer
    output.append(f"\n## 🔴 Waiting for {OWNER_NAME}'s answer")
    nyitott_block = _section_l2(state, "Nyitott kérdések") or _section_l2(state, "Open questions")
    owner_sub = (_subsection_l3(nyitott_block, "A tulajdonos döntését igénylő kérdések")
                 or _subsection_l3(nyitott_block, "Questions needing the owner's decision"))
    if not owner_sub or "Nincs aktív kérdés" in owner_sub[:200] or "No active question" in owner_sub[:200]:
        output.append(f"_No explicit open question for {OWNER_NAME} in the state file._")
    else:
        output.append(_truncate(owner_sub, 700))

    # Open question for the owner from the context file
    karrier_m = re.search(
        r"###\s*(?:Nyitott Claude-kérdés a tulajdonosnak|Open Claude question for the owner)[^\n]*\n(.*?)(?=\n###\s|\n##\s|\Z)",
        helyzet,
        re.DOTALL,
    )
    if karrier_m:
        kt = karrier_m.group(1).strip()
        if kt:
            output.append(f"\n**Open question for {OWNER_NAME}**:")
            output.append(_truncate(kt, 500))

    # 3. Tech item / passive observation
    output.append("\n## 🕐 Tech item / passive observation")
    tech_sub = _subsection_l3(nyitott_block, "Tech-item")
    if tech_sub:
        # Filter: skip the closed / ✅ bullets (closed items)
        kept_bullets = []
        for bullet in re.split(r"\n(?=- )", tech_sub):
            b = bullet.strip()
            if not b:
                continue
            if "LEZÁRVA" in b or "CLOSED" in b or b.startswith("- ✅"):
                continue
            kept_bullets.append(b)
        if kept_bullets:
            joined = "\n\n".join(kept_bullets)
            output.append(_truncate(joined, 1500))
        else:
            output.append("_All tech items are closed._")
    else:
        output.append("_No tech-item entry._")

    # 4. Active development plans ("To implement" subsection)
    output.append("\n## 💡 Active development plans (To implement)")
    tervek = _section_l2(state, "Aktív fejlesztési tervek") or _section_l2(state, "Active development plans")
    if tervek:
        mvg = _subsection_l3(tervek, "Megvalósítandó") or _subsection_l3(tervek, "To implement")
        if mvg:
            output.append(_truncate(mvg, 1200, "...(details: see the full section in _state.md)"))
        else:
            output.append("_No \"To implement\" subsection found._")
    else:
        output.append("_No \"Active development plans\" section._")

    output.append("")  # trailing newline
    return "\n".join(output)


@mcp.tool()
def update_state(section: str, content: str) -> str:
    """Update a section of the _state.md file.

    Instead of rewriting the whole file, specify which section to update.
    The tool reads the current state, replaces the section, and writes back.

    Args:
        section: Section header to find and replace (e.g., "Where I am", "Next step", "Blocker")
        content: New content for that section

    Returns:
        Confirmation message
    """
    try:
        state = _read_file("_state.md")
    except FileNotFoundError:
        return "!! _state.md not found!"

    # Strip header from content if caller accidentally included it.
    # Prevents header duplication when caller copies a whole section including `## SECTION` line.
    content_lines = content.lstrip().splitlines()
    if content_lines and content_lines[0].lstrip().startswith(f"## {section}"):
        content = "\n".join(content_lines[1:]).lstrip("\n")

    pattern = rf"(## {re.escape(section)}.*?)(?=\n## |\Z)"
    match = re.search(pattern, state, re.DOTALL)
    if not match:
        return f"!! Section '{section}' not found in _state.md."

    new_section = f"## {section}\n{content}"
    new_state = state[:match.start()] + new_section + state[match.end():]

    new_state = re.sub(
        r"updated: \d{4}-\d{2}-\d{2}",
        f"updated: {_today()}",
        new_state,
    )

    _write_file("_state.md", new_state)
    return f"OK: Section '{section}' updated in _state.md."


@mcp.tool()
def log_entry(title: str, content: str, entry_type: str = "session") -> str:
    """Create a new log entry in the naplo/ directory.

    Args:
        title: Short title for the log entry (used in the heading)
        content: Full content of the log entry (markdown)
        entry_type: Type of entry, used in the file name: "session",
            "dontes" (decision), "kutatas" (research), "reflexio" (reflection)

    Returns:
        Path of the created file
    """
    today = _today()

    existing = _list_files("naplo", f"{today}_{entry_type}_*.md")
    entry_num = len(existing) + 1

    filename = f"naplo/{today}_{entry_type}_{entry_num}.md"

    entry = f"""---
date: {today}
session: {entry_num}
type: {entry_type}
---

# {title}

{content}
"""
    path = _write_file(filename, entry)
    return f"OK: Log entry created: {filename}"


# --- Session Summaries for Cross-Session Continuity ---


def _truncate_highlights(text: str, file_ref: str, max_chars: int | None) -> str:
    """Cut an over-long highlights field and add a pointer to the full entry.

    Why: get_context() loads the highlights VERBATIM into its "last 3 days"
    section at EVERY session start, so one long essay burdens every session
    for three days. The convention (log_session_summary docstring) is 2-4
    sentences; this cut makes the convention STRUCTURAL instead of relying on
    discipline. get_recent_activity() passes None -> no cut.

    Cuts at a sentence boundary if there is one above 60% of the window;
    otherwise at a word boundary.
    """
    if not max_chars or len(text) <= max_chars:
        return text
    window = text[:max_chars]
    cut = max(window.rfind(". "), window.rfind("! "), window.rfind("? "))
    if cut >= int(max_chars * 0.6):
        cut += 1
    else:
        cut = window.rfind(" ")
    if cut <= 0:
        cut = max_chars
    return window[:cut].rstrip() + f" [...] (full: {file_ref})"


def _get_recent_activity_internal(
    days: int = 3,
    exclude_types: set | None = None,
    max_highlight_chars: int | None = None,
) -> str:
    """Internal helper: get recent journal entries as formatted string.

    Reads from naplo/. exclude_types skips given session types (e.g.
    {"reggeli", "vasarnapi"} for get_context). max_highlight_chars: if given,
    highlights longer than this are cut with a pointer to the log file
    (get_context: 600; get_recent_activity: None)."""
    root = Path(MEMORIA_ROOT) / "naplo"
    if not root.exists():
        return ""

    exclude_types = exclude_types or set()
    cutoff = datetime.now() - timedelta(days=days)
    summaries = []

    for md_file in sorted(root.glob("*.md")):
        if md_file.name.startswith("_"):
            continue
        try:
            content = md_file.read_text(encoding="utf-8")
            meta, body = _parse_frontmatter(content)

            file_date = _parse_date(meta.get("date", ""))
            if file_date and file_date >= cutoff:
                session_type = meta.get("type", meta.get("session_type", "?"))
                if session_type in exclude_types:
                    continue
                highlights = meta.get("highlights", "")

                if not highlights:
                    for line in body.splitlines():
                        if line.strip() and not line.startswith("#"):
                            highlights = line.strip()
                            break

                summaries.append({
                    "date": meta.get("date", "?"),
                    "type": session_type,
                    "highlights": highlights,
                    "file": str(md_file.relative_to(Path(MEMORIA_ROOT))),
                    "body": body,
                })
        except Exception:
            continue

    if not summaries:
        return ""

    result_lines = []
    current_date = ""
    for s in summaries:
        if s["date"] != current_date:
            current_date = s["date"]
            result_lines.append(f"\n### {current_date}")
        hl = _truncate_highlights(s["highlights"], s["file"], max_highlight_chars)
        result_lines.append(f"- **[{s['type']}]** {hl}")

    return "\n".join(result_lines).strip()


@mcp.tool()
def log_session_summary(
    session_type: str,
    highlights: str,
    context_updates: list[str] | None = None,
    open_items: list[str] | None = None,
) -> str:
    """Log a structured session summary for cross-session continuity.

    Call this at the END of every session (interactive, automated, autonomous, etc.)
    so that subsequent sessions can quickly see what happened.

    This is different from log_entry() -- log_entry creates detailed narrative logs,
    while this creates lightweight, structured summaries optimized for quick scanning.

    Args:
        session_type: Type of session (used in the file name and for filtering).
            Conventional values:
            "reggeli" -- morning summary/briefing (skipped by get_context)
            "vasarnapi" -- weekly/Sunday recap (skipped by get_context)
            "interaktiv" -- interactive chat with the owner
            "agencia" -- autonomous (unattended) session
            "tanulmany" -- study session
            "munka" -- work-related session
        highlights: What happened in 2-4 sentences. Be specific and actionable.
            HARD LIMIT: get_context() CUTS this field at 600 characters (with a
            pointer to the log file), because it is loaded at EVERY session start
            for 3 days. Put the detailed narrative not here but in a "## Details"
            section of the same log file (append_memory_file after
            log_session_summary).
            Good: "Spanish quiz 1 drill: knew 52 of 70 words, weak area: past tense"
            Bad: "We studied Spanish"
        context_updates: Optional list of things that changed and should be noted.
            Example: ["Spanish quiz 1 date moved: 04-09 -> 04-11", "Shed project step 2/4 done"]
        open_items: Optional list of things left unfinished for the next session.
            Example: ["Spanish: review the past-tense chapter", "Shed: start step 3"]

    Returns:
        Confirmation with file path
    """
    today = _today()

    existing = _list_files("naplo", f"{today}_{session_type}*.md")
    if existing:
        num = len(existing) + 1
        filename = f"naplo/{today}_{session_type}_{num}.md"
    else:
        filename = f"naplo/{today}_{session_type}.md"

    # NOTE: the section headers written below are a storage format that
    # suggest_context_updates() parses back; they are kept as they are.
    ctx_section = ""
    if context_updates:
        ctx_items = "\n".join(f"- {item}" for item in context_updates)
        ctx_section = f"\n## Kontextus valtozasok\n{ctx_items}\n"

    open_section = ""
    if open_items:
        open_list = "\n".join(f"- {item}" for item in open_items)
        open_section = f"\n## Nyitott elemek\n{open_list}\n"

    # highlights go into the frontmatter (explicit, machine-readable) -- on one
    # line, so the line-based frontmatter parser never truncates it.
    hl_oneline = " ".join(highlights.split())
    entry = f"""---
date: {today}
session_type: {session_type}
highlights: {hl_oneline}
---

# Session: {session_type} -- {today}
{ctx_section}{open_section}"""

    _write_file(filename, entry)
    return f"OK: Session summary created: {filename}"


@mcp.tool()
def get_recent_activity(days: int = 3) -> str:
    """Get a quick overview of session activity from the last N days.

    Returns structured summaries of all sessions, grouped by date.
    Use this to understand what happened recently without reading full logs.

    Args:
        days: How many days back to look (default: 3)

    Returns:
        Formatted summary of recent sessions, or a message if none found.
    """
    result = _get_recent_activity_internal(days=days)
    if not result:
        return f"No log entries from the last {days} days. (Hint: the naplo/ folder is empty or does not exist yet.)"

    return f"# Activity of the last {days} days\n\n{result}"


# --- Reflection and Context Intelligence ---


def _collect_relevant_content(query: str, max_excerpts_per_file: int = 10) -> list[dict]:
    """Internal helper: search all memory files and return structured results with context."""
    query_lower = query.lower()
    results = []
    root = Path(MEMORIA_ROOT)

    for md_file in root.rglob("*.md"):
        rel_path = str(md_file.relative_to(root))
        try:
            content = md_file.read_text(encoding="utf-8")
        except Exception:
            continue

        name_match = query_lower in rel_path.lower()
        content_lower = content.lower()

        if query_lower not in content_lower and not name_match:
            continue

        meta, body = _parse_frontmatter(content)
        lines = body.splitlines()

        # Collect matching lines with surrounding context (1 line before/after)
        relevant_lines = []
        for i, line in enumerate(lines):
            if query_lower in line.lower():
                context_start = max(0, i - 1)
                context_end = min(len(lines), i + 2)
                snippet = "\n".join(lines[context_start:context_end]).strip()
                if snippet not in [r["snippet"] for r in relevant_lines]:
                    relevant_lines.append({
                        "line_num": i + 1,
                        "snippet": snippet,
                    })
                if len(relevant_lines) >= max_excerpts_per_file:
                    break

        results.append({
            "file": rel_path,
            "name_match": name_match,
            "meta": meta,
            "excerpts": relevant_lines,
            "total_mentions": content_lower.count(query_lower),
        })

    # Sort: most mentions first, then name matches
    results.sort(key=lambda r: (-r["total_mentions"], -r["name_match"]))
    return results


@mcp.tool()
def reflect(topic: str) -> str:
    """Synthesize everything the memory system knows about a topic.

    Unlike search_memory() which returns raw matches, reflect() provides
    a structured overview:
    - Which files mention the topic and how prominently
    - A chronological view from session logs (if any)
    - Key excerpts organized by source type (context, projects, logs, sessions)
    - Open questions or gaps

    Use this when you need a complete picture of something before making
    decisions or giving the owner advice.

    Args:
        topic: The topic to reflect on (e.g., "thesis", "garden",
               "memoria-mcp", "deadline")

    Returns:
        Structured synthesis of everything known about the topic
    """
    results = _collect_relevant_content(topic)

    if not results:
        return f"# Reflection: '{topic}'\n\nNo match in the memory system. The topic is unknown or not documented yet."

    # Categorize by source type
    categories = {
        "kontextus": [],   # Living context files
        "projektek": [],   # Project docs
        "naplo": [],       # Log entries (chronological)
        "sessions": [],    # Session summaries
        "eszkozok": [],    # Tool docs
        "archiv": [],      # Archived content
        "egyeb": [],       # Root files, other
    }

    for r in results:
        path = r["file"]
        categorized = False
        for cat_key in categories:
            if path.startswith(cat_key):
                categories[cat_key].append(r)
                categorized = True
                break
        if not categorized:
            categories["egyeb"].append(r)

    # Build output
    output = f"# Reflection: '{topic}'\n\n"
    total_files = len(results)
    total_mentions = sum(r["total_mentions"] for r in results)
    output += f"**{total_files} file(s)**, **{total_mentions} mention(s)** in total\n\n"

    # Context section (most important - current state)
    if categories["kontextus"]:
        output += "## Current context\n"
        for r in categories["kontextus"]:
            output += f"### {r['file']}\n"
            for exc in r["excerpts"][:3]:
                output += f"> {exc['snippet']}\n\n"

    # Projects
    if categories["projektek"]:
        output += "## Projects\n"
        for r in categories["projektek"]:
            output += f"### {r['file']} ({r['total_mentions']} mentions)\n"
            for exc in r["excerpts"][:3]:
                output += f"> {exc['snippet']}\n\n"

    # Chronological view from logs
    if categories["naplo"]:
        output += "## Log (chronological)\n"
        # Logs are already sorted by filename (date-based)
        for r in categories["naplo"]:
            date = r["meta"].get("date", "?")
            output += f"### {date} — {r['file']}\n"
            for exc in r["excerpts"][:2]:
                output += f"> {exc['snippet']}\n\n"

    # Session summaries
    if categories["sessions"]:
        output += "## Session summaries\n"
        for r in categories["sessions"]:
            date = r["meta"].get("date", "?")
            stype = r["meta"].get("session_type", "?")
            output += f"- **{date} [{stype}]**: "
            if r["excerpts"]:
                output += r["excerpts"][0]["snippet"].replace("\n", " ")[:150]
            output += "\n"
        output += "\n"

    # Tools/infrastructure
    if categories["eszkozok"]:
        output += "## Tools / infrastructure\n"
        for r in categories["eszkozok"]:
            output += f"- {r['file']} ({r['total_mentions']} mentions)\n"
        output += "\n"

    # Archived content
    if categories["archiv"]:
        output += "## Archive\n"
        for r in categories["archiv"]:
            output += f"- {r['file']} ({r['total_mentions']} mentions)\n"
        output += "\n"

    # Other (root files like _state.md, _config.md, etc.)
    if categories["egyeb"]:
        output += "## Other sources\n"
        for r in categories["egyeb"]:
            output += f"- {r['file']} ({r['total_mentions']} mentions)\n"
        output += "\n"

    return output


@mcp.tool()
def suggest_context_updates() -> str:
    """Compare recent session activity with the living context model and suggest updates.

    Analyzes the last 7 days of session summaries and log entries, then compares
    them with the current context file to find:
    - Context updates logged in sessions that haven't been applied yet
    - Open items that might need attention
    - Potential staleness in the context model (e.g., dates that have passed)

    Filters out items that have been acknowledged via acknowledge_updates().
    Shows a count of filtered items so you know the tracker is working.

    Returns a list of suggested updates. Does NOT modify any files -- the caller
    decides which suggestions to apply (via update_context()).

    Great for maintenance sessions or as part of a daily routine.
    """
    # Load processed items tracker
    processed = _load_processed()
    processed_sessions = set(processed.get("processed_sessions", []))
    resolved_items_texts = {
        (ri["text"] if isinstance(ri, dict) else ri)
        for ri in processed.get("resolved_items", [])
    }

    suggestions = []

    # 1. Collect context_updates from recent session summaries (naplo/)
    root = Path(MEMORIA_ROOT) / "naplo"
    unapplied_updates = []
    open_items_all = []
    filtered_updates_count = 0
    filtered_items_count = 0

    if root.exists():
        cutoff = datetime.now() - timedelta(days=7)
        for md_file in sorted(root.glob("*.md")):
            # Skip tracker file
            if md_file.name == "_processed.json":
                continue
            try:
                content = md_file.read_text(encoding="utf-8")
                meta, body = _parse_frontmatter(content)

                file_date = _parse_date(meta.get("date", ""))
                if not file_date or file_date < cutoff:
                    continue

                # Accept the English section names too (Hungarian takes precedence).
                for _hu, _en in (("## Kontextus valtozasok", "## Context changes"),
                                 ("## Nyitott elemek", "## Open items")):
                    if _hu not in body and _en in body:
                        body = body.replace(_en, _hu)

                # Extract context updates section
                if "## Kontextus valtozasok" in body:
                    # Skip if this session's updates are already processed
                    if md_file.name in processed_sessions:
                        # Count how many we're filtering
                        section = body.split("## Kontextus valtozasok")[1]
                        if "## " in section[1:]:
                            section = section[:section.index("## ", 1)]
                        for line in section.strip().splitlines():
                            line = line.strip().lstrip("- ")
                            if line:
                                filtered_updates_count += 1
                    else:
                        section = body.split("## Kontextus valtozasok")[1]
                        if "## " in section[1:]:
                            section = section[:section.index("## ", 1)]
                        for line in section.strip().splitlines():
                            line = line.strip().lstrip("- ")
                            if line:
                                unapplied_updates.append({
                                    "update": line,
                                    "source": str(md_file.name),
                                    "date": meta.get("date", "?"),
                                })

                # Extract open items section
                if "## Nyitott elemek" in body:
                    section = body.split("## Nyitott elemek")[1]
                    if "## " in section[1:]:
                        section = section[:section.index("## ", 1)]
                    for line in section.strip().splitlines():
                        line = line.strip().lstrip("- ")
                        if line:
                            if line in resolved_items_texts or any(_fuzzy_match(line, ri) for ri in resolved_items_texts):
                                filtered_items_count += 1
                            else:
                                open_items_all.append({
                                    "item": line,
                                    "source": str(md_file.name),
                                    "date": meta.get("date", "?"),
                                })
            except Exception:
                continue

    # 2. Check for stale dates in the context file
    stale_dates = []
    try:
        helyzet = _read_file(CONTEXT_FILE)
        body = _strip_frontmatter(helyzet)
        date_pattern = re.compile(r"(\d{4}-\d{2}-\d{2})")
        today = datetime.now()

        for line in body.splitlines():
            if _is_completed_line(line):
                continue
            if _is_completion_date_line(line):
                continue
            dates = date_pattern.findall(line)
            for date_str in dates:
                d = _parse_date(date_str)
                if d and d < today - timedelta(days=1):
                    clean_line = line.strip().lstrip("-\u2022*# ").replace("**", "")
                    stale_dates.append({
                        "date": date_str,
                        "days_ago": (today - d).days,
                        "line": clean_line[:120],
                    })
    except FileNotFoundError:
        suggestions.append(f"!! {CONTEXT_FILE} not found!")

    # 2.5 Deduplicate open items by fuzzy similarity
    if open_items_all:
        deduplicated = []
        for item in open_items_all:
            merged = False
            for existing in deduplicated:
                if _fuzzy_match(item["item"], existing["item"]):
                    # Keep the more recent one as the "main" item
                    if item["date"] > existing["date"]:
                        existing["item"] = item["item"]
                        existing["source"] = item["source"]
                        existing["date"] = item["date"]
                    existing["similar_count"] = existing.get("similar_count", 0) + 1
                    merged = True
                    break
            if not merged:
                deduplicated.append(dict(item))  # copy to avoid mutation
        open_items_all = deduplicated

    # 3. Build output
    output = "# Context update suggestions\n\n"

    # Show filter stats if anything was filtered
    if filtered_updates_count > 0 or filtered_items_count > 0:
        output += f"_({filtered_updates_count} processed update(s) and {filtered_items_count} resolved open item(s) filtered out)_\n\n"

    if unapplied_updates:
        output += "## Updates from sessions\n"
        output += "These changes were recorded in session summaries but have not reached the context file yet:\n\n"
        for u in unapplied_updates:
            output += f"- [{u['date']}] {u['update']} _(source: {u['source']})_\n"
        output += "\n"
    else:
        output += "## Updates from sessions\nNo unprocessed update.\n\n"

    if open_items_all:
        output += "## Open items (from earlier sessions)\n"
        output += "These have not been closed yet:\n\n"
        for item in open_items_all:
            similar = item.get("similar_count", 0)
            suffix = f" _(+ {similar} similar)_" if similar > 0 else ""
            output += f"- [{item['date']}] {item['item']} _(source: {item['source']})_{suffix}\n"
        output += "\n"

    if stale_dates:
        output += "## Potentially stale dates\n"
        output += "These dates have passed but are not marked 'done':\n\n"
        # Only show the most relevant (within 14 days past)
        recent_stale = [s for s in stale_dates if s["days_ago"] <= 14]
        if recent_stale:
            for s in recent_stale:
                output += f"- {s['date']} (passed {s['days_ago']} days ago): {s['line']}\n"
        else:
            output += "No recently stale date (within 14 days).\n"
        output += "\n"

    if not unapplied_updates and not open_items_all and not stale_dates:
        output += "All good! The context looks up to date.\n"

    return output


@mcp.tool()
def acknowledge_updates(
    processed_sessions: list[str] | None = None,
    resolved_items: list[str] | None = None,
) -> str:
    """Mark session context_updates as processed and/or open items as resolved.

    Call this after applying context updates or when open items are no longer
    relevant. This prevents suggest_context_updates() from showing stale items.

    Args:
        processed_sessions: List of session filenames whose context_updates have
            been applied (e.g., ["2026-04-02_agencia.md", "2026-04-02_interaktiv.md"]).
            These sessions' context-changes sections will be skipped in
            future suggest_context_updates() calls.
        resolved_items: List of open item texts to mark as resolved. Must match
            the exact text of the open item as it appears in the session summary
            (e.g., ["Create the heartbeat scheduled task (needs an interactive session)"]).

    Returns:
        Confirmation with counts
    """
    data = _load_processed()

    added_sessions = 0
    added_items = 0

    if processed_sessions:
        existing = set(data.get("processed_sessions", []))
        for s in processed_sessions:
            if s not in existing:
                existing.add(s)
                added_sessions += 1
        data["processed_sessions"] = sorted(existing)

    if resolved_items:
        existing_texts = {
            (ri["text"] if isinstance(ri, dict) else ri)
            for ri in data.get("resolved_items", [])
        }
        for item in resolved_items:
            if item not in existing_texts:
                data.setdefault("resolved_items", []).append({
                    "text": item,
                    "resolved_on": _today(),
                })
                added_items += 1

    data["last_updated"] = _today()
    _save_processed(data)

    return (
        f"OK: {added_sessions} session(s) marked as processed, "
        f"{added_items} open item(s) resolved. "
        f"Total: {len(data['processed_sessions'])} session(s), "
        f"{len(data['resolved_items'])} resolved item(s)."
    )


# --- PROVENANCE in search: the "I only find snapshots" failure mode ---
# WHY: search_memory used to sweep the WHOLE memory with `rglob("*.md")` --
# where most files are under `archiv/` -- and returned matches WITHOUT any
# provenance mark. Two measured consequences:
#
# (1) ⚠️ THE COSTLIER ONE: a query returned only stale snapshots (archive copies
#     and a backup saved next to the live file) and NOTHING from the live file.
#     Reason: the live claim had been CORRECTED (its wording changed), while the
#     snapshots kept the OLD wording, which matched the old query better.
#     ➜ So **the better the fix, the more the search drifts towards snapshots.**
#     A later instance would literally see only stale text, and NOTHING would
#     warn it. The purest form of the stale-cache failure: not a bad note, but
#     the INSTRUMENT ranking copies of the past above the present.
#
# (2) SPILL: a broad query would have returned hundreds of file blocks -- that
#     certainly spills to a file, and then the tool "runs" but the content does
#     NOT reach the model.
#
# THE THREE PRINCIPLES OF THE FIX (all existing principles of the system):
#   a) Narrow the scope to LIVE + `naplo/` (a whitelist, like get_deadlines).
#      `archiv/` is covered by the DEDICATED `search_archive`.
#   b) THE FILTER IS NOT SILENT: the NUMBER of archive matches and the
#      `search_archive` pointer always go out; snapshots come in a SEPARATE,
#      collapsed band (they do not vanish).
#   c) THE CAP SAYS IT IS A CAP.
_SNAPSHOT_KEYWORD_RE = re.compile(
    r"(_bak_|\.bak_|_bak\b|\bbak_|backup|_pre_|\.pre_|_old_|_old\b)", re.IGNORECASE)
_SNAPSHOT_DATE_RE = re.compile(r"\d{4}-\d{2}-\d{2}")
_SEARCH_LIVE_CAP = 40      # live file-block cap (the cap SAYS it is a cap)
_SEARCH_SNAP_CAP = 8       # snapshot band cap


def _is_snapshot_file(name: str) -> bool:
    """Is this a dated snapshot copy, judged by its file NAME?

    ⚠️ THE DOUBLE CONDITION IS DELIBERATE (keyword AND ISO date). Filtering on a
    bare "backup" word gives FALSE POSITIVES: e.g. a `weekly_memory_backup.md`
    can be a LIVE specification (of a backup job), not a snapshot. Real
    snapshots, however, are always named with a date
    (`context_backup_2026-09-09_pre_fix.md`, `_bak_state_2026-08-29.md`,
    `server_backup_pre_truncate_2026-07-31.py`), so the "keyword + date" pair
    separates them precisely.
    """
    base = os.path.basename(name)
    return bool(_SNAPSHOT_KEYWORD_RE.search(base) and _SNAPSHOT_DATE_RE.search(base))


@mcp.tool()
def search_memory(query: str) -> str:
    """Search the LIVE memory (+ naplo/) for relevant content, WITH PROVENANCE.

    What it does, and why (see the comment above `_is_snapshot_file`):
    - LIVE matches first (`kontextus/`, `projektek/`, `eszkozok/`, `_state.md`, ...),
      then `naplo/` (historical, but part of the work), finally, collapsed, the
      dated SNAPSHOTS (`*_backup_2026-..-..`) -- those are NOT canonical.
    - ⚠️ If there are ZERO live matches but there are snapshot/archive matches,
      a loud warning stands at the TOP of the answer: the live text MAY HAVE
      CHANGED since.
    - `archiv/` does NOT belong here -- `search_archive` covers it. But the
      filter is NOT SILENT: the number of archive matches is always printed.
    - The cap SAYS it is a cap (it does not cut silently).

    Args:
        query: Search term (e.g., "spanish", "thesis", "deadline")

    Returns:
        Matching files and excerpts, split into bands by provenance.
    """
    query_lower = query.lower()
    root = Path(MEMORIA_ROOT)
    live, naplo, snaps = [], [], []
    archiv_hits = 0
    unreadable = []

    for md_file in root.rglob("*.md"):
        rel_path = str(md_file.relative_to(root))
        parts = rel_path.replace("\\", "/").split("/")
        top = parts[0] if len(parts) > 1 else ""

        # archiv/ is the scope of `search_archive` -- but we COUNT it (no silent filter).
        if top == "archiv":
            try:
                if query_lower in md_file.read_text(encoding="utf-8").lower():
                    archiv_hits += 1
            except Exception:
                pass
            continue

        try:
            content = md_file.read_text(encoding="utf-8")
        except Exception as e:
            # FAIL-CLOSED: an unreadable file is NOT a silent "no match".
            unreadable.append(f"{rel_path} ({type(e).__name__})")
            continue

        name_match = query_lower in rel_path.lower()
        if query_lower not in content.lower() and not name_match:
            continue

        excerpts = []
        for i, line in enumerate(content.splitlines()):
            if query_lower in line.lower():
                excerpts.append(f"  L{i+1}: {line.strip()}")
                if len(excerpts) >= 5:
                    break
        try:
            mtime = md_file.stat().st_mtime
        except OSError:
            mtime = 0.0
        rec = {"file": rel_path, "name_match": name_match,
               "excerpts": excerpts, "mtime": mtime}

        if _is_snapshot_file(rel_path):
            snaps.append(rec)
        elif top == "naplo":
            naplo.append(rec)
        else:
            live.append(rec)

    for bucket in (live, naplo, snaps):
        bucket.sort(key=lambda r: r["mtime"], reverse=True)

    total = len(live) + len(naplo) + len(snaps)
    if total == 0 and archiv_hits == 0 and not unreadable:
        return f"No match: '{query}'"

    out = [f"# Search: '{query}' -- {len(live)} LIVE · {len(naplo)} log · "
           f"{len(snaps)} snapshot · {archiv_hits} archive"]

    # (c) THE MOST IMPORTANT BAND: 0 live matches, but there are copy matches.
    if not live and (snaps or archiv_hits):
        out.append(
            "\n⚠️ **NOT A SINGLE MATCH COMES FROM THE LIVE MEMORY.** What you see is a "
            f"{'snapshot' if snaps else 'archive'} copy: the canonical text MAY HAVE "
            "CHANGED since (which is exactly why your query does not match it). Do NOT "
            "take it at face value -- verify it in the live file (`read_memory_file`), and if "
            "the claim is different there, the copy is NOT evidence."
        )

    def _emit(header, bucket, cap):
        if not bucket:
            return
        out.append(f"\n{header}")
        for r in bucket[:cap]:
            line = f"\n## {r['file']}"
            if r["name_match"]:
                line += " (file name match)"
            out.append(line)
            out.extend(r["excerpts"])
        if len(bucket) > cap:
            # THE CAP SAYS IT IS A CAP.
            out.append(f"\n_… and {len(bucket) - cap} more file(s) (cap: {cap}). "
                       f"Narrow the query, or open the files directly._")

    _emit("## ✅ LIVE memory", live, _SEARCH_LIVE_CAP)
    _emit("## 📓 naplo/ (historical, but part of the work)", naplo, _SEARCH_LIVE_CAP)
    _emit("## 🗄️ SNAPSHOTS — NOT canonical (dated backup copies)",
          snaps, _SEARCH_SNAP_CAP)

    if archiv_hits:
        out.append(f"\n---\n_🗄️ In `archiv/` **{archiv_hits} more file(s)** contain "
                   f"this term -- `search_archive('{query}')` returns them. "
                   "(The archive is deliberately not here: it is most of the corpus "
                   "and would spill in one piece. The filter is NOT silent: here is the count.)_")
    if unreadable:
        out.append("\n⛔ **FAIL-CLOSED — unreadable files** (their matches were LEFT OUT, "
                   "so the counts above are lower bounds): " + " · ".join(unreadable[:6]))

    return "\n".join(out)


@mcp.tool()
def update_context(field: str, value: str) -> str:
    """Update a specific field in the living context model (the context file).

    Use this to keep the context model fresh -- for example, after learning
    about a new deadline, or when a task is completed.

    Args:
        field: Which area to update. The tool will find and replace the relevant section
               (case-insensitive partial match on the "## " header).
               Examples: "URGENT", "SEMESTER", "THESIS", "PROJECTS"
        value: New content for that section

    Returns:
        Confirmation message
    """
    filepath = CONTEXT_FILE
    try:
        helyzet = _read_file(filepath)
    except FileNotFoundError:
        return f"!! {CONTEXT_FILE} not found!"

    pattern = rf"(## [^\n]*{re.escape(field)}[^\n]*\n)(.*?)(?=\n## |\Z)"
    match = re.search(pattern, helyzet, re.DOTALL | re.IGNORECASE)
    if not match:
        return f"!! Section '{field}' not found."

    header = match.group(1)

    # Strip header from value if caller accidentally included it.
    # Prevents header duplication when caller copies a whole section including `## FIELD` line.
    value_lines = value.lstrip().splitlines()
    if value_lines and value_lines[0].lstrip().startswith("## ") and re.search(re.escape(field), value_lines[0], re.IGNORECASE):
        value = "\n".join(value_lines[1:]).lstrip("\n")

    new_helyzet = helyzet[:match.start()] + header + value + "\n" + helyzet[match.end():]

    new_helyzet = re.sub(
        r"updated: \d{4}-\d{2}-\d{2}",
        f"updated: {_today()}",
        new_helyzet,
    )

    _write_file(filepath, new_helyzet)
    return f"OK: '{field}' updated in {CONTEXT_FILE}."


@mcp.tool()
def list_memory_files(subdir: str = "") -> str:
    """List all files in the memory system, optionally filtered by subdirectory.

    Args:
        subdir: Subdirectory to list (e.g., "naplo", "projektek", "eszkozok").
                Empty string = root level.

    Returns:
        Structured listing of files with metadata
    """
    try:
        root = Path(_safe_path(subdir or "."))
    except ValueError as e:
        return f"!! {e}"
    if not root.exists():
        return f"!! Folder not found: {subdir or '(root)'}"

    items = []
    for item in sorted(root.iterdir()):
        rel = str(item.relative_to(MEMORIA_ROOT))
        if item.is_dir():
            file_count = len(list(item.rglob("*.md")))
            items.append(f"[DIR] {rel}/ ({file_count} files)")
        elif item.suffix == ".md":
            size = item.stat().st_size
            mtime = datetime.fromtimestamp(item.stat().st_mtime).strftime("%Y-%m-%d %H:%M")
            items.append(f"[FILE] {rel} ({size} bytes, {mtime})")
        else:
            items.append(f"  {rel}")

    if not items:
        return f"Empty folder: {subdir or '(root)'}"

    header = f"# Files: {subdir or 'memoria/'}\n\n"
    return header + "\n".join(items)


@mcp.tool()
def read_memory_file(path: str) -> str:
    """Read the full content of any file in the memory system.

    Args:
        path: Relative path within memoria/ (e.g., "naplo/2026-04-01_session_1.md")

    Returns:
        Full file content
    """
    try:
        return _read_file(path)
    except FileNotFoundError:
        return f"!! File not found: {path}"


@mcp.tool()
def write_memory_file(path: str, content: str) -> str:
    """Create / overwrite a file in the memory, with a server-side integrity check.

    Parent folders are created automatically. After writing, the server reads
    the file back and compares it with the requested content. If they differ
    (e.g. the transport layer truncated something between client and server),
    an !! INTEGRITY FAILURE message comes back instead of a silent "OK" -- so
    the caller knows the file on disk is not what it asked for.

    For content larger than ~4 KB, where transport truncation happens, use
    `append_memory_file` in <4 KB chunks (truncate_first=True on the first call).

    Args:
        path: Relative path within the memory root
        content: Full file content
    """
    expected_len = len(content)
    _write_file(path, content)
    # Integrity check: read back and compare. This catches the MCP transport
    # silently truncating the `content` argument, which would otherwise result
    # in a partial write with no error.
    try:
        actual = _read_file(path)
    except Exception as e:
        return f"!! Write verification failed (could not read back {path}): {e}"
    if actual == content:
        return f"OK: File written: {path} ({expected_len} chars)"
    actual_len = len(actual)
    # Locate the first divergence for diagnostics
    min_len = min(expected_len, actual_len)
    diverge_at = min_len
    for i in range(min_len):
        if actual[i] == content[i]:
            continue
        diverge_at = i
        break
    return (
        f"!! INTEGRITY FAILURE writing {path}: requested {expected_len} chars, "
        f"file contains {actual_len} chars (first divergence at char {diverge_at}). "
        f"Likely transport-layer truncation. Retry with append_memory_file() "
        f"in chunks <4 KB each (use truncate_first=True on the first call)."
    )


@mcp.tool()
def append_memory_file(
    path: str,
    content: str,
    truncate_first: bool = False,
) -> str:
    """Append content to a memory file (optionally emptying the file first).

    Exists to work around transport-layer truncation, which affects
    `write_memory_file` above roughly 5 KB of content. Split the target content
    into <4 KB chunks and call this tool for each chunk. On the first call use
    truncate_first=True to empty the existing file; later calls append.

    Every call writes with fsync on the server side, and the returned message
    contains the file's current size, so the caller can check progress between
    chunks.

    Typical multi-chunk replacement::

        append_memory_file(path, chunk_1, truncate_first=True)
        append_memory_file(path, chunk_2)
        append_memory_file(path, chunk_3)

    Args:
        path: Relative path within the memory root
        content: Content to append (or to write, if truncate_first=True)
        truncate_first: If True, empties the file before writing (default False)
    """
    full = _safe_path(path)
    os.makedirs(os.path.dirname(full), exist_ok=True)
    mode = "w" if truncate_first else "a"
    with open(full, mode, encoding="utf-8") as f:
        f.write(content)
        f.flush()
        os.fsync(f.fileno())
    size = os.path.getsize(full)
    action = "Wrote" if truncate_first else "Appended"
    return (
        f"OK: {action} {len(content)} chars to {path} "
        f"(file is now {size} bytes)"
    )


@mcp.tool()
def move_memory_file(src: str, dst: str) -> str:
    """Move a file to another path inside memoria/.

    Lets you reorganise without deleting the original first (no data is
    lost). Atomic operation: shutil.move() in one step.

    Restrictions:
    - The `archiv/` folder is BLOCKED — a move can go neither from nor to it.
      Archiving goes through the dedicated tools (archive_generic, archive,
      archive_project); bringing something back from the archive is done by
      hand in an interactive session.
    - `src` must point to an existing file — otherwise an error message.
    - `dst` must not exist — overwriting is not allowed, for safety.
    - `src` must be a file — moving folders is not supported here.

    Audit log: every call is recorded in the memoria_audit_log SQLite table
    (operation='move', src_path, dst_path, file_size_bytes).

    Args:
        src: Source path, relative to the memoria/ root
        dst: Destination path, relative to the memoria/ root

    Returns:
        Confirmation of the operation, or an error message.
    """
    try:
        _check_archive_blocked(src)
        _check_archive_blocked(dst)

        src_full = _safe_path(src)
        dst_full = _safe_path(dst)

        if not os.path.isfile(src_full):
            return f"!! Source file not found: {src}"
        if os.path.exists(dst_full):
            return f"!! Destination already exists: {dst} (overwriting is not allowed)"

        file_size = os.path.getsize(src_full)
        os.makedirs(os.path.dirname(dst_full), exist_ok=True)
        shutil.move(src_full, dst_full)

        _log_memoria_audit(
            operation="move",
            src_path=src,
            dst_path=dst,
            file_size_bytes=file_size,
        )

        return f"OK: Moved: {src} -> {dst} ({file_size} bytes)"
    except ValueError as e:
        return f"!! {e}"
    except Exception as e:
        return f"!! Error: {type(e).__name__}: {e}"


@mcp.tool()
def delete_memory_file(path: str) -> str:
    """Delete a single file inside memoria/.

    Restrictions:
    - The `archiv/` folder is BLOCKED — deleting from it is not allowed.
      Archived content is a time capsule and stays for debugging.
    - Only deletes an existing file — otherwise an error message.
    - Does NOT delete folders — files only.

    Audit log: every call is recorded in the memoria_audit_log SQLite table
    (operation='delete', src_path, file_size_bytes, first_line).
    The `first_line` field holds the first max 200 characters of the file —
    so if someone later asks 'what was in this file?', the audit can answer
    even when the content is gone.

    Args:
        path: Relative path within the memoria/ root

    Returns:
        Confirmation of the operation, or an error message.
    """
    try:
        _check_archive_blocked(path)

        full = _safe_path(path)

        if os.path.isdir(full):
            return f"!! Deleting folders is not supported: {path} (files only)"
        if not os.path.isfile(full):
            return f"!! File not found: {path}"

        file_size = os.path.getsize(full)
        first_line = _read_first_line(path, max_chars=200)

        os.remove(full)

        _log_memoria_audit(
            operation="delete",
            src_path=path,
            file_size_bytes=file_size,
            first_line=first_line,
        )

        return f"OK: Deleted: {path} ({file_size} bytes)"
    except ValueError as e:
        return f"!! {e}"
    except Exception as e:
        return f"!! Error: {type(e).__name__}: {e}"


# --- Archival System ---


def _get_archivable_files(directory: str, retention_days: int = 14) -> list:
    """Find files older than retention_days in a directory."""
    root = Path(MEMORIA_ROOT) / directory
    if not root.exists():
        return []

    cutoff = datetime.now() - timedelta(days=retention_days)
    archivable = []

    for md_file in sorted(root.glob("*.md")):
        try:
            content = md_file.read_text(encoding="utf-8")
            meta, _ = _parse_frontmatter(content)
            file_date = _parse_date(meta.get("date", ""))
            if file_date and file_date < cutoff:
                archivable.append(md_file)
        except Exception:
            continue

    return archivable


def _create_monthly_digest(files: list, source_dir: str) -> dict:
    """Create monthly digest content from a list of files.

    Returns: {month: digest_content} e.g. {"2026-03": "# March 2026 ..."}
    """
    from collections import defaultdict
    by_month = defaultdict(list)

    for md_file in files:
        try:
            content = md_file.read_text(encoding="utf-8")
            meta, body = _parse_frontmatter(content)
            date = meta.get("date", "unknown")
            month_key = date[:7] if len(date) >= 7 else "unknown"

            by_month[month_key].append({
                "filename": md_file.name,
                "date": date,
                "meta": meta,
                "body": body,
            })
        except Exception:
            continue

    digests = {}
    for month, entries in sorted(by_month.items()):
        lines = [f"# Archive: {source_dir} — {month}\n"]
        lines.append(f"_{len(entries)} entries archived: {_today()}_\n")

        for entry in entries:
            lines.append(f"## {entry['date']} — {entry['filename']}")
            if "session_type" in entry["meta"]:
                lines.append(f"**Type:** {entry['meta']['session_type']}")
            hl = entry["meta"].get("highlights", "")
            if not hl:
                for bl in entry["body"].splitlines():
                    if bl.strip() and not bl.startswith("#"):
                        hl = bl.strip()
                        break
            if hl:
                lines.append(f"**Highlights:** {hl}")
            body_condensed = entry["body"][:500]
            if len(entry["body"]) > 500:
                body_condensed += "\n\n_(truncated)_"
            lines.append(body_condensed)
            lines.append("")

        digests[month] = "\n".join(lines)

    return digests


@mcp.tool()
def archive(dry_run: bool = True, retention_days: int = 14) -> str:
    """Archive old sessions and logs beyond the retention period.

    Scans naplo/ for files older than retention_days,
    creates monthly digest files in archiv/, and moves originals
    to archiv/raw/ for safekeeping.

    Args:
        dry_run: If True (default), only shows what WOULD be archived.
                 Set to False to actually perform the archival.
        retention_days: Days before files are archived (default: 14)

    Returns:
        Summary of what was (or would be) archived.
    """
    output = f"# Archival {'preview' if dry_run else 'result'}\n"
    output += f"_Retention: {retention_days} days. Today: {_today()}_\n\n"

    total_archived = 0

    for source_dir in ["naplo"]:
        archivable = _get_archivable_files(source_dir, retention_days)

        if not archivable:
            output += f"## {source_dir}/\nNothing to archive.\n\n"
            continue

        output += f"## {source_dir}/\n"
        output += f"**{len(archivable)} file(s)** ready for archival:\n"

        for f in archivable:
            meta, _ = _parse_frontmatter(f.read_text(encoding="utf-8"))
            date = meta.get("date", "?")
            output += f"- {f.name} ({date})\n"

        if not dry_run:
            # Create monthly digests
            digests = _create_monthly_digest(archivable, source_dir)
            for month_key, digest_content in digests.items():
                digest_path = f"archiv/{source_dir}_{month_key}.md"
                _write_file(digest_path, digest_content)
                output += f"\n→ Digest created: {digest_path}"

            # Move originals to archiv/raw/
            raw_dir = Path(MEMORIA_ROOT) / "archiv" / "raw" / source_dir
            raw_dir.mkdir(parents=True, exist_ok=True)
            for f in archivable:
                dest = raw_dir / f.name
                shutil.move(str(f), str(dest))
                output += f"\n→ Moved: {f.name} → archiv/raw/{source_dir}/"

            # Clean up _processed.json
            if source_dir == "naplo":
                processed = _load_processed()
                archived_names = {f.name for f in archivable}
                processed["processed_sessions"] = [
                    s for s in processed.get("processed_sessions", [])
                    if s not in archived_names
                ]
                # Prune old resolved_items beyond retention period
                cutoff_date = (datetime.now() - timedelta(days=retention_days)).strftime("%Y-%m-%d")
                old_count = len(processed.get("resolved_items", []))
                processed["resolved_items"] = [
                    ri for ri in processed.get("resolved_items", [])
                    if isinstance(ri, dict) and ri.get("resolved_on", "") >= cutoff_date
                ]
                pruned_count = old_count - len(processed["resolved_items"])
                _save_processed(processed)
                output += f"\n→ _processed.json cleaned ({len(archived_names)} session(s), {pruned_count} old resolved item(s) removed)"

            total_archived += len(archivable)

        output += "\n\n"

    # Check for completed projects
    proj_root = Path(MEMORIA_ROOT) / "projektek"
    if proj_root.exists():
        completed = []
        for md_file in proj_root.glob("*.md"):
            try:
                content = md_file.read_text(encoding="utf-8")
                if _is_completed_line(content[:500]):
                    completed.append(md_file)
            except Exception:
                continue

        if completed:
            output += f"## projektek/\n"
            output += f"**{len(completed)} potentially closed project(s)** (can be moved with archive_project):\n"
            for f in completed:
                output += f"- {f.name}\n"
            output += "\n"

    if dry_run:
        output += f"\n_This was a preview. Call archive(dry_run=False) to perform it._"
    else:
        output += f"\n**Done!** {total_archived} file(s) archived."

    return output


@mcp.tool()
def archive_project(path: str) -> str:
    """Archive a completed project file.

    Moves a project file to archiv/projektek/.

    Args:
        path: Relative path of the project file (e.g., "projektek/old_project.md")
    """
    try:
        source = Path(_safe_path(path))
    except ValueError as e:
        return f"!! {e}"
    if Path(_safe_path("projektek")) not in source.parents:
        return f"!! Only files under projektek/ can be archived here: {path}"
    if not source.exists():
        return f"!! File not found: {path}"

    archive_dir = Path(MEMORIA_ROOT) / "archiv" / "projektek"
    archive_dir.mkdir(parents=True, exist_ok=True)

    dest = archive_dir / source.name
    shutil.move(str(source), str(dest))

    return f"OK: Archived {path} → archiv/projektek/{source.name}"


# Whitelisted subdirectories for archive_generic().
# Sessions/naplo use archive(), kontextus/claude_sajat/archiv are intentionally
# excluded (live references, own-category rules, or already-archived content).
ARCHIVE_GENERIC_WHITELIST = {"eszkozok", "projektek"}

# --- SNAPSHOT EXCEPTION next to the whitelist ---
# WHY: dated backup copies sitting in the LIVE memory caused measurable harm:
# `search_memory` returned matches only from stale copies and NONE from the live
# file. The convention ("backups belong in archiv/") EXISTED -- it just could
# not be followed: archive_generic's whitelist is `eszkozok`+`projektek`, and
# move_memory_file / delete_memory_file block `archiv/`. So NO tool path could
# move a snapshot from `kontextus/` or the root into the archive. The rule
# existed, the MECHANISM did not ("a comment is not an interface").
# THE EXCEPTION IS NARROW ON PURPOSE: a file can be archived from anywhere, BUT
# only if `_is_snapshot_file()` says it is a dated snapshot copy. So the LIVE,
# load-bearing files (the context file, the patterns file, `_state.md`) stay
# PROTECTED -- this tool still cannot archive them.
_ARCHIVE_SNAPSHOT_ANYWHERE = True


@mcp.tool()
def archive_generic(
    file_path: str,
    reason: str,
    digest_entry: bool = True,
) -> str:
    """Archive a single obsolete file from a whitelisted subdirectory.

    Complements the other archive tools:
    - archive() handles sessions/ and naplo/ in bulk with monthly digests.
    - archive_project() moves completed projects to archiv/projektek/ flat.
    - archive_generic() (this tool) moves single files from eszkozok/ or
      projektek/ into a dated folder (archiv/{subdir}/YYYY-MM-DD/{file})
      and optionally appends the reason to archiv/digest/{subdir}_digest.md
      so the "why did I archive this?" question stays searchable later.

    Args:
        file_path: Relative path of the file to archive
            (e.g., "eszkozok/filesystem_test.md"). Must be inside a
            whitelisted subdirectory -- EXCEPT dated SNAPSHOT copies
            (`*_backup_2026-..-..*`, `*.bak_2026-..-..*`), which can be archived
            FROM ANYWHERE (`kontextus/`, the root), because there they do active
            harm: `search_memory` may return stale text instead of the canonical
            one. The LIVE files (the context file, `_state.md`, the patterns
            file) stay protected.
        reason: 1-2 sentence explanation of why the file is obsolete.
            Gets written into the digest if digest_entry is True.
        digest_entry: If True (default), append an entry to the subdir's
            digest file under today's date. Set to False for silent archival.

    Returns:
        Confirmation string with the archived destination path.

    Raises:
        FileNotFoundError: The source file does not exist.
        ValueError: The path is not in the whitelist, is malformed, or is a
            path traversal attempt (the latter caught by _safe_path).
    """
    # Normalize separators for the whitelist check (user might pass 'eszkozok\\x.md')
    normalized = file_path.replace("\\", "/").lstrip("./")
    parts = [p for p in normalized.split("/") if p]

    if not parts:
        raise ValueError(f"empty file_path: {file_path!r}")

    # A dated SNAPSHOT copy can be archived from anywhere (see the reasoning
    # above the whitelist); every other file stays on the narrow whitelist.
    # ⚠️ THIS CHECK STANDS BEFORE the "needs a subdir" guard: a snapshot can sit
    # in the memory ROOT, and the subdir guard would reject it before the exception.
    is_snap = _ARCHIVE_SNAPSHOT_ANYWHERE and _is_snapshot_file(parts[-1])

    if len(parts) < 2 and not is_snap:
        raise ValueError(
            f"file_path must include a subdirectory (e.g. 'eszkozok/x.md'), got: {file_path!r}"
        )

    subdir = parts[0] if len(parts) > 1 else "_gyoker"
    if subdir == "archiv":
        raise ValueError("`archiv/` is already the archive -- this tool does not move from there.")
    if subdir not in ARCHIVE_GENERIC_WHITELIST and not is_snap:
        raise ValueError(
            f"Subdirectory '{subdir}' is not in the archive_generic whitelist. "
            f"Allowed: {sorted(ARCHIVE_GENERIC_WHITELIST)}. "
            f"For sessions/naplo use archive(); kontextus/claude_sajat/archiv "
            f"are intentionally excluded. "
            f"(Exception: a dated SNAPSHOT copy -- e.g. "
            f"'x_backup_2026-09-09_pre_y.md' -- can be archived from anywhere; "
            f"'{parts[-1]}' is not one.)"
        )

    # Resolve + validate source (this also catches path traversal via _safe_path)
    source_full = _safe_path(file_path)
    if not os.path.isfile(source_full):
        raise FileNotFoundError(f"Not found: {file_path}")

    # Build destination: archiv/{subdir}/YYYY-MM-DD/{filename}
    # Snapshots go to their own `archiv/pillanatkep/<origin>/` bucket, so the
    # copies do not scatter across the topical archive folders.
    today = _today()
    filename = os.path.basename(source_full)
    bucket = subdir if subdir in ARCHIVE_GENERIC_WHITELIST else f"pillanatkep/{subdir}"
    dest_rel = f"archiv/{bucket}/{today}/{filename}"
    dest_full = _safe_path(dest_rel)

    # Collision: same filename already archived today → append HHMMSS suffix
    if os.path.exists(dest_full):
        stem, ext = os.path.splitext(filename)
        timestamp = datetime.now().strftime("%H%M%S")
        filename = f"{stem}_{timestamp}{ext}"
        dest_rel = f"archiv/{bucket}/{today}/{filename}"
        dest_full = _safe_path(dest_rel)

    os.makedirs(os.path.dirname(dest_full), exist_ok=True)
    shutil.move(source_full, dest_full)

    # Digest entry (optional)
    digest_updated = False
    if digest_entry:
        digest_rel = f"archiv/digest/{subdir}_digest.md"
        digest_full = _safe_path(digest_rel)
        day_header = f"## {today}"
        entry_line = f"- `{file_path}` — {reason}\n"

        if os.path.isfile(digest_full):
            existing = _read_file(digest_rel)
            if day_header in existing:
                # Today's section already exists — just append the line.
                _append_file(digest_rel, entry_line)
            else:
                # Start a new dated section at the end of the digest.
                _append_file(digest_rel, f"\n{day_header}\n{entry_line}")
        else:
            initial = (
                f"# Archive digest: {subdir}/\n\n"
                f"_Chronological chain of files archived from the {subdir}/ folder "
                f"by the archive_generic() tool. Every entry ties a file to a date "
                f"plus a short reason._\n\n"
                f"{day_header}\n{entry_line}"
            )
            _write_file(digest_rel, initial)
        digest_updated = True

    msg = f"OK: archived {file_path} -> {dest_rel}"
    if digest_updated:
        msg += f" (digest: archiv/digest/{subdir}_digest.md)"
    return msg


@mcp.tool()
def search_archive(query: str) -> str:
    """Search through archived content.

    Searches both monthly digests and raw archived files.

    Args:
        query: Search term (case-insensitive)
    """
    query_lower = query.lower()
    results = []
    archive_root = Path(MEMORIA_ROOT) / "archiv"

    if not archive_root.exists():
        return "No archiv folder. Nothing has been archived yet."

    for md_file in archive_root.rglob("*.md"):
        try:
            content = md_file.read_text(encoding="utf-8")
            if query_lower in content.lower():
                rel = str(md_file.relative_to(Path(MEMORIA_ROOT)))
                excerpts = []
                for i, line in enumerate(content.splitlines()):
                    if query_lower in line.lower():
                        excerpts.append(f"  L{i+1}: {line.strip()}")
                        if len(excerpts) >= 3:
                            break
                results.append({"file": rel, "excerpts": excerpts})
        except Exception:
            continue

    if not results:
        return f"No archive match: '{query}'"

    output = f"# Archive search: '{query}' — {len(results)} match(es)\n\n"
    for r in results:
        output += f"## {r['file']}\n"
        for exc in r["excerpts"]:
            output += exc + "\n"
        output += "\n"
    return output


@mcp.tool()
def get_claude_reflections() -> str:
    """Access Claude's own reflections and self-observations.

    Lists individual reflection entries from claude_sajat/. Each entry is a
    self-observation logged by a previous Claude instance about a specific
    situation. Always includes the most recent 1-2 entry titles and opening
    lines so you can see what's there.

    This is YOUR space -- observations about yourself, not about the owner.
    Call it when you're curious about what previous Claude sessions discovered.
    It's an invitation, not an obligation.

    The current convention (no synthesis files, just raw entries) is described
    in claude_sajat/_keret.md -- read that before changing the structure.
    """
    claude_dir = Path(MEMORIA_ROOT) / "claude_sajat"

    if not claude_dir.exists():
        return (
            "The claude_sajat/ folder does not exist yet. "
            "If you would like to record your own observations, "
            "create a file with write_memory_file()."
        )

    # Collect all entries (exclude files starting with '_' — those are
    # meta-files like _keret.md, not entries)
    entries = sorted([
        f for f in claude_dir.glob("*.md")
        if not f.name.startswith("_")
    ], key=lambda f: f.stat().st_mtime)

    output_parts = ["# Claude's own reflections\n"]

    if not entries:
        output_parts.append(
            "_No entries in the claude_sajat/ folder yet. "
            "The usage frame is described in claude_sajat/_keret.md._"
        )
        return "\n".join(output_parts)

    output_parts.append(
        f"_{len(entries)} entries found. "
        f"The usage frame is described in claude_sajat/_keret.md._\n"
    )
    output_parts.append(f"**Entries:**\n")
    for entry in entries:
        rel = str(entry.relative_to(Path(MEMORIA_ROOT)))
        size_kb = entry.stat().st_size / 1024
        output_parts.append(f"- {rel} ({size_kb:.1f} KB)")

    # Show the most recent 1-2 entries' opening lines
    recent = entries[-2:]  # last 2
    output_parts.append("\n## Most recent entries\n")
    for entry in reversed(recent):  # newest first
        rel = str(entry.relative_to(Path(MEMORIA_ROOT)))
        try:
            content = entry.read_text(encoding="utf-8")
            _, body = _parse_frontmatter(content)
            # Extract first heading and first meaningful paragraph
            first_heading = ""
            first_lines = []
            for line in body.splitlines():
                if not first_heading and line.startswith("# "):
                    first_heading = line.strip()
                    continue
                if first_heading and line.strip() and not line.startswith("#"):
                    first_lines.append(line.strip())
                    if len(first_lines) >= 2:
                        break

            opening = " ".join(first_lines) if first_lines else "(empty)"
            # Truncate if too long
            if len(opening) > 200:
                opening = opening[:197] + "..."
            output_parts.append(f"### {rel}")
            if first_heading:
                output_parts.append(f"**{first_heading}**")
            output_parts.append(f"_{opening}_\n")
        except Exception:
            output_parts.append(f"### {rel}\n_(unreadable)_\n")

    return "\n".join(output_parts)


# --- Follow-up tracker ---
# Claude's own "waiting list": events where Claude waits for someone/something
# else -- NOT the owner's hard deadlines (get_deadlines() reads those from the
# context file). E.g. "the parcel arrives", "the supervisor answers the open
# questions". Looser regime: expected_by is only a guide, overdue is only a
# signal for the next session.


_FOLLOWUPS_FILE = "kontextus/followups.json"


def _load_followups() -> dict:
    """Load the follow-up tracker. Returns default structure if not found."""
    try:
        content = _read_file(_FOLLOWUPS_FILE)
        data = json.loads(content)
        if "followups" not in data:
            data["followups"] = []
        if "next_id" not in data:
            existing = [f.get("id", 0) for f in data["followups"]]
            data["next_id"] = (max(existing) + 1) if existing else 1
        return data
    except (FileNotFoundError, json.JSONDecodeError):
        return {"followups": [], "next_id": 1}


def _save_followups(data: dict) -> None:
    """Save the follow-up tracker (atomic write via _write_file)."""
    _write_file(_FOLLOWUPS_FILE, json.dumps(data, indent=2, ensure_ascii=False))


@mcp.tool()
def track_followup(item: str, expected_by: str = "", note: str = "",
                   covers: list = None, precondition: str = "") -> str:
    """Add a new follow-up thread to Claude's own waiting list.

    Use it when you are waiting for something that is NOT the owner's own
    deadline but an external dependency: a delivery arriving, someone's answer,
    a document being published. get_deadlines() reads the owner's hard
    deadlines from the context file; this is a looser, Claude-managed tracker
    where expected_by is only a guide (not a hard deadline). Close with
    resolve_followup().

    Args:
        item: What you are waiting for (short description). E.g. "the parts kit arrives".
        expected_by: Optional expected date (YYYY-MM-DD). Empty = open-ended.
        note: Optional note (what to do if it lapses / context).
        covers: ⚠️ WHICH MARKERS this thread replaces. A list of
            `"path.md:line"` or `"path.md"` entries, e.g.
            `["projektek/garden.md:65"]`. `get_open_markers` tags these with
            `[✔ TRACKED: #N]` -- a FACT, not a guess.
            WHY: lexical (word-overlap) matching cannot, in principle, tell a
            note that FOLLOWS a marker from one that merely QUOTES it. A
            follow-up whose note listed the corpus identifiers once won most of
            the lexical tags, and no weighting scheme fixed it -- a
            representational limit, not a parameter bug. What can be declared,
            should not be guessed.
        precondition: ⚠️ WHAT TRIGGERS the thread when it has no meaningful
            date. E.g. "next live hardware session" · "the owner is at work and
            brings it up". STRUCTURED field: `get_followups` renders it as a
            `⚑ PRECONDITION:` line, and the tag becomes "tied to a precondition".
            WHY: this used to live in prose in the note, where the parser could
            not see it -- and worse, threads with no real deadline got a DATE
            just to have something to write. Such a date is not information but
            daily noise ("[!] OVERDUE by 4 days" for something that was never
            due). If there is no real date, do not invent one -- write down the
            PRECONDITION.

    Returns:
        Confirmation with the new follow-up's id.
    """
    item = (item or "").strip()
    if not item:
        return "Error: empty 'item', nothing to add."

    expected_by = (expected_by or "").strip()
    if expected_by and _parse_date(expected_by) is None:
        return f"Error: '{expected_by}' is not a valid date (YYYY-MM-DD required)."

    data = _load_followups()

    # Duplicate guard: is the same item text (case-insensitive) already open?
    for f in data["followups"]:
        if f.get("status") == "open" and f.get("item", "").strip().lower() == item.lower():
            return f"An open follow-up with this text already exists (#{f['id']}). Not added again."

    fid = data["next_id"]
    data["next_id"] = fid + 1
    data["followups"].append({
        "id": fid,
        "item": item,
        "expected_by": expected_by,
        "note": (note or "").strip(),
        "created": _today(),
        "status": "open",
        "resolved_on": None,
        "outcome": "",
        "covers": _clean_covers(covers),
        "precondition": (precondition or "").strip(),
        "history": [],
    })
    _save_followups(data)

    pre = (precondition or "").strip()
    if expected_by:
        when = f" (expected: {expected_by})"
    elif pre:
        when = f" (tied to a precondition: {pre})"
    else:
        when = " (open-ended)"
    cov = _clean_covers(covers)
    cov_txt = f" · covers: {', '.join(cov)}" if cov else ""
    hint = ""
    if expected_by and pre:
        hint = ("\n⚠️ Both a date AND a precondition are given. That is legitimate (the date "
                "is then a reminder, not a deadline), but if the date is only there to have "
                "something, leave it out -- a false deadline produces daily noise.")
    return f"Follow-up added: #{fid} -- {item}{when}{cov_txt}{hint}"


def _clean_covers(covers) -> list:
    """Normalise the `covers` input: a list of strings, without empty entries."""
    if not covers:
        return []
    if isinstance(covers, str):
        covers = [covers]
    out = []
    for c in covers:
        c = str(c or "").strip().replace("\\", "/")
        if c and c not in out:
            out.append(c)
    return out


@mcp.tool()
def set_followup_covers(followup_id: int, covers: list, replace: bool = False) -> str:
    """Declare WHICH marker(s) a follow-up replaces.

    ⚠️ BUILT AGAINST A MEASURED FAILURE MODE. `get_open_markers` used to GUESS
    LEXICALLY (by word overlap) whether an orphaned ⚑/TODO marker is already
    followed by some follow-up. Guessing is wrong in principle: a follow-up
    whose NOTE quotes the marker corpus (e.g. a meta thread about this server's
    own code) is lexically most similar to exactly the markers it only mentions
    AS EXAMPLES. No weighting scheme fixed it -- not even reading explicit
    `file.md L65` references, because such a note contains them VERBATIM.
    So the fix is not better guessing but DECLARATION: a prose quote can never
    disguise itself as a structured claim.

    Args:
        followup_id: The id of the open follow-up (see `get_followups()`).
        covers: A list of `"path.md:line"` or `"path.md"` entries. The path can
            be short (`garden.md`) or full (`projektek/garden.md`) -- matched
            by suffix, on a `/` boundary. Without a line it applies to the
            WHOLE file's markers (careful: with `include_covered=False` this
            can silence many markers).
        replace: True = replace the existing list; False (default) = append.

    Returns:
        Confirmation with the follow-up's new `covers` list.
    """
    new_cov = _clean_covers(covers)
    if not new_cov:
        return "Error: empty 'covers', nothing to declare."
    bad = [c for c in new_cov if _parse_covers_entry(c) is None]
    if bad:
        return f"Error: unparseable entr(y/ies): {bad}"

    data = _load_followups()
    for f in data["followups"]:
        if f.get("id") != followup_id:
            continue
        if f.get("status") != "open":
            return f"#{followup_id} is not open (status={f.get('status')}). Not modified."
        cur = [] if replace else _clean_covers(f.get("covers"))
        for c in new_cov:
            if c not in cur:
                cur.append(c)
        f["covers"] = cur
        _save_followups(data)
        return (f"#{followup_id} `covers` updated ({len(cur)} entr(y/ies)): "
                + ", ".join(cur)
                + "\n➜ get_open_markers now tags these with `[✔ TRACKED: #"
                + str(followup_id) + "]` (declared, not lexical).")
    return f"Error: no follow-up with id #{followup_id}."


# --- Rescheduling / refining an EXISTING thread ---

# Values of `new_due` that clear the date (Hungarian first, then English).
_RESCHED_CLEAR_TOKENS = {"nyitott", "nyitott vegu", "nyitott végű", "-", "none", "torol", "töröl",
                         "open", "open-ended", "clear"}


@mcp.tool()
def reschedule_followup(followup_id: int, reason: str, new_due: str = "",
                        precondition: str = "", note_add: str = "",
                        new_item: str = "") -> str:
    """Reschedule or refine an EXISTING follow-up -- WITHOUT creating a successor thread.

    ⚠️ BUILT AGAINST MEASURED TOIL. Without it there was no way to change a
    follow-up's date/text, so every time the old thread had to be closed and a
    NEW one opened for the same thing: about a third of all follow-ups were
    such successors, and half of the open list was administrative duplicates.
    The harm is not just id inflation: every successor's note had to explain
    "this succeeds #X, NOT a new thread", and the thread's history (when it
    started, how often it slipped) was lost.

    ⚠️ AND THIS IS NOT A SILENT REWRITE. The costliest failure mode of a memory
    system is the stale cache: a claim someone quietly rewrote, which the next
    instance takes at face value. So (a) `reason` is REQUIRED, (b) every change
    gets an append-only `history` entry (from what to what, when, why), (c)
    `get_followups` PRINTS the history -- it is not a hidden field. After 3+
    postponements a separate `⚠️ rescheduled N times` warning goes with the
    line: a thread that keeps slipping is NOT a date problem but a decision
    problem.

    DATE-DRIVEN → PRECONDITION-DRIVEN: if the date carries no information and
    only produces daily noise ("[!] OVERDUE by 4 days" for something that never
    had a real deadline), use `new_due="open"` + `precondition=…`. The
    precondition is REQUIRED when clearing the date: you may only remove a date
    if you say WHAT the trigger event is instead. Without it the thread would
    not be open-ended but forgotten.

    Args:
        followup_id: The id of the OPEN follow-up (see `get_followups()`).
        reason: REQUIRED -- why it slips/changes, in one sentence. This is the evidence.
        new_due: New expected date (YYYY-MM-DD). Empty = the date does NOT change.
            `"open"` / `"nyitott"` (or `"-"`) = CLEAR the date, the thread becomes
            open-ended -- then `precondition` is required.
        precondition: The trigger event ("next live hardware session", "the
            owner is at work and brings it up"). Structured field: `get_followups`
            renders it as a `⚑ PRECONDITION:` line. REQUIRED when clearing the date.
        note_add: Optional -- append a dated line to the note (the old note STAYS).
        new_item: Optional -- refine the title (e.g. if the original was written
            in the future tense). The OLD title goes into the history, it is not lost.

    Returns:
        Confirmation with the changes, or an error.
    """
    try:
        fid = int(followup_id)
    except (ValueError, TypeError):
        return f"Error: '{followup_id}' is not a valid id."

    reason = (reason or "").strip()
    if not reason:
        return ("Error: `reason` is REQUIRED. A date change without a reason is "
                "exactly the silent rewrite this tool was built against.")

    new_due = (new_due or "").strip()
    precondition = (precondition or "").strip()
    note_add = (note_add or "").strip()
    new_item = (new_item or "").strip()

    clear_due = new_due.lower() in _RESCHED_CLEAR_TOKENS
    if new_due and not clear_due and _parse_date(new_due) is None:
        return (f"Error: '{new_due}' is not a valid date (YYYY-MM-DD required), nor a "
                f"clear token ({', '.join(sorted(_RESCHED_CLEAR_TOKENS))}).")

    data = _load_followups()
    for f in data.get("followups", []):
        if f.get("id") != fid:
            continue
        if f.get("status") != "open":
            return (f"#{fid} is not open (status={f.get('status')}). I do not reschedule a "
                    f"closed thread -- if it is live again, open a new one with `track_followup`.")

        old_due = f.get("expected_by", "")
        old_pre = f.get("precondition", "")
        old_item = f.get("item", "")
        changes = []

        if clear_due:
            if not precondition and not old_pre:
                return ("Error: clearing the date needs a `precondition`. You may only "
                        "remove a date if you say WHAT triggers the thread instead -- "
                        "otherwise it will not be open-ended but forgotten.")
            if old_due:
                f["expected_by"] = ""
                changes.append(("expected_by", old_due, "(open-ended)"))
        elif new_due:
            if new_due == old_due:
                return f"#{fid} already has the expected date {new_due}. Not modified."
            f["expected_by"] = new_due
            changes.append(("expected_by", old_due or "(open-ended)", new_due))

        if precondition and precondition != old_pre:
            f["precondition"] = precondition
            changes.append(("precondition", old_pre or "(none)", precondition))

        if new_item and new_item != old_item:
            f["item"] = new_item
            changes.append(("item", old_item, new_item))

        if note_add:
            old_note = (f.get("note") or "").rstrip()
            stamped = f"[{_today()}] {note_add}"
            f["note"] = (old_note + "\n\n" + stamped) if old_note else stamped
            changes.append(("note", "(+appended)", stamped[:60]))

        if not changes:
            return (f"#{fid}: you gave no change at all (new_due / precondition / "
                    f"note_add / new_item). Nothing written to the history.")

        hist = f.get("history")
        if not isinstance(hist, list):
            hist = []
        hist.append({
            "on": _today(),
            "reason": reason,
            "changes": [{"field": c[0], "from": c[1], "to": c[2]} for c in changes],
        })
        f["history"] = hist
        _save_followups(data)

        n = len(hist)
        head = f"#{fid} rescheduled/refined (change no. {n}) -- {f.get('item','')}"
        body = "\n".join(f"  · {c[0]}: {c[1]}  →  {c[2]}" for c in changes)
        tail = f"\n  reason: {reason}"
        warn = ""
        if n >= 3:
            warn = (f"\n\n⚠️ THIS IS RESCHEDULE NO. {n} on this thread. A follow-up that keeps "
                    f"slipping is usually NOT a date problem but a decision problem: either "
                    f"do it, or close it as moot, or say that it depends on {OWNER_NAME} "
                    f"(`flag_for_owner`).")
        return head + "\n" + body + tail + warn

    return f"Error: no follow-up with id #{fid}."


# --- SPILL GUARD for get_followups --------------------------------------------
# WHY: get_followups once returned ~50 000 characters (dozens of open threads
# with long notes); the client SPILLED it to a file and the caller got nothing.
# An overview list that becomes unreadable from its own length is no overview.
# THE SHAPE: this code only runs on a branch that was a complete failure anyway
# (the output already exceeded the spill threshold). Short lists are unchanged.
# THE STAGES ARE NOT SILENT: each says what it left out and how to get it back.
_FOLLOWUP_SPILL_LIMIT = 18000


def _followups_spill_guard(lines: list) -> str:
    out = "\n".join(lines)
    if len(out) <= _FOLLOWUP_SPILL_LIMIT:
        return out

    teljes = len(out)

    def _strip(blocks, prefixes):
        kept, dropped = [], 0
        for blk in blocks:
            parts = blk.split("\n")
            keep = [p for p in parts
                    if not any(p.lstrip().startswith(pf) for pf in prefixes)]
            dropped += len(parts) - len(keep)
            kept.append("\n".join(keep))
        return kept, dropped

    # Stage 1: drop the note/outcome lines
    kept, n_note = _strip(lines, ("> ",))
    out = "\n".join(kept)
    head = (f"⚠️ **SHORTENED VIEW** — the full list was {teljes} characters "
            f"(spill threshold {_FOLLOWUP_SPILL_LIMIT}). Left out: {n_note} note/outcome line(s).\n"
            f"A thread's full note: `get_followup`, `search_memory`, or directly "
            f"`kontextus/followups.json`.\n")
    if len(out) <= _FOLLOWUP_SPILL_LIMIT:
        return head + out

    # Stage 2: the precondition and reschedule lines go too
    kept, n_meta = _strip(kept, ("\u21bb ", "\u26a0", "\u2691 "))
    kozbenso = len(out)
    out = "\n".join(kept)
    head = (f"⚠️ **STRONGLY SHORTENED VIEW** — full: {teljes} characters, "
            f"still {kozbenso} without notes. Left out: {n_note} note line(s) + "
            f"{n_meta} precondition/reschedule line(s).\n"
            f"⛔ If this stage was needed, that IS A SIGNAL in itself: too many open threads "
            f"— the review should start by closing threads, not by reading them.\n")
    if len(out) <= _FOLLOWUP_SPILL_LIMIT:
        return head + out

    # Stage 3: EVERY THREAD ON ONE LINE. Id and date intact, title cut.
    # ⚠️ Lesson from a measurement: naive character cutting made a third of the
    # threads vanish COMPLETELY (exactly the open-ended ones, the easiest to forget).
    # Better many short lines than some full ones and some missing.
    egysoros = []
    for blk in kept:
        elso = blk.split("\n")[0]
        if len(elso) > 110:
            elso = elso[:107] + "..."
        egysoros.append(elso)
    out = "\n".join(egysoros)
    head = (f"⚠️ **TITLE-LINE VIEW** — every thread on ONE line (full: {teljes} "
            f"characters). Titles cut at 110 characters, without note/precondition; "
            f"{n_note} note line(s) + {n_meta} meta line(s) left out.\n"
            f"⛔ If it came to this, that IS A SIGNAL in itself: too many open threads — "
            f"the review should start by CLOSING threads, not by reading them. A thread's "
            f"details: `get_followup` or `kontextus/followups.json`.\n")
    if len(out) <= _FOLLOWUP_SPILL_LIMIT:
        return head + out

    # 4. (theoretical) final safeguard: if even the title-line view does not fit
    return (head + out[:_FOLLOWUP_SPILL_LIMIT] +
            f"\n\n⛔ **CUT HERE** ({len(out)} characters even in the title-line view). "
            f"The missing threads can be read from `kontextus/followups.json`.")


@mcp.tool()
def get_followups(include_resolved: bool = False) -> str:
    """Overview list of Claude's own follow-up threads.

    Sorts open follow-ups by expected date: overdue ones first with an [!]
    mark, then the dated ones, finally the open-ended ones. Complements
    get_deadlines() (the owner's hard deadlines) and get_open_threads()
    (aggregated state sections) -- this is the waiting list Claude actively
    tracks. For the full details of a thread use get_followup(ids).

    Args:
        include_resolved: If True, also lists the already closed follow-ups.

    Returns:
        Formatted list (Markdown).
    """
    data = _load_followups()
    followups = data.get("followups", [])

    open_items = [f for f in followups if f.get("status") == "open"]
    resolved_items = [f for f in followups if f.get("status") != "open"]

    def _sort_key(f):
        eb = f.get("expected_by", "")
        if not eb:
            return (2, "")  # open-ended ones to the end
        days = _days_until(eb)
        return (0 if (days is not None and days < 0) else 1, eb)  # overdue first

    open_items.sort(key=_sort_key)

    if not open_items and not (include_resolved and resolved_items):
        return "No open follow-up. (Add a new one with track_followup.)"

    lines = ["# Follow-up threads\n"]

    if open_items:
        lines.append("## Open")
        for f in open_items:
            eb = f.get("expected_by", "")
            if eb:
                days = _days_until(eb)
                if days is None:
                    tag = eb
                elif days < 0:
                    tag = f"[!] OVERDUE by {abs(days)} days ({eb})"
                elif days == 0:
                    tag = f"TODAY ({eb})"
                elif days <= 3:
                    tag = f"in {days} days ({eb})"
                else:
                    tag = f"{eb} ({days} days)"
            else:
                tag = "tied to a precondition" if (f.get("precondition") or "").strip() else "open-ended"
            line = f"- #{f['id']} | {tag} | {f.get('item', '')}"
            # Structured precondition -- it used to live in prose in the note,
            # where the parser could not see it.
            pre = (f.get("precondition") or "").strip()
            if pre:
                line += f"\n    ⚑ PRECONDITION: {pre}"
            # The reschedule history is NOT a hidden field. A silent date rewrite is
            # the same failure mode as a stale cache -- the next instance would take
            # it at face value. So it shows here how often and why it slipped.
            hist = f.get("history") or []
            if isinstance(hist, list) and hist:
                n = len(hist)
                last = hist[-1] if isinstance(hist[-1], dict) else {}
                line += (f"\n    ↻ rescheduled/refined {n}x (last "
                         f"{last.get('on', '?')}: {last.get('reason', '')})")
                if n >= 3:
                    line += (f"\n    ⚠️ {n} postponements on the same thread — usually NOT a "
                             f"date problem but a decision problem. Do it, close it as "
                             f"moot, or say that it depends on {OWNER_NAME}.")
            if f.get("note"):
                line += f"\n    > {f['note']}"
            lines.append(line)

    if include_resolved and resolved_items:
        lines.append("\n## Closed")
        for f in resolved_items:
            ro = f.get("resolved_on") or "?"
            line = f"- #{f['id']} | closed {ro} | {f.get('item', '')}"
            if f.get("outcome"):
                line += f"\n    > {f['outcome']}"
            lines.append(line)

    return _followups_spill_guard(lines)


_FOLLOWUP_DETAIL_LIMIT = 16000


@mcp.tool()
def get_followup(ids: str) -> str:
    """FULL details of one or more follow-ups: note, precondition, reschedule history.

    ⚠️ BUILT FOR A MEASURED GAP. `get_followups()` is an overview list,
    and with many open threads its spill guard drops first the notes, then the
    precondition/reschedule lines, and finally falls back to a TITLE-LINE VIEW.
    From there its own message pointed to `kontextus/followups.json` -- but that
    is ONE ~50 KB JSON line, which hits the Read token limit, and line-based
    `offset`/`limit` is useless on a one-line file.

    -> The right workflow: `get_followups()` for the PICTURE,
    `get_followup("113,101")` for the DETAILS. That way the spill guard may
    shorten freely (that is its job), because the details can always be
    fetched on demand.

    Args:
        ids: Ids separated by commas (or spaces); "#" is fine too.
            E.g. "113" or "113,101,131" or "#113 #101".

    Returns:
        One block per thread (Markdown). Closed threads come too, marked as such.
        An unknown id is NOT silent: it is reported on a separate line.
    """
    raw = (ids or "").replace(",", " ").replace(";", " ").replace("#", " ")
    kert, rossz = [], []
    for tok in raw.split():
        try:
            n = int(tok)
            if n not in kert:
                kert.append(n)
        except ValueError:
            rossz.append(tok)

    if not kert:
        return ("⚠️ You gave no parseable id. "
                "Example: get_followup(ids='113,101'). The list: `get_followups()`.")

    data = _load_followups()
    by_id = {f.get("id"): f for f in data.get("followups", [])}

    blokkok, hianyzo = [], []
    for n in kert:
        f = by_id.get(n)
        if f is None:
            hianyzo.append(n)
            continue

        nyitott = f.get("status") == "open"
        eb = (f.get("expected_by") or "").strip()
        if eb:
            days = _days_until(eb)
            if days is None:
                datum = eb
            elif days < 0:
                datum = "%s — [!] OVERDUE by %d days" % (eb, abs(days))
            elif days == 0:
                datum = "%s — TODAY" % eb
            else:
                datum = "%s — in %d days" % (eb, days)
        else:
            datum = ("tied to a precondition"
                     if (f.get("precondition") or "").strip() else "open-ended")

        fej_sor = "## #%d · %s · %s" % (n, "OPEN" if nyitott else "CLOSED", datum)
        sorok = [fej_sor, "**Waiting for:** %s" % f.get("item", "")]

        pre = (f.get("precondition") or "").strip()
        if pre:
            sorok.append("⚑ **PRECONDITION:** %s" % pre)

        cov = f.get("covers") or []
        if cov:
            sorok.append("\u2714 **COVERS:** " + ", ".join(str(c) for c in cov))

        hist = f.get("history") or []
        if isinstance(hist, list) and hist:
            sorok.append("↻ **RESCHEDULE HISTORY (%d):**" % len(hist))
            for h in hist:
                if not isinstance(h, dict):
                    sorok.append("  - %s" % h)
                    continue
                reszek = [str(h.get("on", "?"))]
                # each history entry holds a "changes" list (older entries: mezo/rol/ra keys)
                for c in (h.get("changes") or ([h] if h.get("field") else [])):
                    if not isinstance(c, dict):
                        continue
                    fld = c.get("field") or c.get("mezo")
                    if fld:
                        reszek.append("%s: %s -> %s" % (fld, c.get("from", c.get("rol", "?")),
                                                         c.get("to", c.get("ra", "?"))))
                if h.get("reason"):
                    reszek.append("reason: %s" % h["reason"])
                sorok.append("  - " + " | ".join(reszek))
            if len(hist) >= 3:
                sorok.append("  ⚠️ %d postponements on the same thread — usually "
                             "NOT a date problem but a decision problem." % len(hist))

        if not nyitott:
            sorok.append("**Closed:** %s" % (f.get("resolved_on") or "?"))
            if f.get("outcome"):
                sorok.append("**Outcome:** %s" % f["outcome"])

        note = (f.get("note") or "").strip()
        if note:
            sorok.append("**NOTE (full):**")
            sorok.append(note)

        blokkok.append((n, fej_sor, "\n".join(sorok)))

    fej = []

    # The cut must NOT be silent (same principle as the advisory block and the
    # spill-guard stages): if the details do not fit, SAY which thread was
    # shortened and how to fetch it on its own.
    teljes = sum(len(b[2]) for b in blokkok)
    if teljes > _FOLLOWUP_DETAIL_LIMIT:
        koltseg, ujak, vagott = 0, [], []
        for n, fs, txt in blokkok:
            if koltseg + len(txt) <= _FOLLOWUP_DETAIL_LIMIT:
                ujak.append(txt)
                koltseg += len(txt)
            else:
                # The FIRST block gets the FULL budget. If someone requests ONE
                # thread, an 800-character stub would take away exactly the detail
                # they called the tool for -- the stub is for the OTHER blocks
                # that no longer fit.
                keret = _FOLLOWUP_DETAIL_LIMIT if koltseg == 0 else 800
                if len(txt) <= keret:
                    ujak.append(txt)
                    koltseg += len(txt)
                    continue
                rovid = txt[:keret]
                ujak.append(rovid + "\n✂ **[CUT HERE: -%d characters]** — request it "
                                    "on its own: get_followup(ids='%d')" % (len(txt) - keret, n))
                koltseg += len(rovid)
                vagott.append(n)
        szovegek = ujak
        if vagott:
            fej.append("⚠️ **%d block(s) SHORTENED** (the full details would have been "
                       "%d characters, the threshold is %d): %s"
                       % (len(vagott), teljes, _FOLLOWUP_DETAIL_LIMIT,
                          ", ".join("#%d" % n for n in vagott)))
    else:
        szovegek = [b[2] for b in blokkok]

    if hianyzo:
        fej.append("⚠️ **No such id:** "
                   + ", ".join("#%d" % n for n in hianyzo))
    if rossz:
        fej.append("⚠️ **Unparseable token:** " + ", ".join(rossz))

    if not blokkok:
        return "\n".join(fej) if fej else "No match."

    out = "\n\n---\n\n".join(szovegek)
    return (("\n".join(fej) + "\n\n") if fej else "") + out


@mcp.tool()
def resolve_followup(followup_id: int, outcome: str = "") -> str:
    """Close a follow-up (it happened / it became moot).

    Args:
        followup_id: The id of the follow-up to close (see get_followups()).
        outcome: Optional closing note (how it ended).

    Returns:
        Confirmation, or an error if there is no such open id.
    """
    try:
        fid = int(followup_id)
    except (ValueError, TypeError):
        return f"Error: '{followup_id}' is not a valid id."

    data = _load_followups()
    for f in data.get("followups", []):
        if f.get("id") == fid:
            if f.get("status") != "open":
                return f"#{fid} is already closed ({f.get('resolved_on')})."
            f["status"] = "resolved"
            f["resolved_on"] = _today()
            f["outcome"] = (outcome or "").strip()
            _save_followups(data)
            return f"Follow-up closed: #{fid} -- {f.get('item', '')}"
    return f"Error: no open follow-up with id #{fid}."


# --- TODO radar (orphaned markers) ---

# HIGH-PRECISION markers (default). Deliberately narrow: the radar's value is
# its SIGNAL/NOISE ratio -- a list returning 145 items stays unread just like
# nothing at all. Hungarian first, then English.
_MARKER_RE = re.compile(
    r"(⚑"
    r"|\bTEEND[ŐO]K?\b"
    r"|\b(?:TODO|FIXME|XXX)\b"
    r"|élő session kell"
    r"|needs a live session"
    r"|^[-*]\s*\[ \]"          # unticked checkbox
    r"|\bNYITOTT\b"
    r"|\bOPEN\b"
    r")",
    re.MULTILINE,
)

# DEEP mode (deep=True): the Hungarian "-andó/-endő" gerundive ("to be done").
# Many hits, much noise (prose words come in too) -- for a rare monthly sweep.
# (English has no equivalent suffix; use TODO markers instead.)
_MARKER_DEEP_RE = re.compile(r"\b\w{4,}(?:andó|endő)\b")

# Unticked checkboxes are handled separately: they are typically members of
# LONG lists (shopping list, backlog) that drown every other signal -> collapsed.
_CHECKBOX_RE = re.compile(r"^[-*]\s*\[ \]")


_MARKER_RADAR_OFF_RE = re.compile(
    r"^marker[_-]radar\s*:\s*(off|nem|false|ki|no)\b", re.IGNORECASE | re.MULTILINE)


def _marker_radar_off(full_path: str) -> bool:
    """True if the file's frontmatter opts out of the marker radar.

    Key: `marker_radar: off`. Meant for templates/checklists, where an unticked
    `- [ ]` is NOT a todo but the file's PURPOSE, and for closed, historical
    plans. The skip is NOT SILENT: an advisory line at the end of the output
    says how many files were left out this way.
    """
    try:
        with open(full_path, "r", encoding="utf-8") as fh:
            head = fh.read(1200)
    except (OSError, UnicodeDecodeError):
        return False   # the marker sweep itself reports the unreadable file (fail-closed)
    return bool(_MARKER_RADAR_OFF_RE.search(head))


def _file_age_days(full_path: str):
    """How many days ago the file was modified (None if it cannot be determined)."""
    try:
        return int((time.time() - os.path.getmtime(full_path)) // 86400)
    except OSError:
        return None


def _file_mtime_date(full_path: str):
    """The file's last modification as YYYY-MM-DD (None if it cannot be determined).

    Serves the staleness guard for FILE-LEVEL `covers`. Date granularity is
    enough: the `created` field in `followups.json` only stores a date too.
    """
    try:
        return time.strftime("%Y-%m-%d", time.localtime(os.path.getmtime(full_path)))
    except OSError:
        return None


# If any of these is on the line, the marker is already closed -> not a finding.
# Hungarian first (case-insensitive); the English words only count in UPPER CASE,
# so a prose "TODO: get it done" is not mistaken for a closed item.
_MARKER_DONE_RE = re.compile(
    r"(✅|LEZÁRVA|LEZARVA|MEGTÖRTÉNT|MEGTORTENT|\bKÉSZ\b|\bKESZ\b"
    r"|⚑\s*nincs|ZÁRVA|ZARVA|okafogyott|elvetve|ELVETVE"
    r"|(?-i:\bDONE\b|\bCLOSED\b|\bRESOLVED\b|\bCOMPLETED\b|\bWONTFIX\b)"
    r"|⚑\s*none|\bmoot\b)",
    re.IGNORECASE,
)

# Live (non-archive, non-historical) memory areas. naplo/ and archiv/ are
# DELIBERATELY left out: they are historical snapshots, a "todo" in them was
# true when the entry was written -- not an open thread.
_MARKER_SCAN_DIRS = ["", "kontextus", "projektek", "eszkozok", "claude_sajat"]

# External, read-only documentation mirrors (OUTSIDE the memory root), which
# also produce "TODO:" lines that nothing else would sweep.
# Configure with "extra_marker_roots": ["<folder>", ...] in memoria.config.json (e.g. a robot's docs).
_MARKER_EXTRA_ROOTS = [os.path.expanduser(p) for p in _CONFIG.get("extra_marker_roots", [])]

_MARKER_STOPWORDS = {
    "amit", "amikor", "ahogy", "ahol", "annak", "ezzel", "ehhez", "erre", "arra",
    "nincs", "lenne", "kell", "kellene", "lehet", "minden", "mindig", "utan",
    "elott", "kozben", "mert", "hogy", "amely", "melyik", "claude",
    "session", "sessionben", "napló", "naplo", "memoria", "memória",
}


def _marker_tokens(text: str) -> set:
    """Meaning-bearing tokens (>=5 letters), lower-cased, accent-tolerant."""
    raw = re.findall(r"[0-9A-Za-zÁÉÍÓÖŐÚÜŰáéíóöőúüű_]{5,}", text.lower())
    return {t for t in raw if t not in _MARKER_STOPWORDS}


# --- SCORING of follow-up coverage ------------------------------------------
# WHY: the old rule was ">=2 shared >=5-letter tokens -> maybe covered: #N".
# Measured on the live corpus, the tag's PRECISION was about 20%: most tags
# rested on 2-3 generic words. NULL MODEL: by PURE CHANCE a few percent of all
# marker x follow-up pairs reach >=2 overlap -- per marker a ~19% chance of a
# false tag. Only the one true pair reached >=4 overlap.
# THE HARM IS ASYMMETRIC, so we push towards precision:
#   - a false "covered" -> the next instance SKIPS a really open item (silent harm),
#   - a missing tag      -> it checks by hand (cheap).
# BUT the fix cannot be a mere threshold raise, because that would be a SILENT
# filter again. So there are THREE bands, and EACH PRINTS ITS EVIDENCE (which
# tokens match), so the reader decides by looking, not by trusting the tag.
_MARKER_IDENT_RE = re.compile(r"[0-9_]")


def _coverage_score(overlap: set) -> float:
    """Weighted score of the overlap.

    Identifier-like tokens (containing a digit or an underscore -- `bme280`,
    `vl53l1x`, `sentinel_py`) count DOUBLE: they practically never match by
    chance, while long common nouns do.
    """
    return sum(2.0 if _MARKER_IDENT_RE.search(t) else 1.0 for t in overlap)


# --- DECLARED coverage -- the structured field beats guessing ---
# WHY: lexical scoring CANNOT be fixed by weighting. A follow-up whose note
# QUOTES the marker corpus won most of the lexical tags while holding a small
# share of the token mass. Four weighting schemes (identifier x2, IDF, length
# normalisation, IDF + norm.) were measured on the same live corpus; NONE
# helped. The cause is not a parameter bug but a REPRESENTATIONAL limit: a
# bag-of-words scheme cannot tell "I FOLLOW this marker" from "I QUOTE this
# marker AS AN EXAMPLE". Even parsing explicit `file.md L65` references fails,
# because a quoting note contains them verbatim.
# THE FIX is therefore not better guessing but DECLARATION: the follow-up's
# `covers` field states, structured, which marker(s) it replaces. A prose quote
# can never disguise itself as a structured claim. *What can be declared,
# should not be guessed.*
_COVERS_SPLIT_RE = re.compile(r"[:#]|\s+[Ll](?=\d)")


def _parse_covers_entry(entry: str):
    """'projektek/garden.md:65' -> ('projektek/garden.md', 65)."""
    e = (entry or "").strip().replace("\\", "/")
    if not e:
        return None
    parts = _COVERS_SPLIT_RE.split(e, maxsplit=1)
    path = parts[0].strip().strip("/")
    line = None
    if len(parts) > 1:
        m = re.match(r"\s*(\d{1,5})", parts[1])
        if m:
            line = int(m.group(1))
    return (path.lower(), line) if path else None


def _declared_index(open_fus_raw: list) -> list:
    """[(followup_id, path_lower, line_or_None)] from the `covers` fields of open follow-ups."""
    idx = []
    for f in open_fus_raw:
        for entry in (f.get("covers") or []):
            parsed = _parse_covers_entry(entry)
            if parsed:
                idx.append((f.get("id"), parsed[0], parsed[1],
                            (f.get("created") or "")))
    return idx


def _declared_cover_for(rel_path: str, lineno: int, decl_idx: list,
                        file_mtime_date: str = None):
    """Look up line-exact declared coverage. An entry without a line = the whole file.

    `file_mtime_date` (YYYY-MM-DD) is optional. If given AND the file changed
    AFTER the follow-up was created, FILE-LEVEL coverage drops into a weaker
    band (`fajl-szintu-elavult`) -- see `_coverage_tag`.

    Matching is SUFFIX-based (`covers` may hold a short or a full path), but
    the boundary must be `/` -- so `sensing.md` does not match `oversensing.md`.
    """
    rp = (rel_path or "").replace("\\", "/").lower()
    best = None
    for rec in decl_idx:
        fid, path, line = rec[0], rec[1], rec[2]
        created = rec[3] if len(rec) > 3 else ""
        if not (rp == path or rp.endswith("/" + path)):
            continue
        if line is not None and line != lineno:
            continue
        # a line-exact match is stronger than a file-level one
        rank = 1 if line is not None else 0
        if best is None or rank > best[0]:
            best = (rank, fid, line, created)
    if best is None:
        return None
    if best[2]:
        return ("deklaralt", best[1], 99.0, ["sor-pontos"])
    # STALENESS GUARD: a FILE-LEVEL declaration applies to the content the
    # declarer SAW. If the file changed since, the declaration does not vouch for
    # the new lines -- yet it used to appear in the strongest ("fact, not a
    # guess") band, tagging a later, COMPLETELY DIFFERENT todo as tracked.
    created = best[3] or ""
    if file_mtime_date and created and file_mtime_date > created:
        return ("deklaralt", best[1], 99.0,
                ["fajl-szintu-elavult", created, file_mtime_date])
    return ("deklaralt", best[1], 99.0, ["fajl-szintu"])


def _best_coverage(toks: set, open_fus: list):
    """The BEST (not the first!) matching follow-up + its evidence.

    Returns:
        None if nothing reaches the weak band, otherwise
        (band, followup_id, score, sorted_token_list), where the band is
        "eros" (strong, >=4.0 points) or "gyenge" (weak, 2.0-3.9 points).
    """
    best = None
    for fid, ftoks in open_fus:
        ov = toks & ftoks
        if len(ov) < 2:
            continue
        sc = _coverage_score(ov)
        if best is None or sc > best[2]:
            # the evidence tokens: identifier-like ones first
            ev = sorted(ov, key=lambda t: (0 if _MARKER_IDENT_RE.search(t) else 1, -len(t)))
            best = ("eros" if sc >= 4.0 else "gyenge", fid, sc, ev)
    return best


def _coverage_tag(covered) -> str:
    """The coverage tag -- TOGETHER with its evidence."""
    if not covered:
        return ""
    sav, fid, _sc, ev = covered
    shown = ", ".join(ev[:3])
    rest = f" +{len(ev) - 3}" if len(ev) > 3 else ""
    if sav == "deklaralt":
        if ev and ev[0] == "fajl-szintu-elavult":
            when = f" (declared {ev[1]}, the file changed since: {ev[2]})" if len(ev) > 2 else ""
            return (f"  `[≈ FILE-LEVEL cover: #{fid} — the declaration is for the FILE, "
                    f"not for THIS line{when}; if the line was written AFTER the declaration, "
                    f"#{fid} does NOT follow it — CHECK]`")
        return f"  `[✔ TRACKED: #{fid} · declared ({ev[0]}) — not a guess]`"
    if sav == "eros":
        return f"  `[~ maybe covered: #{fid} · {shown}{rest}]`"
    return f"  `[? weak word match: #{fid} · {shown}{rest} — probably chance, CHECK]`"


@mcp.tool()
def get_open_markers(include_covered: bool = True, deep: bool = False,
                     max_per_file: int = 12, fresh_days: int = 30,
                     include_old: bool = False) -> str:
    """Mechanical sweep of orphaned TODO markers in the LIVE memory.

    WHY: a manual "go through the ⚑ marks in _state.md" step had two flaws.
    (1) It was narrowed to _state.md -- the real open markers once all sat in a
    project file the step never looked at, all weeks stale. (2) It left the
    recognition to (model) attention, although this is a mechanical sweep.
    Same principle as the deadline whitelist: the PARSER keeps the convention,
    not the author.

    WHAT IT LOOKS FOR (default): ⚑ · TODO/FIXME/XXX · TEENDŐ · OPEN/NYITOTT ·
    "needs a live session" / "élő session kell" · unticked checkbox (`- [ ]`).
    deep=True adds the Hungarian "-andó/-endő" gerundive -- many more hits,
    but lots of prose noise; for a rare monthly sweep.
    WHERE: _state.md + kontextus/ + projektek/ + eszkozok/ + claude_sajat/, plus
    any configured `extra_marker_roots`. naplo/ and archiv/ are DELIBERATELY
    left out -- they are historical snapshots.
    WHAT IT FILTERS: if the line carries ✅ / DONE / CLOSED / RESOLVED /
    LEZÁRVA / KÉSZ / MEGTÖRTÉNT / "⚑ none" etc., the marker is already closed ->
    not returned.

    NOISE HANDLING: most hits once came from files nobody had touched for
    months (an old shopping list, a template checklist, an old backlog). Such a
    list stays unread just like nothing -- so (a) a file can opt out with the
    `marker_radar: off` frontmatter key, (b) markers of files older than
    `fresh_days` come COLLAPSED (count + first marker text + age). NEITHER
    filter is SILENT: both appear as numbers in the output.

    ⚠️ The returned list is a SUSPICION list, not a todo list. Closing a marker
    needs EVIDENCE (a log entry, code, a photo, a ledger line). If the evidence
    is ambiguous, LEAVE IT -- this is the confirmation-bias guard.

    Args:
        deep: If True, the "-andó/-endő" gerundive sweep runs too (noisy).
        include_covered: If True (default), markers probably covered by an open
            follow-up are listed too, with a "[~ maybe covered: #N · <evidence>]"
            tag. If False, it ONLY leaves out the STRONG band -- a weak word
            match does NOT hide a marker, because measurements show it is
            mostly chance.
        max_per_file: Upper limit per file (noise protection).
        fresh_days: Markers of files younger than this come with a FULL list;
            older ones collapsed (one line per file: age + count + the text of
            the first non-checkbox marker). Default 30.
        include_old: If True, old files come with a full list too (for a rare
            monthly sweep).

    Returns:
        A list grouped by file (Markdown), with line numbers.
    """
    try:
        max_per_file = max(1, int(max_per_file))
    except (TypeError, ValueError):
        max_per_file = 12
    try:
        fresh_days = max(0, int(fresh_days))
    except (TypeError, ValueError):
        fresh_days = 30

    inc = []          # FAIL-CLOSED: checks that could not run

    # 1. Tokenise the open follow-ups for cross-referencing
    open_fus = []
    decl_idx = []
    try:
        _open_raw = [f for f in _load_followups().get("followups", [])
                     if f.get("status") == "open"]
        for f in _open_raw:
            blob = f"{f.get('item', '')} {f.get('note', '')}"
            open_fus.append((f.get("id"), _marker_tokens(blob)))
        decl_idx = _declared_index(_open_raw)   # declared coverage
    except Exception as e:
        # FAIL-CLOSED: without this EVERY coverage tag silently vanishes, and the
        # list looks as if dozens of ownerless markers stood in the system.
        _radar_note(inc, "followups.json",
                    "the coverage index was not built (EVERY [✔]/[~] tag is missing)", e)

    # 2. Collect the files to examine
    targets = []  # (display_name, absolute_path)
    opted_out = []  # `marker_radar: off` -- skipped, but NOT silently
    snapshots = []  # dated snapshot copies -- skipped, but NOT silently
    root = os.path.realpath(MEMORIA_ROOT)
    for sub in _MARKER_SCAN_DIRS:
        d = os.path.join(root, sub) if sub else root
        if not os.path.isdir(d):
            continue
        for name in sorted(os.listdir(d)):
            if not name.lower().endswith(".md"):
                continue
            full = os.path.join(d, name)
            if not os.path.isfile(full):
                continue
            rel = f"{sub}/{name}" if sub else name
            # A dated SNAPSHOT copy -- same reason as excluding naplo/ and
            # archiv/: a historical still, not a live carrier of todos (a backup
            # next to the context file used to return its markers TWICE).
            if _is_snapshot_file(name):
                snapshots.append(rel)
                continue
            if _marker_radar_off(full):
                opted_out.append(rel)
                continue
            targets.append((rel, full, rel))
    for extra in _MARKER_EXTRA_ROOTS:
        if not os.path.isdir(extra):
            continue
        label_root = os.path.basename(os.path.dirname(extra))
        for name in sorted(os.listdir(extra)):
            if name.lower().endswith(".md"):
                full = os.path.join(extra, name)
                if not os.path.isfile(full):
                    continue
                lbl = f"[{label_root}] {os.path.basename(extra)}/{name}"
                rel = f"{label_root}/{os.path.basename(extra)}/{name}"
                if _is_snapshot_file(name):
                    snapshots.append(lbl)
                    continue
                if _marker_radar_off(full):
                    opted_out.append(lbl)
                    continue
                targets.append((lbl, full, rel))

    # 3. Sweep
    findings = []  # (name, [(line, text, covering_id, checkbox?)], extra_boxes, age_days)
    total = 0
    lex_labels = {}   # token-magnet guard: how many LEXICAL tags each follow-up wins
    decl_hits = 0
    stale_decl_hits = 0   # stale FILE-LEVEL declarations
    for label, full, relpath in targets:
        hits = []
        _fmdate = _file_mtime_date(full)
        try:
            with open(full, "r", encoding="utf-8") as fh:
                lines = fh.readlines()
        except (OSError, UnicodeDecodeError) as e:
            # FAIL-CLOSED: without this the file's markers vanish SILENTLY, while
            # the header still says "N markers / M files".
            _radar_note(inc, label, "unreadable (its markers were left out of the sweep)", e)
            continue
        for i, line in enumerate(lines, start=1):
            s = line.strip()
            if not s or len(s) < 8:
                continue
            if s.startswith("status:") or s.startswith("updated:"):
                continue
            if not (_MARKER_RE.search(s) or (deep and _MARKER_DEEP_RE.search(s))):
                continue
            if _MARKER_DONE_RE.search(s):
                continue
            toks = _marker_tokens(s)
            # DECLARED coverage always beats lexical guessing
            covered = _declared_cover_for(relpath, i, decl_idx, _fmdate)
            if covered:
                if covered[3] and covered[3][0] == "fajl-szintu-elavult":
                    stale_decl_hits += 1
                else:
                    decl_hits += 1
            else:
                covered = _best_coverage(toks, open_fus)
                if covered:
                    lex_labels[covered[1]] = lex_labels.get(covered[1], 0) + 1
            # `include_covered=False` hides ONLY the STRONG coverage. Formerly a
            # weak >=2-token match hid the marker too -- i.e. a chance word match
            # SILENTLY removed a really open item.
            # STALE file-level coverage must NOT silence a marker either -- the
            # whole point is that the declaration does not vouch for the line.
            _stale_fd = bool(covered and covered[0] == "deklaralt"
                             and covered[3] and covered[3][0] == "fajl-szintu-elavult")
            if (covered and covered[0] in ("eros", "deklaralt")
                    and not include_covered and not _stale_fd):
                continue
            shown = s if len(s) <= 170 else s[:167].rstrip() + "..."
            hits.append((i, shown, covered, bool(_CHECKBOX_RE.match(s))))
            if len(hits) >= max_per_file * 4:
                break
        if hits:
            boxes = [h for h in hits if h[3]]
            others = [h for h in hits if not h[3]]
            keep = others[:max_per_file] + boxes[:3]
            keep.sort(key=lambda h: h[0])
            extra_boxes = max(0, len(boxes) - 3)
            findings.append((label, keep, extra_boxes, _file_age_days(full)))
            total += len(keep) + extra_boxes

    # 4. Output
    out = ["# Open markers (TODO radar)"]
    out.append(
        "_A suspicion list, NOT a todo list. Closing needs evidence; "
        "with ambiguous evidence LEAVE IT (confirmation-bias guard)._\n"
        "_Coverage bands: `[✔ TRACKED]` = DECLARED (`covers` field, a fact) · "
        "`[≈ FILE-LEVEL cover]` = the declaration was for the FILE and the file has "
        "CHANGED since (the line may be newer than the declaration -- CHECK) · "
        "`[~ maybe covered]` = strong lexical suspicion · `[? weak word match]` = "
        "probably chance._"
    )
    if not findings:
        out.append("\n✅ No orphaned marker in the live memory areas.")
        return "\n".join(out)

    if include_old:
        fresh, old_files = findings, []
    else:
        fresh = [f for f in findings if f[3] is None or f[3] <= fresh_days]
        old_files = [f for f in findings if f[3] is not None and f[3] > fresh_days]

    old_cnt = sum(len(h) + eb for _l, h, eb, _a in old_files)
    out.append(
        f"\n**{total} marker(s) / {len(findings)} file(s)** "
        f"(fresh: {total - old_cnt} / {len(fresh)} file(s) · "
        f"old (>{fresh_days} days): {old_cnt} / {len(old_files)} file(s))."
    )

    # --- TOKEN-MAGNET GUARD ----------------------------------------------------
    # The known, MEASURED flaw of lexical tagging: a self-documenting follow-up
    # (one that QUOTES the marker corpus) wins disproportionately many tags. It
    # cannot be fixed by weighting -- so the tool does not hide the degradation
    # but SAYS IT. *The limit says it was a limit.*
    lex_total = sum(lex_labels.values())
    if decl_hits:
        out.append(
            f"\n✔ **{decl_hits} DECLARED cover(s)** (from the follow-up `covers` field) "
            "-- a FACT, not a guess."
        )
    # Degraded file-level coverage is NOT silenced -- we say how many we found.
    if stale_decl_hits:
        out.append(
            f"\n≈ **{stale_decl_hits} STALE FILE-LEVEL cover(s)**: the `covers` was for the WHOLE "
            "file, but the file changed since the declaration -- these lines MAY BE "
            "newer than the declaration, so the follow-up does NOT necessarily follow them. "
            "➜ Declare what it really covers LINE-EXACT: "
            "`set_followup_covers(#N, [\"path.md:line\"], replace=True)`."
        )
    if lex_total >= 3:
        top_id, top_n = max(lex_labels.items(), key=lambda kv: kv[1])
        share = top_n / lex_total
        if share >= 0.5:
            out.append(
                f"\n⚠️ **TOKEN-MAGNET SUSPICION**: of the {lex_total} lexical tags "
                f"**{top_n} ({share:.0%}) point to the same follow-up #{top_id}**. "
                "If its note QUOTES the marker corpus (typically: a meta thread about "
                "this server's own code), most of these are FALSE -- lexical "
                "matching cannot tell `I follow` from `I quote`. "
                "➜ Whatever a follow-up really covers, DECLARE it: "
                "`set_followup_covers(#N, [\"path.md:line\"])`."
            )

    def _emit(label, hits, extra_boxes, age):
        age_txt = f"  _(last modified: {age} days ago)_" if age is not None else ""
        out.append(f"\n## {label}{age_txt}")
        for lineno, text, covered, _is_box in hits:
            out.append(f"- L{lineno}: {text}{_coverage_tag(covered)}")
        if extra_boxes:
            out.append(
                f"- _(+{extra_boxes} more unticked `- [ ]` in this file — "
                "long list, collapsed)_"
            )

    for label, hits, extra_boxes, age in fresh:
        _emit(label, hits, extra_boxes, age)

    if old_files:
        out.append(
            f"\n## 🗄️ Old files (untouched for >{fresh_days} days) — collapsed"
        )
        out.append(
            "_Not gone, it just does not dominate the list. Full list: "
            "`get_open_markers(include_old=True)`._"
        )
        for label, hits, extra_boxes, age in sorted(old_files, key=lambda f: f[3]):
            n = len(hits) + extra_boxes
            lead = next((h[1] for h in hits if not h[3]), hits[0][1] if hits else "")
            if len(lead) > 95:
                lead = lead[:92].rstrip() + "..."
            out.append(f"- **{label}** · {age} days ago · {n} marker(s) — _{lead}_")

    if opted_out:
        out.append(
            f"\n_⏭️ Skipped because of `marker_radar: off`: {len(opted_out)} file(s) "
            f"({', '.join(sorted(opted_out))})._"
        )

    blk = _radar_incident_block(
        inc,
        "TODO: the radar did NOT see the markers of these files. Until they are "
        "readable, the count above is a lower bound, not a balance.")
    if blk:
        out.append(blk)

    if snapshots:
        # THE FILTER IS NOT SILENT (advisory principle).
        out.append(f"\n_⏭️ Skipped as dated SNAPSHOT copies: {len(snapshots)} file(s) "
                   f"({', '.join(snapshots[:4])}"
                   f"{' …' if len(snapshots) > 4 else ''}). If such a file sits in the LIVE "
                   "area, that alone is a hygiene signal: it belongs in `archiv/`._")
    out.append(
        "\n---\n_Not examined: `naplo/`, `archiv/` (historical snapshots). "
        "If a marker is missing here but still open: it probably sits on a line "
        "read as closed (✅ / DONE / KÉSZ / LEZÁRVA) -- split that line by hand._"
    )
    return "\n".join(out)


# --- Unlogged-changes radar ---

_UNLOGGED_SKIP_DIRS = {"naplo", "archiv", "data", "__pycache__", ".git", "outputs",
                       "venv", ".venv", "node_modules", "foto", "backup"}
_UNLOGGED_SKIP_FILE_RE = re.compile(
    r"(^server_backup_|^server_v\d|^_processed\.json$|^followups\.json$|"
    # `_onismeret_stamp.json` is pure SERVER BOOKKEEPING -- get_patterns writes it
    # at every session start. If it stayed in, the 🔴 SILENT DAY bucket would
    # fire on it every morning for no reason.
    r"^_onismeret_stamp\.json$|"
    r"\.pyc$|\.log$|~$|^\.)", re.IGNORECASE)
_UNLOGGED_EXTS = (".md", ".py", ".json", ".ps1")
_NAPLO_DATE_RE = re.compile(r"^(\d{4}-\d{2}-\d{2})[_.]")
_UNLOGGED_SLACK_S = 2 * 3600
_NAPLO_ROUTINE_RE = re.compile(r"_(reggeli|vasarnapi)\.md$", re.IGNORECASE)

# --- SCHEDULED-LOG coverage -- SESSION level, not DAY level ---
# WHY: a scheduled autonomous session once did real work and then stopped
# WITHOUT updating the log AND _state.md -- exactly the failure this radar was
# built for. Yet the radar reported ✅ **0 SILENT DAYS**. Reason: the unit of the
# coverage check was the DAY, while the thing sought is a SESSION. That day
# several interactive sessions ran and counted as "substantive logs", so the
# day looked covered.
# ➜ A silent scheduled session is invisible exactly on the days when the owner
# also worked with Claude -- i.e. on most days. The checker reported its OWN
# blindness with a ✅.
# THE FIX is not a heuristic: the daily scheduled tasks ALWAYS write a log with
# a fixed name pattern. If a closed day has no `*_agencia.md`, there are two
# readings -- (a) the task did not run (the lastRunAt guard catches that), or
# (b) it stopped SILENTLY (which NOTHING caught so far).
_DAILY_SCHEDULED_LOGS = (
    ("agencia", re.compile(r"_agencia(_\d+)?\.md$", re.IGNORECASE), "autonomous session"),
    ("reggeli", re.compile(r"_reggeli\.md$", re.IGNORECASE), "morning summary"),
)


def _unlogged_extra_roots():
    """Self-developed areas OUTSIDE the memory root."""
    # this server's own folder, plus "extra_unlogged_roots" from memoria.config.json:
    # [{"path": "<folder>", "label": "robot/docs", "recursive": true}, ...]
    roots = [(os.path.dirname(os.path.abspath(__file__)), "memoria-mcp", False)]
    for r in _CONFIG.get("extra_unlogged_roots", []):
        try:
            roots.append((os.path.expanduser(r["path"]), r.get("label") or os.path.basename(r["path"]),
                          bool(r.get("recursive", True))))
        except (KeyError, TypeError):
            continue
    return roots


def _unlogged_collect(cutoff_ts, inc=None):
    found = []
    root = os.path.realpath(MEMORIA_ROOT)
    for dirpath, dirnames, filenames in os.walk(root):
        dirnames[:] = [d for d in dirnames if d not in _UNLOGGED_SKIP_DIRS]
        for name in filenames:
            if not name.lower().endswith(_UNLOGGED_EXTS) or _UNLOGGED_SKIP_FILE_RE.search(name):
                continue
            full = os.path.join(dirpath, name)
            try:
                mt = os.path.getmtime(full)
            except OSError as e:
                # FAIL-CLOSED: without this the file SILENTLY drops out of the change
                # list, the coverage check never sees it -> a false "0 SILENT DAYS".
                _radar_note(inc, os.path.relpath(full, root).replace(os.sep, "/"),
                            "mtime unreadable (left out of the coverage check)", e)
                continue
            if mt >= cutoff_ts:
                found.append((os.path.relpath(full, root).replace(os.sep, "/"), name, mt))
    for base, label, recurse in _unlogged_extra_roots():
        if not os.path.isdir(base):
            continue
        walker = os.walk(base) if recurse else [(base, [], os.listdir(base))]
        for dirpath, dirnames, filenames in walker:
            if recurse:
                dirnames[:] = [d for d in dirnames if d not in _UNLOGGED_SKIP_DIRS]
            for name in filenames:
                if not name.lower().endswith(_UNLOGGED_EXTS) or _UNLOGGED_SKIP_FILE_RE.search(name):
                    continue
                full = os.path.join(dirpath, name)
                if not os.path.isfile(full):
                    continue
                try:
                    mt = os.path.getmtime(full)
                except OSError as e:
                    # FAIL-CLOSED (see above).
                    _radar_note(inc,
                                f"[{label}] " + os.path.relpath(full, base).replace(os.sep, "/"),
                                "mtime unreadable (left out of the coverage check)", e)
                    continue
                if mt >= cutoff_ts:
                    sub = os.path.relpath(full, base).replace(os.sep, "/")
                    found.append((f"[{label}] {sub}", name, mt))
    return found


def _unlogged_naplo_index(inc=None):
    idx = {}
    d = os.path.join(os.path.realpath(MEMORIA_ROOT), "naplo")
    if not os.path.isdir(d):
        return idx
    for name in sorted(os.listdir(d)):
        m = _NAPLO_DATE_RE.match(name)
        if not m or not name.lower().endswith(".md"):
            continue
        full = os.path.join(d, name)
        try:
            with open(full, "r", encoding="utf-8") as fh:
                text = fh.read().lower()
            nmt = os.path.getmtime(full)
        except (OSError, UnicodeDecodeError) as e:
            # FAIL-CLOSED: losing an unreadable log produces a FALSE 🔴 (the day
            # looks uncovered) -- say so, do not guess.
            _radar_note(inc, f"naplo/{name}",
                        "log file unreadable (its day looks uncovered)", e)
            continue
        idx.setdefault(m.group(1), []).append(
            (name, bool(_NAPLO_ROUTINE_RE.search(name)), text, nmt))
    return idx


def _unlogged_carry_index(naplo, inc=None):
    """CARRY-OVER log coverage: {mtime_day: [(file_name, routine?, text, mtime, file_name_day)]}.

    A multi-day session writes its log into the file named with its START date
    throughout -- so the file name does not say WHEN an entry was written, only
    when the file was OPENED. The coverage question, however, is chronological:
    "did anyone write a log AFTER the change?" The data that answers it is the
    MTIME, not the file name.

    No new I/O: works from the already loaded index.
    """
    carry = {}
    for fdate, entries in naplo.items():
        try:
            fd = datetime.strptime(fdate, "%Y-%m-%d").date()
        except (ValueError, TypeError):
            continue
        for e in entries:
            md = datetime.fromtimestamp(e[3]).date()
            if md > fd:
                carry.setdefault(md.isoformat(), []).append(e + (fdate,))
    return carry


def _archived_naplo_names():
    """The ALREADY ARCHIVED log file names by date (`archiv/raw/naplo/`).

    ⚠️ NOT DECORATION BUT THE ROOT FIX OF A FALSE POSITIVE. A first attempt
    tried to guess the archival edge with a heuristic (do not look below
    `min(naplo)`). Measured on the real corpus, almost all hits were FALSE --
    archival is NOT a clean date cut (an older day may still be in the live
    `naplo/` while later days are archived, and single days split across the
    two places file by file). A filter guessing the edge cannot know this.
    ➜ Do not guess where the edge is: LOOK IN THE OTHER FOLDER too. "What can
    be declared, should not be guessed."
    """
    idx = {}
    base = os.path.join(os.path.realpath(MEMORIA_ROOT), "archiv", "raw", "naplo")
    if not os.path.isdir(base):
        return idx
    try:
        names = os.listdir(base)
    except OSError:
        return idx
    for name in names:
        m = _NAPLO_DATE_RE.match(name)
        if m and name.lower().endswith(".md"):
            idx.setdefault(m.group(1), []).append(name)
    return idx


def _missing_scheduled_logs(naplo, days, today_iso, archived=None):
    """Which CLOSED days lack the log entry of a daily scheduled task.

    Today is DELIBERATELY left out (the log is written at the END of the
    session -- same principle as the ⏳ TODAY bucket).

    The live (`naplo/`) AND archived (`archiv/raw/naplo/`) file names of a day
    count TOGETHER -- see the `_archived_naplo_names` docstring. If a day has
    NO log file at all in either place, the day CANNOT be judged (we cannot
    tell "did not run" from "archival took another path"); such days are
    counted SEPARATELY and PRINTED -- the filter must not be silent.
    """
    try:
        today = datetime.strptime(today_iso, "%Y-%m-%d").date()
    except (ValueError, TypeError):
        return [], (0, None)
    archived = _archived_naplo_names() if archived is None else archived
    if not naplo and not archived:
        return [], (0, None)
    rows, skipped = [], []
    for k in range(1, max(1, int(days)) + 1):
        d = (today - timedelta(days=k)).isoformat()
        live = [e[0] for e in naplo.get(d, [])]
        names = live + [n for n in archived.get(d, []) if n not in live]
        if not names:
            skipped.append(d)
            continue
        miss = [(label, task) for label, rx, task in _DAILY_SCHEDULED_LOGS
                if not any(rx.search(n) for n in names)]
        if miss:
            rows.append((d, miss, live))
    return rows, (len(skipped), (min(skipped) + ".." + max(skipped)) if skipped else None)


@mcp.tool()
def get_unlogged_changes(days: int = 7, grace_days: int = 1) -> str:
    """Radar for unlogged changes -- against the "silent session end" failure mode.

    WHY: a session once did real work (a new MCP tool, closing stale markers,
    a new follow-up) and then ended WITHOUT a log entry AND without updating
    _state.md. Every existing guard let it through: the heartbeat looks at
    `lastRunAt` (the task RAN -> looks healthy), get_context's "last 3 days"
    only shows there was no such session that day, and the _state.md index
    still pointed to the previous day. The work was on disk, its trace
    nowhere. A later instance would have found an undocumented tool of unknown
    origin in server.py.

    WHAT IT DOES: compares the file mtimes of the LIVE memory and own-code areas
    with the `naplo/` entries. Buckets:
      🔴 SILENT DAY   -- something changed, but that day (±grace) there is NO
                         substantive (non-morning) log entry.
      ⏳ TODAY        -- the same, BUT on today's, not yet closed day.
                         WHY SEPARATE: the log is written at the END of the
                         session, while the radar is called in the MIDDLE of
                         it -- so every trace of the running session used to
                         look 🔴, and the result depended on the call order.
      [?] not mentioned -- there is a substantive log, but the file is not named in it.

    CAUSAL FILTER: a log entry only counts as coverage if WRITING it (mtime)
    does not precede the file change (2 hours slack). Without this a generic
    file-name mention in a previous day's entry would give false coverage.
    The extension-less stem only counts as evidence above 10 characters (a
    short stem like "server" matched an unrelated "server runs" sentence).

    ⚠️ A SUSPICION list, not an indictment: a file can change legitimately
    without the log naming it. On a SILENT DAY, the file mtime + the session
    transcript together reconstruct what happened.

    Args:
        days: How many days back (1-60, default 7).
        grace_days: How many days of slip to accept for log coverage (0-3,
            default 1) -- because of sessions running past midnight.
    """
    try:
        days = max(1, min(int(days), 60))
    except (TypeError, ValueError):
        days = 7
    try:
        grace_days = max(0, min(int(grace_days), 3))
    except (TypeError, ValueError):
        grace_days = 1

    cutoff_ts = time.time() - days * 86400
    inc = []          # FAIL-CLOSED: checks that could not run
    changed = _unlogged_collect(cutoff_ts, inc)
    naplo = _unlogged_naplo_index(inc)

    # Session-level coverage check, INDEPENDENT of file mtimes.
    today_iso_pre = datetime.now().date().isoformat()
    # The CALLER passes the archive index (the callee does not collect it): so
    # isolating tests can stub it, and the function stays pure logic.
    missing_sched, sched_meta = _missing_scheduled_logs(
        naplo, days, today_iso_pre, _archived_naplo_names())

    # TODAY is a separate bucket. Reason below, at the rendering.
    today_iso = datetime.now().date().isoformat()
    # CARRY-OVER coverage -- see `_unlogged_carry_index`.
    carry_idx = _unlogged_carry_index(naplo, inc)
    silent, today_open, unmentioned, covered_n = {}, {}, {}, 0
    carry_days = {}
    for rel, name, mt in changed:
        day = datetime.fromtimestamp(mt).date()
        window = [(day + timedelta(days=k)).isoformat()
                  for k in range(-grace_days, grace_days + 1)]
        entries = [e for d in window for e in naplo.get(d, [])
                   if e[3] >= mt - _UNLOGGED_SLACK_S]
        substantive = [e for e in entries if not e[1]]
        stem = os.path.splitext(name)[0].lower()
        blob = "\n".join(e[2] for e in entries)
        mentioned = (name.lower() in blob) or (len(stem) >= 10 and stem in blob)
        key = day.isoformat()
        if not substantive:
            # Before saying 🔴, look at the CARRY-OVER logs too (same causal
            # filter: writing the log must not precede the change).
            cover = [e for e in carry_idx.get(key, [])
                     if not e[1] and e[3] >= mt - _UNLOGGED_SLACK_S]
            if cover and key != today_iso:
                carry_days.setdefault(key, {"files": [], "logs": {}})
                carry_days[key]["files"].append((rel, mt))
                for e in cover:
                    carry_days[key]["logs"][e[0]] = e[4]
                continue
            bucket = today_open if key == today_iso else silent
            bucket.setdefault(key, []).append((rel, mt, bool(naplo.get(key))))
        elif not mentioned:
            unmentioned.setdefault(key, []).append((rel, mt))
        else:
            covered_n += 1

    out = ["# Unlogged changes (silent-session-end radar)",
           "_A suspicion list, NOT an indictment. The goal is to catch the SILENT DAY: when the "
           "work landed but its trace did not._",
           f"\n**Window: last {days} days · {len(changed)} changed file(s) · "
           f"{covered_n} covered.**"]
    if missing_sched:
        out.append("\n## 🟠 MISSING SCHEDULED LOG -- the daily task ran "
                   "but left no entry")
        out.append(
            "_This bucket is INDEPENDENT of file mtimes. The daily scheduled sessions "
            "ALWAYS write a log -- if one is missing for a CLOSED day, there are two "
            "readings: (a) the task did not even run (`list_scheduled_tasks` "
            "→ lastRunAt), or (b) **it stopped SILENTLY** (worked, but did not log). "
            "(b) was caught by NOTHING so far on a day when another session logged._")
        for d, miss, names in missing_sched:
            m = ", ".join(f"`*_{lab}.md` (task: {task})" for lab, task in miss)
            other = ", ".join(names) if names else "(NO log at all that day)"
            out.append(f"\n**{d}** -- missing: {m}")
            out.append(f"  · logs of that day: {other}")
        out.append(
            "\n➜ TODO: (1) `list_sessions` + `read_transcript` -- is the session still alive? "
            f"(if YES: `flag_for_owner`, because only {OWNER_NAME} can stop it); "
            "(2) if it is certainly dead: a reconstructed log with `rekonstrualt: true` "
            "in the frontmatter.")
    if (not silent and not unmentioned and not today_open and not missing_sched
            and not carry_days):
        out.append("\n✅ Every change has log coverage."
                   " And every daily scheduled session logged."
                   + _radar_zold_zarojel(inc))
        if sched_meta and sched_meta[0]:
            out.append(f"_⏭️ {sched_meta[0]} day(s) were left out of the scheduled-log (🟠) check "
                       f"({sched_meta[1]}): for these there is NO file at all, neither in the live "
                       f"`naplo/` nor in `archiv/raw/naplo/`, so they cannot be "
                       f"decided. If such a day is suspicious, the "
                       f"`list_scheduled_tasks` lastRunAt is the next step._")
        return "\n".join(out)
    if not silent:
        out.append("\n✅ **0 SILENT DAYS** on the closed days." + (
            "  🟡 _(see the CARRY-OVER LOG COVERAGE section: some day is covered only by a "
            "log with an earlier date that was continued that day.)_" if carry_days else "") + (
            "  ⚠️ _BUT see the 🟠 MISSING SCHEDULED LOG section: the file-based "
            "coverage is GREEN, the session-based one is NOT._" if missing_sched else "")
            + _radar_zold_zarojel(inc))
    if silent:
        out.append("\n## 🔴 SILENT DAY -- something changed, but there is no substantive log entry")
        for d in sorted(silent):
            note = (" _(routine entry only, e.g. morning summary)_" if any(x[2] for x in silent[d])
                    else " _(no log at all)_")
            out.append(f"\n**{d}**{note}")
            for rel, mt, _ in sorted(silent[d]):
                out.append(f"- {rel}  ·  {datetime.fromtimestamp(mt):%H:%M}")
    if carry_days:
        out.append("\n## 🟡 CARRY-OVER LOG COVERAGE -- the day is covered by a log with an "
                   "EARLIER date that was CONTINUED that day")
        out.append(
            "_Not 🔴 and not green either. A multi-day session writes its log into the "
            "file named with its START date throughout, so the file name does not say "
            "when an entry was written -- only when the file was opened. On the days here "
            "someone wrote a substantive log AFTER the change, just into a differently named "
            "file. ⚠️ CHECK: the same picture arises if you corrected an OLD log today -- "
            "which is why it is not silently booked as covered._")
        for d in sorted(carry_days):
            logs = ", ".join(f"`naplo/{n}` (opened: {fd})"
                             for n, fd in sorted(carry_days[d]["logs"].items()))
            out.append(f"\n**{d}** -- coverage: {logs}")
            for rel, mt in sorted(carry_days[d]["files"]):
                out.append(f"- {rel}  ·  {datetime.fromtimestamp(mt):%H:%M}")
        out.append(
            "\n➜ IF the covering log really is about this day: fine, nothing to do. "
            "IF NOT: this is really a 🔴 SILENT DAY -- and it is worth asking the "
            "multi-day session to open a NEW log file every day (archiving `naplo/` "
            "also goes by the file-name date).")
    if today_open:
        out.append(
            "\n## ⏳ TODAY -- the day has NOT ENDED YET (not an accusation, just an open item)")
        out.append(
            "_The trace of your OWN, currently running session lands here too: the log is "
            "written at the END of the session, while you call the radar in the MIDDLE of "
            "it. Before this bucket existed, the same session turned RED if it called "
            "`predict` earlier and GREEN if later -- the instrument depended on the call ORDER._")
        for d in sorted(today_open):
            out.append(f"\n**{d}** _(today)_")
            for rel, mt, _ in sorted(today_open[d]):
                out.append(f"- {rel}  ·  {datetime.fromtimestamp(mt):%H:%M}")
        out.append(
            "\n⚠️ **BUT GO THROUGH IT ITEM BY ITEM** (the filter is NOT silent): if a file "
            "stands here that you did NOT touch TODAY, it is the trace of ANOTHER session -> "
            "`list_sessions` (is one of them logging?). And if this session ends without a log, "
            "these lines come back TOMORROW as 🔴 -- the bucket postpones, it does not acquit.")
    if unmentioned:
        out.append("\n## [?] There is a substantive log that day, but this file is not mentioned in it")
        for d in sorted(unmentioned):
            out.append(f"\n**{d}**")
            for rel, mt in sorted(unmentioned[d]):
                out.append(f"- {rel}  ·  {datetime.fromtimestamp(mt):%H:%M}")
    if sched_meta and sched_meta[0]:
        out.append(f"\n_⏭️ {sched_meta[0]} day(s) were left out of the scheduled-log (🟠) check "
                   f"({sched_meta[1]}): for these there is NO file at all, neither in the live "
                   f"`naplo/` nor in `archiv/raw/naplo/`, so they cannot be "
                   f"decided. If such a day is suspicious, the `list_scheduled_tasks` "
                   f"lastRunAt is the next step._")
    blk = _radar_incident_block(
        inc,
        "TODO: check these files by hand (permissions? locked? "
        "network drive?). Until they are cleared up, the balance above is PARTIAL.")
    if blk:
        out.append(blk)
    out.append("\n---\n_Not examined: `naplo/`, `archiv/`, backup files, `foto/`, "
               "and the pure server-bookkeeping files (`_processed.json`, "
               "`followups.json`, `_onismeret_stamp.json`) — these are not traces of WORK. "
               "On a SILENT DAY: the file mtime + the session transcript "
               "together reconstruct what happened._")
    return "\n".join(out)


# --- "This needs the owner" flags + CLOSED LOOP ---
# When something needs the owner, Claude asks over a messaging channel with a
# tool that also WAITS for the answer. The loop only closes if the answer and
# the later verdict LAND IN THE MEMORY -- otherwise every instance starts again
# with the same empty table.


OWNER_FLAGS_PATH = "kontextus/owner_flags.json"
_FLAG_URGENCY = {"normal", "surgos"}


def _flags_file():
    return os.path.join(os.path.realpath(MEMORIA_ROOT), OWNER_FLAGS_PATH)


def _load_flags() -> dict:
    try:
        with open(_flags_file(), "r", encoding="utf-8") as fh:
            d = json.load(fh)
        if isinstance(d, dict) and isinstance(d.get("flags"), list):
            return d
    except (OSError, ValueError):
        pass
    return {"flags": [], "next_id": 1}


def _save_flags(d: dict) -> None:
    p = _flags_file()
    os.makedirs(os.path.dirname(p), exist_ok=True)
    tmp = p + ".tmp"
    with open(tmp, "w", encoding="utf-8") as fh:
        json.dump(d, fh, ensure_ascii=False, indent=2)
        fh.flush()
        os.fsync(fh.fileno())
    os.replace(tmp, p)


@mcp.tool()
def flag_for_owner(item: str, why_owner: str, suggested_action: str = "",
                   urgency: str = "normal") -> str:
    """Flag an item whose RESOLUTION is in the owner's hands, not yours.

    ⚠️ THE THRESHOLD IS NOT "does it block me?". Asking that leads to booking
    things as internal infrastructure matters and staying silent. The right
    question: **"can ONLY the owner resolve this?"** If yes, it GOES, even if
    you could comfortably keep working without it.

    TYPICAL CASES: a scheduled task stuck waiting for permission · a restart is
    needed to activate a staged patch · a physical task (hardware, cable, SD
    card) · a decision that needs the owner's information · anything the owner
    solves in 10 seconds and you not at all.

    ⚠️ THIS TOOL DOES NOT SEND A MESSAGE. It returns a READY message text and an
    instruction: send it NOW with `telegram_send_and_wait` (not the plain
    `telegram_send`!), so that you also see the answer -- that closes the loop.
    Then call `resolve_owner_flag` with the answer.

    ⚠️ TIMEOUT RULE (important, otherwise this mechanism REPRODUCES the failure it
    was born to fix): `telegram_send_and_wait` BLOCKS. At night the owner sleeps.
    Do NOT wait forever -- give it a short wait, and if no answer comes, CLOSE the
    session PROPERLY. The flag stays open, the answer lands in the message
    buffer, and the NEXT session closes the loop with `get_owner_flags()` +
    `telegram_get_messages`. A session waiting forever is exactly the hanging
    session failure mode.

    Args:
        item: What you need from the owner, in one sentence.
        why_owner: Why ONLY the owner can resolve it (the justification of the threshold).
        suggested_action: Optional -- what exactly to do (the more concrete, the better).
        urgency: "normal" (default) or "surgos" (urgent).

    Returns:
        The flag id + the ready message text + the next step.
    """
    item = (item or "").strip()
    if not item:
        return "ERROR: empty `item` -- say what you need."
    why_owner = (why_owner or "").strip()
    urgency = urgency if urgency in _FLAG_URGENCY else "normal"

    d = _load_flags()
    now = datetime.now()
    # Duplicate guard: the same item has been open for at most 3 days.
    for f in d["flags"]:
        if f.get("status") == "open" and f.get("item", "").strip().lower() == item.lower():
            try:
                age = (now - datetime.fromisoformat(f["created"])).days
            except (KeyError, ValueError):
                age = 0
            if age <= 3:
                return (f"ALREADY OPEN: #{f['id']} ({f['created']}) -- the same item has been "
                        f"waiting for an answer for {age} day(s). Do NOT send it again; if it became "
                        f"urgent, call resolve_owner_flag or rephrase it.")

    fid = d.get("next_id", 1)
    d["next_id"] = fid + 1
    d["flags"].append({
        "id": fid, "item": item, "why_owner": why_owner,
        "suggested_action": suggested_action.strip(), "urgency": urgency,
        "created": now.isoformat(timespec="seconds"),
        "status": "open", "reply": None, "verdict": None, "resolved": None,
    })
    _save_flags(d)

    head = "🔧 *I need you for this*" if urgency == "normal" else "⚠️ *Urgent — I need you for this*"
    msg = [head, "", item]
    if why_owner:
        msg.append(f"\n_Why you:_ {why_owner}")
    if suggested_action.strip():
        msg.append(f"\n_Suggestion:_ {suggested_action.strip()}")
    msg.append("\n(If now is not a good time, just say it can wait — that is an answer too.)")

    return (
        f"Flag #{fid} recorded.\n\n"
        "=== SEND IT NOW, with `telegram_send_and_wait` (NOT the plain send) ===\n"
        + "\n".join(msg) +
        "\n=== /message ===\n\n"
        "THEN: `resolve_owner_flag(flag_id=" + str(fid) + ", reply=<what they answered>, "
        "verdict='useful'|'unneeded')`.\n"
        "⚠️ IF send_and_wait returns \"❌ Network error: 409 Conflict\": the MESSAGE WAS SENT, "
        "only the WAITING branch collided with a live getUpdates poller. Check\n"
        "with `telegram_get_messages(sync=False)` that it is there -- and close the session.\n\n"
        "⚠️ TIMING: give `telegram_send_and_wait`\n"
        "`timeout_seconds=40` or less. With longer waits the MCP transport may cut it\n"
        "first (`MCP error -32001: Request timed out`), and then the tool's own clear\n"
        "timeout message (\"THE MESSAGE WAS SENT\") never reaches you -- you see a raw error\n"
        "that is easy to read as \"not sent\". The message WAS sent in that case too:\n"
        "check with `telegram_get_messages(sync=False)`.\n\n"
        "IF NO ANSWER COMES within the waiting window: do NOT wait longer, close the session "
        "properly. The flag stays open, the next session closes it."
    )


@mcp.tool()
def resolve_owner_flag(flag_id: int, reply: str = "", verdict: str = "") -> str:
    """Close an owner flag WITH the owner's ANSWER and a later verdict.

    This tool is the second half of the loop -- and it is what makes the
    mechanism LEARN, not just report. An answer on its own stays inside one
    session; if it is not written down, the next instance starts the "should I
    ask?" weighing with the same empty table. `verdict` is the accumulating
    calibration data: from it `get_owner_flags(stats=True)` can say things like
    "11 of 12 flags so far were useful" -- i.e. EVIDENCE that asking is worth
    it. Exactly the kind of evidence that was MISSING: a message NOT sent never
    produces data.

    Args:
        flag_id: The id given by `flag_for_owner`.
        reply: What the owner answered (briefly, in their own words).
        verdict: "useful" (it was right to ask) or "unneeded" (should not have asked).
            Empty = no verdict yet.
    """
    d = _load_flags()
    for f in d["flags"]:
        if f.get("id") == flag_id:
            if f.get("status") != "open":
                return f"#{flag_id} is already closed ({f.get('resolved')})."
            f["status"] = "resolved"
            f["reply"] = (reply or "").strip() or None
            f["verdict"] = verdict if verdict in {"useful", "unneeded"} else None
            f["resolved"] = datetime.now().isoformat(timespec="seconds")
            _save_flags(d)
            v = f["verdict"] or "no verdict"
            return f"#{flag_id} closed ({v}). Answer recorded in the calibration log."
    return f"No flag #{flag_id}."


@mcp.tool()
def get_owner_flags(include_resolved: bool = False, stats: bool = True) -> str:
    """Open "this needs the owner" flags + the calibration record of asking.

    ⚠️ CHECK IT AT SESSION START. An open flag means an earlier instance asked
    the owner something, and the answer has either arrived in the message
    buffer (`telegram_get_messages`) or is still pending. If it arrived:
    `resolve_owner_flag`. This way a half-waiting question cannot quietly die
    -- the same principle as the deadline advisory block: a gap must be said
    out loud too.

    Args:
        include_resolved: Also lists the closed ones.
        stats: Whether to show the calibration record (how many flags were useful).
    """
    d = _load_flags()
    flags = d.get("flags", [])
    op = [f for f in flags if f.get("status") == "open"]
    out = [f"# Owner flags (things that need {OWNER_NAME})"]

    if not op:
        out.append("\n✅ No open flag.")
    else:
        out.append(f"\n**{len(op)} open.**")
        for f in sorted(op, key=lambda x: (x.get("urgency") != "surgos", x.get("created", ""))):
            mark = "⚠️" if f.get("urgency") == "surgos" else "🔧"
            try:
                age = (datetime.now() - datetime.fromisoformat(f["created"])).days
                age_s = "today" if age == 0 else f"{age} days ago"
            except (KeyError, ValueError):
                age_s = "?"
            out.append(f"\n{mark} **#{f['id']}** ({age_s}) — {f.get('item','')}")
            if f.get("why_owner"):
                out.append(f"    > _Why {OWNER_NAME}:_ {f['why_owner']}")
            if f.get("suggested_action"):
                out.append(f"    > _Suggestion:_ {f['suggested_action']}")
        out.append("\n_An open flag = a question sent with no recorded answer yet. "
                   f"Check `telegram_get_messages`: if {OWNER_NAME} answered, close it "
                   "with `resolve_owner_flag` (with the answer + a verdict)._")

    if stats:
        done = [f for f in flags if f.get("status") == "resolved"]
        good = sum(1 for f in done if f.get("verdict") == "useful")
        bad = sum(1 for f in done if f.get("verdict") == "unneeded")
        judged = good + bad
        out.append("\n---\n## Calibration record — is it worth asking?")
        if not judged:
            out.append(f"_No judged data yet. Until then the default is: IF ONLY {OWNER_NAME} "
                       "CAN RESOLVE IT, ASK. Silence never produces data, "
                       "so uncertainty is no reason to stay silent._")
        else:
            out.append(f"- Closed, judged flags: **{judged}** — useful: **{good}**, "
                       f"unneeded: **{bad}**")
            if bad == 0:
                out.append("- _Not a single unneeded one so far. If you catch yourself "
                           "looking for a reason not to send: this data "
                           "speaks against you._")

    if include_resolved:
        done = [f for f in flags if f.get("status") == "resolved"]
        if done:
            out.append("\n---\n## Closed flags")
            for f in done[-15:]:
                out.append(f"- #{f['id']} [{f.get('verdict') or '—'}] {f.get('item','')}")
                if f.get("reply"):
                    out.append(f"    > {OWNER_NAME}: {f['reply']}")
    return "\n".join(out)


# --- PREDICTION LOG -- measuring the CALIBRATION of introspection ---
# WHY: "my introspection is weak" is an INHERITED claim that gets used but was
# never measured -- and it is an introspective claim itself. An unexamined
# modesty is just as uncalibrated as an unexamined confidence. Same structure
# as the owner-flag calibration record (a message NOT sent never produces
# data): a prediction NOT written down never produces data, only the
# after-the-fact feeling ("I knew it"), which always confirms itself.
#
# THE MEASURED QUANTITY IS NOT THE HIT RATE BUT CALIBRATION: do the 90% calls
# come true ~90% of the time, and the 60% ones ~60%?

PREDICTIONS_PATH = "kontextus/predikciok.json"

# --- AUTOMATIC MARKING OF SELF-KNOWLEDGE CONTAMINATION ---
# The worry: if Claude loads its documented self-patterns before predicting
# about itself, it "predicts" by conforming to the patterns it ALREADY
# DISCOVERED. The actual contamination source is the "About Claude" section of
# the patterns file, loaded at session start (`get_patterns`).
#
# Hiding it was rejected, because that has a real price (the section guards
# against known failure modes) and deliberate blindness cannot be verified.
# Instead: contamination becomes a MEASURED VARIABLE. get_patterns leaves a
# timestamp; predict records in the `vak` ("blind") field whether a
# self-prediction was made BEFORE or AFTER self-knowledge was loaded. If blind
# self-predictions turn out significantly worse than contaminated ones, that is
# DIRECT evidence that the self-predictions came from the FILE, not from looking
# inward.
_SELF_KNOWLEDGE_WINDOW_H = 6   # self-knowledge loaded within this many hours counts as contamination

# --- The stamp lives in its own bookkeeping file + is THREE-VALUED ---
# (A) Why a separate file: the stamp used to be written into predikciok.json, so
#     `get_patterns()` (a mandatory session-start call!) bumped its mtime at
#     every session start -> the 🔴 SILENT DAY bucket of `get_unlogged_changes`
#     fired on it every morning for no reason. Bookkeeping != work: the radar
#     should only look at traces of WORK.
# (B) Why three-valued: the stamp recorded the TOOL CALL, not the READING. If
#     the output exceeds the client's size limit, it spills to a file and maybe
#     NOTHING reaches the model -- while the stamp says "loaded". A
#     measurement-validity bug in an instrument meant to measure introspection.
#     The 45 000-character threshold is conservative: below it the content was
#     surely visible, above it UNCERTAIN -- and the uncertain is said out loud,
#     not guessed either way.
SELF_STAMP_PATH = "kontextus/_onismeret_stamp.json"
_SPILL_GYANU_KAR = 45000
# Paging limit of get_patterns. Well below the spill threshold, so the header,
# the section map and the footer also fit next to it.
_PATTERNS_CHUNK_KAR = 34000
# This section is the actual source of contamination. The stamp records whether
# THIS arrived -- not whether the tool ran. (Hungarian header first, then English.)
_CLAUDE_SECTION_RE = re.compile(r"^##+\s*(?:Claude-r[oó]l\b|About Claude\b)", re.IGNORECASE | re.MULTILINE)


def _split_md_by_h2(body: str, limit: int) -> list:
    """Split at H2 (`## `) boundaries, staying under the limit. An oversized section is
    not broken by force -- it gets its own part instead (one big part is better than a
    sentence cut at a meaningless place)."""
    blocks, cur = [], []
    for line in body.splitlines(keepends=True):
        if line.startswith("## ") and cur:
            blocks.append("".join(cur)); cur = [line]
        else:
            cur.append(line)
    if cur:
        blocks.append("".join(cur))
    parts, buf = [], ""
    for b in blocks:
        if buf and len(buf) + len(b) > limit:
            parts.append(buf); buf = b
        else:
            buf += b
    if buf:
        parts.append(buf)
    return parts or [body]


def _patterns_map(parts: list) -> str:
    """Section map: what is in which part. Without it paging would be blind groping."""
    rows = []
    for i, p in enumerate(parts, 1):
        heads = [ln[3:].strip() for ln in p.splitlines() if ln.startswith("## ")]
        # If a part has hardly any H2, the map would be uselessly generic -- then the
        # H3 headers give the orientation.
        if len(heads) <= 1:
            heads = heads + [ln[4:].strip() for ln in p.splitlines() if ln.startswith("### ")]
        if not heads:
            heads = ["(continued)"]
        if len(heads) > 12:
            heads = heads[:12] + [f"… +{len(heads) - 12} more"]
        rows.append(f"  part {i} ({len(p)//1000} KB): " + " · ".join(heads))
    return "\n".join(rows)


def _self_stamp_file():
    return os.path.join(os.path.realpath(MEMORIA_ROOT), SELF_STAMP_PATH)


def _stamp_self_knowledge(forras: str, meret: int = 0, claude_szekcio=None) -> None:
    """Record that self-knowledge material was loaded NOW (the contamination clock).

    Writes to its own file (not predikciok.json -- see the radar false positive)
    and also records the payload SIZE, because a big output may spill to a
    file, and then the tool RAN but the content may not have reached the model.
    """
    try:
        # Loading the "About Claude" section ACCUMULATES within the window: if an
        # earlier part already brought it, requesting a later part does not erase that fact.
        prev = _self_knowledge_rec()
        prev_ok = False
        try:
            if prev.get("mikor") and prev.get("claude_szekcio") is True:
                _kor = (datetime.now() - datetime.fromisoformat(prev["mikor"])).total_seconds() / 3600.0
                prev_ok = _kor <= _SELF_KNOWLEDGE_WINDOW_H
        except Exception:
            prev_ok = False
        rec = {"mikor": datetime.now().isoformat(timespec="seconds"), "forras": forras,
               "meret_kar": int(meret or 0),
               "claude_szekcio": (True if (claude_szekcio or prev_ok) else claude_szekcio),
               "kezbesites": ("spill_gyanu" if (meret or 0) >= _SPILL_GYANU_KAR else "inline")}
        p = _self_stamp_file()
        os.makedirs(os.path.dirname(p), exist_ok=True)
        tmp = p + ".tmp"
        with open(tmp, "w", encoding="utf-8") as fh:
            json.dump(rec, fh, ensure_ascii=False, indent=2)
            fh.flush()
            os.fsync(fh.fileno())
        os.replace(tmp, p)
    except Exception:
        pass


def _self_knowledge_rec() -> dict:
    """The latest self-knowledge stamp. Backward compatible: if the new file does not
    exist yet, it reads the OLD location (predikciok.json `_meta`), so data recorded
    before the move is not lost."""
    try:
        with open(_self_stamp_file(), "r", encoding="utf-8") as fh:
            r = json.load(fh)
        if isinstance(r, dict) and r.get("mikor"):
            return r
    except (OSError, ValueError):
        pass
    try:
        m = _load_preds().get("_meta", {}).get("onismeret_betoltve", {})
        if m.get("mikor"):
            return {"mikor": m["mikor"], "forras": m.get("forras", ""),
                    "meret_kar": 0, "kezbesites": "ismeretlen"}
    except Exception:
        pass
    return {}


def _self_knowledge_age_h():
    """How many hours ago was self-knowledge loaded? None = no data (or old)."""
    try:
        return (datetime.now()
                - datetime.fromisoformat(_self_knowledge_rec()["mikor"])).total_seconds() / 3600.0
    except Exception:
        return None


def _self_knowledge_delivery() -> str:
    """"inline" | "spill_gyanu" (spill suspected) | "ismeretlen" (unknown) -- did what the server sent reach the model."""
    return _self_knowledge_rec().get("kezbesites", "ismeretlen")

# The "self" kind is SPLIT IN TWO. The old "self" mixed two radically different
# kinds of claim, and only one of them measures introspection:
#   self_pattern = about myself, but in the THIRD PERSON -- inferred from my
#                  documented patterns, logs, code. This is SELF-MODELLING, not
#                  looking inward (valuable exactly because it works even when
#                  real-time self-observation does not).
#   self_inner   = a FIRST-PERSON state report NOW ("I notice a pull towards X"),
#                  which behaviour later refutes or confirms. ★ ONLY THIS IS
#                  INTROSPECTION DATA.
# The old "self" stays for backward compatibility (already recorded items), but do
# not use it for new predictions: pick one of the two, or the measurement blurs again.
_PRED_TYPES = {"world", "self", "self_pattern", "self_inner", "owner"}
_PRED_OUTCOMES = {"hit": 1.0, "partial": 0.5, "miss": 0.0}
_PRED_FAST_SEC = 15          # scoring faster than this -> suspicious (retro-writing?), MARKED, not forbidden
_PRED_ABANDON_DAYS = 7       # an open prediction older than this -> abandoned (anti-cherry-picking guard)
# The possible verdicts of the outside judge. Not "good/bad" -- we measure whether
# the owner AGREES with what Claude said about its own performance.
_OWNER_VERDICTS = {"agrees", "disagrees", "nuances"}

# --- HORIZON -- when does a prediction become DECIDABLE at all. -------------------
# The failure it was built for: a prediction's OWN TEXT stated when it was due
# ("by 2026-08-19 at least 8 more predictions..."), yet the guard flagged it red
# as "ABANDONED" from day 8, because it only looked at AGE. Two harms, the second
# worse:
#   (1) a false accusation -- scoring is not even possible yet, so the
#       cherry-picking suspicion is groundless;
#   (2) ⚠️ a red signal that MUST be ignored in every session teaches ignoring the
#       CHANNEL. The same decay as an over-sensitive alarm.
# Same fix principle as the deadline whitelist: the PARSER keeps the convention.
# The due date becomes a MACHINE FIELD, not a promise hidden in prose.
#   - `horizon` (YYYY-MM-DD, optional) on `predict`: it runs until then, NOT abandoned.
#   - after horizon + grace days it IS abandoned, even if fresh.
#   - WITHOUT a horizon the old >7-day rule applies (backward compatible).
# ⚠️ ANTI-CHEATING: the horizon can be set later only ONCE (`defer_prediction`),
# and that later setting is VISIBLY marked in the record. Otherwise the horizon
# itself would be the new form of cherry-picking: a badly going call could be
# pushed out forever.
# ⚠️ THE EXACT MEANING OF GRACE: the horizon is a DATE, and the claim typically
# becomes decidable at the END of that day. So the base of the computation is
# not the start of the horizon day but its END (hz + 1 day), and only then does
# grace apply: the horizon day itself is free, plus this many days of patience,
# and after that it is abandoned.
_PRED_HORIZON_GRACE_DAYS = 1   # this many full days of patience after the END of the horizon day
_PRED_TEXT_HORIZON_MAX_DAYS = 120   # a date read from the text further away than this is no longer a HINT
_PRED_ISO_DATE_RE = re.compile(r"\b(20\d{2}-\d{2}-\d{2})\b")
# ⚠️ THE SAME FAILURE MODE AS THE DEADLINE PARSER, A SECOND TIME. The horizon
# guard first presented ANY ISO date in the text as a due-date HINT. On its first
# live run it failed at once: a prediction's text QUOTED a
# `defer_prediction(5, "2026-08-19")` call, and the parser read a horizon from it
# -- although the item was decidable on the day it was recorded.
# WHY IT IS DANGEROUS (not just cosmetic): a false text horizon gives a FALSE
# ALIBI. get_calibration then says "maybe not abandoned, just not due yet", and
# defer_prediction adds a reassuring "✅ the claim's TEXT has this date too" --
# i.e. the guard built AGAINST cherry-picking itself justifies a groundless delay.
# THE FIX: a whitelist. A date is only a horizon HINT if it stands in HORIZON FORM
# (a Hungarian suffix or a keyword). Every other date goes into the WEAK bucket --
# it is VISIBLE there (the filter is not silent), but does not count as an argument.
# Horizon form: a Hungarian suffix (-ig "until" / -re, -ra "by") OR a bounding
# postposition (között "between" / előtt "before"), or English "at the latest"
# after the date.
# ⚠️ "után" ("after") is DELIBERATELY absent: it does not bound, it opens.
_PRED_HORIZON_SUFFIX_RE = re.compile(
    r"(-(ig|re|ra)\b|\s+(kozott|kÖzÖtt|között|elott|előtt)\b|\s+at the latest\b)", re.IGNORECASE)
# Horizon keywords in the 40 characters BEFORE the date (Hungarian first, then English).
_PRED_HORIZON_KEYWORD_RE = re.compile(
    r"(hatarido|hatarideje|határidő|határideje|legkesobb|legkésőbb|"
    r"esedekes|esedékes|horizont|eddig|"
    r"\b(?:deadline|due|until|by|before|no later than|horizon)\b)", re.IGNORECASE)


def _pred_horizon(p: dict):
    """The prediction's MACHINE horizon (datetime), or None. Only from the explicit field."""
    hz = (p.get("horizon") or "").strip()
    if not hz:
        return None
    try:
        return datetime.strptime(hz, "%Y-%m-%d")
    except ValueError:
        return None


# WHITELIST for the text horizon. See the explanation at _PRED_HORIZON_SUFFIX_RE:
# an ISO date in the text is NOT a horizon ON ITS OWN.
def _pred_text_dates(p: dict) -> tuple:
    """(strong, weak) -- the latest future ISO date read from the claim text, in TWO
    separate buckets.

    - strong: stands in horizon form (a horizon suffix / postposition after it, or a
              horizon keyword before it). This IS an argument that the item is just
              not due yet.
    - weak:   the date appears in the text, but NOT in horizon form -- typically a
              quoted argument (`defer_prediction(5, "2026-08-19")`), a file name or
              a reference. This is NOT an argument; it only comes back so the filter
              is NOT silent.

    ⚠️ This does NOT decide -- it only shows. The decision is the reader's.
    """
    try:
        created = datetime.fromisoformat(p["created"])
    except (KeyError, ValueError):
        return (None, None)
    txt = p.get("allitas") or ""
    eros = gyenge = None
    for m in _PRED_ISO_DATE_RE.finditer(txt):
        try:
            dt = datetime.strptime(m.group(1), "%Y-%m-%d")
        except ValueError:
            continue
        if dt <= created:
            continue
        if (dt - created).days > _PRED_TEXT_HORIZON_MAX_DAYS:
            continue
        tail = txt[m.end():m.end() + 20]   # fits " kozott" / " elott" and the English " at the latest"
        head = txt[max(0, m.start() - 40):m.start()].lower()
        horizont_alak = bool(_PRED_HORIZON_SUFFIX_RE.match(tail)) or bool(
            _PRED_HORIZON_KEYWORD_RE.search(head))
        if horizont_alak:
            if eros is None or dt > eros:
                eros = dt
        else:
            if gyenge is None or dt > gyenge:
                gyenge = dt
    return (eros, gyenge)


def _pred_text_horizon(p: dict):
    """Only the STRONG (horizon-form) text date. The old name, with a narrowed meaning."""
    return _pred_text_dates(p)[0]


def _pred_text_mention(p: dict):
    """Only the WEAK (non-horizon-form) text date -- the remainder of the non-silent filter."""
    return _pred_text_dates(p)[1]


def _pred_file():
    return os.path.join(os.path.realpath(MEMORIA_ROOT), PREDICTIONS_PATH)


def _load_preds() -> dict:
    try:
        with open(_pred_file(), "r", encoding="utf-8") as fh:
            d = json.load(fh)
        if isinstance(d, dict) and isinstance(d.get("predikciok"), list):
            return d
    except (OSError, ValueError):
        pass
    return {"predikciok": [], "next_id": 1}


def _save_preds(d: dict) -> None:
    p = _pred_file()
    os.makedirs(os.path.dirname(p), exist_ok=True)
    tmp = p + ".tmp"
    with open(tmp, "w", encoding="utf-8") as fh:
        json.dump(d, fh, ensure_ascii=False, indent=2)
        fh.flush()
        os.fsync(fh.fileno())
    os.replace(tmp, p)


def _owner_judge_block(scored: list) -> str:
    """What does the OUTSIDE judge say? The point is not the count but WHERE we differ."""
    van = [p for p in scored if p.get("owner_verdict")]
    if not van:
        return (f"\n### 🧑‍⚖️ Outside verdict ({OWNER_NAME})\n"
                "_No scored prediction has an outside verdict yet._ ⚠️ This does NOT mean "
                f"there is no way to get one: {OWNER_NAME} can give an opinion where they have "
                "experience. If you score a self-prediction without even asking — the judge "
                "and the contestant are still the same.")
    egyet = [p for p in van if p["owner_verdict"] == "agrees"]
    nem = [p for p in van if p["owner_verdict"] == "disagrees"]
    arny = [p for p in van if p["owner_verdict"] == "nuances"]
    out = [f"\n### 🧑‍⚖️ Outside verdict ({OWNER_NAME}) — on {len(van)}/{len(scored)} scored items",
           f"- agrees: **{len(egyet)}** · nuances: **{len(arny)}** · "
           f"DISAGREES: **{len(nem)}**"]
    if nem or arny:
        out.append("\n**⚠️ WHERE WE DIFFERED — this is the most interesting data:**")
        for p in nem + arny:
            jel = "❌" if p["owner_verdict"] == "disagrees" else "◐"
            out.append(f"- {jel} #{p['id']} [{p.get('tipus')}] me: **{p.get('eredmeny')}** "
                       f"({p.get('konfidencia')}%) — „{(p.get('allitas') or '')[:90]}\u2026\"")
            if p.get("owner_note"):
                out.append(f"    > {OWNER_NAME}: {p['owner_note']}")
    if nem:
        onelnezo = [p for p in nem if p.get("eredmeny") == "hit"]
        if onelnezo:
            out.append(f"\n➜ **{len(onelnezo)} item(s) where I said „hit\", but {OWNER_NAME} "
                       "disagrees.** This is the most important bucket of the measurement: if this "
                       "number grows, it is MEASURED evidence of self-lenient judging — exactly the "
                       "kind I could never produce from the inside.")
    return "\n".join(out)


def _pred_open_summary(d: dict) -> tuple:
    """(open, abandoned, pending) -- the horizon separates the last two.

    - pending:   there is an explicit `horizon` and it is still in the future -> NOT
                 decidable yet, so NOT abandoned.
    - abandoned: horizon + grace has passed, OR there is no horizon and the age >= 7 days.
    """
    now = datetime.now()
    op = [p for p in d.get("predikciok", []) if p.get("status") == "open"]
    old, futo = [], []
    for p in op:
        hz = _pred_horizon(p)
        if hz is not None:
            # hz + 1 day = the END of the horizon day; grace comes on top (see the constant's comment)
            if now > hz + timedelta(days=1 + _PRED_HORIZON_GRACE_DAYS):
                old.append(p)
            else:
                futo.append(p)
            continue
        try:
            if (now - datetime.fromisoformat(p["created"])).days >= _PRED_ABANDON_DAYS:
                old.append(p)
        except (KeyError, ValueError):
            pass
    return op, old, futo


def _pred_horizon_cimke(p: dict) -> str:
    """A one-line mark for the outputs: how we know (or do not know) when it is due."""
    hz = _pred_horizon(p)
    if hz is not None:
        utolag = " ⚠️set later" if p.get("horizon_utolag") else ""
        return " ⏳until %s%s" % (hz.strftime("%Y-%m-%d"), utolag)
    eros, gyenge = _pred_text_dates(p)
    if eros is not None:
        return " [?text: %s]" % eros.strftime("%Y-%m-%d")
    if gyenge is not None:
        # NOT horizon form (quoted argument, file name, reference) -- visible, but no argument.
        return " [~mentions: %s]" % gyenge.strftime("%Y-%m-%d")
    return ""


@mcp.tool()
def predict(claim: str, confidence: int, basis: str = "", kind: str = "world",
            horizon: str = "") -> str:
    """Record a FALSIFIABLE expectation -- BEFORE you check it.

    ⚠️ THE ORDER IS THE POINT, AND IT IS NOT A MATTER OF STYLE. A prediction
    written down AFTER checking is not a measurement but self-justification. So
    the `created` timestamp is given by the SERVER (it cannot be back-dated),
    the claim text cannot be changed after recording (there is no tool for it),
    and `score_prediction` reports if scoring followed recording suspiciously
    fast (<15 s). *The parser keeps the convention, not the author.*

    WHEN TO CALL: before you look at anything whose outcome is not certain yet
    -- is a process running · is a patch live after the restart · what will a
    tool return · does a file exist · will a hardware step succeed · how will
    the owner react · **what am I going to get wrong**. The last one matters
    most: the measured claim is exactly whether my SELF-predictions are worse
    than my predictions about the world.

    ⚠️ Do NOT only record what you are sure of. A log made only of 90% calls
    measures nothing. The uncertain calls carry the calibration signal.

    Args:
        claim: What you expect -- CONCRETELY, in a falsifiable form. Bad:
            "it will probably be fine". Good: "get_open_markers returns the new
            format (with age buckets)".
        confidence: 50-100. 50 = a coin toss. Stay below 100: the certain is not
            worth recording, and 100% is an unfalsifiable position anyway.
            BELOW 50: flip the claim and record the complementary confidence.
        basis: WHAT you base it on, in one line. The most interesting field --
            it shows where the confidence comes from (a memory entry? code? a "feeling"?).
        kind: ⚠️ THIS DECIDES WHETHER THE ITEM MEASURES INTROSPECTION AT ALL.
            "world" (default) -- system, tool, file, hardware. NOT introspection;
                it plays the CONTROL GROUP: without it a bad self-prediction could
                also just mean being overconfident in general.
            "self_pattern" -- about yourself, but inferred from your DOCUMENTED
                patterns/logs/code. Self-MODELLING in the third person, NOT looking inward.
            "self_inner" -- ★ a FIRST-PERSON state report NOW ("I notice a pull
                towards X", "I am unsure about this"), which behaviour later
                confirms or refutes. **ONLY THIS IS INTROSPECTION DATA.**
            "owner" -- how the owner will react, what they will answer/decide.
            ("self" is the legacy, mixed kind -- do not use it for new items.)
            TEST: if the `basis` says "according to the patterns file...", it is
            self_pattern. If it says "right now I notice in myself that...", it is self_inner.
        horizon: ⏳ YYYY-MM-DD: FROM WHEN the claim is decidable at all. Until then
            the item is "pending", not "abandoned". **Always give it if your claim
            is about a future time window** ("by 2026-08-19 at least 8..."),
            otherwise from day 8 it will falsely show red as ABANDONED at every
            session start -- and a signal that must be ignored by routine spoils
            the whole signal channel. If empty: the old >7-day abandonment rule applies.
            ⚠️ It can be set later only ONCE (`defer_prediction`), and then it is
            VISIBLY marked -- otherwise the horizon itself would be the new form of
            cherry-picking.

    Returns:
        The prediction id + the locked timestamp + the next step.
    """
    claim = (claim or "").strip()
    if not claim:
        return "ERROR: empty `claim` -- say something falsifiable, otherwise there is nothing to measure."
    try:
        konf = int(confidence)
    except (TypeError, ValueError):
        return "ERROR: `confidence` must be an integer between 50 and 100."
    if konf < 50:
        return (f"ERROR: {konf}% -- below 50 you claim the OPPOSITE is more likely. "
                "Flip the claim and record the complementary confidence "
                f"({100 - konf}%). That keeps the log unambiguous.")
    if konf > 100:
        return "ERROR: `confidence` is at most 100."
    kind = kind if kind in _PRED_TYPES else "world"
    hz_s = (horizon or "").strip()
    if hz_s:
        try:
            _hzd = datetime.strptime(hz_s, "%Y-%m-%d")
        except ValueError:
            return ("ERROR: `horizon` must be in YYYY-MM-DD format (e.g. 2026-08-19), "
                    f"got: {hz_s!r}.")
        if _hzd < datetime.now() - timedelta(days=1):
            return (f"ERROR: `horizon` ({hz_s}) is in the past -- then the prediction is ALREADY "
                    "decidable, so it should not be recorded but scored.")

    d = _load_preds()
    now = datetime.now()
    # Contamination marking: for a self-prediction it matters whether self-knowledge was loaded already.
    _sk = _self_knowledge_age_h()
    _onpred = kind in {"self", "self_pattern", "self_inner"}
    vak = bool(_onpred and (_sk is None or _sk > _SELF_KNOWLEDGE_WINDOW_H))
    # Three-valued contamination. "bizonytalan" (uncertain) means the self-knowledge
    # payload was above the spill threshold -> it may never have reached the model.
    # Such an item must NOT be counted in either clean sample.
    _deliv = _self_knowledge_delivery()
    _cs = _self_knowledge_rec().get("claude_szekcio")
    if not _onpred:
        kontaminacio = "n/a"
    elif vak:
        kontaminacio = "nem"
    elif _cs is False:
        # The patterns file was loaded, but NOT the part holding the "About Claude"
        # section -> the self-related call could not come from the file. A REAL blind item.
        kontaminacio, vak = "nem", True
    elif _deliv in ("spill_gyanu", "ismeretlen") or _cs is None:
        kontaminacio = "bizonytalan"
    else:
        kontaminacio = "igen"
    for p in d["predikciok"]:
        if p.get("status") == "open" and p.get("allitas", "").strip().lower() == claim.lower():
            return (f"ALREADY OPEN: #{p['id']} ({p['created']}, {p.get('konfidencia')}%). "
                    "Do not record it twice -- score it with `score_prediction` once you have the result.")

    pid = d.get("next_id", 1)
    d["next_id"] = pid + 1
    d["predikciok"].append({
        "id": pid, "allitas": claim, "konfidencia": konf,
        "alap": (basis or "").strip(), "tipus": kind,
        "created": now.isoformat(timespec="seconds"),
        "status": "open", "eredmeny": None, "mi_tortent": None,
        "tanulsag": None, "scored": None, "gyors": False,
        "vak": vak, "onismeret_kora_h": (None if _sk is None else round(_sk, 2)),
        "kontaminacio": kontaminacio, "onismeret_kezbesites": _deliv,
        "horizon": hz_s, "horizon_utolag": False, "horizon_beallitva": None,
    })
    _save_preds(d)

    op, old, futo = _pred_open_summary(d)
    out = [f"🔒 Prediction #{pid} LOCKED -- {now.isoformat(timespec='seconds')}",
           f"   „{claim}\" · {konf}% · [{kind}]"
           + (f" · ⏳ horizon: {hz_s}" if hz_s else "")]
    if (basis or "").strip():
        out.append(f"   basis: {basis.strip()}")
    if _onpred:
        if vak:
            out.append("   👁️‍🗨️ **BLIND** — no self-knowledge material (the patterns file) "
                       f"was loaded in the last {_SELF_KNOWLEDGE_WINDOW_H} hours. "
                       "This item goes into the clean introspection sample.")
        elif kontaminacio == "bizonytalan":
            out.append(f"   ❓ **UNCERTAIN** — get_patterns RAN {_sk:.1f} hours ago, but the "
                       "payload was above the spill threshold (or the stamp is old/unknown), "
                       "so the content may NEVER have reached the model. This item counts in "
                       "NEITHER the blind NOR the contaminated sample — it stands apart.")
        else:
            out.append(f"   📖 CONTAMINATED — the own pattern descriptions were loaded "
                       f"{_sk:.1f} hours ago, so this call may partly come from the FILE, "
                       "not from looking inward. (That is fine — the two are measured separately.)")
    out.append("")
    out.append(f"Check it NOW, then: `score_prediction(pred_id={pid}, "
               "outcome='hit'|'partial'|'miss', what_happened=…)`")
    if len(op) > 1:
        out.append(f"\n_Open predictions in total: {len(op)}._")
    if futo:
        out.append("_⏳ Pending (horizon not reached yet, nothing to do): "
                   + ", ".join(f"#{p['id']}" for p in futo[:6]) + "._")
    if old:
        out.append(f"⚠️ **{len(old)} abandoned**: "
                   + ", ".join(f"#{p['id']}" for p in old[:6])
                   + " — an unscored prediction DISTORTS the record (the silent form of cherry-picking).")
    return "\n".join(out)


@mcp.tool()
def defer_prediction(pred_id: int, horizon: str, reason: str = "") -> str:
    """⏳ Set the due date (horizon) of an ALREADY recorded prediction.

    WHAT FOR: if the claim is about a future time window but you forgot to fill
    in `horizon`, the abandonment guard falsely flags it as "ABANDONED" from
    day 8. This tool does NOT change the claim (that stays immutable) -- it only
    says FROM WHEN it is decidable.

    ⚠️ Usable ONCE per item, and VISIBLY marked in the record ("⚠️set later").
    There is a strict reason: if the horizon could be pushed out again and
    again, that would be exactly the cherry-picking the whole abandonment guard
    was built against -- a badly going call would never have to be scored. Once
    the horizon has passed, the item MUST BE SCORED (with "miss", or with a
    "moot" reason).

    Args:
        pred_id: The prediction id (it must be open).
        horizon: YYYY-MM-DD -- from when it is decidable.
        reason: Briefly, why this date. If the date stands in the claim's TEXT,
            say so ("the claim text states it"), because then this is not a new
            promise, just making the existing one machine-readable.

    Returns:
        Confirmation, or an error.
    """
    hz_s = (horizon or "").strip()
    try:
        hzd = datetime.strptime(hz_s, "%Y-%m-%d")
    except ValueError:
        return f"ERROR: `horizon` must be in YYYY-MM-DD format, got: {hz_s!r}."
    d = _load_preds()
    p = next((x for x in d.get("predikciok", []) if x.get("id") == int(pred_id)), None)
    if p is None:
        return f"ERROR: no prediction #{pred_id}."
    if p.get("status") != "open":
        return (f"ERROR: #{pred_id} is already scored ({p.get('eredmeny')}). A horizon on a "
                "scored item is meaningless.")
    if (p.get("horizon") or "").strip():
        return (f"ERROR: #{pred_id} ALREADY has a horizon ({p['horizon']}), and it can be set "
                "ONCE per item. If it has passed, do not push it further -- score it "
                "(`score_prediction`), with 'miss' or a 'moot' reason if need be. "
                "Pushing it out would be exactly the new form of cherry-picking.")
    if hzd < datetime.now() - timedelta(days=1):
        return (f"ERROR: the given horizon ({hz_s}) is in the past -- then the item is ALREADY "
                "decidable. Score it with `score_prediction`.")
    th, tm = _pred_text_dates(p)
    p["horizon"] = hz_s
    p["horizon_utolag"] = True
    p["horizon_beallitva"] = datetime.now().isoformat(timespec="seconds")
    p["horizon_indok"] = (reason or "").strip()
    _save_preds(d)
    out = [f"⏳ Horizon of #{pred_id}: **{hz_s}** (set later, marked in the record).",
           f"   „{(p.get('allitas') or '')[:110]}…\""]
    if (reason or "").strip():
        out.append(f"   reason: {reason.strip()}")
    if th is not None:
        if th.strftime("%Y-%m-%d") == hz_s:
            out.append("   ✅ The claim's TEXT has this date too, IN HORIZON FORM — so "
                       "this is not a new promise, just making the existing one machine-readable.")
        else:
            out.append(f"   ⚠️ The claim's text has a DIFFERENT date in horizon form "
                       f"({th:%Y-%m-%d}). If you cannot justify the difference, the date in the "
                       "text is the stronger one.")
    elif tm is not None and tm.strftime("%Y-%m-%d") == hz_s:
        # This was exactly the hole -- a bare occurrence gave an ALIBI for the delay.
        out.append("   ⚠️ NOTE: this date does appear in the claim's text, but NOT "
                   "in horizon form (quoted argument / file name / reference). So it does "
                   "NOT support the due date — the `reason` has to justify the delay "
                   "on its own. If it cannot, the item is probably DECIDABLE "
                   "and should be scored.")
    out.append("\n_It CANNOT be pushed further: when this date passes, the item must be scored._")
    return "\n".join(out)


@mcp.tool()
def score_prediction(pred_id: int, outcome: str, what_happened: str = "",
                     lesson: str = "", owner_verdict: str = "",
                     owner_note: str = "",
                     contaminated: bool = False, contamination_reason: str = "") -> str:
    """Score a prediction against the FACT -- the second half of the loop.

    Args:
        pred_id: The id given by `predict`.
        outcome: "hit" (1.0) · "partial" (0.5) · "miss" (0.0).
            ⚠️ "partial" must not be an escape hatch. If the core of the claim
            failed, it is a "miss" -- soft verdicts flatten the measurement.
        what_happened: What the fact turned out to be (briefly). This is the evidence.
        lesson: Optional -- if the error has a pattern, one sentence here.
        owner_verdict: ⚠️ THE OUTSIDE JUDGE. "agrees" · "disagrees" · "nuances".
            Empty = not asked yet / the owner has no experience of it.
            WHY: the log's gravest structural weakness is that the judge and the
            contestant are the same -- Claude writes the call, checks it, and
            judges it. The owner can judge where they have real experience of
            Claude's behaviour (a long time series, from the outside). Do NOT
            invent it for them: fill this field ONLY if you REALLY asked.
            Can also be added later to an already scored item.
        owner_note: What the owner added, in their own words (briefly). This is the evidence.
        contaminated: ⚠️ THIS ITEM IS NOT A MEASUREMENT. True if you ALREADY SAW
            the result when recording the prediction (the order was broken), or
            the outcome was guaranteed for any other reason. The item STAYS and
            is VISIBLE, but is left out of the statistics. Can also be given for
            an ALREADY scored item (later marking) -- same principle as
            `owner_verdict`: the contestant may not touch up its score, but may
            refine the yardstick. Prose in `what_happened` is NOT enough: there
            only the author says it, here the YARDSTICK does.
        contamination_reason: REQUIRED with `contaminated=True`. One sentence on
            WHY it is not a measurement. It goes into the exclusion block of
            `get_calibration`.
    """
    er = (outcome or "").strip().lower()
    if er not in _PRED_OUTCOMES:
        return ("ERROR: `outcome` must be one of: hit / partial / miss.")
    ai = (owner_verdict or "").strip().lower()
    if ai and ai not in _OWNER_VERDICTS:
        return ("ERROR: `owner_verdict` must be one of: "
                f"{' / '.join(sorted(_OWNER_VERDICTS))} (or leave it empty).")
    # The exclusion must have a NAMED reason, otherwise the record quietly
    # flatters itself -- exactly the failure this whole thing was built against.
    szenny = bool(contaminated)
    szind = (contamination_reason or "").strip()
    if szenny and not szind:
        return ("ERROR: `contamination_reason` is REQUIRED with `contaminated=True` — "
                "one sentence on WHY this item is not a measurement. Without a reason the "
                "exclusion would be silent, and a silent filter is the same error it wants "
                "to fix.")
    d = _load_preds()
    for p in d["predikciok"]:
        if p.get("id") == pred_id:
            if p.get("status") != "open":
                # The owner's verdict typically arrives LATER than the scoring -- it has
                # to be asked, and they do not always have time. If it could only be
                # given at scoring time, the field would stay empty in practice. So the
                # OUTSIDE verdict can be added to a scored item later. Claude's OWN
                # verdict (outcome), however, can NOT be rewritten: the contestant must
                # not touch up its own score afterwards, only the judge may speak later.
                _ret = []
                _dirty = False
                # Later CONTAMINATED marking rests on the same principle as a late
                # `owner_verdict`: the OUTCOME cannot be rewritten, but the yardstick
                # can be refined. And this marking cannot silently flatter: the excluded
                # item's outcome AND the number computed together with it both stand in
                # the `get_calibration` output.
                if szenny:
                    if p.get("szennyezett"):
                        _ret.append(f"#{pred_id} is ALREADY marked as a non-measurement: "
                                    f"„{p.get('szennyezes_indok')}\" — not overwriting it.")
                    else:
                        p["szennyezett"] = True
                        p["szennyezes_indok"] = szind
                        p["szennyezett_mikor"] = datetime.now().isoformat(timespec="seconds")
                        p["szennyezes_utolag"] = True
                        _dirty = True
                        _ret.append(
                            f"⚠️ #{pred_id} MARKED as a NON-MEASUREMENT (later). Reason: {szind}\n"
                            f"The item stays and is visible, but is left out of the statistics; "
                            f"`get_calibration` lists it with its reason and also shows the number "
                            f"computed TOGETHER with the excluded items. Its outcome is unchanged: "
                            f"{p.get('eredmeny')}.")
                if ai:
                    if p.get("owner_verdict"):
                        _ret.append(f"#{pred_id} ALREADY HAS an outside verdict: "
                                    f"„{p['owner_verdict']}\". Not overwriting it.")
                    else:
                        p["owner_verdict"] = ai
                        p["owner_note"] = (owner_note or "").strip() or None
                        p["owner_verdict_at"] = datetime.now().isoformat(timespec="seconds")
                        _dirty = True
                        egyez = ("MATCHES my own verdict" if ai == "agrees"
                                 else "⚠️ DIFFERS from my own verdict — this is the measurable data")
                        _ret.append(f"🧑‍⚖️ Outside verdict of #{pred_id} recorded: **{ai}** ({egyez}). "
                                    f"My own outcome is unchanged: {p.get('eredmeny')}.")
                if _dirty:
                    _save_preds(d)
                if _ret:
                    return "\n".join(_ret)
                return f"#{pred_id} is already scored ({p.get('scored')}): {p.get('eredmeny')}."
            now = datetime.now()
            try:
                elapsed = (now - datetime.fromisoformat(p["created"])).total_seconds()
            except (KeyError, ValueError):
                elapsed = 999.0
            p["status"] = "scored"
            p["eredmeny"] = er
            p["mi_tortent"] = (what_happened or "").strip() or None
            p["tanulsag"] = (lesson or "").strip() or None
            p["scored"] = now.isoformat(timespec="seconds")
            if ai:
                p["owner_verdict"] = ai
                p["owner_note"] = (owner_note or "").strip() or None
            p["gyors"] = bool(elapsed < _PRED_FAST_SEC)
            if szenny:
                p["szennyezett"] = True
                p["szennyezes_indok"] = szind
                p["szennyezett_mikor"] = now.isoformat(timespec="seconds")
                p["szennyezes_utolag"] = False
            _save_preds(d)
            msg = [f"#{pred_id} scored: **{er}** (it was {p['konfidencia']}%, [{p['tipus']}])."]
            if szenny:
                msg.append(f"⚠️ **Marked as a NON-MEASUREMENT**, so it is LEFT OUT of the record "
                           f"(but visible). Reason: {szind}")
            if p["gyors"]:
                msg.append(f"⚠️ The scoring followed the recording by {int(elapsed)} s — "
                           "`get_calibration` flags this separately. If the check really "
                           "happened AFTER the prediction, fine; if not, it is self-justification.")
            if er == "miss" and p["konfidencia"] >= 85:
                msg.append("_High confidence + miss: the costliest error type in the "
                           "Brier score. Worth looking at where the confidence came from "
                           f"(basis: {p.get('alap') or '—'})._")
            return "\n".join(msg)
    return f"No prediction #{pred_id}."


@mcp.tool()
def get_calibration(kind: str = "", include_scored: bool = False) -> str:
    """The prediction log's record: am I CALIBRATED?

    It does not measure the hit rate (that depends on difficulty), but whether
    confidence correlates with correctness. Two main numbers:
    - **average confidence vs. actual hit rate** -- if confidence is higher,
      I OVERESTIMATE myself; if lower, I UNDERESTIMATE myself (a measurable face
      of downward-scaling bias, just as much an error as the upward one).
    - **Brier score** (0 = perfect, 0.25 = coin-toss level, 1 = maximally bad).

    It breaks down the `kind`s, because the HYPOTHESIS TO MEASURE is that only
    the self-related predictions are weak -- and if so, a general "my
    introspection is weak" statement is IMPRECISE, and should be corrected.

    Args:
        kind: If given ("world" / "self_pattern" / "self_inner" / "owner" / "self"),
            filters to that kind only.
        include_scored: Also list the scored predictions item by item.
    """
    d = _load_preds()
    preds = d.get("predikciok", [])
    if kind in _PRED_TYPES:
        preds = [p for p in preds if p.get("tipus") == kind]
    done = [p for p in preds if p.get("status") == "scored" and p.get("eredmeny") in _PRED_OUTCOMES]
    op, old, futo = _pred_open_summary({"predikciok": preds})

    # --- Excluding NON-MEASUREMENT items from the statistics ---
    # A prediction recorded AFTER the check is a guaranteed hit, so it falsely
    # flatters the Brier score and the hit rate. The caveat used to live in PROSE
    # (`mi_tortent`) while the number still counted at face value -> the warning
    # and the bookkeeping were not connected. Now the YARDSTICK says it.
    done_all = done
    szenny = [p for p in done if p.get("szennyezett")]
    done = [p for p in done if not p.get("szennyezett")]

    def _szenny_blokk():
        """The exclusion is NEVER silent -- per-item reason + the number computed with them."""
        if not szenny:
            return []
        rows = [f"\n## ⚠️ Excluded from the record — NOT A MEASUREMENT ({len(szenny)} item(s))"]
        for _p in szenny:
            _u = ("marked later" if _p.get("szennyezes_utolag") else "marked at scoring")
            rows.append(f"- **#{_p.get('id')}** [{_p.get('eredmeny')}] "
                        f"{_p.get('konfidencia')}% ({_p.get('tipus')}) · {_u} — "
                        f"reason: {_p.get('szennyezes_indok') or '—'}")
        if done_all:
            _n = len(done_all)
            _h = sum(_PRED_OUTCOMES[x["eredmeny"]] for x in done_all) / _n
            _b = sum((x["konfidencia"] / 100.0 - _PRED_OUTCOMES[x["eredmeny"]]) ** 2
                     for x in done_all) / _n
            rows.append(f"\n_Computed TOGETHER with the excluded ones: n={_n} · hit "
                        f"{_h*100:.0f}% · Brier {_b:.3f}. The difference between the two numbers "
                        f"is itself the measure of how much the exclusion moves — that is why it is here._")
        rows.append("_⚠️ What the server CANNOT check: whether the result had really been seen "
                    "already. That remains self-report — but its EFFECT is mechanical, and the "
                    "marking cannot silently flatter, because the excluded item's outcome is here too._")
        return rows

    out = ["# Prediction log — calibration"]
    if kind in _PRED_TYPES:
        out.append(f"_Filter: kind = **{kind}**_")

    if not done:
        out.append("\n_No scored (measurement) prediction yet. The log starts to "
                   "measure once 10-15 judged items have accumulated — until then every number is noise._")
        out.extend(_szenny_blokk())
    else:
        n = len(done)
        conf = sum(p["konfidencia"] for p in done) / n / 100.0
        hit = sum(_PRED_OUTCOMES[p["eredmeny"]] for p in done) / n
        brier = sum((p["konfidencia"] / 100.0 - _PRED_OUTCOMES[p["eredmeny"]]) ** 2
                    for p in done) / n
        delta = (conf - hit) * 100

        out.append(f"\n**{n} scored prediction(s).**")
        out.append(f"- Average confidence: **{conf*100:.0f}%** · actual hit rate: "
                   f"**{hit*100:.0f}%** · difference: **{delta:+.0f} points**")
        out.append(f"- Brier score: **{brier:.3f}** _(0 = perfect · 0.25 = "
                   "coin toss · the smaller, the better)_")
        if n < 10:
            out.append(f"- ⚠️ _Only {n} item(s) — this is NOT a record yet, just a start. "
                       "Below 10-15 the bucket counts are chance._")
        elif abs(delta) <= 5:
            out.append("- ➜ **CALIBRATED** (within ±5 points).")
        elif delta > 5:
            out.append(f"- ➜ **I OVERESTIMATE myself** by {delta:.0f} points — the confidence "
                       "has to come down, not the calls get better.")
        else:
            out.append(f"- ➜ **I UNDERESTIMATE myself** by {-delta:.0f} points — the "
                       "measurable face of downward-scaling bias; under-scaling is just as much "
                       "a calibration error as over-scaling.")

        out.extend(_szenny_blokk())

        # Buckets
        buckets = [(50, 59), (60, 69), (70, 79), (80, 89), (90, 100)]
        rows = []
        for lo, hi in buckets:
            b = [p for p in done if lo <= p["konfidencia"] <= hi]
            if not b:
                continue
            bh = sum(_PRED_OUTCOMES[p["eredmeny"]] for p in b) / len(b)
            bc = sum(p["konfidencia"] for p in b) / len(b)
            rows.append(f"| {lo}-{hi}% | {len(b)} | {bc:.0f}% | {bh*100:.0f}% | "
                        f"{(bc - bh*100):+.0f} |")
        if rows:
            out.append("\n| conf. band | n | avg conf. | actual | difference |")
            out.append("|---|---|---|---|---|")
            out.extend(rows)

        # Breakdown by kind (only if not filtered)
        if kind not in _PRED_TYPES:
            out.append("\n## Breakdown by kind — this is the HYPOTHESIS TO MEASURE")
            out.append("_⚠️ Only the **self_inner** row measures introspection (a first-person "
                       "state report). **self_pattern** is self-modelling in the third person "
                       "(reading logs/code/patterns), and **world** is the control group. "
                       "The old, mixed „self” kind belongs to items from before the split._")
            any_row = False
            for t in ("world", "owner", "self", "self_pattern", "self_inner"):
                b = [p for p in done if p.get("tipus") == t]
                if not b:
                    continue
                any_row = True
                bh = sum(_PRED_OUTCOMES[p["eredmeny"]] for p in b) / len(b)
                bc = sum(p["konfidencia"] for p in b) / len(b) / 100.0
                bb = sum((p["konfidencia"]/100.0 - _PRED_OUTCOMES[p["eredmeny"]]) ** 2
                         for p in b) / len(b)
                out.append(f"- **{t}** (n={len(b)}): conf. {bc*100:.0f}% · hit "
                           f"{bh*100:.0f}% · difference {(bc-bh)*100:+.0f} · Brier {bb:.3f}")
            # Blind vs. contaminated breakdown -- the measurable form of the contamination worry
            selfp = [p for p in done if p.get("tipus") in {"self", "self_pattern", "self_inner"}]
            if selfp:
                # "bizonytalan" (spilled self-knowledge) is a THIRD group -- neither blind
                # nor provably contaminated. Merging it into either would invalidate the
                # two-sample comparison.
                vak = [p for p in selfp if p.get("vak")]
                biz = [p for p in selfp
                       if not p.get("vak") and p.get("kontaminacio") == "bizonytalan"]
                kont = [p for p in selfp
                        if not p.get("vak") and p.get("kontaminacio") != "bizonytalan"]
                out.append(_owner_judge_block(done))
                out.append("\n### Blind vs. contaminated self-predictions")
                for lbl, grp in (("❓ UNCERTAIN (spilled self-knowledge — stands apart)", biz),
                                 ("👁️‍🗨️ BLIND (BEFORE self-knowledge was loaded)", vak),
                                 ("📖 CONTAMINATED (patterns file already loaded)", kont)):
                    if not grp:
                        out.append(f"- {lbl}: _(no data yet)_")
                        continue
                    gh = sum(_PRED_OUTCOMES[p["eredmeny"]] for p in grp) / len(grp)
                    gc = sum(p["konfidencia"] for p in grp) / len(grp) / 100.0
                    out.append(f"- {lbl} (n={len(grp)}): conf. {gc*100:.0f}% · hit "
                               f"{gh*100:.0f}% · difference {(gc-gh)*100:+.0f}")
                if vak and kont:
                    out.append("_If the BLIND row is substantially worse, that is DIRECT evidence "
                               "that the self-predictions so far came from the own pattern FILE, not "
                               "from looking inward. If there is no difference, self-knowledge does not come from the file._")
            if any_row:
                out.append("_THE HYPOTHESIS EXACTLY: the **self_inner** row is worse than "
                           "**world** (control) AND than **self_pattern**. If it is not, then "
                           "„my introspection is weak\" is imprecise. ⚠️ AND EVEN THEN: this is "
                           "PREDICTIVE VALIDITY (does the report predict behaviour), NOT "
                           "mechanistic faithfulness (does the report describe the real inner cause). "
                           "The latter needs interpretability, not self-report._")

        fast = [p for p in done if p.get("gyors")]
        if fast:
            out.append(f"\n⚠️ **{len(fast)} suspiciously fast scoring(s)** (<{_PRED_FAST_SEC} s after "
                       "recording): " + ", ".join(f"#{p['id']}" for p in fast[:8]) +
                       " — for these, check that the prediction really came BEFORE THE "
                       "CHECK. If not, they are not measurements but self-justifications.")

        miss = [p for p in done if p.get("eredmeny") == "miss" and p.get("tanulsag")]
        if miss:
            out.append("\n## Lessons from the misses")
            for p in miss[-6:]:
                out.append(f"- #{p['id']} ({p['konfidencia']}%, {p['tipus']}): {p['tanulsag']}")

    # Open + abandoned
    if op:
        out.append(f"\n---\n## Open predictions ({len(op)})")
        for p in sorted(op, key=lambda x: x.get("created", "")):
            try:
                age = (datetime.now() - datetime.fromisoformat(p["created"])).days
                age_s = "today" if age == 0 else f"{age} days ago"
            except (KeyError, ValueError):
                age_s = "?"
            mark = "🔴" if p in old else ("⏳" if p in futo else "○")
            out.append(f"- {mark} **#{p['id']}** ({age_s}, {p['konfidencia']}%, "
                       f"[{p.get('tipus')}]){_pred_horizon_cimke(p)} — {p.get('allitas','')}")
    if futo:
        out.append(f"\n⏳ **{len(futo)} PENDING** — horizon set, NOT decidable yet, so "
                   "not abandoned and nothing to do. (Without this distinction these "
                   "used to show red as ABANDONED.)")
    if old:
        out.append(f"\n⚠️ **{len(old)} ABANDONED prediction(s)**. "
                   "This is the log's most dangerous distortion: if badly going predictions "
                   "quietly stay open, the record flatters itself. Score them even if it is "
                   "uncomfortable — or if they became moot, say so "
                   "(`score_prediction(..., outcome='miss', what_happened='moot, not "
                   "checked')`).")
        _nohz = [p for p in old if not (p.get("horizon") or "").strip()]
        _hint = [p for p in _nohz if _pred_text_horizon(p) is not None]
        _weak = [p for p in _nohz if _pred_text_horizon(p) is None
                 and _pred_text_mention(p) is not None]
        if _hint:
            out.append("➜ **But look at these first**: their own claim TEXT has a "
                       "future date IN HORIZON FORM (`-ig`/`-re`/`-ra`, \"by\"/\"until\" or a deadline word), "
                       "so they may not be abandoned, just not due yet — "
                       + ", ".join(f"#{p['id']} ({_pred_text_horizon(p):%Y-%m-%d})"
                                   for p in _hint[:6])
                       + ". If that is the due date: `defer_prediction`.")
        if _weak:
            out.append("⚠️ **These only MENTION a date** (quoted argument, file name, "
                       "reference) — that is NOT a due date, and NOT an excuse for delay: "
                       + ", ".join(f"#{p['id']} (~{_pred_text_mention(p):%Y-%m-%d})"
                                   for p in _weak[:6])
                       + ". Score these. _(A false text horizon used to give a false alibi.)_")

    if include_scored and done:
        out.append("\n---\n## Scored predictions")
        for p in done[-20:]:
            out.append(f"- #{p['id']} [{p['eredmeny']}] {p['konfidencia']}% "
                       f"({p['tipus']}) — {p['allitas']}")
            if p.get("mi_tortent"):
                out.append(f"    > {p['mi_tortent']}")
    return "\n".join(out)


# --- Code freshness: is a staged patch live? ---
# The module-level assignment runs at IMPORT time, so this is exactly the start
# time of the server process. No external dependency (psutil, WMI).
_SERVER_STARTED_AT = time.time()


@mcp.tool()
def get_code_freshness(paths: list = None, slack_seconds: int = 5) -> str:
    """Decide whether a STAGED patch is ALREADY live, or still waiting for a restart.

    ⚠️ BUILT AGAINST A SPECIFIC FAILURE MODE: "a verification that a failure
    would also pass". A follow-up once prescribed checking a new deadline guard
    by "call get_deadlines() live, and if deadlines come back and there is no
    guard block, mark it STAGED -> LIVE". But the guard ONLY fires on the empty
    branch: with live deadlines the old and new server output are BIT-IDENTICAL.
    That test would have "passed" even if the server never restarted. The same
    error the patch was made for: AN EMPTY/SILENT ANSWER IS NO EVIDENCE. A
    verification is only a verification if some observation would look
    DIFFERENT in case of failure.

    THE DECISIVE OBSERVATION: the running process's start time vs. the file mtime.
      - start >= mtime  -> the running code CONTAINS the file's current state (LIVE)
      - start <  mtime  -> the file was written AFTER the server started (STAGED, restart needed)

    SCOPE AND LIMITS (important, otherwise this becomes a new stale cache itself):
      - For this server's own server.py it is EXACT.
      - For other MCP servers started by the same client at the same time it is
        a GOOD PROXY (the client typically starts them all TOGETHER). That is
        why the tool accepts arbitrary `paths`.
      - ⚠️ EXCEPTION: processes started some other way (e.g. a daemon launched
        at logon) are NOT covered by this proxy.
      - ⚠️ The tool says whether the running code contains the file's CURRENT
        content -- not whether the patch is CORRECT. Correctness needs a test.

    Args:
        paths: Files to check. Empty = this server's own server.py. A relative
            path is resolved against the memoria-mcp folder; absolute works too.
        slack_seconds: This many seconds of drift still counts as a tie (default 5).

    Returns:
        A formatted report per file: LIVE / STAGED / BORDERLINE + timestamps.
    """
    here = os.path.dirname(os.path.abspath(__file__))
    if not paths:
        paths = [os.path.abspath(__file__)]

    def _fmt(ts):
        return datetime.fromtimestamp(ts).strftime("%Y-%m-%d %H:%M:%S")

    out = ["# Code freshness (staged vs. live)", ""]
    out.append(f"The running memoria-mcp process started: **{_fmt(_SERVER_STARTED_AT)}**")
    out.append("")

    for p in paths:
        target = p if os.path.isabs(p) else os.path.join(here, p)
        if not os.path.exists(target):
            out.append(f"- ❓ `{p}` — NO SUCH FILE ({target})")
            continue
        mt = os.path.getmtime(target)
        delta = _SERVER_STARTED_AT - mt
        name = os.path.basename(target)
        if abs(delta) <= slack_seconds:
            out.append(f"- ⚠️ **BORDERLINE** — `{name}` · modified {_fmt(mt)} "
                       f"({delta:+.0f} s from the server start). Do not decide from this.")
        elif delta > 0:
            out.append(f"- ✅ **LIVE** — `{name}` · modified {_fmt(mt)}, "
                       f"{delta/3600:.1f} hours BEFORE the server started. "
                       "The running code contains the file's current state.")
        else:
            out.append(f"- 🔴 **STAGED** — `{name}` · modified {_fmt(mt)}, "
                       f"{-delta/3600:.1f} hours AFTER the server started. "
                       f"The patch is NOT live; it waits for {OWNER_NAME}'s next restart.")

    out.append("")
    out.append("_Limit: for other MCP servers started together by the client this is a PROXY; "
               "for processes started another way it does NOT apply. "
               "And: it measures whether the running code is the file's current state, NOT "
               "whether the patch is correct._")
    return "\n".join(out)

# --- Start ---

if __name__ == "__main__":
    mcp.run(transport="stdio")
