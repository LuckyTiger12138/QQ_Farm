"""页面导航系统：Page + Navigator"""

from core.ui.page import Page, page_main, page_mall, page_mail, page_menu, ALL_PAGES
from core.ui.navigator import Navigator

__all__ = [
    "Page", "Navigator",
    "page_main", "page_mall", "page_mail", "page_menu",
    "ALL_PAGES",
]
