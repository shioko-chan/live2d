"""Inspect a delivered package without loading or modifying its editable rig."""
import argparse
import json
from pathlib import Path

from psd_tools import PSDImage


def inspect_package(path):
    model = next((path / "model").glob("*.model3.json"))
    document = json.loads(model.read_text())
    references = document["FileReferences"]
    entries = []
    for key in ("Moc", "Physics", "DisplayInfo", "Pose", "UserData"):
        if key in references:
            entries.append((key, references[key]))
    entries.extend(("Texture", name) for name in references["Textures"])
    entries.extend(("Expression", x["File"]) for x in references.get("Expressions", []))
    entries.extend(("Motion", x["File"]) for group in references.get("Motions", {}).values() for x in group)
    checked = [{
        "kind": kind, "path": name,
        "exists": (path / "model" / name).is_file(),
        "confined": (path / "model" / name).resolve().is_relative_to((path / "model").resolve()),
    } for kind, name in entries]
    psd = PSDImage.open(path / "sources/source.psd")
    layers = [{"name": x.name, "bbox": list(x.bbox), "visible": x.visible}
              for x in psd.descendants() if not x.is_group()]
    components = json.loads((path / "sources/components.json").read_text())
    return {
        "delivery": str(path), "references": checked,
        "all_references_ok": all(x["exists"] and x["confined"] for x in checked),
        "moc_header": (path / "model" / references["Moc"]).read_bytes()[:4].decode("ascii"),
        "psd_size": list(psd.size), "psd_layers": layers,
        "component_count": len(components if isinstance(components, list) else components["parts"]),
        "source_workspace_exists": (path / "sources/source.psd2live").is_file(),
        "cmo3_exists": bool(list(path.rglob("*.cmo3"))),
        "scope": "Structure and PSD readability only; no Cubism Editor or target application opened.",
    }


if __name__ == "__main__":
    parser = argparse.ArgumentParser()
    parser.add_argument("output", type=Path)
    parser.add_argument("packages", type=Path, nargs="+")
    args = parser.parse_args()
    result = [inspect_package(p.resolve()) for p in args.packages]
    args.output.write_text(json.dumps(result, indent=2) + "\n")
    print([{key: row[key] for key in ("delivery", "all_references_ok", "psd_size", "component_count", "cmo3_exists")}
           for row in result])
