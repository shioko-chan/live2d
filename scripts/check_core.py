"""Optional native Core ABI smoke check in an isolated process; no renderer/GPU.
Requires an explicitly supplied, legally obtained Linux Cubism Core shared library.
"""

import ctypes as C
import json
import sys
from pathlib import Path


def aligned(size):
    buffer = C.create_string_buffer(size + 63)
    address = (C.addressof(buffer) + 63) & ~63
    return buffer, C.c_void_p(address)


def main():
    bundle, library = map(Path, sys.argv[1:])
    entry = next(bundle.glob("*.model3.json"))
    data = json.loads(entry.read_text())
    moc = (bundle / data["FileReferences"]["Moc"]).read_bytes()
    core = C.CDLL(str(library))
    core.csmReviveMocInPlace.argtypes = [C.c_void_p, C.c_uint]
    core.csmReviveMocInPlace.restype = C.c_void_p
    core.csmGetSizeofModel.argtypes = [C.c_void_p]
    core.csmGetSizeofModel.restype = C.c_uint
    core.csmInitializeModelInPlace.argtypes = [C.c_void_p, C.c_void_p, C.c_uint]
    core.csmInitializeModelInPlace.restype = C.c_void_p
    core.csmUpdateModel.argtypes = [C.c_void_p]
    core.csmUpdateModel.restype = None
    moc_buffer, moc_ptr = aligned(len(moc))
    C.memmove(moc_ptr, moc, len(moc))
    revived = core.csmReviveMocInPlace(moc_ptr, len(moc))
    if not revived:
        raise RuntimeError("Core rejected MOC3")
    size = core.csmGetSizeofModel(revived)
    if not size:
        raise RuntimeError("Invalid model memory size")
    model_buffer, model_ptr = aligned(size)
    model = core.csmInitializeModelInPlace(revived, model_ptr, size)
    if not model:
        raise RuntimeError("Core model initialization failed")
    core.csmUpdateModel(model)
    print("Core load + update passed; no render or physics comparison performed.")


if __name__ == "__main__":
    main()
