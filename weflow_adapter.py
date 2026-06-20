# weflow_adapter.py
import json
import time as time_module
import asyncio
from typing import Optional

import httpx

from astrbot import logger
from astrbot.api.platform import (
    Platform, AstrBotMessage, MessageMember,
    PlatformMetadata, MessageType, register_platform_adapter
)
from astrbot.api.event import MessageChain
from astrbot.api.message_components import Plain
from astrbot.core.platform.message_session import MessageSesion

from .weflow_event import WeFlowPlatformEvent


@register_platform_adapter("weflow", "基于 WeFlow SSE + pyautogui 的微信适配器", default_config_tmpl={
    "weflow_base_url": "http://127.0.0.1:5031",
    "access_token": "",
    "listen_user": "",
    "chat_type": "friend"
})
class WeFlowPlatformAdapter(Platform):
    """
    基于 WeFlow SSE 推送 + pyautogui 的微信平台适配器。

    消息接收：通过 httpx 异步连接 WeFlow 的 SSE 接口实时接收微信消息。
    消息发送：通过 pyautogui + pyperclip 模拟键盘鼠标操作发送回复。
    AI 处理：交由 AstrBot 框架统一调度。
    """

    def __init__(self, platform_config: dict, platform_settings: dict, event_queue: asyncio.Queue) -> None:
        super().__init__(platform_config, event_queue)
        self.processed_ids = set()
        self.start_timestamp = int(time_module.time())
        self.running = False
        self._http_client: Optional[httpx.AsyncClient] = None

        logger.info(f"WeFlow 适配器初始化，完整配置: {self.config}")

    async def send_by_session(self, session: MessageSesion, message_chain: MessageChain):
        """按会话发送消息（由框架调用）"""
        await super().send_by_session(session, message_chain)

    def meta(self) -> PlatformMetadata:
        return PlatformMetadata(
            "weflow",
            "WeFlow 微信适配器（基于 WeFlow SSE + pyautogui）",
            id="weflow"
        )

    async def run(self):
        """启动平台监听：建立 SSE 连接并循环处理消息"""
        self.running = True
        self.processed_ids.clear()
        self.start_timestamp = int(time_module.time())

        async with httpx.AsyncClient(timeout=httpx.Timeout(connect=10.0, read=None, write=None, pool=None)) as self._http_client:
            await self._sse_listen_loop()

    async def _sse_listen_loop(self):
        """
        异步 SSE 监听主循环。
        连接 WeFlow 的 SSE 推送接口，实时接收消息。
        连接断开时自动重连。
        """
        sse_url = (
            f"{self.config['weflow_base_url'].rstrip('/')}"
            f"/api/v1/push/messages?access_token={self.config['access_token']}"
        )
        headers = {
            "Accept": "text/event-stream",
            "Cache-Control": "no-cache",
        }

        while self.running:
            try:
                logger.info("正在连接 WeFlow SSE 推送服务...")
                async with self._http_client.stream(
                    "GET", sse_url, headers=headers
                ) as response:
                    if response.status_code == 401:
                        logger.error(
                            "WeFlow 返回 401 未授权：Access Token 错误或为空。"
                            "请在 WeFlow 中复制正确的 Token 并填写到平台配置中。"
                            "30 秒后重试..."
                        )
                        await asyncio.sleep(30)
                        continue
                    elif response.status_code != 200:
                        logger.error(
                            f"WeFlow 连接失败，状态码: {response.status_code}，"
                            "15 秒后重试..."
                        )
                        await asyncio.sleep(15)
                        continue

                    logger.info("已连接到 WeFlow 推送服务，等待新消息...")
                    logger.info(f"忽略 {time_module.strftime('%Y-%m-%d %H:%M:%S', time_module.localtime(self.start_timestamp))} 之前的历史消息")

                    async for line_bytes in response.aiter_lines():
                        if not self.running:
                            break
                        line = line_bytes.strip() if isinstance(line_bytes, str) else ""

                        if not line:
                            continue
                        if line.startswith("data:"):
                            data_str = line[5:].strip()
                            if not data_str:
                                continue
                            try:
                                data = json.loads(data_str)
                                await self._process_weflow_message(data)
                            except json.JSONDecodeError:
                                continue

                # 正常退出循环（连接关闭），自动重连
                if self.running:
                    logger.warning("SSE 连接已关闭，10 秒后重连...")
                    await asyncio.sleep(10)

            except httpx.ConnectError:
                logger.error("无法连接 WeFlow，请确认 WeFlow 已启动且地址正确，15 秒后重试...")
                await asyncio.sleep(15)
            except httpx.TimeoutException:
                logger.warning("SSE 连接超时，15 秒后重连...")
                await asyncio.sleep(15)
            except Exception as e:
                logger.error(f"SSE 监听异常: {e}")
                if self.running:
                    await asyncio.sleep(15)

    async def _process_weflow_message(self, data: dict):
        """处理单条 WeFlow 推送消息"""
        msg_timestamp = data.get("timestamp", 0)
        if msg_timestamp < self.start_timestamp:
            return

        raw_id = data.get("rawid")
        if raw_id in self.processed_ids:
            return
        self.processed_ids.add(raw_id)

        # 跳过非文本前先记日志
        content = data.get("content", "")
        source_name = data.get("sourceName", "") or ""
        talker_name = data.get("talkerName", "") or ""
        msg_type = data.get("type", 0) or data.get("msgType", 0)
        session_type = data.get("sessionType", "")
        group_name = data.get("groupName", "")
        logger.info(f"原始消息: type={msg_type}, sessionType={session_type!r}, sourceName={source_name!r}, groupName={group_name!r}, content={content[:80]!r}")

        if self._should_ignore(data):
            logger.info(f"消息被 _should_ignore 过滤: type={msg_type}, content_empty={not bool(content)}")
            return

        logger.info(f"消息已通过过滤, content长度={len(content)}")

        # 根据 sessionType 判断是群聊还是私聊
        is_group = (session_type == "group")
        listen_user = self.config.get("listen_user", "").strip()
        chat_type = self.config.get("chat_type", "friend")

        # 根据 chat_type 配置过滤：group 只接群聊，friend 只接私聊
        if chat_type == "group" and not is_group:
            logger.info(f"群聊模式下忽略私聊消息: sourceName={source_name!r}")
            return
        if chat_type == "friend" and is_group:
            logger.info(f"私聊模式下忽略群聊消息: groupName={group_name!r}")
            return

        if is_group:
            # 群聊模式
            if not group_name:
                logger.warning("群聊消息但没有 groupName 字段，跳过")
                return
            # 如果配置了监听特定群名，进行过滤
            if listen_user and group_name != listen_user:
                logger.info(f"跳过非监听群聊: groupName={group_name}, listen_user={listen_user}")
                return
            sender = group_name  # 群名，用于查找微信窗口
            group_sender = source_name or talker_name or "未知"  # 群内发言人
            logger.info(f"群聊处理完成: 群名={sender}, 发送者={group_sender}")
        else:
            # 私聊模式
            actual_sender = source_name or talker_name or "未知"
            if listen_user and actual_sender != listen_user:
                logger.debug(f"跳过非监听联系人: sender={actual_sender}, listen_user={listen_user}")
                return
            sender = actual_sender
            group_sender = ""
            logger.info(f"私聊处理完成: 发送者={sender}")

        if content and sender:
            logger.info(f"收到 WeFlow 消息: [{sender}] {content[:60]}")
            abm = self._convert_to_astrbot_message(content, sender, group_sender, is_group, data)
            await self.handle_msg(abm)
        else:
            logger.warning(f"消息因 content 或 sender 为空被跳过: content={bool(content)}, sender={bool(sender)}")

    @staticmethod
    def _should_ignore(data: dict) -> bool:
        """
        过滤非文本消息：语音消息(type=34)、表情包/图片(type=47)、
        以及内容为空的系统消息。
        """
        content = data.get("content", "")
        msg_type = data.get("type", 0) or data.get("msgType", 0)

        if msg_type in (34, 47):
            return True
        if content and ("[语音]" in content or "[表情]" in content):
            return True
        if not content or content.strip() == "":
            return True
        return False

    def _convert_to_astrbot_message(self, content: str, sender: str, group_sender: str, is_group: bool, raw_data: dict) -> Optional[AstrBotMessage]:
        """
        将 WeFlow 消息格式转换为 AstrBot 统一消息格式。

        - 私聊模式：消息类型为 FRIEND_MESSAGE，session_id 为联系人名
        - 群聊模式：消息类型为 GROUP_MESSAGE，group_id 为群名，
          session_id 为群名（整个群共享对话记忆）
        """
        abm = AstrBotMessage()
        abm.message_str = content
        abm.message = [Plain(text=content)]
        abm.raw_message = raw_data
        abm.self_id = "weflow_bot"
        abm.message_id = str(raw_data.get("rawid", ""))
        abm.timestamp = raw_data.get("timestamp", 0)

        if is_group:
            abm.type = MessageType.GROUP_MESSAGE
            abm.group_id = sender  # 群名
            # 群内发送者用 nickname 区分，方便 AstrBot 处理
            abm.sender = MessageMember(user_id=group_sender, nickname=group_sender)
            # 会话 ID 直接用群名，整个群共享对话记忆
            abm.session_id = sender
        else:
            abm.type = MessageType.FRIEND_MESSAGE
            abm.sender = MessageMember(user_id=sender, nickname=sender)
            abm.session_id = sender

        return abm

    async def handle_msg(self, message: AstrBotMessage):
        """将消息事件提交给 AstrBot 框架处理"""
        # 群聊时用 group_id（群名）作为窗口查找依据，私聊时用发送者昵称
        contact_name = message.group_id if message.type == MessageType.GROUP_MESSAGE else message.sender.nickname
        logger.info(f"提交事件到 AstrBot, 回复目标: {contact_name}, 消息类型: {message.type.name}")
        message_event = WeFlowPlatformEvent(
            message_str=message.message_str,
            message_obj=message,
            platform_meta=self.meta(),
            session_id=message.session_id,
            contact_name=contact_name
        )
        self.commit_event(message_event)
