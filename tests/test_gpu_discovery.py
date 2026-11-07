"""Проверки системного auto-discovery без конкретных моделей видеокарт.

Тесты намеренно подают синтетические PCI ID: в продукте нет белого списка GPU.
"""
from __future__ import annotations

import json
from pathlib import Path
import tempfile
import unittest
from unittest.mock import patch

from leshy.gpu_detection import (
    GPUDevice, detect_gpu_devices, gpu_vendor, linux_gpu_devices,
    macos_gpu_devices, windows_gpu_devices,
)
from leshy.hardware import detect_hardware, HardwareConfig, resolve_execution_plan


class GPUDiscoveryTests(unittest.TestCase):
    def test_pci_vendor_ids_not_specific_board_names(self):
        self.assertEqual(gpu_vendor("10DE", "Совершенно неизвестная модель"), "NVIDIA")
        self.assertEqual(gpu_vendor("1002", "Generic PCI Device"), "AMD")
        self.assertEqual(gpu_vendor("8086", "Generic PCI Device"), "Intel")
        self.assertEqual(gpu_vendor("106b", "Generic PCI Device"), "Apple")
        self.assertEqual(gpu_vendor("abcd", "Generic PCI Device"), "Unknown")

    def test_windows_enumerates_mixed_vendors_and_skips_virtual_display(self):
        data = [
            {"Name": "Discrete GPU 1", "PNPDeviceID": r"PCI\\VEN_10DE&DEV_AB01",
             "AdapterRAM": 4294967295, "DriverVersion": "1.2.3"},
            {"Name": "Integrated GPU 2", "PNPDeviceID": r"PCI\\VEN_8086&DEV_CD02",
             "AdapterRAM": 1073741824, "DriverVersion": "2.0"},
            {"Name": "Microsoft Basic Display Adapter", "PNPDeviceID": "BASIC"},
            {"Name": "Discrete GPU 1", "PNPDeviceID": r"PCI\\VEN_10DE&DEV_AB01"},
        ]
        found = windows_gpu_devices(lambda _: data)
        self.assertEqual(len(found), 2)
        self.assertEqual([d.vendor for d in found], ["NVIDIA", "Intel"])
        self.assertEqual(found[0].vendor_id, "10de")
        self.assertEqual(found[0].device_id, "ab01")
        self.assertIsNone(found[0].vram_bytes)  # WMI 4-GB overflow нельзя считать VRAM
        self.assertEqual(found[1].vram_bytes, 1073741824)
        self.assertEqual(found[0].driver_version, "1.2.3")

    def test_windows_no_pci_id_still_discovers_unknown_vendor(self):
        found = windows_gpu_devices(lambda _: {"Name": "Unbranded Accelerator", "PNPDeviceID": ""})
        self.assertEqual(len(found), 1)
        self.assertEqual(found[0].vendor, "Unknown")

    def test_linux_sysfs_discovers_two_vendors_and_vram(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp) / "drm"
            root.mkdir()
            for idx, (bus, ven, dev) in enumerate((
                ("0000:01:00.0", "0x1002", "0xbeef"),
                ("0000:02:00.0", "0x8086", "0xcafe"),
            )):
                physical = Path(tmp) / "pci" / bus
                physical.mkdir(parents=True)
                (physical / "vendor").write_text(ven)
                (physical / "device").write_text(dev)
                (physical / "mem_info_vram_total").write_text("8589934592")
                card = root / f"card{idx}"
                card.mkdir()
                (card / "device").symlink_to(physical, target_is_directory=True)
            found = linux_gpu_devices(root, lspci_output="")
            self.assertEqual(len(found), 2)
            self.assertEqual({d.vendor for d in found}, {"AMD", "Intel"})
            self.assertEqual({d.vram_bytes for d in found}, {8589934592})

    def test_linux_lspci_fallback_without_sysfs(self):
        with tempfile.TemporaryDirectory() as tmp:
            raw = "0000:03:00.0 VGA compatible controller [0300]: Any Vendor Board [10de:abcd] (rev a1)"
            found = linux_gpu_devices(Path(tmp) / "absent", lspci_output=raw)
            self.assertEqual(len(found), 1)
            self.assertEqual(found[0].vendor, "NVIDIA")
            self.assertEqual(found[0].bus_id, "0000:03:00.0")

    def test_macos_profile_does_not_require_predefined_model(self):
        payload = json.dumps({"SPDisplaysDataType": [
            {"sppci_model": "Apple integrated GPU", "spdisplays_vendor": "Apple"},
            {"sppci_model": "Radeon test adapter", "spdisplays_vendor": "AMD"},
        ]})
        found = macos_gpu_devices(system_profiler_json=payload)
        self.assertEqual([d.vendor for d in found], ["Apple", "AMD"])

    def test_unknown_platform_returns_empty_without_failure(self):
        self.assertEqual(detect_gpu_devices(system="OtherOS"), ())

    def test_gpu_metadata_visible_in_hardware_status_without_breaking_auto_plan(self):
        devices = (GPUDevice(name="Generic GPU", vendor="Unknown", vendor_id="abcd"),)
        with (
            patch("leshy.hardware.detect_gpu_devices", return_value=devices),
            patch("leshy.hardware._llama_gpu_offload_supported", return_value=False),
            patch("leshy.hardware._openvino_devices", return_value=()),
            patch("leshy.hardware.platform.system", return_value="Linux"),
        ):
            caps = detect_hardware()
        self.assertTrue(caps.gpu)
        self.assertEqual(caps.gpu_names, ("Generic GPU",))
        self.assertEqual(caps.gpu_devices, devices)
        self.assertFalse(caps.gpu_backend_verified)
        self.assertTrue(caps.gpu_offload_runtime)  # кандидат, требуется запуск/health-check
        self.assertEqual(resolve_execution_plan(HardwareConfig(), caps).chat_backend, "vulkan")

    def test_gpu_missing_never_becomes_verified_by_driver_name(self):
        with (
            patch("leshy.hardware.detect_gpu_devices", return_value=()),
            patch("leshy.hardware._llama_gpu_offload_supported", return_value=False),
            patch("leshy.hardware._openvino_devices", return_value=()),
            patch("leshy.hardware.platform.system", return_value="Linux"),
        ):
            caps = detect_hardware()
        self.assertFalse(caps.gpu)
        self.assertFalse(caps.gpu_backend_verified)
        self.assertEqual(resolve_execution_plan(HardwareConfig(), caps).chat_device, "cpu")


if __name__ == "__main__":
    unittest.main()
