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

    def test_snapshot_returns_every_packet_since_sequence(self):
        state = gui.CsiState("c5-12bit")
        sample = {"mac": "AA:BB:CC:DD:EE:FF", "role": "STA", "rssi": -55,
                  "channel": 6, "amplitudes": [10, 20, 30]}
        state.update(sample)
        state.update({**sample, "amplitudes": [11, 21, 31]})
        self.assertEqual([packet["seq"] for packet in state.snapshot()["packets"]], [1, 2])
        self.assertEqual([packet["amplitudes"] for packet in state.snapshot(1)["packets"]],
                         [[11, 21, 31]])


if __name__ == "__main__":
    unittest.main()
