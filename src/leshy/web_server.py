from __future__ import annotations

from .web_server_impl10 import main, make_handler, serve
from .parcel_presenter import install_parcel_presenter

install_parcel_presenter()

__all__ = ["serve", "main", "make_handler"]


if __name__ == "__main__":
    main()
