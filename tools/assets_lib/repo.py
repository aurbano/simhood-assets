"""Reading the assets repository's tree: packs, manifests, models and the file rules of D12."""

from __future__ import annotations

import hashlib
import os
import re
import subprocess
from dataclasses import dataclass, field
from pathlib import Path

from . import manifests
from .gltf import ALLOWED_EXTENSIONS  # noqa: F401  (re-exported for callers)

GRANDFATHERED_PACKS = ("kaykit-city", "kaykit-forest")
MAX_GLB_BYTES = 4 * 1024 * 1024
MAX_PR_BYTES = 20 * 1024 * 1024
MAX_FOLDERS = 3
EVIDENCE_EXTENSIONS = (".txt", ".html", ".md", ".pdf", ".png")
_NAME = re.compile(r"^[A-Za-z0-9._-]+$")

# Folders no listing of the tree looks into: version control and tool caches.
SKIP_DIRS = {".git", "__pycache__", ".pytest_cache", ".venv", ".ruff_cache", ".mypy_cache"}


@dataclass
class ModelEntry:
    """One manifest and the model file beside it."""

    pack: str  # pack folder name (`kenney`), or "" for an example
    manifest_path: str  # repo-relative, posix
    model_path: str | None
    manifest: manifests.Manifest | None = None
    manifest_error: str | None = None
    area: str = "pack"  # pack | example


@dataclass
class PackEntry:
    name: str
    path: str  # repo-relative, posix
    info: manifests.PackInfo | None = None
    info_error: str | None = None
    models: list[ModelEntry] = field(default_factory=list)
    # Model files beside no manifest, repo-relative.
    orphans: list[str] = field(default_factory=list)


@dataclass
class Tree:
    root: Path
    packs: list[PackEntry] = field(default_factory=list)
    examples: list[ModelEntry] = field(default_factory=list)


def _git_files(root: Path) -> list[str] | None:
    """The files git considers part of the tree (tracked or untracked, not ignored), when `root` is
    the top of a git work tree; otherwise None."""
    try:
        top = subprocess.run(
            ["git", "-C", str(root), "rev-parse", "--show-toplevel"], capture_output=True, text=True, check=False
        )
        if top.returncode != 0 or Path(top.stdout.strip()).resolve() != root.resolve():
            return None
        listing = subprocess.run(
            ["git", "-C", str(root), "ls-files", "-co", "--exclude-standard", "-z"], capture_output=True, check=False
        )
    except OSError:
        return None
    if listing.returncode != 0:
        return None
    return [name for name in listing.stdout.decode("utf-8", "surrogateescape").split("\0") if name]


def list_files(root: Path) -> dict[str, Path]:
    """Every file of the tree at `root` by posix relative path (symlinks included, not followed).

    In a git work tree this is what `git ls-files -co --exclude-standard` lists, so ignored files
    (`out/`, `node_modules`) are never judged, and `.git` never appears. Elsewhere it walks the folder,
    skipping `.git`, `node_modules`, `out` and tool caches.
    """
    tracked = _git_files(root)
    if tracked is not None:
        out: dict[str, Path] = {}
        for rel in sorted(tracked):
            path = root / rel
            # A tracked file deleted in the work tree is still in the index; it is not in the tree.
            if rel.split("/")[0] != ".git" and os.path.lexists(path) and not path.is_dir():
                out[rel] = path
        return out
    out = {}
    for dirpath, dirnames, filenames in os.walk(root, followlinks=False):
        dirnames[:] = sorted(d for d in dirnames if d not in SKIP_DIRS and d not in ("node_modules", "out"))
        base = Path(dirpath)
        for name in sorted(filenames):
            if name in (".DS_Store", ".git"):
                continue
            path = base / name
            out[path.relative_to(root).as_posix()] = path
        # A symlink to a directory is listed among dirnames but never walked.
        for d in list(dirnames):
            if (base / d).is_symlink():
                out[(base / d).relative_to(root).as_posix()] = base / d
                dirnames.remove(d)
    return out


def sha256(path: Path) -> str:
    h = hashlib.sha256()
    with path.open("rb") as f:
        for block in iter(lambda: f.read(1 << 20), b""):
            h.update(block)
    return h.hexdigest()


def _models_in(directory: Path, root: Path, pack_name: str, area: str, skip_top: set[str]) -> tuple[list[ModelEntry], list[str]]:
    """Every manifest (`.toml` but the pack's own and anything under `licences/`) below `directory`."""
    models: list[ModelEntry] = []
    orphans: list[str] = []
    files = []
    for dirpath, dirnames, filenames in os.walk(directory, followlinks=False):
        rel_dir = Path(dirpath).relative_to(directory)
        if rel_dir.parts[:1] and rel_dir.parts[0] in skip_top:
            dirnames[:] = []
            continue
        dirnames[:] = sorted(d for d in dirnames if d not in SKIP_DIRS)
        for name in sorted(filenames):
            files.append(Path(dirpath) / name)
    stems = set()
    for path in sorted(files):
        rel = path.relative_to(directory)
        if path.suffix == ".toml" and not (area == "pack" and rel.as_posix() == "pack.toml") and not (
            area == "example" and rel.as_posix() == "examples.toml"
        ):
            stems.add(path.with_suffix(""))
            models.append(_entry(path, root, pack_name, area))
    for path in sorted(files):
        if path.suffix in (".glb", ".gltf") and path.with_suffix("") not in stems:
            orphans.append(path.relative_to(root).as_posix())
    return models, orphans


def _entry(manifest: Path, root: Path, pack_name: str, area: str) -> ModelEntry:
    rel = manifest.relative_to(root).as_posix()
    model = next((m for m in (manifest.with_suffix(".glb"), manifest.with_suffix(".gltf")) if m.is_file() or m.is_symlink()), None)
    entry = ModelEntry(pack_name, rel, model.relative_to(root).as_posix() if model else None, area=area)
    try:
        entry.manifest = manifests.parse_manifest(manifest.read_text(encoding="utf-8"))
    except (OSError, UnicodeDecodeError) as e:
        entry.manifest_error = f"cannot be read: {e}"
    except manifests.ManifestError as e:
        entry.manifest_error = str(e)
    return entry


def scan(root: Path) -> Tree:
    """The packs and examples of the repository at `root`."""
    tree = Tree(root)
    packs_dir = root / "packs"
    if packs_dir.is_dir():
        for pack_dir in sorted(p for p in packs_dir.iterdir() if p.is_dir() and not p.is_symlink() and p.name not in SKIP_DIRS):
            pack = PackEntry(pack_dir.name, pack_dir.relative_to(root).as_posix())
            pack_toml = pack_dir / "pack.toml"
            try:
                pack.info = manifests.parse_pack(pack_toml.read_text(encoding="utf-8"))
            except FileNotFoundError:
                pack.info_error = "is missing"
            except (OSError, UnicodeDecodeError) as e:
                pack.info_error = f"cannot be read: {e}"
            except manifests.ManifestError as e:
                pack.info_error = str(e)
            pack.models, pack.orphans = _models_in(pack_dir, root, pack_dir.name, "pack", {"licences"})
            tree.packs.append(pack)
    rejected = root / "examples" / "rejected"
    if rejected.is_dir():
        tree.examples, _ = _models_in(rejected, root, "", "example", {"licences"})
    return tree


def check_pack_files(root: Path, pack: PackEntry) -> list[tuple[str, str, str]]:
    """The file rules of D12 for one pack: (path, rule, message) per problem."""
    problems: list[tuple[str, str, str]] = []
    pack_dir = root / pack.path
    grandfathered = pack.name in GRANDFATHERED_PACKS
    for dirpath, dirnames, filenames in os.walk(pack_dir, followlinks=False):
        base = Path(dirpath)
        dirnames[:] = sorted(d for d in dirnames if d not in SKIP_DIRS)
        for d in list(dirnames):
            rel = (base / d).relative_to(root).as_posix()
            if (base / d).is_symlink():
                problems.append((rel, "files.symlink", "is a symlink; no file in a pack may be one"))
                dirnames.remove(d)
            elif not _NAME.match(d):
                problems.append((rel, "files.name", "is a folder whose name uses characters outside A-Z, a-z, 0-9, `.`, `_` and `-`"))
        for name in sorted(filenames):
            if name == ".DS_Store":
                continue
            path = base / name
            rel = path.relative_to(root).as_posix()
            parts = path.relative_to(pack_dir).parts
            if path.is_symlink():
                problems.append((rel, "files.symlink", "is a symlink; no file in a pack may be one"))
                continue
            if not _NAME.match(name):
                problems.append((rel, "files.name", "has a name using characters outside A-Z, a-z, 0-9, `.`, `_` and `-`"))
            if len(parts) - 1 > MAX_FOLDERS:
                problems.append((rel, "files.depth", f"lies {len(parts) - 1} folders below its pack; at most {MAX_FOLDERS} are allowed"))
            if not path.is_file():
                problems.append((rel, "files.type", "is not a regular file"))
                continue
            suffix = path.suffix
            if parts[0] == "licences" and len(parts) > 1:
                if suffix not in EVIDENCE_EXTENSIONS:
                    problems.append((rel, "files.type", f"is not licence evidence: only {', '.join(EVIDENCE_EXTENSIONS)} may sit under licences/"))
                continue
            allowed = (name == "pack.toml" and len(parts) == 1) or suffix in (".toml", ".glb")
            if not allowed and grandfathered and suffix in (".gltf", ".bin", ".png"):
                allowed = True
            if not allowed:
                problems.append(
                    (
                        rel,
                        "files.type",
                        f"is not an allowed file type; a pack holds pack.toml, manifests (.toml), models (.glb) and evidence under licences/"
                        + (" (.gltf, .bin and .png only in kaykit-city and kaykit-forest)" if suffix in (".gltf", ".bin", ".png") else ""),
                    )
                )
                continue
            if suffix == ".glb" and path.stat().st_size > MAX_GLB_BYTES:
                mib = path.stat().st_size / (1024 * 1024)
                problems.append((rel, "files.size", f"is {mib:.1f} MiB; a .glb may be at most 4 MiB"))
    return problems
