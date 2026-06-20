# main.py
"""
WeFlow 微信适配器插件 - AstrBot 平台适配器

通过 WeFlow SSE 推送实时接收微信消息，使用 pyautogui 模拟操作发送回复。
"""
from astrbot.api.star import Context, Star, register


@register(
    "weflow_bot",
    "Your Name",
    "基于 WeFlow SSE + pyautogui 的微信平台适配器插件",
    "1.0.0",
)
class WeFlowPlugin(Star):
    """WeFlow 微信适配器插件入口，加载平台适配器"""

    def __init__(self, context: Context):
        super().__init__(context)
        # 导入平台适配器以触发 @register_platform_adapter 装饰器注册
        from .weflow_adapter import WeFlowPlatformAdapter  # noqa: F401
