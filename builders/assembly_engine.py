"""Assembly Engine — 最终组装：TTS → HTML → 视频导出.

将 Script + AssetPlan 组装为可渲染的完整视频文件。
整合现有的 TTSBuilder 和 CompositionBuilder，添加自动导出能力。

Usage:
    from builders.assembly_engine import AssemblyEngine
    engine = AssemblyEngine(output_dir="output/ep1")
    result = engine.assemble(script, asset_plan)
    # result.html_path → 可浏览器预览
    # result.audio_path → 口播MP3
    # result.srt_path   → 字幕SRT
"""

import os, json, shutil
from dataclasses import dataclass, field
from pathlib import Path
from typing import Optional


# ─── Data Types ─────────────────────────────────────────────

@dataclass
class AssemblyResult:
    """组装结果 — 所有可交付文件."""
    html_path: str          # HyperFrames HTML（浏览器预览 & Chromium渲染）
    audio_path: str         # 口播MP3
    srt_path: str           # 字幕SRT
    bgm_path: str           # 背景音乐（可选）
    output_dir: str
    total_duration_s: float
    metadata: dict


# ─── Engine ─────────────────────────────────────────────────

class AssemblyEngine:
    """脚本+素材 → 最终视频文件.

    Supports two independent component libraries:
      - component_set="ai_flaw_detect" → COMPONENT_REGISTRY (AI照妖镜)
      - component_set="ecommerce"      → ECOMMERCE_REGISTRY (电商带货)
    """

    def __init__(self, output_dir: str = "output", tts_voice: str = None,
                 tts_speed: float = 1.1, canvas_width: int = 1080, canvas_height: int = 1920,
                 component_set: str = "ai_flaw_detect"):
        self.output_dir = Path(output_dir)
        self.output_dir.mkdir(parents=True, exist_ok=True)
        self.tts_voice = tts_voice or "zh-CN-YunxiNeural"
        self.tts_speed = tts_speed
        self.canvas_width = canvas_width
        self.canvas_height = canvas_height
        self.component_set = component_set
        self._component_registry = self._resolve_registry(component_set)

    @staticmethod
    def _resolve_registry(component_set: str) -> dict:
        """Map component_set name → component registry dict.

        The two libraries are completely independent — no shared state.
        """
        if component_set == "ecommerce":
            from .components_ecommerce import ECOMMERCE_REGISTRY
            return ECOMMERCE_REGISTRY
        else:
            from .components import COMPONENT_REGISTRY
            return COMPONENT_REGISTRY

    # ─── Public API ─────────────────────────────────────

    def assemble(self, script, asset_plan: dict,
                 bgm_path: str = "", bgm_tracks: list = None,
                 ref_analysis: dict = None) -> AssemblyResult:
        """完整组装流程：TTS → Storyboard → HTML → 导出.

        Args:
            script: Script对象
            asset_plan: AssetPipeline.resolve()的输出 {beat_index: AssetPlan}
            bgm_path: 背景音乐文件路径（单轨，可选）
            bgm_tracks: 多轨BGM配置 [{"src", "start", "end", "volume"}, ...]（可选）
            ref_analysis: 参考图分析结果（可选，用于metadata）

        Returns:
            AssemblyResult 包含所有输出文件路径
        """
        audio_dir = self.output_dir / "audio"
        audio_dir.mkdir(parents=True, exist_ok=True)

        # 1. 生成TTS口播
        audio_path, srt_path, total_dur = self._generate_tts(script, audio_dir)

        # 1.5. 多轨BGM预混合（bgm_tracks优先于bgm_path）
        if bgm_tracks:
            from .bgm_mixer import BGMMixer
            mixer = BGMMixer()
            mixed_bgm = mixer.mix(bgm_tracks, total_dur,
                                  output_path=str(self.output_dir / "bgm_mixed.mp3"))
            if mixed_bgm:
                bgm_path = mixed_bgm

        # 2. 构建storyboard（兼容CompositionBuilder格式）
        storyboard = self._build_storyboard(script, asset_plan, audio_path, bgm_path, ref_analysis)

        # 3. 保存storyboard JSON（供Jianying exporter使用）
        storyboard["srt_path"] = srt_path
        storyboard_path = str(self.output_dir / "storyboard.json")
        with open(storyboard_path, "w", encoding="utf-8") as f:
            json.dump(storyboard, f, ensure_ascii=False, indent=2)

        # 4. 生成HyperFrames HTML
        html_path = self._build_html(storyboard)

        # 4.5. 复制GSAP到output目录（Chromium渲染需要，避免CDN超时）
        self._copy_gsap()

        # 5. 复制素材到output目录
        self._copy_assets(asset_plan)

        # 6. 写metadata
        metadata = self._write_metadata(script, ref_analysis, total_dur)

        return AssemblyResult(
            html_path=html_path,
            audio_path=audio_path,
            srt_path=srt_path,
            bgm_path=bgm_path,
            output_dir=str(self.output_dir),
            total_duration_s=total_dur,
            metadata=metadata,
        )

    def export_jianying(self, result: AssemblyResult) -> Optional[str]:
        """Export storyboard as Jianying draft with all Route A features."""
        try:
            from builders.jianying_exporter import JianyingDraftExporter
            exporter = JianyingDraftExporter()

            # Reconstruct storyboard with SRT path for subtitle import
            storyboard_path = os.path.join(result.output_dir, "storyboard.json")
            storyboard = {}
            if os.path.exists(storyboard_path):
                import json
                with open(storyboard_path, "r", encoding="utf-8") as f:
                    storyboard = json.load(f)

            storyboard["srt_path"] = result.srt_path

            draft_path = exporter.export(
                storyboard,
                assets_dir=os.path.join(result.output_dir, "assets"),
                srt_path=result.srt_path,
            )
            return draft_path
        except ImportError:
            return None
        except Exception as e:
            print(f"Jianying export failed: {e}")
            return None

    # ─── TTS Generation ──────────────────────────────────

    def _generate_tts(self, script, audio_dir: Path) -> tuple[str, str, float]:
        """生成口播音频 + SRT字幕."""
        try:
            from ..generators.tts_builder import TTSBuilder
            builder = TTSBuilder(voice=self.tts_voice, speed=self.tts_speed)
            timeline = builder.build_from_script(script)
        except ImportError:
            # Fallback: 直接用edge-tts命令行
            timeline = self._tts_fallback(script, audio_dir)

        # 复制到output目录
        audio_dest = str(audio_dir / "narration.mp3")
        srt_dest = str(audio_dir / "subtitles.srt")

        if timeline.audio_path and os.path.exists(timeline.audio_path):
            if timeline.audio_path != audio_dest:
                shutil.copy2(timeline.audio_path, audio_dest)
        if timeline.srt_path and os.path.exists(timeline.srt_path):
            if timeline.srt_path != srt_dest:
                shutil.copy2(timeline.srt_path, srt_dest)

        return audio_dest, srt_dest, timeline.total_duration_s

    def _tts_fallback(self, script, audio_dir: Path):
        """直接用edge-tts命令行生成口播."""
        import subprocess, tempfile
        from dataclasses import dataclass as dc

        @dc
        class FallbackTimeline:
            audio_path: str = ""
            srt_path: str = ""
            total_duration_s: float = 0.0

        # 拼接所有beat的文本
        lines = [b.text for b in script.beats]
        lines.append(script.outro.text)
        full_text = " ".join(lines)

        # 写临时文本文件
        text_file = audio_dir / "_tts_input.txt"
        text_file.write_text(full_text, encoding="utf-8")
        audio_file = audio_dir / "narration.mp3"
        srt_file = audio_dir / "subtitles.srt"

        cmd = [
            "edge-tts", "--voice", self.tts_voice,
            "--rate", f"{int((self.tts_speed - 1) * 100):+d}%",
            "-f", str(text_file),
            "--write-media", str(audio_file),
            "--write-subtitles", str(srt_file),
        ]

        try:
            subprocess.run(cmd, check=True, capture_output=True, timeout=120)
        except (subprocess.CalledProcessError, FileNotFoundError):
            # edge-tts不可用时创建占位文件
            audio_file.write_bytes(b"")
            srt_file.write_text("", encoding="utf-8")

        total = sum(b.duration_s for b in script.beats) + script.outro.duration_s
        return FallbackTimeline(
            audio_path=str(audio_file),
            srt_path=str(srt_file),
            total_duration_s=total,
        )

    # ─── Storyboard Construction ─────────────────────────

    def _build_storyboard(self, script, asset_plan: dict,
                          audio_path: str, bgm_path: str,
                          ref_analysis: dict = None) -> dict:
        """构建CompositionBuilder兼容的storyboard格式.

        优先使用AssetPipeline匹配的素材，匹配不到时回退到参考图。
        """
        from .asset_pipeline import AssetPlan

        # Collect all available reference images for fallback
        ref_images = self._collect_ref_images(ref_analysis)

        scenes = []
        for i, beat in enumerate(script.beats):
            ap = asset_plan.get(beat.index)
            asset = ap.matched_asset if ap else None
            raw_path = asset.file_path if asset and asset.file_path else ""
            img_path = self._relpath(raw_path) if raw_path else ""

            # Fallback: use reference image when no asset matched
            if not img_path and ref_images:
                raw = ref_images[i % len(ref_images)]
                img_path = self._relpath(raw) if raw else ""

            # Derive zoom scale from animation type
            zoom_scale = 2.6
            if beat.animation == "zoom" or beat.emotion in ("surprise", "hook"):
                zoom_scale = 3.0

            component_name = self._pick_component(beat, asset)
            config = self._build_beat_config(beat, img_path, asset, zoom_scale, component_name)

            scene = {
                "component": component_name,
                "start": self._beat_start_time(script, beat.index),
                "duration": beat.duration_s,
                "config": config,
            }
            scenes.append(scene)

        # Outro scene
        ap = asset_plan.get(script.outro.index)
        outro_asset = ap.matched_asset if ap else None
        raw_outro = outro_asset.file_path if outro_asset and outro_asset.file_path else ""
        outro_img = self._relpath(raw_outro) if raw_outro else ""
        if not outro_img and ref_images:
            outro_img = self._relpath(ref_images[0]) if ref_images[0] else ""

        # Build tag string for teaser
        tag_str = " ".join(f"#{t}" for t in (script.tags or [])[:4])

        scenes.append({
            "component": "cta-outro" if self.component_set == "ecommerce" else "outro",
            "start": self._beat_start_time(script, script.outro.index),
            "duration": script.outro.duration_s,
            "config": {
                "caption": script.outro.text,
                "visual_desc": script.outro.visual,
                "animation": "pop",
                "emotion": "action",
                "img_src": outro_img,
                "asset_path": outro_img,
                "asset_type": "image",
                "title": "AI照妖镜",
                "subtitle": "关注我，下次被骗的不是你",
                "teaser": f"下期继续拆解AI破绽 →",
                "checklist": script.checklist,
                "tags": tag_str,
            },
        })

        total_dur = sum(b.duration_s for b in script.beats) + script.outro.duration_s

        return {
            "scenes": scenes,
            "audio_src": self._relpath(audio_path) if audio_path else "",
            "bgm_src": self._relpath(bgm_path) if bgm_path else "",
            "global_overlays": {
                "scan_show_at": 0,
                "scan_hide_at": total_dur,
                "progress_segments": [
                    {"start": self._beat_start_time(script, b.index), "label": f"Beat{b.index}"}
                    for b in script.beats
                ],
            },
            "metadata": {
                "title": script.title,
                "hook_type": script.hook_type,
                "bgm_style": script.bgm_style,
                "tags": script.tags,
                "checklist": script.checklist,
            },
            "style": {
                **self._extract_brand_style(ref_analysis),
                "canvas_width": self.canvas_width,
                "canvas_height": self.canvas_height,
            },
        }

    def _build_html(self, storyboard: dict) -> str:
        """调用CompositionBuilder生成HTML，传入当前组件库."""
        from .composition_builder import CompositionBuilder
        builder = CompositionBuilder(component_registry=self._component_registry)
        html = builder.build_from_dict(storyboard)

        html_path = self.output_dir / "index.html"
        html_path.write_text(html, encoding="utf-8")
        return str(html_path)

    def _pick_component(self, beat, asset) -> str:
        """根据beat内容和素材类型选择合适的视觉组件.

        Two independent mappings — one per component_set.
        """
        if self.component_set == "ecommerce":
            return self._pick_component_ecommerce(beat, asset)
        return self._pick_component_ai_flaw_detect(beat, asset)

    def _pick_component_ai_flaw_detect(self, beat, asset) -> str:
        """AI照妖镜 component mapping."""
        animation = beat.animation
        emotion = beat.emotion

        # Non-image components for visual variety
        if beat.is_save_trigger:
            return "checklist-card"
        elif animation == "zoom" or emotion in ("surprise", "curiosity"):
            # Check if this beat needs comparison (real vs AI)
            if any(kw in beat.visual for kw in ["对比", "真人", "真实", "正常", "vs"]):
                return "compare-split"
            return "zoom-analyze"
        elif emotion == "hook":
            return "reveal-text"
        elif beat.is_comment_trigger:
            return "social-frame"
        elif animation == "pulse" or emotion == "trust":
            return "data-card"
        elif emotion == "action":
            return "glitch-transition"
        elif animation == "slide" or "对比" in beat.visual:
            return "compare-split"
        else:
            return "title-card"

    def _pick_component_ecommerce(self, beat, asset) -> str:
        """E-commerce component mapping — product-centric narrative beats."""
        animation = beat.animation
        emotion = beat.emotion

        # Opening hook → product reveal with title + price
        if emotion == "hook" or beat.index == 1:
            return "hook-reveal"
        # Zoom/detail → feature highlight with tag chips
        elif animation == "zoom" or emotion in ("surprise", "curiosity"):
            return "feature-highlight"
        # Lifestyle scene → product in environment
        elif emotion == "desire":
            return "scene-lifestyle"
        # Social proof → trust signals
        elif emotion == "trust":
            return "trust-signal"
        # Before/after comparison
        elif animation == "slide":
            return "before-after"
        elif self._is_before_after_visual(beat):
            return "before-after"
        # CTA / save / end
        elif emotion == "action" or beat.is_save_trigger or beat.is_comment_trigger:
            return "cta-outro"
        else:
            return "hook-reveal"

    @staticmethod
    def _is_before_after_visual(beat) -> bool:
        """Check if beat.visual describes a before/after comparison scene."""
        keywords = ["对比", "分屏", "before", "after", "前后", "左右对比",
                    "左边", "右边", "一半", "对比图", "vs", "versus"]
        visual = getattr(beat, "visual", "") or ""
        text = getattr(beat, "text", "") or ""
        combined = (visual + " " + text).lower()
        return any(kw in combined for kw in keywords)

    def _build_beat_config(self, beat, img_path: str, asset,
                           zoom_scale: float, component_name: str) -> dict:
        """Build rich config dict for a beat based on its component type.

        Each component type receives only the keys it actually consumes,
        populated from beat-level script data so nothing is hardcoded.
        """
        base = {
            "caption": beat.text,
            "visual_desc": beat.visual,
            "animation": beat.animation,
            "emotion": beat.emotion,
            "img_src": img_path,
            "asset_path": img_path,
            "asset_type": asset.asset_type if asset else "image",
            "zoom_scale": zoom_scale,
            "crop": asset.crop if asset and asset.crop else None,
            "scale": asset.scale if asset else 1.0,
            "is_save_trigger": beat.is_save_trigger,
            "is_share_trigger": beat.is_share_trigger,
            "is_comment_trigger": beat.is_comment_trigger,
        }

        # --- Component-specific enrichments ---

        if component_name == "social-frame":
            base.update({
                "username": "AI照妖镜",
                "avatar_letter": "鉴",
                "post_text": beat.text,
                "post_time": "刚刚",
                "likes": "1.2k",
                "comments": [
                    ("小明", "这怎么看出来的？教教我"),
                    ("小红", "天哪我一直以为是真的"),
                    ("AI照妖镜", "点个关注，下期教你"),
                ],
            })

        elif component_name == "reveal-text":
            base.update({
                "reveal_text": beat.text.replace("|", "<br/>"),
                "anticipation_text": "来，我放大细节给你看 ↓",
            })

        elif component_name == "zoom-analyze":
            # Extract keyword from visual description
            keyword = beat.visual[:12] if beat.visual else beat.text[:10]
            base.update({
                "label": f"破绽 {beat.index}",
                "keyword_text": keyword,
                "keyword_color": "#ff1744",
                "keyword_top": 800,
                "keyword_left": 80,
                "keyword_delay": 1.2,
                "markers": [
                    {"id": f"mk_a_{beat.index}", "x": 540, "y": 620, "w": 200, "h": 200, "delay": 1.5},
                    {"id": f"mk_b_{beat.index}", "x": 380, "y": 1050, "w": 160, "h": 160, "delay": 2.3},
                ],
            })

        elif component_name == "compare-split":
            base.update({
                "ai_img": img_path,
                "real_img": img_path,  # Will be overridden when real photo is available
                "checks": [
                    {"label": beat.visual or "AI生成", "fail": "✗ 有破绽", "pass": "✓ 真实"},
                ],
                "summary_text": beat.text,
            })

        elif component_name == "title-card":
            base.update({
                "title_text": beat.text,
                "subtitle_text": beat.visual,
                "icon": self._icon_for_beat(beat),
            })

        elif component_name == "data-card":
            base.update({
                "big_number": self._extract_stat(beat),
                "label": beat.text,
                "source_text": "来源：AI生成模型分析",
            })

        elif component_name == "checklist-card":
            base.update({
                "title_text": beat.text,
                "items": self._build_checklist_items(beat),
            })

        return base

    @staticmethod
    def _icon_for_beat(beat) -> str:
        icon_map = {
            "hook": "🎯", "curiosity": "🔍", "surprise": "⚡",
            "trust": "🛡️", "desire": "💡", "action": "📌",
        }
        return icon_map.get(beat.emotion, "🔍")

    @staticmethod
    def _extract_stat(beat) -> str:
        """Extract a percentage/number from beat text for data-card."""
        import re
        pct = re.findall(r'(\d+%)', beat.text)
        if pct:
            return pct[0]
        num = re.findall(r'(\d+)', beat.text)
        if num:
            return f"{num[0]}%"
        return "70%"

    @staticmethod
    def _build_checklist_items(beat) -> list[dict]:
        """Build checklist items from beat visual/checklist text."""
        raw = beat.visual or beat.text
        # Split by common separators
        import re
        parts = re.split(r'[①-⑥①②③④⑤⑥]', raw)
        parts = [p.strip() for p in parts if p.strip() and len(p.strip()) > 2]
        if not parts:
            parts = [line.strip() for line in raw.replace('；', ';').replace('。', ';').split(';') if line.strip()]

        items = []
        for part in parts[:5]:
            items.append({"label": part[:20], "result": "✗", "is_pass": False})
        return items if items else [
            {"label": "检查点 1", "result": "✗", "is_pass": False},
            {"label": "检查点 2", "result": "✓", "is_pass": True},
        ]

    def _beat_start_time(self, script, beat_index: int) -> float:
        """计算某个beat的累计开始时间."""
        t = 0.0
        for b in script.beats:
            if b.index >= beat_index:
                break
            t += b.duration_s
        # 如果beat_index大于所有beat数，那就是outro
        if beat_index > len(script.beats):
            t = sum(b.duration_s for b in script.beats)
        return t

    def _relpath(self, abspath: str) -> str:
        """Convert absolute path to HTTP-server-relative path (from output_dir)."""
        if not abspath:
            return ""
        try:
            return str(Path(os.path.abspath(abspath)).relative_to(self.output_dir.resolve())).replace("\\", "/")
        except ValueError:
            return abspath.replace("\\", "/")

    @staticmethod
    def _extract_brand_style(ref_analysis: dict = None) -> dict:
        """Extract brand identity (colors, mood) from ref_analysis for CompositionBuilder CSS.

        Passed through as storyboard.style → CompositionBuilder.style → CSS custom properties.
        Overrides the hardcoded AI照妖镜 cyberpunk green theme with creative brief colors.
        """
        if not ref_analysis:
            return {}
        brand = ref_analysis.get("brand_style", {})
        if not brand:
            return {}
        style = {}
        colors = brand.get("colors", {})
        if colors.get("primary"):
            style["primary_color"] = colors["primary"]
        if colors.get("secondary"):
            style["secondary_color"] = colors["secondary"]
        if colors.get("accent"):
            style["accent_color"] = colors["accent"]
        if brand.get("concept_name"):
            style["concept_name"] = brand["concept_name"]
        return style

    @staticmethod
    def _collect_ref_images(ref_analysis: dict = None) -> list[str]:
        """Extract all available image paths from reference analysis results."""
        images = []
        if not ref_analysis:
            return images

        results = ref_analysis.get("results", [ref_analysis])
        if isinstance(results, dict):
            results = [results]

        for r in results:
            img = r.get("image", "") or r.get("image_path", "")
            if img and os.path.exists(img) and img not in images:
                images.append(img)

        # Also check top-level image_path
        top_img = ref_analysis.get("image_path", "")
        if top_img and os.path.exists(top_img) and top_img not in images:
            images.append(top_img)

        return images

    # ─── Asset Management ─────────────────────────────────

    def _copy_gsap(self):
        """Copy local GSAP library to output dir so Chromium doesn't hit CDN."""
        gsap_src = Path(__file__).parent / "static" / "gsap.min.js"
        gsap_dst = self.output_dir / "gsap.min.js"
        if gsap_src.exists() and not gsap_dst.exists():
            shutil.copy2(gsap_src, gsap_dst)

    def _copy_assets(self, asset_plan: dict):
        """复制素材文件到output目录."""
        assets_dir = self.output_dir / "assets"
        assets_dir.mkdir(parents=True, exist_ok=True)

        for beat_idx, ap in asset_plan.items():
            src = ap.matched_asset.file_path
            if src and os.path.exists(src) and not src.startswith(str(self.output_dir)):
                dst = assets_dir / os.path.basename(src)
                if not dst.exists():
                    shutil.copy2(src, dst)

    def _write_metadata(self, script, ref_analysis: dict,
                        total_dur: float) -> dict:
        """写入视频元数据JSON."""
        meta = {
            "title": script.title,
            "hook_type": script.hook_type,
            "beats": len(script.beats),
            "total_duration_s": total_dur,
            "bgm_style": script.bgm_style,
            "tags": script.tags,
            "checklist": script.checklist,
            "has_save_trigger": any(b.is_save_trigger for b in script.beats),
            "has_share_trigger": any(b.is_share_trigger for b in script.beats),
            "has_comment_trigger": any(b.is_comment_trigger for b in script.beats),
        }

        if ref_analysis:
            meta["ref_source"] = ref_analysis.get("image_path", "")

        meta_path = self.output_dir / "metadata.json"
        meta_path.write_text(json.dumps(meta, ensure_ascii=False, indent=2),
                             encoding="utf-8")
        return meta


# ─── Convenience ────────────────────────────────────────────

def quick_assemble(script, asset_plan: dict, output_dir: str = "output") -> AssemblyResult:
    """快速组装（使用默认参数）."""
    engine = AssemblyEngine(output_dir=output_dir)
    return engine.assemble(script, asset_plan)
