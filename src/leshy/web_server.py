from __future__ import annotations

from .app import LeshyApp
from .help_catalog import install_help_routing
from .web_server_impl8 import main, make_handler, serve
from .parcel_presenter import install_parcel_presenter

install_help_routing(LeshyApp)
install_parcel_presenter()

__all__ = ["serve", "main", "make_handler"]


if __name__ == "__main__":
    main()
