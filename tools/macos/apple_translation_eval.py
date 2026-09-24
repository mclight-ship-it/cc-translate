"""Real Apple Translation component evaluation, isolated from the product.

Requires an arm64 Mac, macOS 15+, Xcode 16.4+, and a logged-in GUI session.
Uses the macOS 15 API's system default; no SDK 26 strategy selection is made.
No accounts, paid model calls, product imports, or simulated translations.
The quality decision is manual; successful execution is not a quality score.
"""

from __future__ import annotations

import argparse
import hashlib
import json
import math
import os
from pathlib import Path
import platform
import plistlib
import signal
import subprocess
import sys
import time
import uuid


HERE = Path(__file__).resolve().parent
SOURCE = HERE / "apple_translation_eval" / "Harness.swift"
SCHEMA = 1
APP_NAME = "AppleTranslationEval"
PAIR_IDS = ("en-zh", "zh-en")
STATUS_VALUES = {"installed", "supported", "unsupported", "unknown"}
LIMITS = {
    "build": (30, 600), "run": (30, 1800), "prepare": (5, 900),
    "request": (1, 120), "availability": (1, 60), "cancel": (1, 30),
}


class EvalError(ValueError):
    pass


def need(condition, message):
    if not condition:
        raise EvalError(message)


def corpus():
    """Original public-domain-style test text; references are NOT exact-match keys."""
    examples = [
        ("negation", "Do not restart the device unless the indicator is green. The backup is not complete.",
         "除非指示灯为绿色，否则不要重启设备。备份尚未完成。",
         ["negation"], ["Preserve both negations and the unless condition; do not imply completion."]),
        ("numbers", "Order 104 contains 1,250 items, not 1,520. The total is $37.50, including a 7.5% fee.",
         "104号订单包含1,250件商品，而不是1,520件。总额为37.50美元，其中包含7.5%的费用。",
         ["numbers", "negation"], ["Preserve identifiers, decimal values, currency, fee inclusion, and the corrected quantity."]),
        ("units", "Keep the sample at −5 °C for 2.5 hours, then move it 30 cm. Its mass is 0.75 kg.",
         "将样品在−5摄氏度下保存2.5小时，然后移动30厘米。它的质量为0.75千克。",
         ["numbers", "units"], ["Preserve the minus sign, durations, length and mass; do not convert units incorrectly."]),
        ("terminology", "The cache invalidation policy must preserve idempotency. Retry with exponential backoff after a timeout.",
         "缓存失效策略必须保持幂等性。超时后，采用指数退避策略重试。",
         ["terminology"], ["Check standard technical meanings of cache invalidation, idempotency and exponential backoff."]),
        ("mixed", "请在 Settings 中开启 Dark Mode，但不要修改 API_BASE_URL 或 v2.4.1。",
         "In Settings, enable Dark Mode, but do not change API_BASE_URL or v2.4.1.",
         ["mixed", "terminology", "negation"], ["Keep the UI labels understandable and the identifier and version unchanged."]),
        ("list", "Checklist:\n1. Save the draft.\n2. Close the preview, not the editor.\n3. Ask Mei to review it.",
         "检查清单：\n1. 保存草稿。\n2. 关闭预览，而不是编辑器。\n3. 请梅检查草稿。",
         ["format", "negation"], ["Preserve ordered steps, line boundaries, distinction between editor and preview, and the reviewer."]),
        ("markdown", "| Item | Count |\n| --- | ---: |\n| Blue cup | 12 |\n| Red cup | 3 |",
         "| 物品 | 数量 |\n| --- | ---: |\n| 蓝色杯子 | 12 |\n| 红色杯子 | 3 |",
         ["format", "numbers"], ["Check table structure, alignment markers, row association and quantities."]),
        ("code", "Keep `retry_count = 3` unchanged. Print the literal string `hello_world`; do not translate code identifiers.",
         "保持`retry_count = 3`不变。打印字面字符串`hello_world`；不要翻译代码标识符。",
         ["code", "format", "negation"], ["Preserve code tokens and backticks exactly; translate the explanatory prose only."]),
        ("idiom", "We are not out of the woods yet, but the new plan is a step in the right direction.",
         "我们还没有摆脱困境，但新计划是朝正确方向迈出的一步。",
         ["idiom", "negation"], ["Render the figurative meaning, not a literal forest; retain the cautious improvement."]),
        ("ambiguity", "The seal on the jar is damaged. Please replace the seal, not the entire jar.",
         "罐子的密封圈损坏了。请更换密封圈，而不是整个罐子。",
         ["terminology", "negation"], ["Resolve seal using the jar context, not the animal; retain the repair scope."]),
        ("dates", "The workshop is on 2031-04-05 at 09:30 UTC. Registration closes 48 hours earlier.",
         "研讨会于2031年4月5日09:30（UTC）举行。报名在48小时前截止。",
         ["numbers", "units"], ["Preserve the unambiguous date, time zone, clock time and relative deadline."]),
        ("question", "Could you tell Lin whether the meeting was moved to Friday? I have not confirmed the change.",
         "你能告诉林会议是否改到星期五了吗？我尚未确认这一变动。",
         ["negation", "ambiguity"], ["Preserve the question and uncertainty; do not assert that the meeting moved."]),
        ("paragraphs", "The west entrance is closed for repairs.\n\nVisitors may use the east entrance until 18:00. Staff should use the north door.",
         "西侧入口因维修关闭。\n\n访客可在18:00之前使用东侧入口。工作人员应使用北门。",
         ["format", "numbers"], ["Preserve paragraphs, compass directions, separate audiences and the time restriction."]),
    ]
    # The mixed-language example is intentionally Chinese-first in the source list.
    examples[4] = (examples[4][0], examples[4][2], examples[4][1], *examples[4][3:])
    en_paragraphs = [
        "The neighborhood library will test a new reservation system for six weeks. "
        "The trial begins on 2031-09-08 and ends on 2031-10-19. The library will remain open during the trial. "
        "The children's reading room will keep its usual opening hours, and no existing library cards need to be replaced.",
        "Readers may reserve up to three books at a time. A reservation expires after 48 hours, "
        "but an existing loan does not expire when a reservation does. No late fees will be added during the first week.",
        "The kiosk displays both the book title and the shelf number. If the shelf number is missing, "
        "ask a librarian rather than assuming the book is unavailable. A printed receipt is optional, not required. "
        "The receipt does not contain a home address. Keep the reservation number until the book is collected.",
        "On Tuesday mornings, volunteers will explain the new system in a quiet room near the east entrance. "
        "The session lasts 45 minutes. People who prefer paper forms may continue to use them throughout the trial.",
        "At the end of the trial, staff will compare waiting times and review anonymous comments. "
        "Faster service alone will not determine whether the system stays. The final report must describe "
        "accessibility problems, failed reservations, and the experience of readers who did not use the kiosk.",
    ]
    zh_paragraphs = [
        "社区图书馆将试用新的预约系统，为期六周。试用期从2031年9月8日开始，到2031年10月19日结束。图书馆在试用期间将照常开放。"
        "儿童阅览室将保持原来的开放时间，现有借书证均无需更换。",
        "读者每次最多可预约三本书。预约在48小时后失效，但现有借阅不会随预约一起到期。第一周不会收取逾期费用。",
        "自助终端会同时显示书名和书架编号。如果缺少书架编号，请咨询图书管理员，不要据此认定图书不可借阅。打印凭条是可选的，并非必需。"
        "凭条不包含家庭地址。请保留预约编号，直到取到书。",
        "每周二上午，志愿者将在东侧入口附近的一间安静房间介绍新系统。每次讲解持续45分钟。偏好纸质表格的读者在整个试用期间仍可使用纸质表格。",
        "试用结束后，工作人员将比较等候时间并审阅匿名意见。服务速度更快并不是决定保留系统的唯一标准。最终报告必须说明无障碍问题、预约失败情况，以及未使用自助终端的读者的体验。",
    ]
    examples.append(("long", "\n\n".join(en_paragraphs), "\n\n".join(zh_paragraphs),
                     ["long", "format", "numbers", "negation"],
                     ["Translate the full text, not a summary; preserve all five paragraphs and their qualifications.",
                      "Check the six-week interval, three-book limit, 48-hour expiry, first-week fee exception and 45-minute session.",
                      "Retain accessibility and non-kiosk readers in the final decision criteria."]))
    cases = []
    for name, english, chinese, tags, rubric in examples:
        for pair, source, reference in (("en-zh", english, chinese), ("zh-en", chinese, english)):
            cases.append({"id": f"{pair}-{name}", "pair": pair, "source": source,
                          "reference": reference, "tags": tags, "rubric": rubric})
    return cases


def manifest():
    cases = corpus()
    serialized = json.dumps(cases, ensure_ascii=False, sort_keys=True, separators=(",", ":"))
    return {"version": 1, "origin": "original public synthetic text; no user documents",
            "sha256": hashlib.sha256(serialized.encode("utf-8")).hexdigest(),
            "purpose": "faithful text translation only; long-text cases do not recommend disabling summary-first",
            "code_scope": "translate prose while preserving literal code; not code explanation or generation",
            "assessment": "manual semantic assessment against rubric; reference is not an exact-match target",
            "cases": cases}


def write_json(path, value):
    path = Path(path)
    stage = path.with_suffix(path.suffix + ".writing")
    stage.write_text(json.dumps(value, ensure_ascii=False, indent=2, allow_nan=False) + "\n",
                     encoding="utf-8")
    stage.replace(path)


def new_report(run_id):
    return {
        "summary": {"status": "not_started", "blocked_condition": None, "completed_outputs": 0,
                    "quality_assessment": "not_assessed", "quality_score": None,
                    "production_flow": "unchanged; summary stays first",
                    "code_explanation": "out of scope; Apple is not a replacement",
                    "scope": "isolated Apple Translation component, not a production pipeline comparison"},
        "schema_version": SCHEMA, "run_id": run_id, "corpus": manifest(),
        "metadata": {"host_system": platform.system(), "host_architecture": platform.machine(),
                     "host_os": platform.mac_ver()[0], "strategy": "system_default",
                     "strategy_control": "macOS 15 API; not an explicit SDK 26 lowLatency/highFidelity selection",
                     "model_coldness": "unknown; first process/session call is not proof of a cold OS model",
                     "latency_scope": "observed API wall time plus small harness overhead, including any latent model work; no zero-time assumptions",
                     "readiness_diagnosis": "a prepareTranslation deadline alone cannot distinguish unaccepted consent from a stalled download",
                     "download_cleanup": "only the owned app is stopped; OS-managed language downloads may continue"},
        "commands": [], "runtime": None, "diagnostics": [],
    }


def validate_report(report, *, require_complete=False):
    need(report.get("schema_version") == SCHEMA, "invalid schema_version")
    need(isinstance(report.get("run_id"), str) and report["run_id"], "missing run identity")
    summary = report.get("summary", {})
    need(summary.get("quality_assessment") == "not_assessed" and summary.get("quality_score") is None,
         "the harness must not claim translation quality")
    need(summary.get("production_flow") == "unchanged; summary stays first", "production-flow declaration changed")
    need(summary.get("code_explanation") == "out of scope; Apple is not a replacement",
         "code-explanation scope declaration changed")
    need(report.get("corpus") == manifest(), "corpus or rubric mismatch")
    runtime = report.get("runtime")
    if runtime is None:
        need(not require_complete and summary.get("status") != "completed", "missing real app report")
        return
    need(runtime.get("run_id") == report["run_id"], "app run identity mismatch")
    need(runtime.get("engine") == "Apple.Translation.TranslationSession", "not the real Apple engine")
    need(runtime.get("strategy") == "system_default", "unexpected translation strategy")
    need(isinstance(runtime.get("os_version"), str) and runtime["os_version"], "missing actual app OS")
    cases = {row["id"]: row for row in corpus()}
    seen = set()
    pairs = runtime.get("pairs", [])
    need(isinstance(pairs, list), "invalid pairs")
    for pair in pairs:
        need(pair.get("id") in PAIR_IDS and pair.get("before") in STATUS_VALUES, "invalid pair availability")
        if "after" in pair:
            need(pair["after"] in STATUS_VALUES, "invalid final availability")
        for key in ("prepare_ms", "availability_before_ms", "availability_after_ms"):
            if key in pair:
                timing(pair[key])
    outputs = runtime.get("outputs", [])
    need(isinstance(outputs, list), "invalid outputs")
    for row in outputs:
        case = cases.get(row.get("case_id"))
        need(case is not None and row.get("pair") == case["pair"], "unknown output case")
        need(type(row.get("pass")) is int and row["pass"] in (0, 1), "invalid pass")
        key = (row["case_id"], row["pass"])
        need(key not in seen, "duplicate output")
        seen.add(key)
        need(row.get("phase") in ("first_call_session", "retained_session"), "invalid call phase")
        need(row.get("status") in ("ok", "error"), "invalid output status")
        timing(row.get("elapsed_ms"))
        timing(row.get("since_app_start_ms"))
        need(isinstance(row.get("target_text"), str), "missing actual output text")
        need(row.get("source_text") == case["source"], "API source text differs from corpus")
        if row["status"] == "error":
            need(isinstance(row.get("error"), dict), "missing error diagnostics")
    cancellations = runtime.get("cancellations", [])
    need(isinstance(cancellations, list), "invalid cancellations")
    for row in cancellations:
        need(row.get("pair") in PAIR_IDS and row.get("mechanism") == "swift_task_cancel",
             "invalid cancellation probe")
        need(row.get("outcome") in ("cancelled", "error_before_cancel", "error_after_cancel", "completed_before_cancel",
                                    "completed_after_cancel"), "invalid cancellation outcome")
        need(type(row.get("cancel_requested")) is bool, "missing cancellation state")
        need(isinstance(row.get("target_text"), str), "missing cancellation output")
        timing(row.get("elapsed_ms"))
        if row["cancel_requested"]:
            timing(row.get("cancel_requested_ms"))
        need((row["outcome"] not in ("cancelled", "error_after_cancel", "completed_after_cancel")
              or row["cancel_requested"]), "claimed cancellation without a cancellation request")
    if require_complete or summary.get("status") == "completed":
        need(runtime.get("status") == "completed", "app did not complete")
        metadata = report.get("metadata", {})
        need(all(isinstance(metadata.get(key), str) and metadata[key]
                 for key in ("sw_vers", "xcode", "sdk_version", "swift", "swift_source_sha256")),
             "missing measured OS/build metadata")
        need(metadata.get("host_system") == "Darwin" and metadata.get("host_architecture") == "arm64",
             "complete evaluation must run on a real arm64 Mac")
        need(seen == {(case["id"], repeat) for case in corpus() for repeat in (0, 1)},
             "incomplete corpus coverage")
        need(all(row["status"] == "ok" for row in outputs), "translation errors are not a complete run")
        need(len(pairs) == 2 and {row["id"] for row in pairs} == set(PAIR_IDS), "missing pair observations")
        need(all(row.get("after") == "installed" and row.get("ready_before_requests") == "installed"
                 and "prepare_ms" in row for row in pairs),
             "language readiness not observed")
        need(isinstance(runtime.get("supported_languages"), list) and runtime["supported_languages"],
             "missing actual supported language query")
        need(len(cancellations) == 2 and {row["pair"] for row in cancellations} == set(PAIR_IDS),
             "missing cancellation probes")
        for pair in PAIR_IDS:
            rows = [row for row in outputs if row["pair"] == pair]
            expected_order = [(case["id"], repeat) for repeat in (0, 1)
                              for case in corpus() if case["pair"] == pair]
            need([(row["case_id"], row["pass"]) for row in rows] == expected_order,
                 "corpus execution order invalid")
            need(rows[0]["phase"] == "first_call_session" and
                 all(row["phase"] == "retained_session" for row in rows[1:]),
                 "first/retained session ordering invalid")
        need(summary.get("completed_outputs") == len(outputs), "summary output count mismatch")
    elif summary.get("status") in ("availability_only", "prepared"):
        need(runtime.get("status") == summary["status"], "readiness phase did not complete")
        need(outputs == [] and cancellations == [], "readiness-only mode must not translate")
        need(len(pairs) == 2 and {row["id"] for row in pairs} == set(PAIR_IDS),
             "missing directed availability observations")
        need(isinstance(runtime.get("supported_languages"), list) and runtime["supported_languages"],
             "missing supported language query")
        if summary["status"] == "prepared":
            need(all(row.get("after") == "installed" and row.get("ready_before_requests") == "installed"
                     and "prepare_ms" in row for row in pairs), "preparation readiness not observed")


def timing(value):
    need(type(value) in (int, float) and math.isfinite(value) and value >= 0, "invalid measured timing")
    return value


def command(argv, timeout, report, *, env=None, log=None):
    started = time.monotonic()
    item = {"argv": [str(value) for value in argv], "timeout_seconds": timeout}
    report["commands"].append(item)
    try:
        result = subprocess.run(item["argv"], capture_output=True, text=True, timeout=timeout,
                                env=env, check=False)
        item.update(returncode=result.returncode, timed_out=False)
        text = result.stdout + result.stderr
        item["output"] = text[-12000:]
        if log:
            Path(log).write_text(text, encoding="utf-8")
        need(result.returncode == 0, f"command_failed:{Path(argv[0]).name}:{result.returncode}")
        return result.stdout.strip()
    except subprocess.TimeoutExpired as error:
        item.update(timed_out=True, returncode=None)
        text = "".join(value.decode("utf-8", errors="replace") if isinstance(value, bytes) else value or ""
                       for value in (error.stdout, error.stderr))
        item["output"] = text[-12000:]
        if log:
            Path(log).write_text(text, encoding="utf-8")
        raise EvalError(f"command_timeout:{Path(argv[0]).name}") from None
    finally:
        item["elapsed_ms"] = (time.monotonic() - started) * 1000


def build_commands(output, sdk):
    app = output / f"{APP_NAME}.app"
    binary = app / "Contents" / "MacOS" / APP_NAME
    return app, binary, [
        ["/usr/bin/xcrun", "--sdk", "macosx", "swiftc", "-parse-as-library", "-O",
         "-swift-version", "5", "-target", "arm64-apple-macos15.0", "-sdk", sdk,
         "-module-cache-path", str(output / "module-cache"), str(SOURCE),
         "-framework", "SwiftUI", "-framework", "AppKit", "-framework", "Translation",
         "-framework", "CoreGraphics", "-o", str(binary)],
        ["/usr/bin/codesign", "--force", "--sign", "-", str(app)],
    ]


def build_app(output, args, report, env):
    need(sys.platform == "darwin" and platform.machine() == "arm64", "requires_macos_arm64")
    need(int(platform.mac_ver()[0].split(".")[0]) >= 15, "requires_macos_15_or_newer")
    report["metadata"]["sw_vers"] = command(["/usr/bin/sw_vers"], 10, report, env=env)
    report["metadata"]["xcode"] = command(["/usr/bin/xcodebuild", "-version"], 20, report, env=env)
    report["metadata"]["sdk_version"] = command(
        ["/usr/bin/xcrun", "--sdk", "macosx", "--show-sdk-version"], 10, report, env=env)
    sdk = command(["/usr/bin/xcrun", "--sdk", "macosx", "--show-sdk-path"], 10, report, env=env)
    report["metadata"]["swift"] = command(["/usr/bin/xcrun", "swiftc", "--version"], 20, report, env=env)
    need(int(report["metadata"]["sdk_version"].split(".")[0]) >= 15, "requires_macos_15_sdk")
    app, binary, commands = build_commands(output, sdk)
    binary.parent.mkdir(parents=True)
    info = {
        "CFBundleExecutable": APP_NAME, "CFBundleIdentifier": "org.cctranslate.AppleTranslationEval",
        "CFBundleName": APP_NAME, "CFBundleDisplayName": "Apple Translation Evaluation",
        "CFBundlePackageType": "APPL", "CFBundleVersion": "1", "CFBundleShortVersionString": "1.0",
        "LSMinimumSystemVersion": "15.0", "NSHighResolutionCapable": True, "LSUIElement": False,
    }
    (app / "Contents" / "Info.plist").write_bytes(plistlib.dumps(info))
    for index, argv in enumerate(commands):
        command(argv, args.build_timeout, report, env=env, log=output / f"build-{index}.log")
    report["metadata"]["swift_source_sha256"] = hashlib.sha256(SOURCE.read_bytes()).hexdigest()
    return binary


def load_runtime(path, run_id):
    if not path.is_file():
        return None
    need(path.stat().st_size <= 8_000_000, "oversized_app_state")
    result = json.loads(path.read_text(encoding="utf-8"))
    need(isinstance(result, dict) and result.get("run_id") == run_id, "app_state_identity_mismatch")
    return result


def download_script(pid):
    """Follow only this owned app's language-download sheet, including table rows."""
    need(type(pid) is int and pid > 1, "invalid owned pid")
    return f'''
tell application "System Events"
    set owned to first application process whose unix id is {pid}
    tell owned
        if not (exists window 1) then return "no_window"
        set targets to {{"Download", "Download Languages", "Download and Translate", "下载", "下载语言", "下载并翻译"}}
        set seenButtons to {{}}
        set seenRoles to {{}}
        repeat with ownedWindow in windows
                set nodes to entire contents of ownedWindow
                if (count nodes) > 512 then return "download_sheet_too_large"
                set doneButton to missing value
                set languageSheet to false
                repeat with node in nodes
                    set nodeRole to role of node
                    set seenRoles to seenRoles & {{nodeRole}}
                    if nodeRole is "AXStaticText" then
                        set textValue to value of node
                        if textValue is "Download Languages to Translate" then set languageSheet to true
                    end if
                    if nodeRole is "AXButton" then
                        set labels to {{name of node, description of node, value of node}}
                        if description of node is "button" then
                            set childNodes to get entire contents of node
                            repeat with labelNode in childNodes
                                if role of labelNode is "AXStaticText" then set labels to labels & {{value of labelNode}}
                            end repeat
                        end if
                        repeat with candidateLabel in labels
                            set labelText to contents of candidateLabel
                            if labelText is not missing value then
                                set seenButtons to seenButtons & {{labelText as text}}
                                if targets contains labelText then
                                    if enabled of node then
                                        click node
                                        return "clicked_download"
                                    end if
                                end if
                                if labelText is "Done" and enabled of node then set doneButton to contents of node
                            end if
                        end repeat
                    end if
                end repeat
                if languageSheet and doneButton is not missing value then
                    click doneButton
                    return "closed_language_download_sheet"
                end if
        end repeat
        return "download_button_not_found; buttons=" & (seenButtons as text) & "; roles=" & (seenRoles as text)
    end tell
end tell
'''


def retryable_automation_error(error, output):
    return (isinstance(error, EvalError) and (
        str(error) == "command_timeout:osascript"
        or any(code in output for code in ("(-1719)", "(-10000)"))))


def capture_window(output, runtime, report):
    window = (runtime or {}).get("window_id")
    if type(window) is not int or window <= 0:
        report["diagnostics"].append({"screenshot": "unavailable_no_owned_window_id"})
        return
    try:
        index = sum(Path(value).suffix == ".png"
                    for entry in report["commands"] for value in entry["argv"])
        image = output / f"app-window-{index:02d}.png"
        command(["/usr/sbin/screencapture", "-x", "-l", str(window), str(image)],
                5, report)
    except (EvalError, OSError) as error:
        report["diagnostics"].append({"screenshot": str(error)})


def stop_owned_process(process):
    """The app is our direct child; no process-name or system-service termination."""
    if process.poll() is None:
        process.terminate()
        try:
            process.wait(timeout=3)
        except subprocess.TimeoutExpired:
            process.kill()
            process.wait(timeout=3)


def run_app(binary, output, args, report, env):
    config_path, state_path = output / "input.json", output / "app-state.json"
    write_json(config_path, {
        "run_id": report["run_id"], "state_path": str(state_path), "cases": corpus(), "mode": args.mode,
        "prepare_timeout": args.prepare_timeout, "request_timeout": args.request_timeout,
        "availability_timeout": args.availability_timeout, "cancel_timeout": args.cancel_timeout,
        "cancel_delay_ms": args.cancel_delay_ms,
    })
    argv = [str(binary), str(config_path)]
    item = {"argv": argv, "timeout_seconds": args.run_timeout, "launch": "direct_owned_app_executable"}
    report["commands"].append(item)
    started = time.monotonic()
    process = None
    runtime = None
    last_automation = -float("inf")
    automation_disabled = False
    automation_failures = 0
    screenshots = set()
    previous_phase = None
    phase_started = 0
    previous_automation = None
    try:
        with (output / "app.stdout.log").open("w", encoding="utf-8") as stdout, \
                (output / "app.stderr.log").open("w", encoding="utf-8") as stderr:
            process = subprocess.Popen(argv, stdout=stdout, stderr=stderr, env=env, start_new_session=True)
            item["pid"] = process.pid
            while process.poll() is None:
                runtime = load_runtime(state_path, report["run_id"])
                elapsed = time.monotonic() - started
                current_phase = ((runtime or {}).get("phase"), (runtime or {}).get("active_pair"))
                if current_phase != previous_phase:
                    previous_phase, phase_started = current_phase, elapsed
                    print(json.dumps({"phase": current_phase, "elapsed_seconds": round(elapsed, 1)}), flush=True)
                if elapsed >= args.availability_timeout and (
                        runtime is None or runtime.get("phase") == "initializing"):
                    item["timed_out"] = True
                    report["summary"]["blocked_condition"] = (
                        "app_did_not_publish_initial_checkpoint:see_app_stderr_log" if runtime is None
                        else "app_launch_task_not_started:gui_or_app_lifecycle_unavailable")
                    break
                if (runtime and args.screenshot_on_block and runtime.get("phase") in (
                        "preparing_languages", "waiting_language_install", "blocked")
                        and (runtime.get("phase") == "blocked" or elapsed - phase_started >= 5)):
                    key = (runtime.get("phase"), runtime.get("active_pair"))
                    if key not in screenshots:
                        capture_window(output, runtime, report)
                        screenshots.add(key)
                if elapsed >= args.run_timeout:
                    item["timed_out"] = True
                    phase = (runtime or {}).get("phase", "before_first_app_checkpoint")
                    report["summary"]["blocked_condition"] = f"overall_deadline:{phase}"
                    if args.screenshot_on_block:
                        capture_window(output, runtime, report)
                    break
                if (args.accept_download and not automation_disabled and runtime
                        and runtime.get("phase") in ("preparing_languages", "waiting_language_install")
                        and elapsed - phase_started >= 3
                        and elapsed - last_automation >= 3):
                    last_automation = elapsed
                    try:
                        answer = command(["/usr/bin/osascript", "-e", download_script(process.pid)],
                                         min(10, max(0.1, args.run_timeout - elapsed)), report, env=env)
                        report["diagnostics"].append({"download_ui": answer})
                        if answer != previous_automation:
                            previous_automation = answer
                            print(json.dumps({"download_ui": answer}, ensure_ascii=False), flush=True)
                    except (EvalError, OSError) as error:
                        automation_failures += 1
                        transient = (automation_failures < 3 and retryable_automation_error(
                            error, report["commands"][-1].get("output", "")))
                        automation_disabled = not transient
                        report["diagnostics"].append({
                            "download_ui": ("automation_transient_error; retrying_owned_ui" if transient else
                                            "automation_unavailable; grant normal Accessibility/Automation permission or click manually"),
                            "error": str(error)})
                time.sleep(0.2)
            item.setdefault("timed_out", False)
    finally:
        if process is not None:
            stop_owned_process(process)
            item["returncode"] = process.returncode
        item["elapsed_ms"] = (time.monotonic() - started) * 1000
        report["runtime"] = load_runtime(state_path, report["run_id"])
    runtime = report["runtime"]
    need(runtime is not None, report["summary"]["blocked_condition"]
         or "app_exited_without_checkpoint:see_app_stderr_log")
    expected_status = {"evaluate": "completed", "availability_only": "availability_only",
                       "prepare_only": "prepared"}[args.mode]
    if runtime.get("status") != expected_status or item["returncode"] != 0 or item["timed_out"]:
        report["summary"]["status"] = "blocked" if runtime.get("status") != "failed" else "failed"
        report["summary"]["blocked_condition"] = (
            report["summary"]["blocked_condition"] or runtime.get("blocked_condition")
            or f"app_exit:{item['returncode']}:{runtime.get('phase')}")
    else:
        report["summary"]["status"] = expected_status
    report["summary"]["completed_outputs"] = sum(
        row.get("status") == "ok" for row in runtime.get("outputs", []))


def parser():
    result = argparse.ArgumentParser(description=__doc__)
    result.add_argument("--output", type=Path, required=True, help="new, empty artifact directory inside this repository")
    result.add_argument("--developer-dir", type=Path, help="Xcode Contents/Developer; otherwise use active Xcode")
    modes = result.add_mutually_exclusive_group()
    modes.add_argument("--corpus-only", action="store_true", help="write only the public corpus, without running a model")
    modes.add_argument("--availability-only", dest="mode", action="store_const", const="availability_only",
                       help="read-only supported-language and directed-pair queries; no preparation or translations")
    modes.add_argument("--prepare-only", dest="mode", action="store_const", const="prepare_only",
                       help="query and legitimately prepare both pairs, then stop without any translation requests")
    result.set_defaults(mode="evaluate")
    result.add_argument("--accept-download", action="store_true",
                        help="opt in to exact Download-button automation in this app only; needs legitimate GUI permissions")
    result.add_argument("--screenshot-on-block", action="store_true",
                        help="capture only this app window, never the desktop; screen-recording permission may be required")
    for name, default in (("build", 300), ("run", 900), ("prepare", 300),
                          ("request", 45), ("availability", 20), ("cancel", 10)):
        result.add_argument(f"--{name}-timeout", type=int, default=default, metavar="SECONDS")
    result.add_argument("--cancel-delay-ms", type=int, default=20)
    return result


def validate_args(args):
    for name, (minimum, maximum) in LIMITS.items():
        value = getattr(args, name + "_timeout")
        need(type(value) is int and minimum <= value <= maximum,
             f"{name}_timeout_must_be_{minimum}_to_{maximum}")
    need(type(args.cancel_delay_ms) is int and 0 <= args.cancel_delay_ms <= 1000,
         "cancel_delay_ms_must_be_0_to_1000")
    need(args.cancel_delay_ms < args.cancel_timeout * 1000, "cancel_delay_must_precede_probe_deadline")
    output = args.output.resolve()
    need(output.is_relative_to(HERE.parents[1]), "output_must_be_inside_repository")
    need(not output.exists() or (output.is_dir() and not any(output.iterdir())),
         "output_directory_must_be_new_or_empty")
    return output


def main(argv=None):
    args = parser().parse_args(argv)
    try:
        output = validate_args(args)
    except EvalError as error:
        print(str(error), file=sys.stderr)
        return 2
    output.mkdir(parents=True, exist_ok=True)
    report = new_report(str(uuid.uuid4()))
    report["metadata"]["mode"] = args.mode
    write_json(output / "corpus.json", report["corpus"])
    if args.corpus_only:
        report["summary"]["status"] = "corpus_only"
        write_json(output / "report.json", report)
        print(output / "report.json")
        return 0
    env = os.environ.copy()
    if args.developer_dir:
        env["DEVELOPER_DIR"] = str(args.developer_dir)
    # Do not redirect HOME: Apple manages its own legitimately installed language assets.
    env["CLANG_MODULE_CACHE_PATH"] = str(output / "module-cache")
    previous_term = signal.getsignal(signal.SIGTERM)

    def interrupted(number, _frame):
        raise InterruptedError(f"supervisor_signal:{number}")

    signal.signal(signal.SIGTERM, interrupted)
    try:
        report["summary"]["status"] = "building"
        write_json(output / "report.json", report)
        binary = build_app(output, args, report, env)
        report["summary"]["status"] = "running"
        write_json(output / "report.json", report)
        run_app(binary, output, args, report, env)
        validate_report(report)
    except (EvalError, OSError, ValueError, KeyboardInterrupt) as error:
        report["summary"]["status"] = "blocked" if report["runtime"] is None else "failed"
        report["summary"]["blocked_condition"] = str(error) or type(error).__name__
    finally:
        signal.signal(signal.SIGTERM, previous_term)
        if report["runtime"]:
            report["summary"]["completed_outputs"] = sum(
                isinstance(row, dict) and row.get("status") == "ok"
                for row in report["runtime"].get("outputs", []))
        write_json(output / "report.json", report)
    print(json.dumps(report["summary"], ensure_ascii=False))
    print(output / "report.json")
    return 0 if report["summary"]["status"] in ("completed", "availability_only", "prepared") else 2


if __name__ == "__main__":
    raise SystemExit(main())
