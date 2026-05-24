"""Script Engine — 抖音算法原生Beat级脚本生成器.

核心思路：一个超强Prompt替代多个Agent循环。
内嵌抖音2026算法规则（收藏>评论>转发>点赞）、钩子模式、
信息密度公式、收藏/转发诱因机制。

一次LLM调用产出完整的、可直接用于素材匹配的Beat级脚本。

Usage:
    from generators.script_engine import ScriptEngine
    engine = ScriptEngine()
    script = engine.generate(
        video_type="product_promo",    # 视频类型
        topic="无刷电机绕线机",         # 选题
        ref_analysis={...},             # 参考图/素材分析结果
        brand_dna={...},                # 品牌DNA
    )
"""

import json, os, re
from dataclasses import dataclass, field
from typing import Optional


# ─── Data Types ─────────────────────────────────────────────

@dataclass
class Beat:
    """一个Beat = 一句口播 + 一个画面动作。"""
    index: int
    text: str                       # 口播文案（≤35字）
    visual: str                     # 画面描述（供素材匹配用）
    animation: str                  # 动画类型: zoom/fade/slide/pop/pulse/none
    emotion: str                    # 心理功能: hook/curiosity/surprise/trust/desire/action
    duration_s: float
    is_save_trigger: bool = False   # 是否是收藏诱因点
    is_share_trigger: bool = False  # 是否是转发诱因点
    is_comment_trigger: bool = False # 是否是评论引爆点
    # Extended FrameCraft dimensions (from visual-hub pipeline_bridge)
    caption: str = ""               # On-screen subtitle text per shot
    how_to_shoot: str = ""          # Shooting instruction for production
    tier: str = "L1"                # Shooting tier: L1(phone)/L2(pro)/L3(studio)
    audio_l2_text: str = ""         # Alternate narration layer (B-roll)


@dataclass
class Script:
    """完整Beat级生产脚本."""
    title: str
    hook_type: str                  # 悬念型/反常识型/恐惧型/好奇心型/身份认同型
    beats: list[Beat]
    outro: Beat
    tags: list[str]                 # 话题标签
    bgm_style: str                  # BGM风格
    checklist: str                  # 可截图保存的检查清单（收藏诱因）
    total_duration_s: float
    # Extended FrameCraft dimensions (from visual-hub pipeline_bridge)
    bgm_search_keywords: list[str] = field(default_factory=list)
    bgm_tempo_bpm: str = ""
    bgm_usage_tips: str = ""
    composition_style: str = ""
    model_direction: str = ""
    differentiation: str = ""
    key_features: list[str] = field(default_factory=list)
    top_hook_types: list[str] = field(default_factory=list)


# ─── Video Type Templates ───────────────────────────────────

VIDEO_TYPES = {
    "ai_flaw_detect": {
        "name": "AI识别教学",
        "hook_patterns": [
            "悬念型: 这张图里藏着一个破绽，你发现了吗",
            "反常识型: AI永远做不好这件事——",
            "恐惧型: 你可能已经被AI生成的图片骗了",
            "好奇心型: 放大3倍后，我看到了诡异的东西",
            "反诈联动型: 反诈APP说这张图是假的，但我教你怎么自己看出来",
            "教学型: 3秒学会识别AI假图的第X招",
            "挑战型: 看到第3个破绽还没发现的人，评论区扣1",
        ],
        "beat_structure": "hook(3s) → reveal_full_image(2s) → zoom_flaw_1(5s) → zoom_flaw_2(5s) → zoom_flaw_3(5s) → teach_principle(5s) → checklist_summary(5s)",
        "save_trigger": "结尾放检查清单：识别AI三大要点，截图保存下次对照",
        "share_trigger": "转发给你身边经常刷到假图的朋友，帮TA练眼睛",
        "comment_trigger": "你还见过什么AI破绽？发评论区我下期讲",
        "bgm_style": "赛博朋克电子/科技悬疑",
        "tags": ["AI照妖镜", "AI识别", "AI真假辨别", "反诈", "AI鉴定"],
        "info_density": "每5秒至少1个破绽点或识别技巧，每期至少教1个可用的识别方法",
        "key_positioning": "只教方法，不替代检测工具。反诈APP=答案，我们=解题思路。",
    },
    "product_promo": {
        "name": "带货种草",
        "hook_patterns": [
            "痛点型: 你家的XX是不是经常...",
            "反常识型: 这个价格能做到这个品质？",
            "身份认同型: 做XX的都懂这种痛",
            "结果展示型: 用了它之后，效果也太夸张了",
        ],
        "beat_structure": "pain_point(3s) → product_reveal(3s) → feature_demo×3(each 5s) → social_proof(5s) → price_reveal(3s) → cta(5s)",
        "save_trigger": "结尾放产品参数对比表/选购清单",
        "share_trigger": "转发给正在挑XX的朋友，帮她省一笔",
        "comment_trigger": "你买过最值的XX是什么？评论区安利一下",
        "bgm_style": "轻快电子/温暖生活",
        "tags": ["好物推荐", "平价好物", "测评"],
        "info_density": "每5秒一个卖点或使用场景",
    },
    "factory_promo": {
        "name": "工厂宣传/B2B",
        "hook_patterns": [
            "实力型: 一台好的XX是怎么造出来的",
            "痛点型: 为什么你采购的XX总出问题",
            "数据型: 300%效率提升，0.01mm精度",
        ],
        "beat_structure": "strength_show(5s) → craftsmanship(8s) → spec_showcase(8s) → customer_proof(5s) → cta(4s)",
        "save_trigger": "结尾放核心参数表/选型指南",
        "share_trigger": "转发给你们的采购/技术负责人",
        "comment_trigger": "你们的设备最大痛点是什么？评论区聊聊",
        "bgm_style": "科技大气/工业电子",
        "tags": ["源头工厂", "中国制造", "自动化设备"],
        "info_density": "每5秒一个技术参数或信任背书",
    },
    "tutorial": {
        "name": "知识教程",
        "hook_patterns": [
            "问题型: XX总做不好？问题出在这",
            "效率型: 别人花1小时，你只需要3分钟",
            "避坑型: 90%的人都会犯的XX错误",
        ],
        "beat_structure": "problem(3s) → answer_preview(3s) → step×3(each 6s) → common_mistake(5s) → checklist(5s)",
        "save_trigger": "结尾放完整步骤清单，截图保存",
        "share_trigger": "转发给正在学XX的朋友",
        "comment_trigger": "你还有什么好方法？评论区教教我",
        "bgm_style": "轻松学习/轻快钢琴",
        "tags": ["教程", "干货", "学习方法"],
        "info_density": "每5秒一个操作步骤或避坑点",
    },
    "vlog": {
        "name": "个人Vlog/生活记录",
        "hook_patterns": [
            "共鸣型: 这种感觉谁懂啊",
            "记录型: 一个普通XX的一天",
            "治愈型: 看完心情变好了",
        ],
        "beat_structure": "moment_hook(3s) → scene×3(each 6s) → emotional_peak(5s) → reflection(5s)",
        "save_trigger": "喜欢的画面留作壁纸/收藏治愈时刻",
        "share_trigger": "转发给懂你的人",
        "comment_trigger": "你最近被什么治愈了？",
        "bgm_style": "治愈吉他/温暖钢琴/Lo-fi",
        "tags": ["日常", "治愈", "生活记录"],
        "info_density": "每5秒一个情绪点",
    },
}


# ─── Brand DNA ──────────────────────────────────────────────

DEFAULT_BRAND_DNA = {
    "brand_name": "AI照妖镜",
    "slogan": "反诈APP给你答案，我给你眼睛",
    "visual_style": "深色背景 + 霓虹绿扫描线(#00e676) + 红色圆圈标注(#ff1744) + 赛博朋克检测界面风格",
    "tone": "冷静客观+口语化+'我教你'教学感（非'我帮你查'工具感）",
    "bgm_style": "赛博朋克电子",
    "outro_template": "反诈APP帮你查，{brand_name}教你看。{slogan}",
}


# ─── Engine ─────────────────────────────────────────────────

class ScriptEngine:
    """Beat级脚本生成器 — 一次LLM调用出完整脚本.

    Supports multi-provider fallback via LLMDispatcher.
    Pass dispatcher=my_dispatcher to use custom provider config.
    """

    def __init__(self, api_key: str = None, base_url: str = None, model: str = None,
                 dispatcher=None):
        self.api_key = api_key or os.environ.get("DEEPSEEK_API_KEY")
        self.base_url = base_url or os.environ.get("DEEPSEEK_BASE_URL", "https://api.deepseek.com")
        self.model = model or "deepseek-chat"
        self._dispatcher = dispatcher  # LLMDispatcher instance (optional)

    def generate(self, video_type: str, topic: str = "",
                 ref_analysis: dict = None, brand_dna: dict = None,
                 style_hint: str = "") -> Script:
        """生成完整Beat级脚本.

        Args:
            video_type: 视频类型 (ai_flaw_detect/product_promo/factory_promo/tutorial/vlog)
            topic: 选题/主题
            ref_analysis: 参考图/素材分析结果 (可选，有参考图时传入)
            brand_dna: 品牌DNA配置 (可选，用DEFAULT_BRAND_DNA)
            style_hint: 额外的风格提示 (可选)
        """
        vt = VIDEO_TYPES.get(video_type, VIDEO_TYPES["ai_flaw_detect"])
        brand = {**DEFAULT_BRAND_DNA, **(brand_dna or {})}

        prompt = self._build_prompt(vt, brand, topic, ref_analysis, style_hint)
        response = self._call_llm(prompt)
        return self._parse_response(response, vt, brand)

    # ─── Prompt Building ─────────────────────────────────

    def _build_prompt(self, vt: dict, brand: dict, topic: str,
                      ref_analysis: dict, style_hint: str) -> str:
        """构建超强Prompt — 内嵌所有抖音算法规则."""

        ref_section = ""
        if ref_analysis:
            ref_section = f"""
## 参考素材分析结果
{json.dumps(ref_analysis, ensure_ascii=False, indent=2)}

⚠️ 以上分析结果中提到的具体特征、卖点、破绽等，口播文案中必须具体引用。
不要用模糊描述（如"这里有问题"），要说清楚具体是什么问题、什么特征。
"""

        style_section = f"\n## 额外风格要求\n{style_hint}" if style_hint else ""

        return f"""你是抖音爆款短视频脚本撰写专家，专做AI内容识别教学。

## 账号定位（重要）

中国国家反诈中心APP已于2026年3月上线AI内容检测功能，用户可以直接上传图片/视频让APP判定"是否AI生成"。

我们的差异化定位：
- 反诈APP = 答案（"这张图是假的"）
- {brand['brand_name']} = 解题思路（"我教你为什么它是假的"）

**核心原则：每条视频必须让观众带走一个可用的识别技巧。不是替观众查，是教观众看。**

## 品牌信息
- 品牌名：{brand['brand_name']}
- 标语：{brand['slogan']}
- 视觉风格：{brand['visual_style']}
- 语气：{brand['tone']}

## 视频类型：{vt['name']}
## 选题：{topic}
{ref_section}
{style_section}

## ⚠️ 抖音2026算法核心规则（必须遵守）

1. **收藏率是第一权重**：视频必须让观众想"保存下来以后用"
   → 每条视频必须有至少1个"可截图保存"的内容（检查清单/对比表/参数汇总/步骤清单）
   → 口播中自然引导收藏："建议收藏，下次直接对照检查"

2. **转发率靠社交货币**：观众转发是因为"这显得我聪明/有品味/关心朋友"
   → 必须有至少1个转发诱因："转发给你身边经常被AI假图骗的朋友"
   → 内容本身要让转发者显得有眼光

3. **评论率靠讨论点**：评论区越热闹，二次推荐越多
   → 结尾必须抛一个开放式问题或选择题
   → 让观众忍不住想表达自己的看法

4. **前3秒决定生死**：观众1.8秒内决定划走还是留下
   → 第一个Beat必须是钩子（不是陈述句！）
   → 钩子类型：{', '.join(vt['hook_patterns'][:3])}

5. **信息密度**：每5秒至少1个新信息点
   → {vt['info_density']}
   → 不能有废话，每句话都推动内容前进

6. **教学感 > 炫技感**：每个破绽讲完要解释背后的原理
   → 不只说"这里有破绽"，要说"为什么这是个破绽"
   → 例如："AI脸皮肤像塑料，因为它学习的全是精修过的广告图"（讲原理）vs "皮肤好假"（只讲结论）

7. **每句口播≤35字**：太长观众听不进去

## Beat级脚本结构

推荐结构：{vt['beat_structure']}

每个Beat包含：
- 口播文案（≤35字）
- 画面描述（具体可执行，供素材匹配）
- 动画类型（zoom放大 / fade渐显 / slide滑入 / pop弹入 / pulse脉冲 / none无）
- 心理功能（hook钩子 / curiosity好奇 / surprise惊讶 / trust信任 / desire欲望 / action行动）
- 时长（秒）

## 必须有的元素

- ✅ 收藏诱因：{vt['save_trigger']}
- ✅ 转发诱因：{vt['share_trigger']}
- ✅ 评论引爆：{vt['comment_trigger']}

## 必须避免

- ❌ 第一个Beat是陈述句（没钩子）
- ❌ 超过15秒没有任何新信息
- ❌ 口播全是套话没有任何具体数据或案例
- ❌ 结尾没有收藏/转发/评论引导
- ❌ AI感太强（生硬的播音腔、不自然的比喻）

## 输出格式

请用以下JSON格式回复（只输出JSON，不要有其他文字）：

```json
{{
  "title": "视频标题（15字以内，含钩子元素）",
  "hook_type": "{'/'.join(vt['hook_patterns'][:1])}（选一个最适合的）",
  "beats": [
    {{
      "index": 1,
      "text": "口播文案（≤35字）",
      "visual": "画面描述：展示什么、强调什么、什么角度",
      "animation": "zoom|fade|slide|pop|pulse|none",
      "emotion": "hook|curiosity|surprise|trust|desire|action",
      "duration_s": 3.0,
      "is_save_trigger": false,
      "is_share_trigger": false,
      "is_comment_trigger": false
    }}
  ],
  "outro": {{
    "text": "结尾口播（≤40字，只含品牌slogan。收藏/转发/评论引导已在前面beat完成）",
    "visual": "结尾画面：品牌logo + 引导关注动画 + 可截图清单",
    "duration_s": 6.0
  }},
  "checklist": "可截图保存的检查清单/对比表/步骤汇总（纯文本，50字以内）",
  "tags": ["话题1", "话题2", "话题3", "话题4", "话题5"],
  "bgm_style": "{vt['bgm_style']}"
}}
```

## 特别注意

1. 每个beat的visual描述必须具体，能直接用于素材检索（如"产品45度旋转特写"而不是"展示产品"）
2. 收藏诱因、转发诱因、评论引爆至少各1个beat标记为true
3. 如果提供了参考素材分析，口播必须引用其中的具体信息（如标题中的乱码文字、产品参数等）
4. checklist要简洁有力，能在视频结尾作为静态画面停留2-3秒供截图
5. 结尾口播使用品牌outro模板：反诈APP帮你查，AI照妖镜教你看。反诈APP给你答案，我给你眼睛
6. 每条视频至少教1个观众"下次自己也能看出来"的识别方法（不只说破绽在哪，更要说为什么）"""

    # ─── LLM Call ────────────────────────────────────────

    def _call_llm(self, prompt: str) -> str:
        """调用LLM — 优先走多Provider调度，失败回退到直接DeepSeek API."""
        messages = [
            {"role": "system", "content": "你是抖音AI识别教学类爆款脚本专家。风格是'我教你'而非'我帮你查'。每条视频教一个观众能自己用的识别技巧。请严格JSON格式回复。"},
            {"role": "user", "content": prompt},
        ]

        # Try multi-provider dispatcher first
        if self._dispatcher:
            try:
                result = self._dispatcher.chat(messages, model=self.model)
                return result.content
            except RuntimeError:
                pass  # fall through to direct API call

        # Try lazy-global dispatcher (auto-detects all configured providers)
        try:
            from .llm_providers import get_dispatcher
            dispatcher = get_dispatcher()
            # Only use if more than 1 provider is actually available
            statuses = dispatcher.get_providers_status()
            available = [s for s in statuses if s.available]
            if len(available) >= 1:
                result = dispatcher.chat(messages, model=self.model)
                return result.content
        except Exception:
            pass  # fall through to direct API call

        # Direct DeepSeek API call (original fallback)
        import urllib.request, urllib.error

        data = json.dumps({
            "model": self.model,
            "messages": messages,
            "temperature": 0.8,
            "max_tokens": 4096,
        }).encode("utf-8")

        req = urllib.request.Request(
            f"{self.base_url}/v1/chat/completions",
            data=data,
            headers={
                "Content-Type": "application/json",
                "Authorization": f"Bearer {self.api_key}",
            },
        )

        try:
            with urllib.request.urlopen(req, timeout=120) as resp:
                result = json.loads(resp.read())
                return result["choices"][0]["message"]["content"]
        except urllib.error.HTTPError as e:
            body = e.read().decode()[:500]
            raise RuntimeError(f"API HTTP {e.code}: {body}")
        except urllib.error.URLError as e:
            raise RuntimeError(f"API unreachable: {e.reason}")

    # ─── Response Parsing ─────────────────────────────────

    @staticmethod
    def _safe_json_parse(text: str) -> dict:
        """Robust JSON parsing for LLM responses. Tries multiple repair strategies."""
        # Strategy 1: direct parse
        try:
            return json.loads(text)
        except json.JSONDecodeError:
            pass

        # Strategy 2: extract from code fence and parse
        match = re.search(r'```(?:json)?\s*\n?(.*?)\n?```', text, re.DOTALL)
        if match:
            inner = match.group(1).strip()
            start = inner.find('{')
            end = inner.rfind('}')
            if start >= 0 and end > start:
                try:
                    return json.loads(inner[start:end + 1])
                except json.JSONDecodeError:
                    pass

        # Strategy 3: fix common LLM JSON errors
        cleaned = text
        # Remove trailing commas before } or ]
        cleaned = re.sub(r',\s*}', '}', cleaned)
        cleaned = re.sub(r',\s*]', ']', cleaned)
        # Fix unescaped newlines in string values
        # Fix missing commas between key-value pairs
        cleaned = re.sub(r'"\s*\n\s*"', '",\n"', cleaned)
        # Remove BOM and other invisible chars
        cleaned = cleaned.replace('﻿', '').replace('\r', '')

        start = cleaned.find('{')
        end = cleaned.rfind('}')
        if start >= 0 and end > start:
            cleaned = cleaned[start:end + 1]
            try:
                return json.loads(cleaned)
            except json.JSONDecodeError:
                pass

        # Strategy 4: try to find and parse any valid JSON object in the text
        for m in re.finditer(r'\{[^{}]*\{[^{}]*\}[^{}]*\}', text, re.DOTALL):
            try:
                return json.loads(m.group())
            except json.JSONDecodeError:
                continue

        # Strategy 5: raise with helpful context
        raise ValueError(
            f"Could not parse LLM response as JSON. "
            f"Response starts with: {text[:200]}... "
            f"Response ends with: ...{text[-200:]}"
        )

    def _parse_response(self, response: str, vt: dict, brand: dict) -> Script:
        """解析LLM返回的JSON → Script对象."""
        json_str = response

        # 去掉markdown code fence
        match = re.search(r'```(?:json)?\s*\n?(.*?)\n?```', response, re.DOTALL)
        if match:
            json_str = match.group(1)

        # 找到第一个{和最后一个}
        start = json_str.find('{')
        end = json_str.rfind('}')
        if start >= 0 and end > start:
            json_str = json_str[start:end + 1]

        data = self._safe_json_parse(json_str)

        beats = []
        for b in data.get("beats", []):
            beats.append(Beat(
                index=b["index"],
                text=b["text"],
                visual=b["visual"],
                animation=b.get("animation", "fade"),
                emotion=b.get("emotion", "trust"),
                duration_s=b["duration_s"],
                is_save_trigger=b.get("is_save_trigger", False),
                is_share_trigger=b.get("is_share_trigger", False),
                is_comment_trigger=b.get("is_comment_trigger", False),
            ))

        outro_data = data.get("outro", {})
        outro = Beat(
            index=len(beats) + 1,
            text=outro_data.get("text", brand["outro_template"].format(**brand)),
            visual=outro_data.get("visual", "品牌logo + 关注引导"),
            animation="pop",
            emotion="action",
            duration_s=outro_data.get("duration_s", 6.0),
            is_save_trigger=True,
            is_share_trigger=True,
            is_comment_trigger=True,
        )

        total = sum(b.duration_s for b in beats) + outro.duration_s

        return Script(
            title=data.get("title", ""),
            hook_type=data.get("hook_type", vt["hook_patterns"][0]),
            beats=beats,
            outro=outro,
            tags=data.get("tags", vt.get("tags", [])),
            bgm_style=data.get("bgm_style", vt.get("bgm_style", "")),
            checklist=data.get("checklist", ""),
            total_duration_s=total,
        )


# ─── Convenience ────────────────────────────────────────────

def script_to_storyboard(script: Script) -> list[dict]:
    """将Script转换为兼容旧pipeline的分镜表格式."""
    shots = []
    for beat in script.beats:
        shots.append({
            "shot": beat.index,
            "duration": f"{beat.duration_s}s",
            "visual": beat.visual,
            "audio": beat.text,
            "caption": beat.text,
            "animation": beat.animation,
            "emotion": beat.emotion,
        })
    shots.append({
        "shot": script.outro.index,
        "duration": f"{script.outro.duration_s}s",
        "visual": script.outro.visual,
        "audio": script.outro.text,
        "caption": script.outro.text,
        "animation": "pop",
        "emotion": "action",
    })
    return shots
