"""CompositionBuilder — storyboard config → complete HyperFrames HTML.

Reads a structured storyboard (component sequence + timing), instantiates
each component, collects HTML/CSS/GSAP/SFX/subtitles, and assembles them
into a single renderable HyperFrames HTML file.

Usage:
    from builders import CompositionBuilder
    builder = CompositionBuilder(style_config)
    html = builder.build(storyboard_json)
    # Write html to index.html, then run: npm run render
"""

import json, os
from dataclasses import dataclass, field
from pathlib import Path
from typing import Optional

from .components import (
    COMPONENT_REGISTRY,
    Component,
    SFXTrigger,
    SubtitleLine,
    load_components,
)


# ---------------------------------------------------------------------------
# Data types
# ---------------------------------------------------------------------------

@dataclass
class StoryboardConfig:
    """Complete storyboard for one video.

    Attributes:
        scenes: ordered list of {component, start, duration, config}
        audio_src: path to TTS narration MP3
        bgm_src: optional path to background music
        srt_path: path to SRT subtitle file
        global_overlays: scan overlay + progress bar timing
        metadata: title, author, etc.
    """
    scenes: list[dict]
    audio_src: str = ""
    bgm_src: str = ""
    srt_path: str = ""
    global_overlays: dict = field(default_factory=dict)
    metadata: dict = field(default_factory=dict)
    style: dict = field(default_factory=dict)

    @classmethod
    def from_json(cls, path: str) -> "StoryboardConfig":
        with open(path, "r", encoding="utf-8") as f:
            data = json.load(f)
        return cls(
            scenes=data.get("scenes", []),
            audio_src=data.get("audio_src", ""),
            bgm_src=data.get("bgm_src", ""),
            srt_path=data.get("srt_path", ""),
            global_overlays=data.get("global_overlays", {}),
            metadata=data.get("metadata", {}),
            style=data.get("style", {}),
        )

    def total_duration(self) -> float:
        if not self.scenes:
            return 0.0
        return max(s["start"] + s["duration"] for s in self.scenes)


# ---------------------------------------------------------------------------
# HTML template sections (static, assembled once)
# ---------------------------------------------------------------------------

_HTML_PREAMBLE = """<!doctype html>
<html lang="zh">
<head>
  <meta charset="UTF-8" />
  <meta name="viewport" content="width=1080, height=1920" />
  <script src="./gsap.min.js"></script>
  <style>
    * { margin: 0; padding: 0; box-sizing: border-box; }
    html, body {
      width: 1080px; height: 1920px; overflow: hidden;
      background: #06060b;
      font-family: "Noto Sans SC", "PingFang SC", "Microsoft YaHei", sans-serif;
    }
    .clip { position: absolute; top: 0; left: 0; width: 1080px; height: 1920px; }

    /* ── Global scene backgrounds ── */
    .scene-bg {
      position: absolute; top: 0; left: 0; width: 1080px; height: 1920px;
      background: radial-gradient(ellipse at 50% 40%, #0d1b2a 0%, #06060b 70%);
    }
    .scene-bg-dark {
      position: absolute; top: 0; left: 0; width: 1080px; height: 1920px;
      background: #06060b;
    }

    .gradient-top {
      position: absolute; top: 0; left: 0; width: 1080px; height: 200px;
      background: linear-gradient(to bottom, rgba(6,6,11,0.7) 0%, transparent 100%);
    }
    .gradient-bottom {
      position: absolute; bottom: 0; left: 0; width: 1080px; height: 500px;
      background: linear-gradient(to top, rgba(6,6,11,0.95) 0%, rgba(6,6,11,0.5) 50%, transparent 100%);
    }

    /* ── Click-to-start overlay ── */
    .start-overlay {
      position: fixed; top: 0; left: 0; width: 100%; height: 100%;
      background: rgba(6,6,11,0.92); z-index: 9999;
      display: flex; flex-direction: column; align-items: center; justify-content: center;
      cursor: pointer;
    }
    .start-overlay .play-icon {
      width: 120px; height: 120px; border-radius: 50%;
      border: 3px solid #00e676; color: #00e676;
      font-size: 40px; display: flex; align-items: center; justify-content: center;
      margin-bottom: 30px; transition: transform 0.2s;
    }
    .start-overlay:hover .play-icon { transform: scale(1.1); }
    .start-overlay .start-text {
      color: #aaa; font-size: 28px; letter-spacing: 4px;
    }

    /* ── Subtitle / Caption overlay ── */
    .subtitle-container {
      position: absolute; bottom: 200px; left: 60px; right: 60px;
      z-index: 50; pointer-events: none;
      text-align: center;
    }
    .subtitle-line {
      display: inline-block; color: #fff; font-size: 42px; font-weight: 700;
      line-height: 1.5; letter-spacing: 0.04em;
      padding: 12px 32px; border-radius: 12px;
      background: rgba(0,0,0,0.65);
      text-shadow: 0 2px 8px rgba(0,0,0,0.8);
      max-width: 960px;
    }
    .subtitle-line .sub-highlight {
      color: #ffeb3b; font-weight: 900;
    }
"""

_CSS_SEPARATOR = "\n    /* ── Component styles ── */\n"

_HTML_CLOSE_STYLE = """  </style>
</head>
<body>
  <div class="start-overlay" id="startOverlay" onclick="startVideo()">
    <div class="play-icon">▶</div>
    <div class="start-text">点击播放</div>
  </div>
"""

_HTML_BODY_END = """
</body>
</html>"""

_JS_PREAMBLE = """
<script>
// ═══════════════════════════════════════════════════════════
// HyperFrames timeline
// ═══════════════════════════════════════════════════════════
var E = "power3.inOut";
var AUTOPLAY = new URLSearchParams(window.location.search).has("autoplay");
var tl = gsap.timeline({ paused: !AUTOPLAY });

// ── Global timeline registration ──
window.__timelines = window.__timelines || {};
window.__timelines["main"] = tl;
"""

_JS_FOOTER = """
// ── Autoplay mode (recording) ──
if (AUTOPLAY) {
  var overlay = document.getElementById("startOverlay");
  if (overlay) overlay.style.display = "none";
  var a = document.getElementById("narration");
  if (a) a.play().catch(function(e) { console.log("Audio:", e); });
  var b = document.getElementById("bgm");
  if (b) { b.volume = 0.12; b.play().catch(function(){}); }
  tl.play();
}

// ── Click-to-start (fixes browser autoplay policy) ──
function startVideo() {
  var overlay = document.getElementById("startOverlay");
  if (overlay) overlay.style.display = "none";
  var a = document.getElementById("narration");
  if (a) a.play().catch(function(e) { console.log("Audio:", e); });
  var b = document.getElementById("bgm");
  if (b) { b.volume = 0.12; b.play().catch(function(){}); }
  tl.play();
}
</script>"""


# ---------------------------------------------------------------------------
# Builder
# ---------------------------------------------------------------------------

class CompositionBuilder:
    """Assembles storyboard config → complete HyperFrames HTML file.

    Accepts an injectable component_registry so the same builder can render
    AI照妖镜 and e-commerce component sets without cross-contamination.
    """

    def __init__(self, style_config: Optional[dict] = None,
                 component_registry: Optional[dict[str, type[Component]]] = None):
        load_components()
        self.style = style_config or {}
        self.component_registry = component_registry or COMPONENT_REGISTRY

    # ─── Public API ──────────────────────────────────────────

    def build(self, storyboard: StoryboardConfig) -> str:
        """Main entry: storyboard → complete HTML string."""
        components = self._instantiate_components(storyboard)
        return self._assemble(storyboard, components)

    def build_from_json(self, storyboard_path: str) -> str:
        """Load storyboard JSON and build HTML."""
        sb = StoryboardConfig.from_json(storyboard_path)
        if self.style:
            sb.style = {**self.style, **sb.style}
        return self.build(sb)

    def build_from_dict(self, storyboard_dict: dict) -> str:
        """Build from a raw storyboard dict (for programmatic use)."""
        sb = StoryboardConfig(
            scenes=storyboard_dict.get("scenes", []),
            audio_src=storyboard_dict.get("audio_src", ""),
            bgm_src=storyboard_dict.get("bgm_src", ""),
            srt_path=storyboard_dict.get("srt_path", ""),
            global_overlays=storyboard_dict.get("global_overlays", {}),
            metadata=storyboard_dict.get("metadata", {}),
            style={**self.style, **storyboard_dict.get("style", {})},
        )
        return self.build(sb)

    # ─── Internal: instantiation ─────────────────────────────

    def _instantiate_components(self, sb: StoryboardConfig) -> list[Component]:
        """Create Component instances from the scene list."""
        instances = []
        for scene in sb.scenes:
            comp_name = scene["component"]
            cls = self.component_registry.get(comp_name)
            if cls is None:
                raise KeyError(
                    f"Unknown component '{comp_name}'. "
                    f"Available: {list(self.component_registry)}"
                )
            inst = cls(
                config=scene.get("config", {}),
                start=scene["start"],
                duration=scene["duration"],
            )
            instances.append(inst)
        return instances

    # ─── Internal: assembly ──────────────────────────────────

    def _assemble(self, sb: StoryboardConfig, components: list[Component]) -> str:
        """Wire everything into a complete HTML document."""
        parts: list[str] = []

        # 1. Preamble (DOCTYPE → <style> start)
        parts.append(_HTML_PREAMBLE)

        # 1.5 Canvas dimensions override (from platform setting)
        parts.append(self._canvas_override_css(sb))

        # 2. Global CSS
        parts.append(self._global_css(sb))

        # 3. Component CSS (deduplicated — same component type reuses one CSS block)
        seen_css = set()
        deduped_css = []
        for c in components:
            css = c.css()
            if css and css not in seen_css:
                seen_css.add(css)
                deduped_css.append(css)
        if deduped_css:
            parts.append(_CSS_SEPARATOR)
            parts.append("\n".join(deduped_css))

        # 4. Close <style>, open <body>
        parts.append(_HTML_CLOSE_STYLE)

        # 5. Audio elements
        parts.append(self._audio_elements(sb))

        # 6. Global backgrounds (scene-bg, gradients — referenced by components)
        parts.append(self._global_bg_elements())

        # 7. Component HTML (body content) — wrapped in scene containers
        scene_visibility_js = []
        for i, comp in enumerate(components):
            html = comp.html()
            if html.strip():
                start = comp.start
                dur = comp.duration
                parts.append(f'<div id="scene_{i}" class="clip" style="opacity:0">')
                parts.append(html)
                parts.append('</div>')
                # Cross-fade scene in/out (0.25s transition instead of hard cut)
                show_at = max(0, start - 0.3)
                fade_dur = 0.25
                scene_visibility_js.append(
                    f'tl.set("#scene_{i}",{{opacity:0}},0);'
                )
                scene_visibility_js.append(
                    f'tl.to("#scene_{i}",{{opacity:1,duration:{fade_dur},ease:"power3.out"}},{show_at:.2f});'
                )
                scene_visibility_js.append(
                    f'tl.to("#scene_{i}",{{opacity:0,duration:{fade_dur},ease:"power3.in"}},{start + dur - fade_dur:.2f});'
                )

        # 8. Global overlays (scan line, progress bar)
        parts.append(self._global_overlay_elements(sb))

        # 8.5. Subtitle container
        parts.append(self._subtitle_elements(sb))

        # 9. Close body, open <script>
        parts.append(_HTML_BODY_END)

        # 10. JS preamble + GSAP timeline registration
        parts.append(_JS_PREAMBLE)

        # 11. Component GSAP code
        for comp in components:
            gsap_code = comp.gsap()
            if gsap_code.strip():
                parts.append(gsap_code)

        # 12. Global overlay GSAP
        parts.append(self._global_overlay_gsap(sb))

        # 12.5. Subtitle GSAP
        parts.append(self._subtitle_gsap(sb))

        # 13. Scene visibility control (show/hide each scene at correct time)
        parts.append("\n// Scene visibility")
        parts.extend(scene_visibility_js)

        # 14. JS footer (tl.play())
        parts.append(_JS_FOOTER)

        return "\n".join(parts)

    # ─── Global elements ─────────────────────────────────────

    def _canvas_override_css(self, sb: StoryboardConfig) -> str:
        """Override default 1080×1920 canvas when platform specifies different dimensions."""
        style = getattr(sb, "style", {}) or {}
        w = style.get("canvas_width", 1080)
        h = style.get("canvas_height", 1920)
        if w == 1080 and h == 1920:
            return ""  # default, no override needed
        return f"""
    /* ── Platform canvas override ── */
    html, body, .clip, .scene-bg, .scene-bg-dark, .scan-overlay,
    .scan-line, .progress-wrap, .gradient-top, .gradient-bottom {{
      width: {w}px !important;
      height: {h}px !important;
    }}
"""

    def _global_css(self, sb: StoryboardConfig) -> str:
        """CSS for global overlays (scan, progress bar).

        When brand colors are provided via storyboard.style, CSS custom properties
        override the default AI照妖镜 cyberpunk green with the creative brief's palette.
        """
        style = getattr(sb, "style", {}) or {}
        primary = style.get("primary_color", "#00e676")
        accent = style.get("accent_color", "#ff1744")

        return f"""
    /* ── Brand color overrides (from creative brief) ── */
    :root {{
      --brand-primary: {primary};
      --brand-accent: {accent};
    }}

    /* ── Scan overlay ── */
    .scan-overlay {{
      position: absolute; top: 0; left: 0; width: 1080px; height: 1920px;
      background: repeating-linear-gradient(
        0deg, transparent, transparent 2px,
        rgba(0,230,118,0.006) 2px, rgba(0,230,118,0.006) 4px
      );
      z-index: 30; pointer-events: none;
    }}
    .scan-line {{
      position: absolute; left: 0; width: 1080px; height: 2px;
      background: linear-gradient(90deg, transparent, rgba(0,230,118,0.12), transparent);
      z-index: 31; pointer-events: none;
    }}

    /* ── Progress bar ── */
    .progress-wrap {{
      position: absolute; top: 0; left: 0; width: 1080px; height: 4px;
      z-index: 32; background: rgba(255,255,255,0.03);
    }}
    .progress-fill {{
      height: 100%; width: 0%;
      background: linear-gradient(90deg, var(--brand-primary), {primary}cc);
    }}
    .progress-dot {{
      position: absolute; top: 0; width: 3px; height: 6px;
      background: var(--brand-accent);
    }}
"""

    def _audio_elements(self, sb: StoryboardConfig) -> str:
        """Generate <audio> elements for narration + optional BGM."""
        lines = []
        if sb.audio_src:
            audio_name = os.path.basename(sb.audio_src)
            lines.append(
                f'  <audio id="narration" src="audio/{audio_name}" '
                f'style="display:none;"></audio>'
            )
        if sb.bgm_src:
            bgm_name = os.path.basename(sb.bgm_src)
            lines.append(
                f'  <audio id="bgm" src="{bgm_name}" loop '
                f'style="display:none;"></audio>'
            )
        return "\n".join(lines)

    def _global_bg_elements(self) -> str:
        return """  <div class="scene-bg-dark" id="globalBgDark"></div>
  <div class="gradient-top" id="gradTop"></div>
  <div class="gradient-bottom" id="gradBot"></div>"""

    def _global_overlay_elements(self, sb: StoryboardConfig) -> str:
        """Generate scan overlay + progress bar HTML."""
        overlays = sb.global_overlays
        total = sb.total_duration()

        parts = [
            '  <div class="scan-overlay" id="scanOverlay"></div>',
            '  <div class="scan-line" id="scanLine" style="top:0;"></div>',
            '  <div class="progress-wrap"><div class="progress-fill" id="progressFill"></div>',
        ]

        # Segment dots on progress bar
        segments = overlays.get("progress_segments", [])
        for seg in segments:
            pct = (seg["start"] / total) * 100
            parts.append(
                f'    <div class="progress-dot" style="left:{pct:.1f}%;"></div>'
            )

        parts.append('  </div>')
        return "\n".join(parts)

    def _global_overlay_gsap(self, sb: StoryboardConfig) -> str:
        """GSAP for scan overlay + progress bar."""
        overlays = sb.global_overlays
        total = sb.total_duration()

        scan_show = overlays.get("scan_show_at", 0)
        scan_hide = overlays.get("scan_hide_at", total)

        return f"""
// ── Scan overlay ──
tl.set("#scanOverlay",{{opacity:0}},0);
tl.set("#scanLine",{{opacity:0}},0);
tl.to("#scanOverlay",{{opacity:1,duration:0.4}},{scan_show});
tl.to("#scanLine",{{opacity:1,duration:0.4}},{scan_show});
tl.to("#scanOverlay",{{opacity:0,duration:0.3}},{scan_hide});
tl.to("#scanLine",{{opacity:0,duration:0.3}},{scan_hide});
tl.fromTo("#scanLine",{{top:"-2%"}},{{top:"102%",duration:{scan_hide-scan_show},ease:"none"}},{scan_show});

// ── Progress bar ──
tl.to("#progressFill",{{width:"100%",duration:{total},ease:"none"}},0);

// ── Audio now handled by startVideo() click handler ──"""

    # ─── Subtitle rendering ──────────────────────────────────

    @staticmethod
    def _parse_srt(srt_path: str) -> list[dict]:
        """Parse SRT file into list of {start, end, text} dicts."""
        entries = []
        if not srt_path or not os.path.exists(srt_path):
            return entries

        with open(srt_path, "r", encoding="utf-8") as f:
            content = f.read().strip()

        if not content:
            return entries

        import re
        blocks = re.split(r'\n\s*\n', content)
        for block in blocks:
            lines = block.strip().split('\n')
            if len(lines) < 3:
                continue
            # Line 0: index (skip), Line 1: timestamp, Line 2+: text
            ts_match = re.match(
                r'(\d+):(\d+):(\d+)[,.](\d+)\s*-->\s*(\d+):(\d+):(\d+)[,.](\d+)',
                lines[1]
            )
            if not ts_match:
                continue
            start_s = (
                int(ts_match.group(1)) * 3600
                + int(ts_match.group(2)) * 60
                + int(ts_match.group(3))
                + int(ts_match.group(4)) / 1000.0
            )
            end_s = (
                int(ts_match.group(5)) * 3600
                + int(ts_match.group(6)) * 60
                + int(ts_match.group(7))
                + int(ts_match.group(8)) / 1000.0
            )
            text = " ".join(lines[2:]).strip()
            entries.append({"start": start_s, "end": end_s, "text": text})

        return entries

    def _subtitle_elements(self, sb: StoryboardConfig) -> str:
        """Generate subtitle container + individual line divs from SRT."""
        import os as _os
        srt_entries = self._parse_srt(sb.srt_path)
        if not srt_entries:
            return '  <div class="subtitle-container" id="subContainer"></div>'

        lines_html = []
        for i, entry in enumerate(srt_entries):
            lines_html.append(
                f'    <div class="subtitle-line" id="sub{i}" '
                f'style="opacity:0;">{entry["text"]}</div>'
            )

        html = '\n  <div class="subtitle-container" id="subContainer">\n'
        html += '\n'.join(lines_html)
        html += '\n  </div>'
        return html

    def _subtitle_gsap(self, sb: StoryboardConfig) -> str:
        """GSAP animation to show/hide subtitle lines at correct times."""
        srt_entries = self._parse_srt(sb.srt_path)
        if not srt_entries:
            return "\n// Subtitles: no SRT data"

        lines = ["\n// ── Subtitles (from SRT) ──"]
        for i, entry in enumerate(srt_entries):
            start = entry["start"]
            end = entry["end"]
            # Show subtitle at start time
            lines.append(
                f'tl.set("#sub{i}",{{opacity:0}},{start:.2f});'
            )
            lines.append(
                f'tl.to("#sub{i}",{{opacity:1,duration:0.15,ease:"power3.out"}},{start:.2f});'
            )
            # Hide at end time
            lines.append(
                f'tl.to("#sub{i}",{{opacity:0,duration:0.1,ease:"power3.in"}},{end:.2f});'
            )
        return "\n".join(lines)

    # ─── Extraction helpers (for other pipeline stages) ───────

    def extract_sfx(self, storyboard: StoryboardConfig) -> list[SFXTrigger]:
        """Extract all SFX triggers from a storyboard (for external SFX mixing)."""
        components = self._instantiate_components(storyboard)
        triggers = []
        for comp in components:
            triggers.extend(comp.sfx())
        return sorted(triggers, key=lambda t: t.at_time)

    def extract_subtitles(self, storyboard: StoryboardConfig) -> list[SubtitleLine]:
        """Extract all subtitle lines from a storyboard."""
        components = self._instantiate_components(storyboard)
        lines = []
        for comp in components:
            lines.extend(comp.subtitles())
        return sorted(lines, key=lambda s: s.start)
