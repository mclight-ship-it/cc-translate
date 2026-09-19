"""Measure shipped classification and dictionary code with the shipped interpreter."""

import json
from pathlib import Path
import platform
import sys
import time


def run(app, asset):
    core = app / "Contents/Resources/Core"
    python = app / "Contents/Resources/python/bin/python3"
    if (sys.platform != "darwin" or platform.machine() != "arm64" or
            Path(sys.executable).resolve() != python.resolve() or
            not sys.flags.isolated or not sys.dont_write_bytecode):
        raise RuntimeError("performance worker requires isolated bundled arm64 macOS Python")
    sys.path[:0] = [str(core), str(Path(__file__).resolve().parents[3])]
    import cc_classify
    import cc_dictionary_artifact_core
    import cc_dictionary_lookup
    import cc_dictionary_presentation
    import cc_dictionary_store
    from tools.macos.performance_metrics import CLASSIFICATION_CASES, DICTIONARY_CASES, measure

    modules = (cc_classify, cc_dictionary_artifact_core, cc_dictionary_lookup,
               cc_dictionary_presentation, cc_dictionary_store)
    for module in modules:
        if Path(module.__file__).resolve().parent != core.resolve():
            raise RuntimeError("performance worker imported non-bundled product code")

    def equal(actual, expected):
        if actual != expected:
            raise RuntimeError("fixed classification result changed")

    classification = {
        name: measure(lambda text=text: cc_classify.classify_selection(text),
                      lambda value, expected=expected: equal(value, expected), 2)
        for name, text, expected in CLASSIFICATION_CASES
    }
    if asset.stat().st_size != cc_dictionary_artifact_core.ARTIFACT_SIZE:
        raise RuntimeError("performance dictionary size differs from the shipped pin")
    start = time.perf_counter_ns()
    dictionary = cc_dictionary_lookup.LocalDictionary(str(asset), cc_dictionary_artifact_core.ARTIFACT_SHA256)
    try:
        if not dictionary.status.available:
            raise RuntimeError("pinned performance dictionary is unavailable")
        opened_ms = (time.perf_counter_ns() - start) / 1_000_000

        def lookup(query):
            result = dictionary.lookup(query)
            if result is None or not result.is_high_confidence:
                raise RuntimeError("fixed dictionary entry is missing")
            return cc_dictionary_presentation.format_dictionary_plain(result, "en_US")

        def formatted(value):
            if not value or "Sources & licenses:" not in value:
                raise RuntimeError("dictionary formatting or attribution is missing")

        queries = {query: measure(lambda query=query: lookup(query), formatted, 10)
                   for query in DICTIONARY_CASES}
    finally:
        dictionary.close_thread()
    manifest = json.loads((app / "Contents/Resources/source-manifest.json").read_bytes())
    return {
        "source_sha": manifest["source_commit"], "classification": classification, "dictionary": queries,
        "dictionary_open_and_pin_validation_ms": opened_ms,
        "dictionary_sha256": cc_dictionary_artifact_core.ARTIFACT_SHA256,
        "dictionary_data_version": cc_dictionary_artifact_core.ARTIFACT_DATA_VERSION,
        "product_modules": [Path(module.__file__).relative_to(core).as_posix() for module in modules],
        "python": platform.python_version(), "os": platform.mac_ver()[0], "architecture": platform.machine(),
    }


if __name__ == "__main__":
    print(json.dumps(run(Path(sys.argv[1]).resolve(), Path(sys.argv[2]).resolve()), allow_nan=False))
