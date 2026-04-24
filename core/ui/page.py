"""页面图定义：Page 数据类 + 预定义页面 + 连接关系。"""

from __future__ import annotations

from dataclasses import dataclass, field


@dataclass
class Page:
    """表示一个游戏页面。"""

    name: str
    cn_name: str
    check_templates: list[str] = field(default_factory=list)
    links: dict[str, list[str]] = field(default_factory=dict)
    close_templates: list[str] = field(default_factory=list)

    def __eq__(self, other):
        if not isinstance(other, Page):
            return False
        return self.name == other.name

    def __hash__(self):
        return hash(self.name)

    def __str__(self):
        return self.cn_name


# ── 页面定义 ──────────────────────────────────────────────

page_main = Page(
    name="main",
    cn_name="主页",
    check_templates=["ui_farm_overview", "btn_land_right", "btn_land_left"],
)

page_mall = Page(
    name="mall",
    cn_name="商城",
    check_templates=["ui_shangcheng", "mall_check"],
    close_templates=["btn_close", "btn_shop_close", "btn_xc_close"],
)

page_menu = Page(
    name="menu",
    cn_name="菜单",
    check_templates=["menu_check"],
    close_templates=["btn_close"],
)

page_mail = Page(
    name="mail",
    cn_name="邮件",
    check_templates=["mail_check"],
    close_templates=["btn_close"],
)

# ── 页面图连接 ────────────────────────────────────────────

page_main.links = {
    "mall": ["btn_shop", "main_goto_mall"],
    "menu": ["main_goto_menu"],
}

page_mall.links = {
    "main": ["btn_close", "btn_shop_close", "btn_xc_close", "mall_goto_main", "btn_shangcehng_fanhui"],
}

page_menu.links = {
    "mail": ["menu_goto_mail", "btn_mail_entry"],
    "main": ["btn_close", "menu_goto_main"],
}

page_mail.links = {
    "main": ["btn_close"],
}

ALL_PAGES = [page_main, page_mall, page_menu, page_mail]
