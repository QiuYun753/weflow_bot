# weflow_event.py
"""
WeFlow 平台事件处理模块。

负责将 AstrBot 框架生成的回复通过 pyautogui 模拟操作发送到微信窗口。
"""
import time

import pyautogui
import pyperclip
import pygetwindow as gw

from astrbot import logger
from astrbot.api.event import AstrMessageEvent, MessageChain
from astrbot.api.platform import AstrBotMessage, PlatformMetadata
from astrbot.api.message_components import Plain


class WeFlowPlatformEvent(AstrMessageEvent):
    """WeFlow 平台消息事件，处理消息发送到微信窗口"""

    def __init__(
        self,
        message_str: str,
        message_obj: AstrBotMessage,
        platform_meta: PlatformMetadata,
        session_id: str,
        contact_name: str,
    ):
        super().__init__(message_str, message_obj, platform_meta, session_id)
        self.contact_name = contact_name

    async def send(self, message: MessageChain):
        """
        将 AstrBot 的回复消息通过 pyautogui 发送到微信。

        支持文本（Plain）类型消息组件。
        同时支持私聊窗口和群聊窗口（通过窗口标题匹配群名）。
        """
        for component in message.chain:
            if isinstance(component, Plain):
                success = self._send_to_wechat(self.contact_name, component.text)
                if success:
                    # 从消息对象判断是群聊还是私聊
                    chat_type = "群聊" if self.message_obj.type.name == "GROUP_MESSAGE" else "私聊"
                    logger.info(f"已回复 {chat_type} {self.contact_name}: {component.text[:60]}...")
        await super().send(message)

    def _send_to_wechat(self, contact: str, message: str) -> bool:
        """
        使用 pyautogui + pyperclip 模拟键盘鼠标操作发送消息到微信。

        流程：查找窗口 → 激活窗口 → 搜索联系人（如需要）→ 定位输入框 → 粘贴发送。
        """
        try:
            win, is_direct = self._find_chat_window(contact)
            if win is None:
                logger.error("未找到微信窗口，请确认微信已登录")
                return False

            # 激活窗口
            try:
                if win.isMinimized:
                    win.restore()
                win.activate()
                time.sleep(0.3)
            except Exception as e:
                logger.error(f"无法激活窗口: {e}")
                return False

            # 如果在主窗口（非独立窗口），需要搜索联系人
            if not is_direct:
                logger.info(f"在主窗口搜索联系人: {contact}")
                try:
                    pyautogui.hotkey('ctrl', 'f')
                    time.sleep(0.3)
                    pyautogui.hotkey('ctrl', 'a')
                    pyautogui.press('delete')
                    time.sleep(0.1)
                    pyperclip.copy(contact)
                    pyautogui.hotkey('ctrl', 'v')
                    time.sleep(1.0)
                    pyautogui.press('enter')
                    time.sleep(1.5)

                    # 等待独立窗口弹出
                    start_time = time.time()
                    new_win = None
                    while time.time() - start_time < 5:
                        new_win, is_direct = self._find_chat_window(contact, exclude_window=win)
                        if new_win and is_direct:
                            break
                        time.sleep(0.3)

                    if new_win and is_direct:
                        win = new_win
                        logger.info(f"已定位到独立聊天窗口: {win.title}")
                        if win.isMinimized:
                            win.restore()
                        win.activate()
                        time.sleep(0.3)
                    else:
                        logger.warning("未检测到独立窗口，尝试在主窗口输入")
                except Exception as e:
                    logger.error(f"搜索联系人失败: {e}")
                    return False

            # 点击输入框区域
            left, top, width, height = win.left, win.top, win.width, win.height
            if is_direct:
                click_x = left + width // 2
                click_y = top + height - 70
                pyautogui.click(click_x, click_y)
                time.sleep(0.2)
            else:
                click_x = left + width - 250
                click_y = top + height - 60
                pyautogui.click(click_x, click_y)
                time.sleep(0.2)
                pyautogui.click(click_x, click_y)  # 二次点击确保焦点
                time.sleep(0.1)

            # 清空输入框
            pyautogui.hotkey('ctrl', 'a')
            pyautogui.press('delete')
            time.sleep(0.1)

            # 粘贴并发送
            pyperclip.copy(message)
            pyautogui.hotkey('ctrl', 'v')
            time.sleep(0.2)
            pyautogui.press('enter')

            return True

        except Exception as e:
            logger.error(f"发送消息到微信失败: {e}")
            return False

    @staticmethod
    def _find_chat_window(contact: str, exclude_window=None):
        """
        查找微信聊天窗口。

        返回 (窗口对象, 是否为独立窗口)
        优先查找标题包含联系人名称且不是"微信"的独立窗口，
        否则返回微信主窗口（标题为"微信"）。
        """
        all_windows = gw.getAllWindows()
        # 尝试查找独立窗口
        for win in all_windows:
            if not win.visible:
                continue
            if exclude_window and win == exclude_window:
                continue
            title = win.title
            if contact in title and title != "微信":
                return win, True
        # 退回主窗口
        for win in all_windows:
            if win.title == "微信" and win.visible:
                return win, False
        return None, False
