"""Same-run artifact identity and runtime checks; never builds or signs the tested app."""

import argparse
import hashlib
import json
import math
import os
from pathlib import Path
import platform
import re
import shutil
import stat
import subprocess
import sys
import zipfile

if __package__:
    from . import bundle, performance, smoke
else:
    sys.path.insert(0, str(Path(__file__).resolve().parents[2]))
    from tools.macos import bundle, performance, smoke


ARCHIVE = "CCTranslateMac-P0.zip"
APP_NAME = "CCTranslateMac-P0.app"
RECEIPT = "runtime-receipt.json"
ROOT = Path(__file__).resolve().parents[2]
need = bundle.need
INTEGRATION_TESTS = (
    "testOptionalBundledHelperHandshakeFixtureAndShutdown",
    "testBundledConfigurationLoadSaveNormalizeStopAndReopen",
    "testBundledConfigurationCorruptFileFailsWithoutChangingBytes",
    "testBundledConfigurationCompetingHelperFailsThenTakesReleasedOwnership",
    "testBundledConfigurationWriteAndMigrationBudgetsPreserveReadableData",
    "testBundledHistoryLifecyclePaginationUnicodeAndConfigurationCoexistence",
    "testBundledHistoryCorruptOversizedAndRejectedAddsPreserveBytes",
    "testBundledHistoryCompetingHelpersReleaseBothOwners",
    "testBundledWorkerStartFailureFramesAreDeterminateForAllBusinessOperations",
    "testBundledTranslationConfigurationStreamHistoryCacheAndReopen",
    "testBundledCustomModelSettingsSurviveReopenAndReachExactProviderID",
    "testBundledModelCatalogReadsExactMetadataWithoutTurnsThenTranslatesKnownModel",
    "testBundledImageTranslationOwnsPNGStreamsWithoutCacheAndDrainsCancellationOrUnknown",
    "testBundledOCRTextPreservesLayoutClassificationAndNeverUsesCacheOrAutomaticSummary",
    "testBundledTranslationConcurrentOptoutAndCancellationDrain",
    "testBundledTranslationCorruptionAndOutputBudgets",
    "testBundledTranslationCompetingHelpersForceStopAndReopen",
    "testBundledResultActionsUseNativeProviderWithoutReadingOrWritingHistory",
    "testBundledResultActionCancellationDrainsOwnedGroupsWithoutHistory",
    "testBundledDictionaryInstallLookupCacheHistoryAndLiveOptoutWithoutCLI",
    "testBundledDictionaryInvalidStagingDiscardDisableDeleteAndReopenWithoutCLI",
)


def json_digest(value):
    return hashlib.sha256(json.dumps(value, sort_keys=True, separators=(",", ":")).encode()).hexdigest()


def tree_digest(app):
    content = smoke.snapshot(app)
    return json_digest([
        [name, content[name], stat.S_IMODE((app / name).lstat().st_mode)]
        for name in sorted(content)
    ])


def environment_record(major, xcode):
    need(sys.platform == "darwin" and platform.machine() == "arm64", "arm64 macOS required")
    developer = Path("/Applications") / ("Xcode_" + xcode + ".app") / "Contents/Developer"
    need(os.environ.get("DEVELOPER_DIR") == str(developer) and developer.is_dir(),
         "explicit preinstalled harness Xcode required")
    product = bundle.run(["/usr/bin/sw_vers", "-productVersion"])
    need(product.split(".")[0] == str(major), "runtime OS major mismatch")
    actual_xcode = bundle.run(["/usr/bin/xcodebuild", "-version"])
    need(actual_xcode.splitlines()[0] == "Xcode " + xcode, "harness Xcode version mismatch")
    return {
        "os_version": product,
        "os_build": bundle.run(["/usr/bin/sw_vers", "-buildVersion"]),
        "architecture": platform.machine(),
        "image_os": os.environ["ImageOS"],
        "image_version": os.environ["ImageVersion"],
        "xcode": actual_xcode,
        "swift": bundle.run(["/usr/bin/xcrun", "swift", "--version"]),
        "sdk": bundle.run(["/usr/bin/xcrun", "--sdk", "macosx", "--show-sdk-version"]),
    }


def verify_checkout(sha):
    need(re.fullmatch(r"[0-9a-f]{40}", sha) is not None, "invalid source SHA")
    need(bundle.run(["git", "-C", ROOT, "rev-parse", "HEAD"]) == sha, "checkout SHA mismatch")
    need(not bundle.run(["git", "-C", ROOT, "status", "--porcelain"]), "checkout is dirty")


def verify_archive(path):
    with zipfile.ZipFile(path) as archive:
        names = set()
        for entry in archive.infolist():
            safe = bundle.archive_path(entry.filename)
            need(safe.parts[0] in (APP_NAME, "__MACOSX"), "unexpected archive root")
            need(entry.filename not in names, "duplicate archive entry")
            names.add(entry.filename)
            need(not (entry.external_attr >> 16) & 0o7000, "privileged archive mode")
        need(archive.testzip() is None, "archive CRC failed")
        for name in ("Contents/MacOS/CCTranslateMac", "Contents/Resources/python/bin/python3.12"):
            entry = archive.getinfo(APP_NAME + "/" + name)
            need(stat.S_IMODE(entry.external_attr >> 16) == 0o755, "archive executable mode mismatch")
        link = archive.getinfo(APP_NAME + "/Contents/Resources/python/bin/python3")
        need(stat.S_ISLNK(link.external_attr >> 16) and archive.read(link) == b"python3.12",
             "archive Python link mismatch")


def verify_source(app, sha):
    contents = app / "Contents"
    manifest = json.loads((contents / "Resources/source-manifest.json").read_bytes())
    need(manifest["source_commit"] == sha and manifest["source_tree_dirty"] is False,
         "bundle source identity mismatch")
    need(manifest["toolchain"]["xcode"].splitlines()[0] == "Xcode 16.4",
         "product was not built with Xcode 16.4")
    core = contents / "Resources/Core"
    sources = [p for p in core.rglob("*") if p.is_file() and
               (p.suffix == ".py" or p.name == "codex_instructions.txt")]
    for path in sources:
        relative = path.relative_to(core)
        source = ROOT / ("cc_macos/launch.py" if relative.as_posix() == "launch.py" else relative)
        need(not path.is_symlink() and source.is_file() and bundle.digest(path) == bundle.digest(source),
             "bundle/checkout source bytes differ")
    return manifest, len(sources)


def verify_receipt(app, directory, sha, run_id, expected_hash):
    receipt = json.loads((directory / RECEIPT).read_bytes())
    need(receipt["schema"] == 1 and receipt["source_sha"] == sha
         and receipt["run_id"] == run_id and receipt["archive"] == ARCHIVE, "receipt identity mismatch")
    archive = directory / ARCHIVE
    need(re.fullmatch(r"[0-9a-f]{64}", expected_hash) is not None, "invalid producer hash")
    need(receipt["archive_sha256"] == expected_hash == bundle.digest(archive), "archive hash mismatch")
    need(receipt["archive_bytes"] == archive.stat().st_size, "archive size mismatch")
    verify_archive(archive)
    manifest, count = verify_source(app, sha)
    need(receipt["producer_toolchain"] == manifest["toolchain"], "producer toolchain receipt mismatch")
    need(tree_digest(app) == receipt["tree_sha256"], "extracted app content/mode/link mismatch")
    return receipt, manifest, count


def prepare_harness(destination):
    need(not destination.exists(), "harness directory already exists")
    destination.mkdir()
    for name in ("CCProcessSupport", "CCTranslateSupport"):
        shutil.copytree(ROOT / "macos/Sources" / name, destination / "Sources" / name)
    test = Path("Tests/CCTranslateSupportTests/HelperIntegrationTests.swift")
    (destination / test).parent.mkdir(parents=True)
    shutil.copy2(ROOT / "macos" / test, destination / test)
    shutil.copy2(ROOT / "tools/macos/RuntimeHarnessPackage.swift", destination / "Package.swift")
    need(bundle.digest(destination / test) == bundle.digest(ROOT / "macos" / test),
         "integration test source changed")
    for source, target in (
            ("Sources/CCTranslateMac/AboutResources.swift", "Sources/CCTranslateAppResources/AboutResources.swift"),
            ("Tests/CCTranslateMacTests/BundledAboutTests.swift", "Tests/CCTranslateMacTests/BundledAboutTests.swift")):
        target = destination / target
        target.parent.mkdir(parents=True, exist_ok=True)
        shutil.copy2(ROOT / "macos" / source, target)
        need(bundle.digest(target) == bundle.digest(ROOT / "macos" / source),
             "bundled About reader/test source changed")
    for relative in (
            "Tests/CCTranslateSupportTests/LocalOCRTests.swift",
            "Tests/CCTranslateSupportTests/PlainTextPasteTests.swift",
            "Tests/CCTranslateSupportTests/FreshCopyClipboardTests.swift",
            "Tests/CCTranslateSupportTests/ClipboardProcessTests.swift",
            "Tests/CCTranslateSupportTests/Fixtures/about-metadata-zh-narrow.png"):
        target = destination / relative
        target.parent.mkdir(parents=True, exist_ok=True)
        shutil.copy2(ROOT / "macos" / relative, target)
        need(bundle.digest(target) == bundle.digest(ROOT / "macos" / relative),
             "native clipboard/OCR test/fixture source changed")
    fixture = Path("Tests/CCTranslateSupportTests/Fixtures/ClipboardProducer")
    shutil.copytree(ROOT / "macos" / fixture, destination / fixture)
    for source in (ROOT / "macos" / fixture).rglob("*"):
        if source.is_file():
            target = destination / source.relative_to(ROOT / "macos")
            need(bundle.digest(target) == bundle.digest(source),
                 "clipboard producer fixture source changed")
    return tree_digest(destination)


def run_swift_harness(harness, environment, arguments, error):
    result = subprocess.run(
        ["/usr/bin/xcrun", "swift", *arguments, "--package-path", str(harness),
         "--triple", "arm64-apple-macosx14.0"],
        env=environment, stdout=subprocess.PIPE, stderr=subprocess.STDOUT, text=True)
    print(result.stdout, flush=True)
    need(result.returncode == 0, error)
    return result.stdout


def require_xctest_passes(text, test_class, methods):
    for outcome in ("started", "passed"):
        found = re.findall(
            r"Test Case '-\[" + re.escape(test_class) + r" (\w+)\]' " + outcome, text)
        need(len(found) == len(methods) and set(found) == set(methods),
             "XCTest method set incomplete or duplicated")
    matches = re.findall(r"Executed (\d+) tests?, with (?:(\d+) tests? skipped and )?(\d+) failures", text)
    need(bool(matches), "XCTest not discovered")
    need(all(int(total) == len(methods) and int(skipped or 0) == 0 and int(failed) == 0
             for total, skipped, failed in matches), "XCTest failed/skipped/wrong count")


def integration_result(text):
    source = (ROOT / "macos/Tests/CCTranslateSupportTests/HelperIntegrationTests.swift").read_text(encoding="utf-8")
    methods = re.findall(r"\bfunc (test\w+)\(", source)
    need(len(methods) == len(INTEGRATION_TESTS) and set(methods) == set(INTEGRATION_TESTS),
         "integration source method set changed")
    require_xctest_passes(text, "CCTranslateSupportTests.HelperIntegrationTests", INTEGRATION_TESTS)
    timings = re.findall(r"(?m)^CC_TRANSLATE_DICTIONARY_TIMINGS (\{[^\n]+\})\s*$", text)
    need(len(timings) == 1, "dictionary warm lookup measurements missing or duplicated")
    timing = json.loads(timings[0])
    need(isinstance(timing, dict) and
         set(timing) == {"scope", "unit", "samples", "use_cache", "record_history"} and
         timing["scope"] == "config_only_foundation_helper_round_trip_not_gui" and
         timing["unit"] == "ms" and timing["use_cache"] is False and timing["record_history"] is False,
         "dictionary measurement scope mismatch")
    samples = timing["samples"]
    need(isinstance(samples, list) and len(samples) == 8 and
         all(type(value) in (int, float) and math.isfinite(value) and value >= 0 for value in samples),
         "dictionary measurement samples invalid")
    return {"tests_run": len(INTEGRATION_TESTS), "failures": 0, "skipped": 0,
            "methods": list(INTEGRATION_TESTS), "dictionary_warm_lookup": timing}


def verify_integration(args):
    verify_checkout(args.source_sha)
    report = integration_result((args.directory / "integration-tests.log").read_text(encoding="utf-8"))
    bundle.write_json(args.directory / "integration-tests.json", report)


PRODUCT_DICTIONARY_METHOD = "testBundledWarmDictionaryIntentToNativePaintReportsMeasuredGoal"


def dictionary_product_result(text):
    require_xctest_passes(text, "CCTranslateMacTests.DictionaryProductIntegrationTests", [PRODUCT_DICTIONARY_METHOD])
    markers = re.findall(r"(?m)^CC_TRANSLATE_DICTIONARY_PRODUCT_TIMINGS (\{[^\n]+\})\s*$", text)
    need(len(markers) == 1, "dictionary product timing missing or duplicated")
    measurement = json.loads(markers[0])
    need(isinstance(measurement, dict), "dictionary product measurement is not an object")
    expected = {
        "scope": "same_source_model_and_retained_swiftui_appkit_view_with_bundled_configuration_helper",
        "source_cohort": "current Swift package model/view; helper from CC_TRANSLATE_APP; not the packaged GUI process",
        "endpoint": "explicit_model_translate_to_matching_read_only_nstextview_and_offscreen_bitmap_paint",
        "physical_keyboard": False, "full_gui": False, "onscreen_presentation": False,
        "unit": "ms", "warmup_intents": 2, "warm_intents": 10,
        "native_view_goal_ms": 150, "goal_is_asserted": False,
        "query_format_goal_ms": 10, "query_format_directly_measured": False,
        "setup_and_install_included": False, "network_download_measured": True,
        "installation_transport": "native_URLSession_HTTPS",
        "history_enabled": False, "cache_hits": False, "cli_candidates": 0,
        "poll_interval_ms": 1, "ocr_source_visible": True,
    }
    need(all(type(measurement.get(key)) is type(value) and measurement[key] == value
             for key, value in expected.items()), "dictionary product measurement scope mismatch")
    duration, downloaded = measurement.get("native_download_ms"), measurement.get("native_download_bytes")
    need(type(duration) in (int, float) and math.isfinite(duration) and duration >= 0 and
         type(downloaded) is int and downloaded > 0, "native dictionary download measurement invalid")
    for key in ("native_paint_samples", "intent_to_lookup_terminal_samples"):
        samples = measurement.get(key)
        need(isinstance(samples, list) and len(samples) == 10 and
             all(type(value) in (int, float) and math.isfinite(value) and value >= 0 for value in samples),
             "dictionary product samples invalid")
    samples = sorted(measurement["native_paint_samples"])
    p95, maximum = samples[math.ceil(len(samples) * 0.95) - 1], samples[-1]
    need(all(type(measurement.get(key)) in (int, float) for key in ("native_paint_p95", "native_paint_max")) and
         measurement["native_paint_p95"] == p95 and measurement["native_paint_max"] == maximum,
         "dictionary product percentiles do not match samples")
    goal = "met" if p95 <= 150 else "measured_miss"
    need(measurement.get("native_view_goal_result") == goal, "dictionary product goal result mismatch")
    need(all(terminal <= painted for terminal, painted in
             zip(measurement["intent_to_lookup_terminal_samples"], measurement["native_paint_samples"])),
         "dictionary product endpoints out of order")
    return {"tests_run": 1, "failures": 0, "skipped": 0, "measurement": measurement}


def verify_dictionary_product(args):
    verify_checkout(args.source_sha)
    source = ROOT / "macos/Tests/CCTranslateMacTests/DictionaryProductIntegrationTests.swift"
    need(re.findall(r"\bfunc (test\w+)\(", source.read_text(encoding="utf-8")) == [PRODUCT_DICTIONARY_METHOD],
         "dictionary product source test inventory changed")
    report = dictionary_product_result((args.directory / "dictionary-product-tests.log").read_text(encoding="utf-8"))
    report.update(source_sha=args.source_sha, run_id=args.run_id,
                  producer_attempt=os.environ["GITHUB_RUN_ATTEMPT"])
    bundle.write_json(args.directory / "dictionary-product-tests.json", report)


BUNDLED_ABOUT_METHOD = "testPackagedAboutReadsRealMetadataAndEveryCompleteBundledLicenseWithoutLaunchingAnything"


def about_result(text):
    source = ROOT / "macos/Tests/CCTranslateMacTests/BundledAboutTests.swift"
    need(re.findall(r"\bfunc (test\w+)\(", source.read_text(encoding="utf-8")) == [BUNDLED_ABOUT_METHOD],
         "bundled About source test inventory changed")
    require_xctest_passes(text, "CCTranslateMacTests.BundledAboutTests", [BUNDLED_ABOUT_METHOD])
    return {"tests_run": 1, "failures": 0, "skipped": 0, "methods": [BUNDLED_ABOUT_METHOD]}


def verify_about(args):
    verify_checkout(args.source_sha)
    report = about_result((args.directory / "about-tests.log").read_text(encoding="utf-8"))
    report.update(source_sha=args.source_sha, run_id=args.run_id,
                  producer_attempt=os.environ["GITHUB_RUN_ATTEMPT"])
    bundle.write_json(args.directory / "about-tests.json", report)


LOCAL_OCR_METHODS = (
    "testOriginalSmallAboutNavigationRecognizesChineseAndEnglish",
    "testSmallPureEnglishRemainsReadableWithoutLanguageCorrection",
    "testOrdinaryMixedSimplifiedChineseAndEnglishInEitherLineOrder",
    "testOrdinaryMixedTraditionalChineseAndEnglishRemainRecognizable",
)


def local_ocr_result(text):
    source = ROOT / "macos/Tests/CCTranslateSupportTests/LocalOCRTests.swift"
    methods = re.findall(r"\bfunc (test\w+)\(", source.read_text(encoding="utf-8"))
    need(len(methods) == len(LOCAL_OCR_METHODS) and set(methods) == set(LOCAL_OCR_METHODS),
         "local OCR source test inventory changed")
    require_xctest_passes(text, "CCTranslateSupportTests.LocalOCRTests", LOCAL_OCR_METHODS)
    return {"tests_run": len(LOCAL_OCR_METHODS), "failures": 0, "skipped": 0,
            "methods": list(LOCAL_OCR_METHODS),
            "scope": "production_Vision_on_synthetic_images_not_screen_capture"}


PLAIN_TEXT_PASTE_METHODS = (
    "testDefaultConstructionAndSettingsDoNotTouchClipboardInputOrRegisterShortcut",
    "testExplicitPasteWritesThenSubmitsOneUnconfirmedChordToCapturedTarget",
    "testInitialTrustSecureInputOrTargetFailureNeverReadsClipboard",
    "testCaptureBoundaryReentrantCancelCannotStartLateClipboardRead",
    "testDelayedReadCancelKeepsMainActorResponsiveAndRejectsAnotherWorker",
    "testDefaultClipboardDeadlineAllowsDelayedTransferBeyondTwoSeconds",
    "testTimedOutPromisedReadCannotWriteWhenItEventuallyReturns",
    "testCancelledOldCallbacksCannotCancelNewExplicitRequestAfterReaderDrains",
    "testDisableAndShutdownDiscardLateReadsWithoutReplayWhenEnabledAgain",
    "testUnavailableInvalidOrChangedClipboardReadCannotReachWriteOrPost",
    "testModifiersAndTriggerKeyMustReleaseBeforeConversionAndPaste",
    "testHeldModifiersTimeoutDoesNotChangeClipboard",
    "testTargetTrustOrSecureInputChangeWhileWaitingPreventsWrite",
    "testCancelWhileWaitingDiscardsTimerAndNeverReplaysAfterRelease",
    "testWriteFailureDistinguishesUntouchedAndClearedClipboardWithoutRestore",
    "testCancellationDuringWriteReportsUncertainThenActualPartialEffect",
    "testDestinationChangeAfterConversionIsPartialNotSuccessfulPaste",
    "testNewClipboardOwnerAfterWritePreventsPostAndDoesNotRestore",
    "testTargetIsRevalidatedAfterAsynchronousClipboardOwnershipCheck",
    "testModifiersPressedAgainAfterWriteWaitWithoutConvertingTwice",
    "testDisableOrShutdownAfterWriteCannotPostLateVerificationResult",
    "testPostFailureAndPartialSubmissionNeverClaimPasteSucceeded",
    "testReentrantCancelAtPostingBoundaryPreventsKeysAndPreservesPartialOutcome",
    "testLateCancelDisableAndShutdownDoNotClaimSubmittedEventsWerePrevented",
    "testPrivateRichAndPlainClipboardPrefersExactUnicodePlainText",
    "testPrivateTextWithAlternativeImageRepresentationDoesNotReadImageData",
    "testPrivateRTFOnlyClipboardUsesAppKitConversion",
    "testPrivateMultipleTextItemsHaveExplicitNewlineBoundariesAndNoTranslationCharacterLimit",
    "testPrivateImageFileAndMixedFileTextClipboardsRemainUntouched",
    "testPrivateHTMLOnlyIsNotRenderedOrConvertedViaNetworkCapableImporter",
    "testPrivateMixedTextAndFileItemsAreNotPartiallyConverted",
    "testPrivateTabularTextRetainsTabsAndLineBreaks",
    "testPrivateInvalidRTFDoesNotClearClipboard",
    "testPrivatePromisedButUnavailableTextReturnsFailureWithoutChangingClipboard",
    "testPrivateRichHTMLProviderIsNeverAskedWhenPlainTextIsAvailable",
    "testPrivateProviderChangingOwnerDuringReadCannotProduceStaleSnapshot",
    "testPrivatePasteboardServiceStripsFormattingAndRequestsExactlyOneInjectedPaste",
    "testPrivateNewOwnerBetweenReadAndWriteIsNeverOverwrittenOrRestored",
    "testPrivateCancelledSnapshotCannotWriteAndEmptyStringIsStillValidText",
    "testPrivateUTF16AndLegacyPlainTextDecodeWithoutLoss",
    "testPrivateInvalidUTF8DoesNotBecomeReplacementCharacters",
    "testPrivateFulfilledCPromisePreservesTextAndCanBeWrittenOnce",
    "testPrivateWrittenLeaseIsInvalidatedBySameProcessAppKitOwner",
    "testPrivateSnapshotCannotCrossAdaptersOrReplayAfterAnotherRead",
    "testPrivateCancelWhileCPromiseWaitsDrainsWithoutPosting",
    "testPrivateMixedFileClipboardDoesNotFulfillEarlierTextPromise",
)


def plain_text_paste_result(text):
    source = ROOT / "macos/Tests/CCTranslateSupportTests/PlainTextPasteTests.swift"
    methods = re.findall(r"\bfunc (test\w+)\(", source.read_text(encoding="utf-8"))
    need(len(methods) == len(PLAIN_TEXT_PASTE_METHODS) and set(methods) == set(PLAIN_TEXT_PASTE_METHODS),
         "plain text paste source test inventory changed")
    require_xctest_passes(text, "CCTranslateSupportTests.PlainTextPasteTests", PLAIN_TEXT_PASTE_METHODS)
    need("NSPasteboard: synchronous promise fulfillment requested from a background thread" not in text,
         "plain text paste still invokes AppKit background promise fulfillment")
    return {"tests_run": len(PLAIN_TEXT_PASTE_METHODS), "failures": 0, "skipped": 0,
            "methods": list(PLAIN_TEXT_PASTE_METHODS),
            "scope": "same_source_service_and_private_pasteboards_with_injected_input_not_global_shortcut_or_editor"}


FRESH_COPY_METHODS = (
    "testReaderConstructionAndInvalidAuthorizationDoNotAccessAnyPasteboard",
    "testExactFreshUnicodeTextIsReadWithoutChangingAnyRepresentations",
    "testOldRevisionAndChangeImmediatelyBeforeDataReadAreRejected",
    "testCancellationAfterReadDiscardsTextWithoutClearingUserCopy",
    "testFileImageConcealedAndHTMLOnlyCopiesAreNotTextFallbacks",
    "testBrowserPlainTextIgnoresRichSourceAndVendorMetadataWithoutMutatingCopy",
    "testBrowserMetadataAloneNeverBecomesTextAndDoesNotHideSensitiveMarkers",
    "testMultipleTextItemsAreJoinedInOrderAndEmptyOrMixedBoardsAreNotTruncated",
    "testUTF8BudgetInvalidUnicodeEmptyAndNULAreNotInventedSelections",
    "testEmptyFirstFlavorFallsThroughToValidCopiedTextWithoutReadingMetadata",
    "testRTFTextUsesNativeConversionButMalformedDataIsNotASelection",
    "testCombinedTextItemsHonorTotalBudgetWithoutTruncation",
)


def fresh_copy_result(text):
    source = ROOT / "macos/Tests/CCTranslateSupportTests/FreshCopyClipboardTests.swift"
    methods = re.findall(r"\bfunc (test\w+)\(", source.read_text(encoding="utf-8"))
    need(len(methods) == len(FRESH_COPY_METHODS) and set(methods) == set(FRESH_COPY_METHODS),
         "fresh copy source test inventory changed")
    require_xctest_passes(text, "CCTranslateSupportTests.FreshCopyClipboardTests", FRESH_COPY_METHODS)
    return {"tests_run": len(FRESH_COPY_METHODS), "failures": 0, "skipped": 0,
            "methods": list(FRESH_COPY_METHODS),
            "scope": "same_source_fresh_text_reader_private_pasteboards_not_global_events_or_TCC"}


CLIPBOARD_PROCESS_METHODS = (
    "testDecoderPreservesFragmentedBodyBeyondTranslationFrameLimit",
    "testDecoderRejectsWrongRequestVersionAndBackgroundReader",
    "testDecoderRejectsEarlyResultDuplicateHelloAndOversizedMetadata",
    "testDecoderRejectsTruncatedTrailingAndMalformedUTF8Bodies",
    "testDecoderRequiresMatchingSuccessfulProcessReceipt",
    "testUnrelatedLaunchDoesNotEnterClipboardWorker",
    "testActualAppRejectsMalformedWorkerInvocationWithoutUIBootstrap",
    "testExitedWorkerIsNotTimedOutDuringMandatoryGroupCleanup",
    "testConstructionAndPrecancelledReadDoNotLaunchAWorker",
    "testMissingExecutableFailsWithoutChangingClipboard",
    "testActualAppTransfersLargeExactBytesAndReapsBeforeGrantingLease",
    "testTimeoutReapsOnlyReaderAndDoesNotClaimExternalProducerStopped",
)


def clipboard_process_result(text):
    source = ROOT / "macos/Tests/CCTranslateSupportTests/ClipboardProcessTests.swift"
    methods = re.findall(r"\bfunc (test\w+)\(", source.read_text(encoding="utf-8"))
    need(len(methods) == len(CLIPBOARD_PROCESS_METHODS) and set(methods) == set(CLIPBOARD_PROCESS_METHODS),
         "clipboard process source test inventory changed")
    require_xctest_passes(text, "CCTranslateSupportTests.ClipboardProcessTests", CLIPBOARD_PROCESS_METHODS)
    need("NSPasteboard: synchronous promise fulfillment requested from a background thread" not in text,
         "clipboard worker still invokes AppKit background promise fulfillment")
    return {"tests_run": len(CLIPBOARD_PROCESS_METHODS), "failures": 0, "skipped": 0,
            "methods": list(CLIPBOARD_PROCESS_METHODS),
            "scope": "actual_app_worker_private_clipboard_external_producer_and_owned_process_cleanup"}


def seal(args):
    verify_checkout(args.source_sha)
    environment = environment_record(15, "16.4")
    app, archive = args.directory / APP_NAME, args.directory / ARCHIVE
    verify_archive(archive)
    manifest, count = verify_source(app, args.source_sha)
    report = {
        "schema": 1, "source_sha": args.source_sha, "run_id": args.run_id,
        "producer_attempt": os.environ["GITHUB_RUN_ATTEMPT"],
        "archive": ARCHIVE, "archive_sha256": bundle.digest(archive),
        "archive_bytes": archive.stat().st_size, "tree_sha256": tree_digest(app),
        "producer": environment, "producer_toolchain": manifest["toolchain"],
        "source_files_verified": count,
    }
    bundle.write_json(args.directory / RECEIPT, report)
    with open(os.environ["GITHUB_OUTPUT"], "a", encoding="utf-8") as output:
        output.write("archive_sha256=" + report["archive_sha256"] + "\n")
    print(json.dumps(report, indent=2))


def run_runtime(args):
    app = args.directory / APP_NAME
    output = args.output
    need(not output.is_relative_to(app), "runtime reports must be outside the app")
    need(not output.exists(), "runtime report directory already exists")
    output.mkdir(parents=True)
    report = {"status": "NOT PASSED", "source_sha": args.source_sha, "run_id": args.run_id,
              "stage": "identity", "not_tested": ["Finder", "GUI/TCC", "Gatekeeper", "Intel"]}
    before = None
    try:
        need(args.allow_https, "runtime requires explicit --allow-https")
        verify_checkout(args.source_sha)
        need(sys.flags.isolated and sys.dont_write_bytecode, "bundled runtime requires -I -B")
        need(Path(sys.executable).resolve() == (app / "Contents/Resources/python/bin/python3").resolve(),
             "host Python cannot run runtime validation")
        report["runtime"] = environment_record(args.os_major, args.xcode)
        receipt, manifest, count = verify_receipt(
            app, args.directory, args.source_sha, args.run_id, args.archive_sha256)
        before = tree_digest(app)
        report.update(archive_sha256=receipt["archive_sha256"], tree_sha256=before,
                      producer=receipt["producer"], producer_toolchain=manifest["toolchain"],
                      producer_attempt=receipt["producer_attempt"], source_files_verified=count)
        report["stage"] = "audit-before"
        audited = bundle.audit_bundle(app, bundle.load_lock(), os.environ.copy())
        producer_audit = json.loads((args.directory / "bundle-audit.json").read_bytes())
        need(audited["inventory"] == producer_audit["inventory"], "producer/runtime inventory mismatch")
        report["inventory_entries"] = len(audited["inventory"])
        report["macho_count"] = len(audited["checks"])
        report["resource_hashes"] = len(manifest["resource_hashes"])
        for suite in ("process", "core"):
            report["stage"] = suite
            target = output / (suite + ".json")
            subprocess.run([sys.executable, "-I", "-B", str(ROOT / "tools/macos/bundled_tests.py"),
                            "--app", str(app), "--suite", suite, "--report", str(target)], check=True)
            report[suite] = json.loads(target.read_bytes())
            need(report[suite]["status"] == "passed", "bundled suite did not pass")
        report["stage"] = "https-smoke"
        need(smoke.run_smoke(app, output) == 0, "bundled smoke failed")
        report["smoke"] = json.loads((output / "helper-smoke.json").read_bytes())
        report["stage"] = "model-free-performance"
        asset = os.environ.get("CC_TRANSLATE_DICTIONARY_TEST_ASSET")
        need(bool(asset), "performance requires the explicit pinned dictionary fixture")
        report["performance"] = performance.run_measurements(app, Path(asset))
        bundle.write_json(output / "performance.json", report["performance"])
        report["stage"] = "integration-harness"
        harness = output / "harness"
        report["harness_source_sha256"] = prepare_harness(harness)
        environment = os.environ.copy()
        environment["CC_TRANSLATE_APP"] = str(app)
        report["stage"] = "clipboard-producer-build"
        run_swift_harness(harness, environment, ["build", "--product", "CCClipboardTestProducer"],
                          "clipboard producer fixture build failed")
        for stage, test_class, key, validate, error in (
                ("integration-harness", "HelperIntegrationTests", "integration", integration_result,
                 "integration harness compile/run failed"),
                ("about-resource-harness", "BundledAboutTests", "about", about_result,
                 "bundled About reader harness failed"),
                ("local-ocr-harness", "LocalOCRTests", "local_ocr", local_ocr_result,
                 "local OCR harness failed"),
                ("fresh-copy-harness", "FreshCopyClipboardTests", "fresh_copy", fresh_copy_result,
                 "fresh copy clipboard harness failed"),
                ("clipboard-worker-harness", "ClipboardProcessTests", "clipboard_worker", clipboard_process_result,
                 "clipboard worker harness failed"),
                ("plain-text-paste-harness", "PlainTextPasteTests", "plain_text_paste", plain_text_paste_result,
                 "plain text paste harness failed")):
            report["stage"] = stage
            text = run_swift_harness(harness, environment, ["test", "--filter", test_class], error)
            report[key] = validate(text)
        report["stage"] = "audit-after"
        after = bundle.audit_bundle(app, bundle.load_lock(), os.environ.copy())
        need(after["inventory"] == audited["inventory"] and tree_digest(app) == before,
             "runtime tests modified app bytes/modes/links")
        report.update(status="passed", stage="complete", bundle_unchanged=True)
    finally:
        if before is not None:
            report["bundle_unchanged"] = tree_digest(app) == before
        bundle.write_json(output / "runtime-report.json", report)
    need(report.get("bundle_unchanged") is True, "runtime final app snapshot changed")


def main(argv=None):
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("command", choices=("seal", "run", "integration", "dictionary-product", "about"))
    parser.add_argument("--directory", type=Path, required=True)
    parser.add_argument("--source-sha", required=True)
    parser.add_argument("--run-id", required=True)
    parser.add_argument("--archive-sha256")
    parser.add_argument("--output", type=Path)
    parser.add_argument("--os-major", type=int, choices=(14, 26))
    parser.add_argument("--xcode", choices=("16.2", "26.6"))
    parser.add_argument("--allow-https", action="store_true")
    args = parser.parse_args(argv)
    args.directory = args.directory.resolve()
    if args.command == "run":
        if any(value is None for value in (args.output, args.archive_sha256, args.os_major, args.xcode)):
            parser.error("runtime arguments required")
        args.output = args.output.resolve()
    try:
        if args.command == "seal":
            seal(args)
        elif args.command == "integration":
            verify_integration(args)
        elif args.command == "dictionary-product":
            verify_dictionary_product(args)
        elif args.command == "about":
            verify_about(args)
        else:
            run_runtime(args)
    except (bundle.BundleError, OSError, ValueError, KeyError, zipfile.BadZipFile,
            subprocess.SubprocessError) as error:
        detail = str(error) if isinstance(error, bundle.BundleError) else type(error).__name__
        print("BLOCKED: runtime validation:", detail, file=sys.stderr)
        return 1
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
