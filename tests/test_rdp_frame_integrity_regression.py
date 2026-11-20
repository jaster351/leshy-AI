"""Regression: a decoded RDP frame must have exactly width*height*channels bytes."""
import unittest

from leshy.superagent_rdp_session import RDPSessionController, RDPFrame


class FakeBackend:
    def __init__(self, pixels, fmt="RGB"):
        self.pixels = pixels
        self.fmt = fmt

    def connect(self, host, *, expected_tls_sha256):
        return object()

    def capture(self, connection):
        return RDPFrame(width=2, height=2, pixels=self.pixels, pixel_format=self.fmt)

    def close(self, connection):
        pass


class RDPFrameIntegrityTests(unittest.TestCase):
    def test_rejects_truncated_and_padded_decoded_frames(self):
        for fmt, expected in (("RGB", 12), ("RGBA", 16)):
            for size in (expected - 1, expected + 1):
                with self.subTest(fmt=fmt, size=size):
                    backend = FakeBackend(b"x" * size, fmt)
                    broker = RDPSessionController(backend, authorize=lambda owner, node: None)
                    sid = broker.open("owner", "node", "a" * 64)
                    with self.assertRaisesRegex(ValueError, "byte count"):
                        broker.observe_frame("owner", sid)
                    broker.close("owner", sid)

    def test_accepts_exact_decoded_frame(self):
        backend = FakeBackend(b"x" * 12)
        broker = RDPSessionController(backend, authorize=lambda owner, node: None)
        sid = broker.open("owner", "node", "a" * 64)
        receipt, pixels = broker.observe_frame("owner", sid)
        self.assertEqual((receipt.width, receipt.height, len(pixels)), (2, 2, 12))
        broker.close("owner", sid)


if __name__ == "__main__":
    unittest.main()
