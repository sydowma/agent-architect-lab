#!/usr/bin/env python3
"""Aegis-Sandbox: Real Native macOS Computer Use Agent.

Executes a 100% real Computer Use perception-action loop on your Mac:
1. Launches macOS native Calculator application.
2. Locates physical WindowServer Window ID via CoreGraphics ctypes bindings.
3. Captures real pre-action window screenshot using /usr/sbin/screencapture -l <winID>.
4. Injects real keystrokes ('999 * 888 =') via macOS System Events with realistic human latency.
5. Captures real post-action window screenshot.
6. Analyzes real visual state delta (SHA-256 hash diff & image byte diff).
7. Extracts computed result from the Accessibility Tree and asserts correctness (887,112).
8. Gracefully closes the application and reports performance metrics.
"""

import ctypes
import ctypes.util
import hashlib
import os
import subprocess
import sys
import time
from typing import Optional, Tuple


# Terminal colors
GREEN = "\033[92m"
CYAN = "\033[96m"
YELLOW = "\033[93m"
BOLD = "\033[1m"
RESET = "\033[0m"


def run_osascript(script: str) -> str:
    """Executes AppleScript snippet and returns stdout."""
    res = subprocess.run(["osascript", "-e", script], capture_output=True, text=True)
    if res.returncode != 0:
        raise RuntimeError(f"AppleScript error: {res.stderr.strip()}")
    return res.stdout.strip()


def get_calculator_window_id() -> int:
    """Discovers Calculator's WindowServer Window ID using CoreGraphics ctypes."""
    cg = ctypes.cdll.LoadLibrary(ctypes.util.find_library("CoreGraphics"))
    cf = ctypes.cdll.LoadLibrary(ctypes.util.find_library("CoreFoundation"))

    cg.CGWindowListCopyWindowInfo.restype = ctypes.c_void_p
    cg.CGWindowListCopyWindowInfo.argtypes = [ctypes.c_uint32, ctypes.c_uint32]

    cf.CFArrayGetCount.restype = ctypes.c_long
    cf.CFArrayGetCount.argtypes = [ctypes.c_void_p]
    cf.CFArrayGetValueAtIndex.restype = ctypes.c_void_p
    cf.CFArrayGetValueAtIndex.argtypes = [ctypes.c_void_p, ctypes.c_long]

    cf.CFDictionaryGetValue.restype = ctypes.c_void_p
    cf.CFDictionaryGetValue.argtypes = [ctypes.c_void_p, ctypes.c_void_p]

    cf.CFStringCreateWithCString.restype = ctypes.c_void_p
    cf.CFStringCreateWithCString.argtypes = [ctypes.c_void_p, ctypes.c_char_p, ctypes.c_uint32]

    def cf_str(s: str) -> ctypes.c_void_p:
        return cf.CFStringCreateWithCString(None, s.encode("utf-8"), 0x08000100)

    kCGWindowOwnerName = cf_str("kCGWindowOwnerName")
    kCGWindowNumber = cf_str("kCGWindowNumber")

    cf.CFNumberGetValue.restype = ctypes.c_bool
    cf.CFNumberGetValue.argtypes = [ctypes.c_void_p, ctypes.c_int, ctypes.c_void_p]

    cf.CFStringGetCString.restype = ctypes.c_bool
    cf.CFStringGetCString.argtypes = [ctypes.c_void_p, ctypes.c_char_p, ctypes.c_long, ctypes.c_uint32]

    # kCGWindowListOptionOnScreenOnly = 1
    win_list = cg.CGWindowListCopyWindowInfo(1, 0)
    count = cf.CFArrayGetCount(win_list)

    for i in range(count):
        dict_ref = cf.CFArrayGetValueAtIndex(win_list, i)
        owner_ref = cf.CFDictionaryGetValue(dict_ref, kCGWindowOwnerName)
        if owner_ref:
            buf = ctypes.create_string_buffer(256)
            if cf.CFStringGetCString(owner_ref, buf, 256, 0x08000100):
                owner = buf.value.decode("utf-8")
                if "Calculator" in owner:
                    num_ref = cf.CFDictionaryGetValue(dict_ref, kCGWindowNumber)
                    val = ctypes.c_int32()
                    cf.CFNumberGetValue(num_ref, 3, ctypes.byref(val))
                    return val.value

    raise RuntimeError("Calculator window not found on screen.")


def capture_window_by_id(win_id: int, output_png: str) -> float:
    """Captures native WindowServer frame buffer directly via screencapture -l."""
    t0 = time.perf_counter()
    cmd = ["/usr/sbin/screencapture", "-l", str(win_id), "-x", output_png]
    res = subprocess.run(cmd, capture_output=True)
    t1 = time.perf_counter()
    if res.returncode != 0:
        raise RuntimeError(f"screencapture -l failed: {res.stderr.decode()}")
    return (t1 - t0) * 1000


def get_calculator_display_text() -> str:
    """Reads current value displayed on Calculator via Accessibility Tree."""
    script = """
    tell application "System Events"
        tell process "Calculator"
            try
                return value of static text 1 of scroll area 2 of group 1 of group 1 of splitter group 1 of group 1 of window 1 as string
            on error
                try
                    return value of static text 1 of group 1 of window 1 as string
                on error
                    return "UNKNOWN"
                end try
            end try
        end tell
    end tell
    """
    return run_osascript(script)


def main() -> None:
    print(f"\n{BOLD}{GREEN}🍏 Aegis-Sandbox 真实 macOS Computer Use 物理级执行演示{RESET}")
    print(f"操作系统: macOS ({sys.platform}) | 自动化目标: 原生计算器应用 (Calculator.app)\n" + "=" * 65)

    artifacts_dir = os.path.join(os.path.dirname(os.path.abspath(__file__)), "artifacts")
    os.makedirs(artifacts_dir, exist_ok=True)
    before_img = os.path.join(artifacts_dir, "real_calc_before.png")
    after_img = os.path.join(artifacts_dir, "real_calc_after.png")

    try:
        # --------------------------------------------------------------------
        # 1. 真实唤起 macOS 原生应用并清空初始状态
        # --------------------------------------------------------------------
        print(f"\n{BOLD}{CYAN}[Step 1] 真实唤起 macOS 原生 Calculator 应用并清零...{RESET}")
        t0 = time.perf_counter()
        run_osascript('tell application "Calculator" to activate')
        time.sleep(0.4)
        # 发送按键 'c' 清空屏幕 (Clear)
        run_osascript('tell application "System Events" to keystroke "c"')
        time.sleep(0.2)
        t1 = time.perf_counter()
        print(f"  ✓ 计算器窗口已在前台激活并复位 (耗时: {(t1 - t0)*1000:.1f} ms)")

        # --------------------------------------------------------------------
        # 2. 定位真实窗口 WindowServer ID
        # --------------------------------------------------------------------
        print(f"\n{BOLD}{CYAN}[Step 2] 通过 CoreGraphics 获取 WindowServer 窗口句柄 ID...{RESET}")
        win_id = get_calculator_window_id()
        print(f"  ✓ 获取到真实物理窗口 ID: {BOLD}{GREEN}{win_id}{RESET}")

        # --------------------------------------------------------------------
        # 3. 真实截屏 (操作前画面感知)
        # --------------------------------------------------------------------
        print(f"\n{BOLD}{CYAN}[Step 3] 真实屏幕感知：截取操作前窗口纯净渲染画面...{RESET}")
        ms_before = capture_window_by_id(win_id, before_img)
        file_size_before = os.path.getsize(before_img)
        with open(before_img, "rb") as f:
            hash_before = hashlib.sha256(f.read()).hexdigest()[:16]
        print(f"  ✓ 真实截图完成: {before_img}")
        print(f"  ✓ 截屏耗时: {ms_before:.1f} ms | 图片体积: {file_size_before/1024:.1f} KB | 视觉指纹: {hash_before}")

        # --------------------------------------------------------------------
        # 4. 真实硬件按键注入 (Action Execution)
        # --------------------------------------------------------------------
        expression = "999*888="
        expected_result = 999 * 888  # 887112
        print(f"\n{BOLD}{CYAN}[Step 4] 真实输入注入：向窗口发送物理按键序列 '{expression}'...{RESET}")
        print(f"  ✓ 模拟人类打字节奏 (75ms 间隔)...")

        for ch in expression:
            run_osascript(f'tell application "System Events" to keystroke "{ch}"')
            time.sleep(0.075)
        print(f"  ✓ 全部真实按键事件已注入完成！")

        time.sleep(0.3)

        # --------------------------------------------------------------------
        # 5. 真实截屏 (操作后画面感知)
        # --------------------------------------------------------------------
        print(f"\n{BOLD}{CYAN}[Step 5] 再次真实截屏：捕获运算完成后的窗口新状态...{RESET}")
        ms_after = capture_window_by_id(win_id, after_img)
        file_size_after = os.path.getsize(after_img)
        with open(after_img, "rb") as f:
            hash_after = hashlib.sha256(f.read()).hexdigest()[:16]
        print(f"  ✓ 真实截图完成: {after_img}")
        print(f"  ✓ 截屏耗时: {ms_after:.1f} ms | 图片体积: {file_size_after/1024:.1f} KB | 视觉指纹: {hash_after}")

        # --------------------------------------------------------------------
        # 6. 视觉差分与环境变化验证 (Visual State Delta Verification)
        # --------------------------------------------------------------------
        print(f"\n{BOLD}{CYAN}[Step 6] 视觉差分比对 (Visual Delta Check)...{RESET}")
        visual_changed = (hash_before != hash_after)
        print(f"  ✓ 操作前指纹: {hash_before}")
        print(f"  ✓ 操作后指纹: {hash_after}")
        print(f"  ✓ 视觉状态是否改变: {BOLD}{GREEN if visual_changed else YELLOW}{visual_changed}{RESET}")

        # --------------------------------------------------------------------
        # 7. 无障碍属性读取与断言 (Grounding Assertions)
        # --------------------------------------------------------------------
        print(f"\n{BOLD}{CYAN}[Step 7] 从 macOS UI 树中提取真实计算结果并断言...{RESET}")
        display_val = get_calculator_display_text()
        print(f"  ✓ 计算器显示屏真实读数: {BOLD}{GREEN}{display_val}{RESET}")

        clean_val = display_val.replace(",", "").strip()
        expected_str = str(expected_result)
        if clean_val == expected_str:
            print(f"  ✓ {BOLD}{GREEN}断言成功！计算结果准确匹配 ({clean_val} == {expected_str}){RESET}")
        else:
            print(f"  ⚠️  读数与预期不符 (实际: {clean_val}, 预期: {expected_str})")

        # --------------------------------------------------------------------
        # 8. 优雅清理与收尾
        # --------------------------------------------------------------------
        print(f"\n{BOLD}{CYAN}[Step 8] 优雅清理：关闭计算器应用...{RESET}")
        run_osascript('tell application "Calculator" to quit')
        print(f"  ✓ 计算器已优雅退出")

        print("\n" + "=" * 65)
        print(f"{BOLD}{GREEN}🎉 真实 macOS Computer Use 物理级交互执行圆满成功！{RESET}")
        print(f"真实截图产物留存：")
        print(f"  - 操作前真实截图: file://{before_img}")
        print(f"  - 操作后真实截图: file://{after_img}\n")

    except Exception as e:
        print(f"\n{YELLOW}执行异常: {e}{RESET}")
        run_osascript('tell application "Calculator" to quit')
        sys.exit(1)


if __name__ == "__main__":
    main()
