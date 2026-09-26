"""Private synthetic PNG and image-translation preparation; never runs a model or discovers a CLI."""

import argparse
import hashlib
import json
import os
from pathlib import Path
import struct
import sys
import zlib


def png_chunk(kind, content):
    return struct.pack(">I", len(content)) + kind + content + struct.pack(">I", zlib.crc32(kind + content))


PNG_BYTES = (
    b"\x89PNG\r\n\x1a\n"
    + png_chunk(b"IHDR", struct.pack(">IIBBBBB", 2, 1, 8, 6, 0, 0, 0))
    + png_chunk(b"IDAT", zlib.compress(b"\0\xff\0\0\xff\0\xff\0\xff"))
    + png_chunk(b"IEND", b"")
)
VERIFIED_IMAGE_PATH = "<verified-owned-image>"


def prepare(root, application_id, scenario="normal", *, model=None, direction="auto"):
    from cc_config import Config
    from cc_direction import DIRECTION_MODES
    from cc_macos.translation import snapshot_for_image
    from cc_macos.translation_fixture import prepare as prepare_translation
    from cc_providers.codex_cli import build_codex_prompt

    if direction not in DIRECTION_MODES:
        raise ValueError("synthetic_direction_required")
    fixture = prepare_translation(root, application_id, scenario, model=model)
    native = Path(fixture["root"])
    attachment = native / "app-image"
    attachment.mkdir(mode=0o700)
    source = attachment / "region.png"
    fd = os.open(source, os.O_CREAT | os.O_EXCL | os.O_WRONLY, 0o600)
    with os.fdopen(fd, "wb") as target:
        target.write(PNG_BYTES)
    fixture["config"]["direction"] = direction
    request = {"operation": "translate_image", "image_path": str(source),
               "image_bytes": len(PNG_BYTES), "image_sha256": hashlib.sha256(PNG_BYTES).hexdigest(),
               "app_language": "zh_CN", "record_history": True}
    snapshot = snapshot_for_image(Config(fixture["config"]), request, VERIFIED_IMAGE_PATH)
    fixture["request"] = request
    fixture["expected"].update(prompt=build_codex_prompt(snapshot.request), model=snapshot.selection.model,
                               task="image", kind="ocr", target_lang=snapshot.target_lang, summarize=False,
                               signature=snapshot.sig, stream=snapshot.stream_enabled)
    (native / "expected-request.json").write_text(json.dumps(fixture["expected"], ensure_ascii=False), encoding="utf-8")
    return fixture


def validate_turn_image(inputs, cwd):
    """Fail unless the real localImage block names the exact private copied bytes at turn/start."""
    import stat

    if (not isinstance(inputs, list) or len(inputs) != 2
            or set(inputs[1]) != {"type", "path"} or inputs[1]["type"] != "localImage"):
        raise ValueError("synthetic_image_input")
    path = Path(inputs[1]["path"])
    if (not path.is_absolute() or path.name != "region.png" or path.is_symlink()
            or not path.parent.name.startswith(".cc-image-")
            or path.parent.parent.resolve() != Path(cwd).resolve()
            or path.read_bytes() != PNG_BYTES
            or stat.S_IMODE(path.stat().st_mode) != 0o600
            or stat.S_IMODE(path.parent.stat().st_mode) != 0o700):
        raise ValueError("synthetic_image_copy")


def main(arguments=None):
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--prepare", required=True, type=Path)
    parser.add_argument("--application-id", required=True)
    parser.add_argument("--scenario", default="normal")
    parser.add_argument("--model")
    parser.add_argument("--direction", default="auto")
    arguments = parser.parse_args(arguments)
    fixture = prepare(arguments.prepare, arguments.application_id, arguments.scenario,
                      model=arguments.model, direction=arguments.direction)
    sys.stdout.write(json.dumps(fixture, ensure_ascii=False) + "\n")
    return 0


if __name__ == "__main__":
    sys.path.insert(0, str(Path(__file__).resolve().parent.parent))
    raise SystemExit(main())
