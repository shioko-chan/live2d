import contextlib
import fcntl
import hashlib
import json
import os
import uuid
from pathlib import Path
from .schema import Revision


def digest(path: Path) -> str:
    h = hashlib.sha256()
    with path.open("rb") as f:
        for block in iter(lambda: f.read(1024 * 1024), b""):
            h.update(block)
    return h.hexdigest()


def fingerprint(value) -> str:
    return hashlib.sha256(json.dumps(value, sort_keys=True, ensure_ascii=False).encode()).hexdigest()


def write_json(path: Path, value):
    path.parent.mkdir(parents=True, exist_ok=True)
    tmp = path.with_name(path.name + ".tmp")
    with tmp.open("w") as f:
        json.dump(value, f, ensure_ascii=False, indent=2)
        f.flush()
        os.fsync(f.fileno())
    tmp.replace(path)


def read_json(path: Path):
    return json.loads(path.read_text())


@contextlib.contextmanager
def lock(path: Path):
    path.parent.mkdir(parents=True, exist_ok=True)
    with path.open("a") as f:
        try:
            fcntl.flock(f, fcntl.LOCK_EX | fcntl.LOCK_NB)
        except BlockingIOError:
            raise RuntimeError(f"Task already running: {path}") from None
        try:
            yield
        finally:
            fcntl.flock(f, fcntl.LOCK_UN)


class Store:
    def __init__(self, root: Path):
        self.root = root.resolve()

    def init(self):
        self.root.mkdir(parents=True, exist_ok=True)
        if (self.root / "project.json").exists():
            raise ValueError("Project already exists")
        write_json(self.root / "project.json", {"head": None, "accepted": None})

    def revision_dir(self, rid):
        if not isinstance(rid, str) or not rid.isalnum():
            raise ValueError("Invalid revision ID")
        return self.root / "revisions" / rid

    def get(self, rid=None):
        rid = rid or read_json(self.root / "project.json")["head"]
        return Revision.model_validate(read_json(self.revision_dir(rid) / "revision.json"))

    def save(self, revision):
        write_json(self.revision_dir(revision.id) / "revision.json", revision.model_dump())

    def create(self, plan, source=None, reference=None, *, parent=..., checkout=True):
        import shutil

        state = read_json(self.root / "project.json")
        parent = state["head"] if parent is ... else parent
        if parent is not None:
            self.get(parent)
        rev = Revision(id=uuid.uuid4().hex[:16], parent=parent, plan=plan)
        directory = self.revision_dir(rev.id)
        directory.mkdir(parents=True)
        for key, path in (("source", source), ("reference", reference)):
            if path is not None:
                path = Path(path)
                if path.suffix.lower() not in {".png", ".jpg", ".jpeg", ".psd"}:
                    raise ValueError("Input must be PNG, JPEG or PSD")
                dest = directory / (key + path.suffix.lower())
                shutil.copyfile(path, dest)
                setattr(rev, key, dest.name)
                if key == "source":
                    rev.source_hash = digest(dest)
        self.save(rev)
        if checkout:
            state["head"] = rev.id
            write_json(self.root / "project.json", state)
        return rev

    def checkout(self, rid):
        self.get(rid)
        state = read_json(self.root / "project.json")
        state["head"] = rid
        write_json(self.root / "project.json", state)

    def accept(self, rid):
        rev = self.get(rid)
        if rev.status not in {"built", "accepted"}:
            raise ValueError("Only a completed build can be accepted")
        state = read_json(self.root / "project.json")
        state["accepted"] = rid
        rev.status = "accepted"
        self.save(rev)
        write_json(self.root / "project.json", state)

    def history(self):
        return [read_json(p) for p in sorted(self.root.glob("revisions/*/revision.json"))]
