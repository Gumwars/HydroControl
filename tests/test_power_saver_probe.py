# SPDX-License-Identifier: MIT
"""power_saver_probe resolves the RTX rather than assuming its bus address."""

import os
import tempfile
import unittest

import power_saver_probe as probe


def _dev(root, name, vendor, cls, drm=()):
    d = os.path.join(root, name)
    os.makedirs(os.path.join(d, "drm"))
    for f, v in (("vendor", vendor), ("class", cls)):
        with open(os.path.join(d, f), "w") as fh:
            fh.write(v + "\n")
    for n in drm:
        os.makedirs(os.path.join(d, "drm", n))
    return d


class FindDgpuTest(unittest.TestCase):

    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self.tmp.cleanup)

    def test_picks_the_nvidia_display_controller(self):
        _dev(self.tmp.name, "0000:00:02.0", "0x8086", "0x030000")
        want = _dev(self.tmp.name, "0000:02:00.0", "0x10de", "0x030000")
        self.assertEqual(probe.find_dgpu(self.tmp.name), want)

    def test_ignores_nvidia_audio_function(self):
        # The RTX's HDMI audio is the same vendor on the next function.
        _dev(self.tmp.name, "0000:01:00.1", "0x10de", "0x040300")
        self.assertIsNone(probe.find_dgpu(self.tmp.name))

    def test_none_without_an_nvidia_gpu(self):
        _dev(self.tmp.name, "0000:00:02.0", "0x8086", "0x030000")
        self.assertIsNone(probe.find_dgpu(self.tmp.name))

    def test_drm_nodes_come_from_that_device(self):
        d = _dev(self.tmp.name, "0000:01:00.0", "0x10de", "0x030000",
                 drm=("card0", "renderD129", "controlD64"))
        nodes = probe.dgpu_nodes(d)
        self.assertIn("/dev/dri/card0", nodes)
        self.assertIn("/dev/dri/renderD129", nodes)
        self.assertNotIn("/dev/dri/controlD64", nodes)


if __name__ == "__main__":
    unittest.main()
