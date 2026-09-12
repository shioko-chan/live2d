from pathlib import Path
from PIL import Image
from .store import read_json, digest


def validate_bundle(directory: Path):
    entries = list(directory.glob("*.model3.json"))
    if len(entries) != 1:
        raise ValueError("Expected exactly one model3.json")
    document = read_json(entries[0])
    if document.get("Version") != 3:
        raise ValueError("Expected model3 Version 3")
    refs = document["FileReferences"]

    def resolve(name):
        path = (directory / name).resolve()
        if not path.is_relative_to(directory.resolve()) or not path.is_file():
            raise ValueError(f"Invalid or missing bundle reference: {name}")
        return path

    moc = resolve(refs["Moc"])
    with moc.open("rb") as f:
        header = f.read(8)
    if moc.stat().st_size < 64 or header[:4] != b"MOC3":
        raise ValueError("Not a MOC3 binary; placeholder export rejected")
    if not refs.get("Textures"):
        raise ValueError("No textures")
    for texture in refs["Textures"]:
        with Image.open(resolve(texture)) as im:
            im.verify()
    for key in ("Physics", "Pose", "DisplayInfo", "UserData"):
        if refs.get(key):
            read_json(resolve(refs[key]))
    for expression in refs.get("Expressions", []):
        read_json(resolve(expression["File"]))
    for motions in refs.get("Motions", {}).values():
        for motion in motions:
            read_json(resolve(motion["File"]))
    return {
        "structural_check": "passed",
        "runtime_verified": False,
        "warning": "Header and references only; does not prove Core compatibility or visual quality",
        "files": {str(p.relative_to(directory)): digest(p) for p in directory.rglob("*") if p.is_file()},
    }
