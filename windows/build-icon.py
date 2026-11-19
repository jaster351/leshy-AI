"""Generate the Windows executable/installer icon from the shared Leshy image.

All outputs stay in .autobuild/ to avoid modifying the tracked Git checkout.
"""
from __future__ import annotations

from pathlib import Path
import sys

from PIL import Image


def main() -> int:
    repo = Path(__file__).resolve().parent.parent
    source = repo / "assets" / "leshy-icon.jpg"
    root = repo / ".autobuild" / "brand"
    if not source.is_file():
        raise FileNotFoundError(f"Missing branded icon: {source}")
    root.mkdir(parents=True, exist_ok=True)
    with Image.open(source) as original:
        icon = original.convert("RGBA").resize((256, 256), Image.Resampling.LANCZOS)
        icon.save(root / "leshy-icon.png", format="PNG")
        icon.save(
            root / "Leshy.ico",
            format="ICO",
            sizes=[(16, 16), (24, 24), (32, 32), (48, 48), (64, 64), (128, 128), (256, 256)],
        )
    print(f"LESHY_BRAND_ICON: {root / 'Leshy.ico'}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
