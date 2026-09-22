#!/usr/bin/env python3
"""
Module: voyagertools/paramsstore.py
Purpose: Read and write the versioned Voyager analysis parameters kept in the
         voyagerparams checkout (vicarius/modules/voyagerparams/github_repo):
         list versions, branches and custom run sets, load one by ref, diff
         two sets, save a new default under the versioning rule of the program
         spec section 7.7 (MINOR when values change, MAJOR when a key is
         added, removed or renamed), save a branch or a custom set, resolve the
         GitHub URL of a file, and commit and push through an injectable git
         runner. Every write is atomic and serialised on <phase>/.store.lock.
Inputs:  the checkout root (default from VICARIUS_ROOT), a phase name
         (voyager1 or voyager2), refs, nested value mappings, the editor
         schema params_schema_<phase>.yaml beside this file.
Outputs: <phase>/versions/v<X.Y.Z>.yaml, <phase>/branches/<slug>/v<X.Y.Z>.yaml,
         <phase>/custom/<run_id>.yaml, the <phase>/DEFAULT pointer, git
         commits and pushes.

Spec: /mnt/rip/vicarius_drive/docs/superpowers/specs/2026-09-03-voyager-program-design.md
(sections 5.3 and 7.7). Plan task A7 of
/mnt/rip/vicarius_drive/docs/superpowers/plans/2026-09-03-voyager-program-build.md.
"""

from __future__ import annotations

import fcntl
import os
import re
import subprocess
import tempfile
import time
from dataclasses import dataclass, field
from datetime import datetime, timedelta, timezone
from pathlib import Path
from typing import Any, Callable, Iterable, Optional

import yaml

# --- constants -------------------------------------------------------------

PHASES = ("voyager1", "voyager2")
DEFAULT_VICARIUS_ROOT = "/mnt/rip/vicarius_drive/vicarius"
REPO_URL = "https://github.com/laurenkolinger/voyagerparams"
REPO_BRANCH = "main"
DEFAULT_FILE = "DEFAULT"
VERSIONS_DIR = "versions"
BRANCHES_DIR = "branches"
CUSTOM_DIR = "custom"
LOCK_NAME = ".store.lock"
YAML_SUFFIX = ".yaml"

VERSION_RE = re.compile(r"^v(\d+)\.(\d+)\.(\d+)$")
SLUG_RE = re.compile(r"^[a-z0-9][a-z0-9_-]{0,39}$")
RUN_ID_RE = re.compile(r"^[A-Za-z0-9][A-Za-z0-9_.+-]{0,119}$")
ACTOR_RE = re.compile(r"^[A-Za-z0-9_.-]{1,40}$")
KEY_RE = re.compile(r"^[A-Za-z0-9_]+$")
REF_MAX_CHARS = 200
HEADER_LINE_RE = re.compile(r"^# ([a-z_]+): ?(.*)$")
HEADER_KEYS = ("params_version", "phase", "kind", "status", "branch", "base_ref", "run_id",
               "description", "why", "saved_at", "saved_by")
SCALAR_LINE_RE = re.compile(r"^(?P<indent>\s*)(?P<key>[A-Za-z0-9_]+):(?P<rest>.*)$")
VALUE_COMMENT_RE = re.compile(r'^(?P<lead>\s*)(?P<value>"[^"]*"|\'[^\']*\'|[^#]*?)(?P<comment>\s+#.*)?$')

MAX_DEPTH = 8            # nesting levels a values mapping may have
MAX_LEAVES = 2000        # leaves a values mapping may have
MAX_VALUE_CHARS = 100_000  # characters one string value may have
MAX_LIST_ITEMS = 1000    # elements one list-typed leaf may hold, checked before it is dumped
MAX_DESCRIPTION_CHARS = 2000
MAX_FILE_BYTES = 16 * 1024 * 1024  # a parameter file larger than this is refused
MAX_YAML_NODES = 200_000  # nodes a parsed file may expand to (alias bombs stop here)
LOCK_WAIT_S = 5.0
LOCK_POLL_S = 0.05
GIT_TIMEOUT_S = 120

SCHEMA_TYPES = ("integer", "number", "boolean", "string", "list")
SCHEMA_ENTRY_FIELDS = ("path", "type", "default", "label", "tooltip", "section", "min", "max",
                       "min_exclusive", "max_exclusive", "choices", "unit", "hidden", "form_field")

AST = timezone(timedelta(hours=-4))


# --- small types -----------------------------------------------------------


class GitError(RuntimeError):
    """A git add or commit failed; the message carries git's stderr."""


class StoreLocked(RuntimeError):
    """Another writer holds <root>/.store.lock past the wait budget."""


@dataclass(frozen=True)
class GitResult:
    """What a git runner returns for one invocation."""
    returncode: int
    stdout: str
    stderr: str


GitRunner = Callable[[list[str], Path], GitResult]


@dataclass
class ParamsEntry:
    """One row of list_versions: a file in the checkout, header parsed."""
    phase: str
    ref: str
    kind: str
    version: Optional[str]
    rel_path: str
    branch: Optional[str] = None
    run_id: Optional[str] = None
    description: str = ""
    saved_at: str = ""
    saved_by: str = ""
    status: str = ""
    is_default: bool = False
    url: str = ""


@dataclass
class ParamsFile:
    """A loaded parameter file: header fields, values and the raw text."""
    phase: str
    ref: str
    kind: str
    version: Optional[str]
    path: Path
    rel_path: str
    values: dict = field(default_factory=dict)
    text: str = ""
    branch: Optional[str] = None
    run_id: Optional[str] = None
    base_ref: str = ""
    description: str = ""
    why: str = ""
    saved_at: str = ""
    saved_by: str = ""
    status: str = ""


# --- pure helpers ----------------------------------------------------------


def now_iso() -> str:
    """Current time in Atlantic Standard Time as ISO with the -04:00 offset."""
    return datetime.now(AST).isoformat(timespec="seconds")


def default_checkout() -> Path:
    """The voyagerparams checkout under VICARIUS_ROOT (env or the platform default)."""
    root = os.environ.get("VICARIUS_ROOT", DEFAULT_VICARIUS_ROOT)
    return Path(root) / "modules" / "voyagerparams" / "github_repo"


def flatten(mapping: dict, prefix: str = "") -> dict[str, Any]:
    """Flatten nested mappings to dotted paths; lists, scalars and empty
    mappings are leaves.

    flatten({"a": {"b": 1}, "c": [1]}) -> {"a.b": 1, "c": [1]}
    """
    flat: dict[str, Any] = {}
    for key, value in mapping.items():
        path = f"{prefix}.{key}" if prefix else str(key)
        if isinstance(value, dict) and value:
            flat.update(flatten(value, path))
        else:
            flat[path] = value
    return flat


def unflatten(flat: dict[str, Any]) -> dict:
    """Rebuild a nested mapping from dotted paths (the inverse of flatten)."""
    nested: dict = {}
    for path, value in flat.items():
        node = nested
        parts = path.split(".")
        for part in parts[:-1]:
            node = node.setdefault(part, {})
        node[parts[-1]] = value
    return nested


def _same_value(old: Any, new: Any) -> bool:
    """Equality that keeps bool apart from int and int apart from str."""
    if isinstance(old, bool) or isinstance(new, bool):
        return isinstance(old, bool) and isinstance(new, bool) and old == new
    if isinstance(old, str) or isinstance(new, str):
        return isinstance(old, str) and isinstance(new, str) and old == new
    return old == new


def classify_change(old_flat: dict[str, Any], new_flat: dict[str, Any]) -> Optional[str]:
    """Apply the bump rule of spec 7.7 to two flattened sets.

    Returns "major" when a key was added, removed or renamed, "minor" when
    only values changed, None when nothing changed.
    """
    if set(old_flat) != set(new_flat):
        return "major"
    if any(not _same_value(old_flat[path], new_flat[path]) for path in old_flat):
        return "minor"
    return None


def version_key(version: str) -> tuple[int, int, int]:
    """Sort key for v<MAJOR>.<MINOR>.<PATCH> strings; raises ValueError on any other form."""
    match = VERSION_RE.fullmatch(version or "")
    if not match:
        raise ValueError(f"not a version of the form vX.Y.Z: {version!r}")
    return tuple(int(part) for part in match.groups())  # type: ignore[return-value]


def next_version(version: str, level: str) -> str:
    """Bump a vX.Y.Z version: "minor" -> vX.(Y+1).0, "major" -> v(X+1).0.0."""
    major, minor, _patch = version_key(version)
    if level == "minor":
        return f"v{major}.{minor + 1}.0"
    if level == "major":
        return f"v{major + 1}.0.0"
    raise ValueError(f"bump level must be 'minor' or 'major', not {level!r}")


def parse_header(text: str) -> dict[str, str]:
    """Read the leading '# key: value' comment lines of a parameter file.

    Parsing stops at the first line that is not a header line, so the file's
    own preamble comments and the YAML body are never mistaken for fields.
    """
    header: dict[str, str] = {}
    for line in (text or "").splitlines():
        match = HEADER_LINE_RE.match(line)
        if not match or match.group(1) not in HEADER_KEYS:
            break
        header[match.group(1)] = match.group(2).strip()
    return header


def _header_text(fields: dict[str, str]) -> str:
    """Render header fields in HEADER_KEYS order, one comment line each;
    newlines and tabs inside a value collapse to single spaces."""
    lines = []
    for key in HEADER_KEYS:
        if key in fields and fields[key] is not None:
            value = " ".join(str(fields[key]).split())
            lines.append(f"# {key}: {value}")
    return "\n".join(lines) + "\n"


def _strip_header(text: str) -> str:
    """Return the file text without its leading header lines."""
    lines = text.splitlines(keepends=True)
    count = 0
    for line in lines:
        match = HEADER_LINE_RE.match(line.rstrip("\n"))
        if not match or match.group(1) not in HEADER_KEYS:
            break
        count += 1
    return "".join(lines[count:])


def parse_ref(ref: Any) -> tuple[str, Optional[str], Optional[str]]:
    """Split a ref into (kind, name, version).

    Forms: "default"; "v1.2.0"; "branches/<slug>/v1.2.0"; "branches/<slug>"
    (the newest file of that branch); "custom/<run_id>". Anything else, and
    anything that is not a str, raises ValueError (TypeError for non-str).
    """
    if not isinstance(ref, str):
        raise TypeError(f"ref must be a str, not {type(ref).__name__}")
    if not ref or len(ref) > REF_MAX_CHARS or ref != ref.strip() or "\n" in ref:
        raise ValueError(f"malformed ref {ref!r}")
    if ref == "default":
        return ("default", None, None)
    if VERSION_RE.fullmatch(ref):
        return ("version", None, ref)
    parts = ref.split("/")
    if parts[0] == BRANCHES_DIR and len(parts) in (2, 3) and SLUG_RE.fullmatch(parts[1]):
        if len(parts) == 2:
            return ("branch", parts[1], None)
        if VERSION_RE.fullmatch(parts[2]):
            return ("branch", parts[1], parts[2])
    if parts[0] == CUSTOM_DIR and len(parts) == 2 and RUN_ID_RE.fullmatch(parts[1]):
        return ("custom", parts[1], None)
    raise ValueError(f"malformed ref {ref!r}: expected default, vX.Y.Z, branches/<slug>[/vX.Y.Z] or custom/<run_id>")


# --- strict YAML loading ---------------------------------------------------


class _StrictLoader(yaml.SafeLoader):
    """SafeLoader that refuses duplicate mapping keys and alias bombs."""

    def __init__(self, stream):
        """Start with a fresh node budget for this document."""
        super().__init__(stream)
        self._node_budget = MAX_YAML_NODES

    def construct_mapping(self, node, deep=False):
        """Build a mapping, raising on the second occurrence of any key."""
        seen = set()
        for key_node, _value_node in node.value:
            key = self.construct_object(key_node, deep=True)
            if key in seen:
                raise yaml.constructor.ConstructorError(
                    None, None, f"duplicate key {key!r}", key_node.start_mark)
            seen.add(key)
        return super().construct_mapping(node, deep=deep)

    def construct_object(self, node, deep=False):
        """Build one node, charging it to the budget so a huge file stops early."""
        self._node_budget -= 1
        if self._node_budget < 0:
            raise yaml.constructor.ConstructorError(
                None, None, f"file expands to more than {MAX_YAML_NODES} nodes", node.start_mark)
        return super().construct_object(node, deep=deep)


def load_yaml_mapping(text: str, where: str) -> dict:
    """Parse text as one YAML mapping, naming `where` in every error."""
    if not text.strip():
        raise ValueError(f"{where}: file is empty")
    try:
        data = yaml.load(text, Loader=_StrictLoader)
    except yaml.YAMLError as exc:
        raise ValueError(f"{where}: not valid YAML: {exc}") from exc
    except RecursionError as exc:
        raise ValueError(f"{where}: YAML nests too deeply") from exc
    if not isinstance(data, dict):
        raise ValueError(f"{where}: top level must be a mapping, not {type(data).__name__}")
    return data


# --- schema and validation -------------------------------------------------


def load_schema(phase: str) -> Optional[dict]:
    """Load params_schema_<phase>.yaml beside this file; None when the phase
    has no schema yet. The phase name is validated against PHASES."""
    _check_phase(phase)
    path = Path(__file__).resolve().parent / f"params_schema_{phase}.yaml"
    if not path.exists():
        return None
    schema = load_yaml_mapping(path.read_text(encoding="utf-8"), str(path))
    for required in ("phase", "sections", "keys"):
        if required not in schema:
            raise ValueError(f"{path}: schema lacks the {required!r} field")
    return schema


def _type_ok(kind: str, value: Any) -> bool:
    """Whether a value has the schema type (bool is never an integer or number)."""
    if kind == "integer":
        return isinstance(value, int) and not isinstance(value, bool)
    if kind == "number":
        return isinstance(value, (int, float)) and not isinstance(value, bool)
    if kind == "boolean":
        return isinstance(value, bool)
    if kind == "string":
        return isinstance(value, str)
    if kind == "list":
        return isinstance(value, list)
    return False


def check_value(entry: dict, value: Any) -> list[str]:
    """Problems with one value against one schema entry (type, choices, range)."""
    path = entry.get("path", "?")
    kind = entry.get("type", "")
    if not _type_ok(kind, value):
        return [f"{path}: expected {kind}, got {type(value).__name__} {value!r}"]
    problems = []
    if "choices" in entry and value not in entry["choices"]:
        problems.append(f"{path}: {value!r} is not one of {entry['choices']}")
    if kind in ("integer", "number"):
        if "min" in entry:
            too_low = value <= entry["min"] if entry.get("min_exclusive") else value < entry["min"]
            if too_low:
                bound = "above" if entry.get("min_exclusive") else "at least"
                problems.append(f"{path}: {value!r} must be {bound} {entry['min']}")
        if "max" in entry:
            too_high = value >= entry["max"] if entry.get("max_exclusive") else value > entry["max"]
            if too_high:
                bound = "below" if entry.get("max_exclusive") else "at most"
                problems.append(f"{path}: {value!r} must be {bound} {entry['max']}")
    return problems


def _check_shape(node: Any, path: str, depth: int, counter: list[int]) -> list[str]:
    """Structural checks on a values mapping: key names, depth, leaf count,
    value types and sizes. Returns problem strings."""
    problems: list[str] = []
    if depth > MAX_DEPTH:
        return [f"{path or '<root>'}: nested deeper than {MAX_DEPTH} levels"]
    for key, value in node.items():
        if not isinstance(key, str) or not KEY_RE.fullmatch(key):
            problems.append(f"{path or '<root>'}: key {key!r} must match letters, digits and underscores")
            continue
        child = f"{path}.{key}" if path else key
        if isinstance(value, dict) and value:
            problems.extend(_check_shape(value, child, depth + 1, counter))
            continue
        counter[0] += 1
        if counter[0] > MAX_LEAVES:
            return problems + [f"more than {MAX_LEAVES} values"]
        problems.extend(_check_leaf(child, value))
    return problems


def _check_leaf(path: str, value: Any) -> list[str]:
    """Problems with one leaf value: allowed types, string length, and, for a
    list, its item count and its dumped size (checked in that order so a
    huge list is rejected by its length before anything tries to render it,
    since a list is one leaf regardless of size and MAX_LEAVES never counts
    its elements)."""
    if value is None or isinstance(value, (bool, int, float)):
        return []
    if isinstance(value, str):
        if len(value) > MAX_VALUE_CHARS:
            return [f"{path}: string longer than {MAX_VALUE_CHARS} characters"]
        return []
    if isinstance(value, dict):
        return []  # an empty mapping is a leaf
    if isinstance(value, list):
        if len(value) > MAX_LIST_ITEMS:
            return [f"{path}: list holds more than {MAX_LIST_ITEMS} items"]
        try:
            dumped = yaml.safe_dump(value)
        except yaml.YAMLError:
            return [f"{path}: list holds a value YAML cannot write"]
        if len(dumped) > MAX_VALUE_CHARS:
            return [f"{path}: list serialises to more than {MAX_VALUE_CHARS} characters"]
        return []
    return [f"{path}: unsupported value type {type(value).__name__}"]


def validate_values(phase: str, values: Any) -> list[str]:
    """Every problem with a values mapping for a phase: shape first, then the
    schema's type, choice and range rules for the keys the schema knows.
    Keys the schema does not know are allowed (they become a MAJOR bump)."""
    _check_phase(phase)
    if not isinstance(values, dict):
        raise TypeError(f"values must be a mapping, not {type(values).__name__}")
    problems = _check_shape(values, "", 1, [0])
    if problems:
        return problems
    schema = load_schema(phase)
    if schema is None:
        return []
    flat = flatten(values)
    for entry in schema["keys"]:
        if entry["path"] in flat:
            problems.extend(check_value(entry, flat[entry["path"]]))
    return problems


# --- text rendering --------------------------------------------------------


def _scalar_text(value: Any, old_text: str) -> str:
    """YAML text for one scalar, keeping double quotes when the old value had them."""
    if isinstance(value, bool):
        return "true" if value else "false"
    if isinstance(value, str) and old_text.strip().startswith('"') and '"' not in value and "\\" not in value \
            and "\n" not in value:
        return f'"{value}"'
    dumped = yaml.safe_dump(value, default_flow_style=True, allow_unicode=True, width=10_000)
    return dumped.splitlines()[0].removesuffix(" ...").strip()


def _patch_scalars(body: str, changes: dict[str, Any]) -> Optional[str]:
    """Rewrite the value of each changed scalar key in place, keeping the
    line's indentation and trailing comment. Returns None when any changed
    key is not a plain 'key: value' line (a list, a mapping, or absent)."""
    lines = body.splitlines(keepends=True)
    stack: list[tuple[int, str]] = []
    remaining = dict(changes)
    for index, raw in enumerate(lines):
        match = SCALAR_LINE_RE.match(raw.rstrip("\n"))
        if not match or raw.lstrip().startswith("#"):
            continue
        indent = len(match.group("indent"))
        while stack and stack[-1][0] >= indent:
            stack.pop()
        path = ".".join(key for _, key in stack) + ("." if stack else "") + match.group("key")
        stack.append((indent, match.group("key")))
        if path not in remaining:
            continue
        parsed = VALUE_COMMENT_RE.match(match.group("rest"))
        if not parsed or not parsed.group("value").strip():
            return None
        new_value = remaining.pop(path)
        if isinstance(new_value, (dict, list)) or new_value is None:
            return None
        text = _scalar_text(new_value, parsed.group("value"))
        lines[index] = f"{match.group('indent')}{match.group('key')}:{parsed.group('lead') or ' '}{text}" \
            f"{parsed.group('comment') or ''}\n"
    return "".join(lines) if not remaining else None


def render_file(base_text: str, base_values: dict, values: dict, header: dict[str, str]) -> str:
    """Header plus body. When only scalar values changed, the base body is
    patched line by line so every comment survives; otherwise the body is a
    clean YAML dump of the values."""
    base_flat, new_flat = flatten(base_values), flatten(values)
    body = None
    if set(base_flat) == set(new_flat):
        changes = {path: new_flat[path] for path in new_flat if not _same_value(base_flat[path], new_flat[path])}
        body = _patch_scalars(_strip_header(base_text), changes)
    if body is None:
        body = yaml.safe_dump(values, sort_keys=False, default_flow_style=False, allow_unicode=True, width=10_000)
    return _header_text(header) + body


# --- git -------------------------------------------------------------------


def run_git_subprocess(args: list[str], cwd: Path) -> GitResult:
    """The default git runner: `git <args>` in cwd with a timeout, never raising
    on a non-zero exit (the caller reads returncode)."""
    completed = subprocess.run(["git", *args], cwd=str(cwd), capture_output=True, text=True,
                               timeout=GIT_TIMEOUT_S, check=False)
    return GitResult(completed.returncode, completed.stdout, completed.stderr)


# --- the store -------------------------------------------------------------


def _check_phase(phase: Any) -> str:
    """Return the phase name when it is one of PHASES; ValueError otherwise."""
    if not isinstance(phase, str) or phase not in PHASES:
        raise ValueError(f"phase must be one of {PHASES}, not {phase!r}")
    return phase


def _check_actor(actor: Any) -> str:
    """Return the actor initials when well formed; TypeError or ValueError otherwise."""
    if not isinstance(actor, str):
        raise TypeError(f"actor must be initials as a str, not {type(actor).__name__}")
    if not ACTOR_RE.fullmatch(actor):
        raise ValueError(f"actor must be initials (letters, digits, . _ -; 1 to 40 chars), not {actor!r}")
    return actor


def _check_text(name: str, value: Any) -> str:
    """Return a header text field (description, why) when it is a bounded str."""
    if not isinstance(value, str):
        raise TypeError(f"{name} must be a str, not {type(value).__name__}")
    if len(value) > MAX_DESCRIPTION_CHARS:
        raise ValueError(f"{name} longer than {MAX_DESCRIPTION_CHARS} characters")
    return value


class ParamsStore:
    """Reader and writer of one voyagerparams checkout.

    root: the checkout (default: default_checkout()). run_git: a callable
    (args, cwd) -> GitResult; the default shells out to git. now: a callable
    returning the ISO AST timestamp for headers. lock_wait_s: how long a
    write waits for <root>/.store.lock before raising StoreLocked.
    """

    def __init__(self, root: Optional[Path | str] = None, run_git: Optional[GitRunner] = None,
                 repo_url: str = REPO_URL, now: Optional[Callable[[], str]] = None,
                 lock_wait_s: float = LOCK_WAIT_S):
        """Bind the store to a checkout that must already exist on disk."""
        self.root = Path(root) if root is not None else default_checkout()
        if not self.root.is_dir():
            raise FileNotFoundError(f"voyagerparams checkout not found at {self.root}")
        self.run_git: GitRunner = run_git or run_git_subprocess
        self.repo_url = repo_url.rstrip("/")
        self.now = now or now_iso
        self.lock_wait_s = float(lock_wait_s)
        self._pending: list[str] = []

    # -- paths and refs --

    @property
    def pending_paths(self) -> list[str]:
        """Checkout-relative paths written since the last successful commit."""
        return list(self._pending)

    def phase_dir(self, phase: str) -> Path:
        """<root>/<phase>, which must exist."""
        folder = self.root / _check_phase(phase)
        if not folder.is_dir():
            raise FileNotFoundError(f"phase folder {folder} does not exist in the checkout")
        return folder

    def default_version(self, phase: str) -> str:
        """The version named by <phase>/DEFAULT, for example "v1.0.0"."""
        pointer = self.phase_dir(phase) / DEFAULT_FILE
        try:
            text = pointer.read_text(encoding="utf-8")
        except FileNotFoundError as exc:
            raise FileNotFoundError(f"default pointer missing: {pointer}") from exc
        except OSError as exc:
            raise OSError(f"cannot read default pointer {pointer}: {exc}") from exc
        version = text.strip()
        if not VERSION_RE.fullmatch(version):
            raise ValueError(f"default pointer {pointer} must hold one version of the form vX.Y.Z, not {text!r}")
        return version

    def resolve(self, phase: str, ref: str) -> tuple[str, str, Optional[str], Optional[str], Optional[str]]:
        """Resolve a ref to (concrete ref, rel_path, kind, name, version)
        without requiring the file to exist. A bare branch ref resolves to
        the newest version in that branch and raises FileNotFoundError when
        the branch has none."""
        folder = self.phase_dir(phase)
        kind, name, version = parse_ref(ref)
        if kind == "default":
            version = self.default_version(phase)
            return (version, f"{VERSIONS_DIR}/{version}{YAML_SUFFIX}", "version", None, version)
        if kind == "version":
            return (ref, f"{VERSIONS_DIR}/{version}{YAML_SUFFIX}", kind, None, version)
        if kind == "custom":
            return (ref, f"{CUSTOM_DIR}/{name}{YAML_SUFFIX}", kind, name, None)
        if version is None:
            versions = self._branch_versions(folder, name)
            if not versions:
                raise FileNotFoundError(f"branch {name!r} has no files under {folder / BRANCHES_DIR / name}")
            version = versions[-1]
        return (f"{BRANCHES_DIR}/{name}/{version}", f"{BRANCHES_DIR}/{name}/{version}{YAML_SUFFIX}", "branch", name, version)

    def version_url(self, phase: str, ref: str) -> str:
        """GitHub URL of the file a ref names; the file must exist."""
        _ref, rel_path, _kind, _name, _version = self.resolve(phase, ref)
        path = self.phase_dir(phase) / rel_path
        if not path.is_file():
            raise FileNotFoundError(f"no parameter file for {phase} {ref!r} at {path}")
        return f"{self.repo_url}/blob/{REPO_BRANCH}/{phase}/{rel_path}"

    # -- listing and loading --

    def _versions_in(self, folder: Path) -> list[str]:
        """Version names of the well-named yaml files in a folder, ascending."""
        if not folder.is_dir():
            return []
        found = []
        for path in folder.iterdir():
            if path.is_file() and path.suffix == YAML_SUFFIX and VERSION_RE.fullmatch(path.stem):
                found.append(path.stem)
        return sorted(found, key=version_key)

    def _branch_versions(self, phase_folder: Path, slug: str) -> list[str]:
        """Ascending version names present in one branch folder."""
        return self._versions_in(phase_folder / BRANCHES_DIR / slug)

    def _entry(self, phase: str, ref: str, rel_path: str, kind: str, default: str) -> ParamsEntry:
        """Build a listing row; an unreadable header leaves the fields blank."""
        header: dict[str, str] = {}
        try:
            header = parse_header((self.phase_dir(phase) / rel_path).read_text(encoding="utf-8", errors="replace"))
        except OSError:
            header = {}
        _kind, name, version = parse_ref(ref)
        return ParamsEntry(
            phase=phase, ref=ref, kind=kind, version=version, rel_path=rel_path,
            branch=name if kind == "branch" else None, run_id=name if kind == "custom" else None,
            description=header.get("description", ""), saved_at=header.get("saved_at", ""),
            saved_by=header.get("saved_by", ""), status=header.get("status", ""),
            is_default=(kind == "version" and version == default),
            url=f"{self.repo_url}/blob/{REPO_BRANCH}/{phase}/{rel_path}")

    def list_versions(self, phase: str) -> list[ParamsEntry]:
        """Every file of a phase: the default first, then the other numbered
        versions newest first, then branches (slug order, newest first within
        a branch), then custom sets in run id order. Badly named files and
        folders are ignored; missing branches/ or custom/ folders are fine."""
        folder = self.phase_dir(phase)
        default = self.default_version(phase)
        entries: list[ParamsEntry] = []
        versions = self._versions_in(folder / VERSIONS_DIR)
        ordered = ([default] if default in versions else []) + [v for v in reversed(versions) if v != default]
        for version in ordered:
            entries.append(self._entry(phase, version, f"{VERSIONS_DIR}/{version}{YAML_SUFFIX}", "version", default))
        branches_dir = folder / BRANCHES_DIR
        slugs = sorted(p.name for p in branches_dir.iterdir() if p.is_dir() and SLUG_RE.fullmatch(p.name)) \
            if branches_dir.is_dir() else []
        for slug in slugs:
            for version in reversed(self._branch_versions(folder, slug)):
                entries.append(self._entry(phase, f"{BRANCHES_DIR}/{slug}/{version}",
                                           f"{BRANCHES_DIR}/{slug}/{version}{YAML_SUFFIX}", "branch", default))
        custom_dir = folder / CUSTOM_DIR
        run_ids = sorted(p.stem for p in custom_dir.iterdir()
                         if p.is_file() and p.suffix == YAML_SUFFIX and RUN_ID_RE.fullmatch(p.stem)) \
            if custom_dir.is_dir() else []
        for run_id in run_ids:
            entries.append(self._entry(phase, f"{CUSTOM_DIR}/{run_id}", f"{CUSTOM_DIR}/{run_id}{YAML_SUFFIX}", "custom", default))
        return entries

    def load(self, phase: str, ref: str) -> ParamsFile:
        """Load one parameter file by ref: header fields, values and text."""
        concrete, rel_path, kind, name, version = self.resolve(phase, ref)
        path = self.phase_dir(phase) / rel_path
        if not path.is_file():
            raise FileNotFoundError(f"no parameter file for {phase} {ref!r} at {path}")
        if path.stat().st_size > MAX_FILE_BYTES:
            raise ValueError(f"{path}: larger than {MAX_FILE_BYTES} bytes; refusing to parse")
        try:
            text = path.read_text(encoding="utf-8")
        except (OSError, UnicodeDecodeError) as exc:
            raise ValueError(f"cannot read {path}: {exc}") from exc
        values = load_yaml_mapping(text, str(path))
        header = parse_header(text)
        return ParamsFile(
            phase=phase, ref=concrete, kind=kind, version=version, path=path, rel_path=rel_path,
            values=values, text=text, branch=name if kind == "branch" else None,
            run_id=name if kind == "custom" else None, base_ref=header.get("base_ref", ""),
            description=header.get("description", ""), why=header.get("why", ""),
            saved_at=header.get("saved_at", ""), saved_by=header.get("saved_by", ""),
            status=header.get("status", ""))

    def diff(self, a: Any, b: Any, phase: str = "voyager1") -> list[dict]:
        """Rows {path, kind, a, b} for every leaf that differs between two
        sets, each a ref (resolved in `phase`) or a nested mapping, sorted by
        path. kind is changed, added (only in b) or removed (only in a)."""
        flat_a, flat_b = self._flat_of(phase, a), self._flat_of(phase, b)
        rows = []
        for path in sorted(set(flat_a) | set(flat_b)):
            if path not in flat_b:
                rows.append({"path": path, "kind": "removed", "a": flat_a[path], "b": None})
            elif path not in flat_a:
                rows.append({"path": path, "kind": "added", "a": None, "b": flat_b[path]})
            elif not _same_value(flat_a[path], flat_b[path]):
                rows.append({"path": path, "kind": "changed", "a": flat_a[path], "b": flat_b[path]})
        return rows

    def _flat_of(self, phase: str, source: Any) -> dict[str, Any]:
        """Flatten a ref (loaded in phase) or a mapping for diffing."""
        if isinstance(source, str):
            return flatten(self.load(phase, source).values)
        if isinstance(source, dict):
            return flatten(source)
        raise TypeError(f"diff takes a ref or a mapping, not {type(source).__name__}")

    # -- writing --

    def _validated(self, phase: str, values: Any) -> dict:
        """Return values when validate_values finds nothing; ValueError listing problems otherwise."""
        problems = validate_values(phase, values)
        if problems:
            shown = "; ".join(problems[:10])
            more = f" (and {len(problems) - 10} more)" if len(problems) > 10 else ""
            raise ValueError(f"values rejected for {phase}: {shown}{more}")
        return values

    def _write_atomic(self, path: Path, text: str) -> None:
        """Write text to path through a temporary file in the same folder."""
        path.parent.mkdir(parents=True, exist_ok=True)
        handle = None
        try:
            handle, tmp_name = tempfile.mkstemp(prefix=f".{path.name}.", suffix=".tmp", dir=str(path.parent))
            with os.fdopen(handle, "w", encoding="utf-8") as fh:
                handle = None
                fh.write(text)
                fh.flush()
                os.fsync(fh.fileno())
            os.replace(tmp_name, path)
        except OSError as exc:
            raise OSError(f"cannot write {path}: {exc}") from exc

    def _locked(self, phase: str):
        """Context manager holding an exclusive flock on <root>/.store.lock
        (one lock for the whole checkout, so DEFAULT and the files it names
        never change under a concurrent writer)."""
        store = self

        class _Lock:
            def __enter__(self_inner):
                """Acquire the flock, polling until lock_wait_s runs out."""
                store.phase_dir(phase)
                self_inner.path = store.root / LOCK_NAME
                self_inner.fh = open(self_inner.path, "a+")
                deadline = time.monotonic() + store.lock_wait_s
                while True:
                    try:
                        fcntl.flock(self_inner.fh.fileno(), fcntl.LOCK_EX | fcntl.LOCK_NB)
                        return self_inner
                    except BlockingIOError:
                        if time.monotonic() >= deadline:
                            self_inner.fh.close()
                            raise StoreLocked(
                                f"another writer holds {self_inner.path} for longer than {store.lock_wait_s} s")
                        time.sleep(LOCK_POLL_S)

            def __exit__(self_inner, *_exc):
                """Release the flock and close the lock file."""
                fcntl.flock(self_inner.fh.fileno(), fcntl.LOCK_UN)
                self_inner.fh.close()
                return False

        return _Lock()

    def _write_params(self, phase: str, rel_path: str, base: ParamsFile, values: dict,
                      header: dict[str, str]) -> None:
        """Render and write one parameter file, recording it as pending."""
        text = render_file(base.text, base.values, values, header)
        self._write_atomic(self.phase_dir(phase) / rel_path, text)
        self._record(f"{phase}/{rel_path}")

    def _record(self, rel_path: str) -> None:
        """Remember a written checkout-relative path for the next commit."""
        if rel_path not in self._pending:
            self._pending.append(rel_path)

    def save_default(self, phase: str, values: dict, description: str, actor: str) -> ParamsFile:
        """Write values as the next numbered version and point DEFAULT at it.

        MINOR bump when only values changed against the current default,
        MAJOR when a key was added, removed or renamed; a set identical to
        the default is refused with ValueError.
        """
        _check_actor(actor)
        _check_text("description", description)
        self._validated(phase, values)
        with self._locked(phase):
            base = self.load(phase, "default")
            level = classify_change(flatten(base.values), flatten(values))
            if level is None:
                raise ValueError(f"nothing changed against the {phase} default {base.version}; no new version written")
            version = next_version(base.version or "v0.0.0", level)
            header = {"params_version": version[1:], "phase": phase, "kind": "version", "base_ref": base.ref,
                      "description": description, "saved_at": self.now(), "saved_by": actor}
            rel_path = f"{VERSIONS_DIR}/{version}{YAML_SUFFIX}"
            self._write_params(phase, rel_path, base, values, header)
            self._write_atomic(self.phase_dir(phase) / DEFAULT_FILE, version + "\n")
            self._record(f"{phase}/{DEFAULT_FILE}")
        return self.load(phase, version)

    def save_branch(self, phase: str, base_ref: str, slug: str, description: str, why: str,
                    values: dict, actor: str) -> ParamsFile:
        """Write values as a file of branch <slug>.

        The first file of a branch takes the version of base_ref (normally
        the default); later files bump within the branch by the MINOR and
        MAJOR rule against the branch's newest file, and an unchanged set is
        refused. A branch never moves DEFAULT.
        """
        _check_actor(actor)
        _check_text("description", description)
        _check_text("why", why)
        if not isinstance(slug, str):
            raise TypeError(f"slug must be a str, not {type(slug).__name__}")
        if not SLUG_RE.fullmatch(slug):
            raise ValueError(f"branch slug must be lowercase letters, digits, _ or -, 1 to 40 chars, starting with a letter or digit, not {slug!r}")
        self._validated(phase, values)
        with self._locked(phase):
            base = self.load(phase, base_ref)
            existing = self._branch_versions(self.phase_dir(phase), slug)
            if existing:
                latest = self.load(phase, f"{BRANCHES_DIR}/{slug}/{existing[-1]}")
                level = classify_change(flatten(latest.values), flatten(values))
                if level is None:
                    raise ValueError(f"nothing changed against branch {slug} {latest.version}; no file written")
                version = next_version(latest.version or "v0.0.0", level)
            else:
                version = base.version or "v0.0.0"
            header = {"params_version": version[1:], "phase": phase, "kind": "branch", "branch": slug,
                      "base_ref": base.ref, "description": description, "why": why,
                      "saved_at": self.now(), "saved_by": actor}
            rel_path = f"{BRANCHES_DIR}/{slug}/{version}{YAML_SUFFIX}"
            self._write_params(phase, rel_path, base, values, header)
        return self.load(phase, f"{BRANCHES_DIR}/{slug}/{version}")

    def save_custom(self, phase: str, run_id: str, values: dict, actor: str, base_ref: Optional[str] = None,
                    description: str = "", overwrite: bool = False) -> ParamsFile:
        """Write the exact set one run used as custom/<run_id>.yaml.

        base_ref names the file the set started from (default: the phase
        default) and is recorded in the header. An existing file is refused
        with FileExistsError unless overwrite is True. Never moves DEFAULT.
        """
        _check_actor(actor)
        _check_text("description", description)
        if not isinstance(run_id, str):
            raise TypeError(f"run_id must be a str, not {type(run_id).__name__}")
        if not RUN_ID_RE.fullmatch(run_id):
            raise ValueError(f"run_id must be letters, digits, _ . + or -, 1 to 120 chars, starting with a letter or digit, not {run_id!r}")
        self._validated(phase, values)
        with self._locked(phase):
            base = self.load(phase, base_ref if base_ref is not None else "default")
            rel_path = f"{CUSTOM_DIR}/{run_id}{YAML_SUFFIX}"
            target = self.phase_dir(phase) / rel_path
            if target.exists() and not overwrite:
                raise FileExistsError(f"custom set already exists at {target}; pass overwrite=True to replace it")
            header = {"params_version": (base.version or "v0.0.0")[1:], "phase": phase, "kind": "custom",
                      "run_id": run_id, "base_ref": base.ref, "description": description,
                      "saved_at": self.now(), "saved_by": actor}
            self._write_params(phase, rel_path, base, values, header)
        loaded = self.load(phase, f"{CUSTOM_DIR}/{run_id}")
        loaded.version = base.version
        return loaded

    # -- git --

    def commit_and_push(self, message: str, paths: Optional[Iterable[str]] = None) -> dict:
        """Stage the given checkout-relative paths (default: everything written
        since the last commit), commit with message, and push to origin when
        a remote exists.

        Returns {committed, pushed, commit, paths, detail}. A failed add or
        commit raises GitError and keeps the pending list; a failed push is
        reported in detail with pushed False, never raised, so the local
        commit stands until the network is back.
        """
        if not isinstance(message, str):
            raise TypeError(f"commit message must be a str, not {type(message).__name__}")
        if not message.strip():
            raise ValueError("commit message must not be empty")
        targets = self._commit_paths(paths)
        if not targets:
            return {"committed": False, "pushed": False, "commit": "", "paths": [], "detail": "nothing to commit"}
        self._git_or_raise(["add", "--", *targets])
        self._git_or_raise(["commit", "-m", message, "--", *targets])
        sha = self._git_or_raise(["rev-parse", "--short", "HEAD"]).stdout.strip()
        if paths is None:
            self._pending = []
        remotes = self._git_or_raise(["remote"]).stdout.split()
        if not remotes:
            return {"committed": True, "pushed": False, "commit": sha, "paths": targets,
                    "detail": "committed; no remote configured, push skipped"}
        push = self._git(["push", "origin", "HEAD"])
        if push.returncode != 0:
            return {"committed": True, "pushed": False, "commit": sha, "paths": targets,
                    "detail": f"committed; push failed: {(push.stderr or push.stdout).strip()}"}
        return {"committed": True, "pushed": True, "commit": sha, "paths": targets, "detail": "committed and pushed"}

    def _commit_paths(self, paths: Optional[Iterable[str]]) -> list[str]:
        """The paths to stage: the pending list, or the caller's list validated."""
        if paths is None:
            return list(self._pending)
        if isinstance(paths, (str, bytes)):
            raise TypeError("paths must be an iterable of checkout-relative paths, not a single string")
        cleaned = []
        for item in paths:
            if not isinstance(item, str) or not item or item.startswith("/") or ".." in item.split("/"):
                raise ValueError(f"path must be checkout-relative without '..', not {item!r}")
            cleaned.append(item)
        return cleaned

    def _git(self, args: list[str]) -> GitResult:
        """Run git through the runner; any runner failure becomes GitError."""
        try:
            return self.run_git(args, self.root)
        except Exception as exc:  # noqa: BLE001  (any runner failure is a git failure to the caller)
            raise GitError(f"git {' '.join(args[:2])} could not run in {self.root}: {exc}") from exc

    def _git_or_raise(self, args: list[str]) -> GitResult:
        """Run git and raise GitError with stderr on a non-zero exit."""
        result = self._git(args)
        if result.returncode != 0:
            raise GitError(f"git {' '.join(args[:2])} failed in {self.root}: {(result.stderr or result.stdout).strip()}")
        return result


__all__ = [
    "PHASES", "REPO_URL", "REPO_BRANCH", "LOCK_NAME", "MAX_DEPTH", "MAX_LEAVES", "MAX_VALUE_CHARS",
    "MAX_LIST_ITEMS",
    "SCHEMA_TYPES", "SCHEMA_ENTRY_FIELDS", "GitError", "StoreLocked", "GitResult", "ParamsEntry",
    "ParamsFile", "ParamsStore", "now_iso", "default_checkout", "flatten", "unflatten",
    "classify_change", "version_key", "next_version", "parse_header", "parse_ref",
    "load_yaml_mapping", "load_schema", "check_value", "validate_values", "render_file",
    "run_git_subprocess",
]
