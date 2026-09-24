"""Unit tests for Aegis-Sandbox Display & Input Subsystem.

Zero-dependency standard unittest suite testing:
1. Coordinate transformation and boundary clamping.
2. 1000x1000 grid translation (Claude / UI-TARS format).
3. Cubic Bezier trajectory generation and Ease-In-Out distribution.
4. Zero-copy screen capture latency profiling (< 8ms target).
5. FrameBuffer direct pixel memory modification and hash verification.
6. Kernel /dev/uinput mouse click and EV_SYN atomic event pairing.
7. Multi-key modifier shortcuts (atomically pressed and reverse-released).
8. Unified DisplayManager Perception-Action integration.
"""

import time
import unittest

from display import (
    BaseInputDriver,
    BaseScreenCaptureDriver,
    CoordinateTransformer,
    DisplayGeometry,
    DisplayManager,
    FrameBufferData,
    HermeticMockDisplayDriver,
    HermeticMockInputDriver,
    InputEventType,
    KernelInputEvent,
)


class TestDisplayAndInputSubsystem(unittest.TestCase):
    """Test suite for Aegis-Sandbox Display & Input Subsystem."""

    def setUp(self) -> None:
        self.geometry = DisplayGeometry(width=1920, height=1080, scale_factor=1.0)
        self.transformer = CoordinateTransformer(self.geometry)

    def test_coordinate_denormalization_and_clamping(self) -> None:
        """Validates normalized coordinate mapping to physical screen pixels with clamping."""
        # Exact corners
        self.assertEqual(self.transformer.denormalize(0.0, 0.0), (0, 0))
        self.assertEqual(self.transformer.denormalize(1.0, 1.0), (1919, 1079))

        # Center point
        mid_x, mid_y = self.transformer.denormalize(0.5, 0.5)
        self.assertEqual((mid_x, mid_y), (960, 540))

        # Clamping out-of-bounds inputs
        self.assertEqual(self.transformer.denormalize(-0.5, 1.8), (0, 1079))

    def test_1000_grid_mapping(self) -> None:
        """Validates Claude / UI-TARS 1000x1000 grid coordinate translation."""
        self.assertEqual(self.transformer.from_1000_grid(0, 0), (0, 0))
        self.assertEqual(self.transformer.from_1000_grid(1000, 1000), (1919, 1079))
        self.assertEqual(self.transformer.from_1000_grid(500, 500), (960, 540))

    def test_bezier_trajectory_smoothness(self) -> None:
        """Validates that Cubic Bezier path is continuous and bounded."""
        start = (100, 100)
        end = (800, 600)
        trajectory = self.transformer.generate_bezier_trajectory(start, end, steps=20)

        self.assertEqual(trajectory[0], start)
        self.assertEqual(trajectory[-1], end)
        self.assertGreaterEqual(len(trajectory), 15)

        for px, py in trajectory:
            self.assertTrue(0 <= px < self.geometry.width)
            self.assertTrue(0 <= py < self.geometry.height)

    def test_screen_capture_zero_copy_latency(self) -> None:
        """Benchmarks FrameBuffer capture latency (guaranteed < 8ms)."""
        driver = HermeticMockDisplayDriver(self.geometry)

        # Warm up
        driver.capture()

        # Measure 50 consecutive frame captures
        latencies = []
        for _ in range(50):
            t0 = time.perf_counter()
            frame = driver.capture()
            t1 = time.perf_counter()
            latencies.append((t1 - t0) * 1000)

        avg_latency = sum(latencies) / len(latencies)
        self.assertLess(avg_latency, 8.0, f"Capture latency too high: {avg_latency:.2f}ms (target < 8ms)")
        self.assertEqual(frame.width, 1920)
        self.assertEqual(frame.height, 1080)
        self.assertEqual(frame.total_bytes, 1920 * 1080 * 4)
        self.assertTrue(len(frame.frame_hash) > 0)
        driver.close()

    def test_framebuffer_direct_pixel_draw(self) -> None:
        """Validates memory-mapped pixel modification and hash reaction."""
        driver = HermeticMockDisplayDriver(self.geometry)
        f1 = driver.capture()

        # Draw a red block at (0, 0)
        driver.draw_pixel(0, 0, r=255, g=0, b=0, a=255)
        f2 = driver.capture()

        # Verify raw bytes at pixel (0, 0)
        self.assertEqual(f2.raw_bytes[:4], bytes([255, 0, 0, 255]))
        self.assertNotEqual(f1.frame_hash, f2.frame_hash)
        driver.close()

    def test_mouse_click_and_ev_syn_pairing(self) -> None:
        """Validates that mouse click emits strict EV_SYN paired events."""
        input_driver = HermeticMockInputDriver(self.transformer)
        input_driver.click(450, 600, button="left")

        # Verify event stream: MOVE -> SYN -> MOUSE_DOWN -> SYN -> MOUSE_UP -> SYN
        events = input_driver.events_log
        self.assertEqual(len(events), 6)
        self.assertEqual(events[0].event_type, InputEventType.MOUSE_MOVE)
        self.assertEqual(events[1].event_type, InputEventType.EV_SYN)
        self.assertEqual(events[2].event_type, InputEventType.MOUSE_DOWN)
        self.assertEqual(events[2].code, "BTN_LEFT")
        self.assertEqual(events[3].event_type, InputEventType.EV_SYN)
        self.assertEqual(events[4].event_type, InputEventType.MOUSE_UP)
        self.assertEqual(events[4].code, "BTN_LEFT")
        self.assertEqual(events[5].event_type, InputEventType.EV_SYN)

    def test_keyboard_modifier_atomic_sequence(self) -> None:
        """Validates shortcut combination: pressed in sequence, released in reverse."""
        input_driver = HermeticMockInputDriver(self.transformer)
        input_driver.key_combination(["Control", "Shift", "t"])

        events = input_driver.events_log
        # 3 keys down + 3 syn + 3 keys up + 3 syn = 12 events
        self.assertEqual(len(events), 12)

        # Check key down order: CONTROL -> SHIFT -> T
        down_events = [e for e in events if e.event_type == InputEventType.KEY_DOWN]
        self.assertEqual([e.code for e in down_events], ["KEY_CONTROL", "KEY_SHIFT", "KEY_T"])

        # Check key up order (REVERSE): T -> SHIFT -> CONTROL
        up_events = [e for e in events if e.event_type == InputEventType.KEY_UP]
        self.assertEqual([e.code for e in up_events], ["KEY_T", "KEY_SHIFT", "KEY_CONTROL"])

        # Modifiers set should be completely empty after release
        self.assertEqual(len(input_driver.active_modifiers), 0)

    def test_display_manager_integration(self) -> None:
        """Validates high-level DisplayManager perception and action integration."""
        dm = DisplayManager(geometry=self.geometry)

        # 1. Take initial screenshot
        frame = dm.take_screenshot()
        self.assertEqual(frame.width, 1920)

        # 2. Click normalized coordinate (0.5, 0.5)
        phys_x, phys_y = dm.click_normalized(0.5, 0.5)
        self.assertEqual((phys_x, phys_y), (960, 540))

        # 3. Click 1000-grid coordinate (250, 750)
        g_x, g_y = dm.click_grid_1000(250, 750)
        self.assertEqual((g_x, g_y), (480, 809))

        # 4. Type text
        dm.type_text("Hello Agent")
        dm.close()


if __name__ == "__main__":
    unittest.main()
