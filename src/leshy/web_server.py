from __future__ import annotations

from .web_server_impl15 import main, make_handler, serve
from .parcel_presenter import install_parcel_presenter
from .phone_osint_runtime import install_phone_osint_runtime
from .telegram_api_diagnostics import install_telegram_http_error_details
from .app import LeshyApp
from .realtime_chat import RealtimeChatManager
from .torrent_parser_chat import install_torrent_parser_chat
from .help_catalog import install_help_routing

install_telegram_http_error_details()
install_parcel_presenter()
install_phone_osint_runtime(LeshyApp)
install_help_routing(LeshyApp)
install_torrent_parser_chat(LeshyApp, RealtimeChatManager)

__all__ = ["serve", "main", "make_handler"]


if __name__ == "__main__":
    main()
