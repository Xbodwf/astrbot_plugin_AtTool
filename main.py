import re
import json
import time
from typing import List, Dict, Any, Optional
from astrbot.api.star import Star, Context
from astrbot.api.event import filter, AstrMessageEvent
from astrbot.api import logger
from astrbot.api.provider import ProviderRequest
from astrbot.core.message.components import Plain, At, BaseMessageComponent
import astrbot.api.message_components as Comp
from astrbot.core.platform.sources.aiocqhttp.aiocqhttp_message_event import AiocqhttpMessageEvent

class LLMAtToolPlugin(Star):
    def __init__(self, context: Context):
        super().__init__(context)
        # 匹配合法的 [at:123456]
        self.valid_at_pattern = re.compile(r'\[at:(\d+)\]')
        # 匹配疑似标签但格式错误的，用于除杂 (例如 [at:某人], [at:unknown])
        self.garbage_at_pattern = re.compile(r'\[at:[^\]]+\]')

    @filter.on_llm_request()
    async def inject_at_instruction(self, event: AstrMessageEvent, req: ProviderRequest):
        """
        在 LLM 请求前注入 System Prompt。
        使用 XML 格式定义艾特协议。
        """
        instruction = (
            "\n\n"
            "<at_mention_protocol>\n"
            "    <description>协议用于在群聊中艾特(At)特定成员以引起注意。</description>\n"
            "    <workflow>\n"
            "        <step index='1'>判断是否需要艾特某人（如回复特定提问、提醒）。</step>\n"
            "        <step index='2'>调用工具 `at_member(keyword)`，传入昵称/群名片/QQ号/角色。</step>\n"
            "        <step index='3'>工具会自动查询并直接发送真实 At，不返回可见文本。</step>\n"
            "    </workflow>\n"
            "</at_mention_protocol>\n"
        )
        req.system_prompt += instruction
    
    @filter.llm_tool(name="at_member")
    async def at_member(self, event: AstrMessageEvent, keyword: str = ""):
        """At以提醒一个群成员
        
        Args:
            keyword(str): 群成员的昵称/群名片/QQ号(如"张三"、"123456") 
        """
        group_id = event.get_group_id()
        if not group_id or not isinstance(event, AiocqhttpMessageEvent):
            return
        q = (keyword or "").strip().lstrip("@")
        try:
            raw_members = await event.bot.api.call_action('get_group_member_list', group_id=group_id)
        except Exception:
            raw_members = []
        if not raw_members:
            return
        role_map = {
            "owner": "owner",
            "admin": "admin",
            "member": "member",
            "群主": "owner",
            "管理员": "admin",
            "成员": "member",
        }
        resolved_uid = None
        if q.isdigit():
            resolved_uid = q
        role_key = role_map.get(q.lower(), None)
        if not resolved_uid and role_key:
            for m in raw_members:
                if m.get("role", "member") == role_key:
                    resolved_uid = str(m.get("user_id", ""))
                    break
        exact_uid = None
        if not resolved_uid:
            for m in raw_members:
                uid = str(m.get("user_id", ""))
                nickname = m.get("nickname", "") or ""
                card = m.get("card", "") or ""
                if q == nickname or q == card:
                    exact_uid = uid
                    break
            if exact_uid:
                resolved_uid = exact_uid
        candidates = []
        if not resolved_uid:
            for m in raw_members:
                uid = str(m.get("user_id", ""))
                nickname = m.get("nickname", "") or ""
                card = m.get("card", "") or ""
                if q in nickname or (card and q in card):
                    candidates.append(uid)
            if candidates:
                resolved_uid = candidates[0]
        if resolved_uid:
            chain = [Comp.At(qq=resolved_uid)]
            yield event.chain_result(chain)
        return
