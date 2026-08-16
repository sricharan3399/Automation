#!/usr/bin/env python
"""Repository security audit.

Run this BEFORE staging anything. It answers one question: is it safe to commit
what is currently in this working tree?

    python scripts/repository_audit.py
    python scripts/repository_audit.py --json
    python scripts/repository_audit.py --staged     # audit only staged files

Design notes that matter:

* **Findings are classified against git, not guessed.** A `.db` file that git
  already ignores is not a problem; the same file tracked would be. The audit
  asks git via ``git check-ignore`` rather than re-implementing ignore rules,
  so it can never disagree with what ``git add`` would actually do.

* **Secret *values* are never printed.** Findings report the file, the line
  number and the rule that matched. The matched text is redacted, because an
  audit tool that echoes secrets into a terminal or a CI log has become part of
  the problem.

* **Placeholders are not secrets.** `.env.example` exists to carry field names
  with empty or obviously-fake values. Flagging those would train people to
  ignore this tool.

* **Not every JSON or image is generated output.** `package.json`,
  `tsconfig.json`, `config/*.yaml` and the synthetic golden fixtures are source.
  Classification is by location and purpose, not by extension alone.

Exit codes: ``0`` safe to commit, ``1`` unsafe, ``2`` the audit itself failed.
"""

from __future__ import annotations

import argparse
import json
import re
import subprocess
import sys
from collections.abc import Iterable
from dataclasses import asdict, dataclass, field
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parents[1]

#: Never walked: enormous, generated, and never committed anyway.
SKIP_DIRS = {
    ".git", ".venv", "venv", "env", "node_modules", "__pycache__",
    ".pytest_cache", ".mypy_cache", ".ruff_cache", ".vite", "htmlcov",
    ".runtime", "dist", "build", ".idea", ".vscode",
}

LARGE_FILE_BYTES = 10 * 1024 * 1024  # 10 MB

# ---------------------------------------------------------------------------
# What we look for
# ---------------------------------------------------------------------------

#: Raw AV recordings and sensor data. None of this belongs in git.
AV_DATA_EXTENSIONS = {
    ".bag", ".db3", ".mcap", ".pcap", ".pcapng",
    ".las", ".laz", ".ply", ".pcd",
    ".mp4", ".avi", ".mov", ".mkv", ".h264",
    ".raw", ".bin", ".npy", ".npz", ".parquet", ".feather",
}

DATABASE_EXTENSIONS = {".db", ".sqlite", ".sqlite3", ".db-journal", ".db-wal", ".db-shm"}

CREDENTIAL_EXTENSIONS = {".pem", ".key", ".p12", ".pfx", ".jks", ".keystore", ".ppk"}

CREDENTIAL_FILENAMES = {
    "credentials.json", "credentials.yaml", "credentials.yml",
    "secrets.json", "secrets.yaml", "secrets.yml",
    "id_rsa", "id_dsa", "id_ecdsa", "id_ed25519",
    ".npmrc", ".pypirc", ".netrc",
}

ENV_FILE_PATTERN = re.compile(r"^\.env(\..+)?$")

#: Directories whose contents are run outputs, never source.
GENERATED_DIRS = {"output", "exports", "evidence", "recordings", "datasets", "logs"}

#: Extensions that are *sometimes* generated output and sometimes source.
AMBIGUOUS_EXTENSIONS = {".csv", ".xlsx", ".xls", ".json", ".png", ".jpg", ".jpeg", ".pdf", ".svg"}

#: Locations where an ambiguous extension is definitely legitimate source.
SOURCE_ASSET_PREFIXES = (
    "dashboard/src", "dashboard/public", "dashboard/package.json",
    "dashboard/package-lock.json", "dashboard/tsconfig.json",
    "config/", ".github/", "docs/", "scripts/",
    "tests/golden_dataset/",  # synthetic, deterministic, generated from source
)

# ---------------------------------------------------------------------------
# Secret detection
# ---------------------------------------------------------------------------

#: High-confidence: these shapes are secrets wherever they appear.
HIGH_CONFIDENCE_SECRETS: list[tuple[str, re.Pattern[str]]] = [
    ("private_key_block", re.compile(r"-----BEGIN (?:RSA |EC |DSA |OPENSSH |PGP )?PRIVATE KEY-----")),
    ("github_pat", re.compile(r"\bgh[pousr]_[A-Za-z0-9]{16,}\b")),
    ("github_fine_grained_pat", re.compile(r"\bgithub_pat_[A-Za-z0-9_]{20,}\b")),
    ("aws_access_key_id", re.compile(r"\b(?:AKIA|ASIA)[0-9A-Z]{16}\b")),
    ("slack_token", re.compile(r"\bxox[baprs]-[A-Za-z0-9-]{10,}\b")),
    ("google_api_key", re.compile(r"\bAIza[0-9A-Za-z_\-]{35}\b")),
    ("jwt", re.compile(r"\beyJ[A-Za-z0-9_-]{10,}\.eyJ[A-Za-z0-9_-]{10,}\.[A-Za-z0-9_-]{10,}\b")),
]

#: Assignment-shaped: `SECRET = "value"`. Only a finding when the value looks real.
SECRET_KEY_NAMES = (
    "api_key", "api_token", "access_token", "auth_token", "refresh_token",
    "password", "passwd", "secret", "client_secret", "private_key",
    "bearer", "authorization", "aws_access_key", "aws_secret_access_key",
    "database_password", "github_token", "datascout_token", "session_key",
)
ASSIGNMENT_PATTERN = re.compile(
    r"(?i)\b(" + "|".join(SECRET_KEY_NAMES) + r")\b\s*[:=]\s*[\"']?([^\s\"',;{}()\[\]#]+)",
)

#: Values that are obviously not real credentials.
PLACEHOLDER_VALUES = {
    "", "none", "null", "true", "false", "changeme", "change_me", "change-me",
    "your-token", "your_token", "yourtoken", "example", "placeholder", "todo",
    "xxx", "xxxx", "redacted", "withheld", "dummy", "sample", "test", "fake",
    "n/a", "na", "tbd", "unset", "notset", "not_set", "secret", "password",
}
PLACEHOLDER_PATTERN = re.compile(
    r"^(?:<.*>|\{\{.*\}\}|\$\{.*\}|\*+|x+|\.\.\.|-+|_+)$", re.IGNORECASE
)

#: Lines that reference a secret *name* without carrying a value.
SAFE_CONTEXT_PATTERN = re.compile(
    r"(?i)(env\[|environ|getenv|resolve_secret|credential_key|\.get\(|"
    r"os\.environ|SECRET_KEY_NAMES|secret_is_available|keyring|"
    r"password_manager|type=|:\s*str|:\s*string|Optional\[)"
)

TEXT_EXTENSIONS = {
    ".py", ".ts", ".tsx", ".js", ".jsx", ".json", ".yaml", ".yml", ".toml",
    ".ini", ".cfg", ".md", ".txt", ".ps1", ".bat", ".cmd", ".sh", ".env",
    ".html", ".css", ".xml", ".example",
}
MAX_SCAN_BYTES = 2 * 1024 * 1024

#: Extensions that are source code. An ignored file with one of these is almost
#: certainly an over-broad ignore rule, not a deliberate exclusion.
SOURCE_EXTENSIONS = {
    ".py", ".ts", ".tsx", ".js", ".jsx", ".ps1", ".psm1", ".bat", ".cmd",
    ".yaml", ".yml", ".toml", ".cfg", ".html", ".css",
}

#: Directories whose contents are legitimately ignored source-shaped files.
LEGITIMATELY_IGNORED_ROOTS = (
    ".venv/", "venv/", "node_modules/", "dashboard/dist/", "dashboard/node_modules/",
    "build/", "dist/", ".runtime/", "output/", "data/", "htmlcov/",
    ".mypy_cache/", ".pytest_cache/", ".ruff_cache/", "__pycache__/",
)


@dataclass
class Finding:
    category: str
    path: str
    detail: str
    ignored_by_git: bool
    severity: str  # BLOCK | REVIEW | INFO
    line: int | None = None

    def to_dict(self) -> dict[str, object]:
        return asdict(self)


@dataclass
class AuditReport:
    secrets: list[Finding] = field(default_factory=list)
    env_files: list[Finding] = field(default_factory=list)
    av_data: list[Finding] = field(default_factory=list)
    databases: list[Finding] = field(default_factory=list)
    generated: list[Finding] = field(default_factory=list)
    large_files: list[Finding] = field(default_factory=list)
    credential_files: list[Finding] = field(default_factory=list)
    ignored_source: list[Finding] = field(default_factory=list)
    scanned_files: int = 0
    git_available: bool = False

    def all_findings(self) -> list[Finding]:
        return [
            *self.secrets, *self.env_files, *self.av_data, *self.databases,
            *self.generated, *self.large_files, *self.credential_files,
            *self.ignored_source,
        ]

    @property
    def blockers(self) -> list[Finding]:
        """Findings that must be resolved before committing.

        Most are things that would be committed and should not be. The
        ``ignored_source`` findings are the mirror image: source files that
        would NOT be committed and must be. Both break the repository, so both
        block.
        """
        return [
            f
            for f in self.all_findings()
            if f.severity == "BLOCK" and (not f.ignored_by_git or f.category == "ignored_source")
        ]

    @property
    def reviews(self) -> list[Finding]:
        return [f for f in self.all_findings() if f.severity == "REVIEW" and not f.ignored_by_git]

    @property
    def safe(self) -> bool:
        return not self.blockers


# ---------------------------------------------------------------------------
# Git integration
# ---------------------------------------------------------------------------
def _git(args: list[str], stdin: bytes | None = None) -> tuple[int, bytes]:
    """Run git and return ``(returncode, raw stdout)``.

    Deliberately binary. Text mode would translate ``\\n`` to ``\\r\\n`` on
    Windows when writing to git's stdin, so git would receive paths ending in a
    carriage return and echo them back quoted — and every path would then fail
    to match, making ignored files look committable.
    """
    try:
        result = subprocess.run(
            ["git", *args],
            cwd=REPO_ROOT,
            input=stdin,
            capture_output=True,
            timeout=120,
        )
        return result.returncode, result.stdout
    except (OSError, subprocess.SubprocessError):
        return 1, b""


def git_available() -> bool:
    return _git(["rev-parse", "--is-inside-work-tree"])[0] == 0


def ignored_paths(paths: list[str]) -> set[str]:
    """Ask git which of ``paths`` it ignores.

    Delegating to ``git check-ignore`` rather than re-implementing ignore rules
    means this audit can never disagree with what ``git add`` would actually do.
    ``-z`` keeps input and output NUL-separated, so no newline translation and
    no shell quoting can corrupt a path.
    """
    if not paths:
        return set()
    payload = b"\0".join(p.encode("utf-8") for p in paths) + b"\0"
    code, out = _git(["check-ignore", "-z", "--stdin"], stdin=payload)
    if code not in (0, 1):  # 1 simply means "nothing matched"
        return set()
    return {
        chunk.decode("utf-8", errors="replace").replace("\\", "/")
        for chunk in out.split(b"\0")
        if chunk
    }


def ignore_rule_for(path: str) -> str:
    """Which .gitignore line excluded ``path``, for an actionable message."""
    code, out = _git(["check-ignore", "-v", "--", path])
    if code != 0 or not out:
        return "unknown rule"
    first = out.decode("utf-8", errors="replace").splitlines()[0]
    return first.split("\t")[0] if "\t" in first else first


def staged_files() -> list[str]:
    code, out = _git(["diff", "--cached", "--name-only", "-z"])
    if code != 0:
        return []
    return [
        chunk.decode("utf-8", errors="replace")
        for chunk in out.split(b"\0")
        if chunk
    ]


# ---------------------------------------------------------------------------
# Scanning
# ---------------------------------------------------------------------------
def iter_files(root: Path) -> Iterable[Path]:
    for path in root.rglob("*"):
        if not path.is_file():
            continue
        if any(part in SKIP_DIRS for part in path.parts):
            continue
        yield path


def relative(path: Path) -> str:
    try:
        return path.relative_to(REPO_ROOT).as_posix()
    except ValueError:
        return path.as_posix()


def is_placeholder(value: str) -> bool:
    token = value.strip().strip("\"'").lower()
    if token in PLACEHOLDER_VALUES:
        return True
    if PLACEHOLDER_PATTERN.match(token):
        return True
    # Env-var references and short tokens are not credentials.
    if token.startswith(("$", "%", "{", "<", "av_", "your")):
        return True
    return len(token) < 8


def scan_text_for_secrets(path: Path, rel: str) -> list[tuple[str, int, str]]:
    """Return ``(rule, line_number, redacted_detail)`` — never the value itself."""
    try:
        if path.stat().st_size > MAX_SCAN_BYTES:
            return []
        text = path.read_text(encoding="utf-8", errors="ignore")
    except OSError:
        return []

    hits: list[tuple[str, int, str]] = []
    is_example = path.name.endswith(".example") or path.name == ".env.example"

    for index, line in enumerate(text.splitlines(), start=1):
        if len(line) > 4000:
            continue

        for rule, pattern in HIGH_CONFIDENCE_SECRETS:
            if pattern.search(line):
                hits.append((rule, index, "high-confidence credential pattern (value redacted)"))

        if is_example:
            # A template of field names is exactly what .env.example is for.
            continue

        match = ASSIGNMENT_PATTERN.search(line)
        if match and not is_placeholder(match.group(2)):
            if SAFE_CONTEXT_PATTERN.search(line):
                continue  # a reference to the name, not an assigned value
            hits.append(
                (
                    f"assigned_{match.group(1).lower()}",
                    index,
                    f"'{match.group(1)}' assigned a non-placeholder value (value redacted)",
                )
            )
    return hits


def classify_ambiguous(rel: str) -> str | None:
    """Decide whether an ambiguous file is source, fixture or generated output."""
    lowered = rel.lower()
    if lowered.startswith(SOURCE_ASSET_PREFIXES):
        return None  # legitimate source or approved fixture
    first = lowered.split("/", 1)[0]
    if first in GENERATED_DIRS:
        return "generated run output"
    if "/evidence/" in lowered or "/output/" in lowered or "/exports/" in lowered:
        return "generated run output"
    if lowered.endswith(("results.csv", "rejected_records.csv", "evidence_manifest.csv")):
        return "generated AV test result"
    return None


def audit(only_staged: bool = False) -> AuditReport:
    report = AuditReport(git_available=git_available())

    if only_staged and report.git_available:
        candidates = [REPO_ROOT / p for p in staged_files()]
        candidates = [p for p in candidates if p.is_file()]
    else:
        candidates = list(iter_files(REPO_ROOT))

    rels = [relative(p) for p in candidates]
    ignored = ignored_paths(rels) if report.git_available else set()
    report.scanned_files = len(candidates)

    for path in candidates:
        rel = relative(path)
        is_ignored = rel in ignored
        suffix = path.suffix.lower()

        try:
            size = path.stat().st_size
        except OSError:
            continue

        # --- environment files ---------------------------------------
        if ENV_FILE_PATTERN.match(path.name):
            if path.name == ".env.example":
                report.env_files.append(
                    Finding("env", rel, "template of field names (expected in git)", is_ignored, "INFO")
                )
            else:
                report.env_files.append(
                    Finding("env", rel, "local environment file", is_ignored, "BLOCK")
                )

        # --- credential files ------------------------------------------
        if suffix in CREDENTIAL_EXTENSIONS or path.name.lower() in CREDENTIAL_FILENAMES:
            report.credential_files.append(
                Finding("credential_file", rel, f"credential-bearing file ({suffix or path.name})", is_ignored, "BLOCK")
            )

        # --- AV raw data ------------------------------------------------
        if suffix in AV_DATA_EXTENSIONS:
            report.av_data.append(
                Finding("av_data", rel, f"raw AV/sensor data ({suffix}, {size / 1e6:.1f} MB)", is_ignored, "BLOCK")
            )

        # --- runtime databases --------------------------------------------
        if suffix in DATABASE_EXTENSIONS:
            report.databases.append(
                Finding("database", rel, f"runtime database ({size / 1e6:.2f} MB)", is_ignored, "BLOCK")
            )

        # --- generated output ---------------------------------------------
        if suffix in AMBIGUOUS_EXTENSIONS:
            reason = classify_ambiguous(rel)
            if reason:
                report.generated.append(Finding("generated", rel, reason, is_ignored, "BLOCK"))

        # --- large files -----------------------------------------------------
        if size > LARGE_FILE_BYTES:
            report.large_files.append(
                Finding("large_file", rel, f"{size / 1e6:.1f} MB exceeds the {LARGE_FILE_BYTES / 1e6:.0f} MB threshold",
                        is_ignored, "REVIEW")
            )

        # --- source files that would NOT be committed --------------------------
        # The mirror image of every other check. An over-broad ignore rule that
        # silently drops a source file produces a repository that builds locally
        # and fails on every fresh clone, which is harder to diagnose than an
        # obviously missing file.
        if is_ignored and suffix in SOURCE_EXTENSIONS:
            lowered = rel.lower()
            if not lowered.startswith(LEGITIMATELY_IGNORED_ROOTS) and ".venv/" not in lowered:
                if not ENV_FILE_PATTERN.match(path.name):
                    matched_rule = ignore_rule_for(rel)
                    report.ignored_source.append(
                        Finding(
                            "ignored_source",
                            rel,
                            f"source file excluded by .gitignore ({matched_rule})",
                            True,
                            "BLOCK",
                        )
                    )

        # --- secrets -----------------------------------------------------------
        if suffix in TEXT_EXTENSIONS or ENV_FILE_PATTERN.match(path.name):
            for rule, line_number, detail in scan_text_for_secrets(path, rel):
                severity = "BLOCK" if not is_ignored else "INFO"
                report.secrets.append(Finding("secret", rel, f"{rule}: {detail}", is_ignored, severity, line_number))

    return report


# ---------------------------------------------------------------------------
# Reporting
# ---------------------------------------------------------------------------
def _count(findings: list[Finding]) -> str:
    if not findings:
        return "0"
    committed = [f for f in findings if not f.ignored_by_git]
    if not committed:
        return f"{len(findings)} IGNORED"
    if len(committed) == len(findings):
        return f"{len(committed)}"
    return f"{len(committed)} ({len(findings) - len(committed)} IGNORED)"


def print_report(report: AuditReport) -> None:
    print("=" * 49)
    print("REPOSITORY SECURITY AUDIT")
    print("=" * 49)
    print()
    print(f"{'Files scanned:':<31}{report.scanned_files}")
    print(f"{'Git ignore rules applied:':<31}{'YES' if report.git_available else 'NO (git unavailable)'}")
    print()
    print(f"{'Secrets Detected:':<31}{_count(report.secrets)}")
    print(f"{'Environment Files:':<31}{_count(report.env_files) if any(f.severity == 'BLOCK' for f in report.env_files) else 'REVIEW' if report.env_files else '0'}")
    print(f"{'AV Raw Data Files:':<31}{_count(report.av_data)}")
    print(f"{'Runtime Databases:':<31}{_count(report.databases)}")
    print(f"{'Generated Results:':<31}{_count(report.generated)}")
    print(f"{'Large Files:':<31}{_count(report.large_files)}")
    print(f"{'Suspicious Credentials:':<31}{_count(report.credential_files)}")
    print(f"{'Source Files Wrongly Ignored:':<31}{len(report.ignored_source)}")
    print()

    blockers = report.blockers
    unwanted = [f for f in blockers if f.category != "ignored_source"]
    missing = [f for f in blockers if f.category == "ignored_source"]

    if unwanted:
        print("-" * 49)
        print("WOULD BE COMMITTED - MUST BE RESOLVED")
        print("-" * 49)
        for finding in unwanted:
            location = f":{finding.line}" if finding.line else ""
            print(f"  [{finding.category}] {finding.path}{location}")
            print(f"      {finding.detail}")
        print()

    if missing:
        print("-" * 49)
        print("WOULD NOT BE COMMITTED BUT MUST BE")
        print("-" * 49)
        print("  An ignore rule is excluding source code. The repository would")
        print("  build locally and fail on every fresh clone.")
        print()
        for finding in missing:
            print(f"  {finding.path}")
            print(f"      {finding.detail}")
        print()

    reviews = report.reviews
    if reviews:
        print("-" * 49)
        print("REVIEW BEFORE COMMITTING")
        print("-" * 49)
        for finding in reviews:
            print(f"  [{finding.category}] {finding.path}")
            print(f"      {finding.detail}")
        print()

    print("=" * 49)
    print("Repository Safe to Commit:")
    print("YES" if report.safe else "NO")
    print("=" * 49)
    if not report.safe:
        print()
        print("Add the offending paths to .gitignore, or remove them from the working tree.")
        print("No secret values were printed by this tool.")


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description="Audit the repository before committing.")
    parser.add_argument("--json", action="store_true", help="emit machine-readable JSON")
    parser.add_argument("--staged", action="store_true", help="audit only staged files")
    args = parser.parse_args(argv)

    try:
        report = audit(only_staged=args.staged)
    except Exception as exc:  # pragma: no cover - the audit must never be the failure
        print(f"Repository audit failed to run: {exc}", file=sys.stderr)
        return 2

    if args.json:
        print(
            json.dumps(
                {
                    "safe": report.safe,
                    "scanned_files": report.scanned_files,
                    "git_available": report.git_available,
                    "blockers": [f.to_dict() for f in report.blockers],
                    "reviews": [f.to_dict() for f in report.reviews],
                    "counts": {
                        "secrets": len(report.secrets),
                        "env_files": len(report.env_files),
                        "av_data": len(report.av_data),
                        "databases": len(report.databases),
                        "generated": len(report.generated),
                        "large_files": len(report.large_files),
                        "credential_files": len(report.credential_files),
                    },
                },
                indent=2,
            )
        )
    else:
        print_report(report)

    return 0 if report.safe else 1


if __name__ == "__main__":
    raise SystemExit(main())
