"""Explicit, isolated configuration owner readback for the update fixture."""

import hashlib
import json
from pathlib import Path
import plistlib
import sys

def configuration_evidence(session, path, mode):
    assert mode in ("seed", "read")
    config = session.perform({"operation": "config_load"})["config"]
    if mode == "seed":
        config["font_size"] = 16
        config["update_fixture_marker"] = "synthetic preserved configuration"
        session.perform({"operation": "config_save", "config": config})
        config = session.perform({"operation": "config_load"})["config"]
    assert config["font_size"] == 16
    assert config["update_fixture_marker"] == "synthetic preserved configuration"
    return {
        "font_size": config["font_size"],
        "marker": config["update_fixture_marker"],
        "config_sha256": hashlib.sha256(path.read_bytes()).hexdigest(),
    }


def main():
    app, home, identifier, mode = sys.argv[1:]
    assert sys.platform == "darwin"
    assert identifier.startswith("dev.cc-translate.update-fixture.")
    assert plistlib.loads((Path(app) / "Contents/Info.plist").read_bytes())["CFBundleIdentifier"] == identifier
    core = Path(app) / "Contents/Resources/Core"
    sys.path.insert(0, str(core))
    from cc_macos.configuration import ConfigurationSession
    from cc_storage import macos_user_paths

    session = ConfigurationSession(Path(home), identifier)
    session.open()
    try:
        path = macos_user_paths(home, identifier).application_support / "config.json"
        print(json.dumps(configuration_evidence(session, path, mode), sort_keys=True))
    finally:
        session.close()


if __name__ == "__main__":
    main()
