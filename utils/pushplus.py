"""PushPlus 微信消息发送。"""

import json
from urllib.request import Request, urlopen


PUSHPLUS_SEND_URL = "https://www.pushplus.plus/send"


def format_seed_shortage_message(
    instance_id: str, primary: str, secondary: str, tertiary: str, *, test: bool = False
) -> tuple[str, str]:
    """生成自动提醒和手动测试共用的纯文本消息。"""
    prefix = "【测试】" if test else ""
    title = f"{prefix}QQ 农场缺种提醒 ({instance_id})"
    details = f"实例：{instance_id}\n首选：{primary}\n次级：{secondary}\n三级：{tertiary}\n"
    if test:
        content = (
            "这是一条手动测试消息，未实际检查种子库存。\n" + details
            + "测试场景：三个作物均未在选种面板找到种子，且本轮未成功购买种子。"
        )
    else:
        content = details + "三个作物均未在选种面板找到种子，且本轮未成功购买种子，请检查库存或自动买种。"
    return title, content


def send_pushplus(token: str, title: str, content: str) -> tuple[bool, str]:
    """提交微信消息；成功仅表示 PushPlus 接收请求。"""
    if not token.strip():
        return False, "未配置 Token"

    payload = {
        "token": token.strip(),
        "title": title,
        "content": content,
        "template": "txt",
        "channel": "wechat",
    }
    request = Request(
        PUSHPLUS_SEND_URL,
        data=json.dumps(payload, ensure_ascii=False).encode("utf-8"),
        headers={"Content-Type": "application/json; charset=utf-8"},
        method="POST",
    )
    try:
        with urlopen(request, timeout=10) as response:
            result = json.loads(response.read(65536).decode("utf-8"))
    except (OSError, ValueError) as exc:
        return False, f"请求失败 ({type(exc).__name__})"

    if not isinstance(result, dict) or result.get("code") != 200:
        code = result.get("code") if isinstance(result, dict) else "invalid"
        return False, f"接口返回码 {code}"
    return True, "PushPlus 已接收请求"
