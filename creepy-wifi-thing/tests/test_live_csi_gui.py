import importlib.util
from pathlib import Path
import unittest


SOURCE = Path(__file__).resolve().parents[1] / "tools" / "live_csi_gui.py"
spec = importlib.util.spec_from_file_location("live_csi_gui", SOURCE)
gui = importlib.util.module_from_spec(spec)
spec.loader.exec_module(gui)


class CsiDecodeTests(unittest.TestCase):
    def test_signed_12_bit_iq_words(self):
        # I = 52, Q = -52. The high byte 0x0f sign-extends the second word.
        self.assertAlmostEqual(gui.decode_c5_12bit([52, 0, -52, 15])[0], 73.54, places=2)

    def test_parses_real_csv_shape_and_ignores_tail(self):
        fields = ["CSI_DATA", "STA", "AA:BB:CC:DD:EE:FF", "-65"]
        fields += ["0"] * 12
        fields += ["11"]
        fields += ["0"] * 7
        fields += ["6", "[52 0 -52 15 0 0 ]"]
        sample = gui.parse_csi_line(",".join(fields))
        self.assertEqual(sample["channel"], 11)
        self.assertEqual(sample["mac"], "AA:BB:CC:DD:EE:FF")
        self.assertEqual(len(sample["amplitudes"]), 1)
        self.assertAlmostEqual(sample["amplitudes"][0], 73.54, places=2)

    def test_rejects_truncated_packet(self):
        self.assertIsNone(gui.parse_csi_line("CSI_DATA,STA"))


if __name__ == "__main__":
    unittest.main()
