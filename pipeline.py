"""End-to-end video production pipeline — Universal AI Video Platform.

New 6-stage flow (v2):
  1. Ref Analysis — vision model analyzes reference image for specifics
  2. Script Engine — 1 LLM call produces Beat-level script with algorithm triggers
  3. Quality Check — deterministic rules validate; auto-fix when possible
  4. TTS Narration — edge-tts generates spoken audio + SRT
  5. Asset Pipeline — multi-source: local → AI gen → stock fallback
  6. Assembly Engine — HTML generation + optional Jianying export

Supports ALL video types: ai_flaw_detect, product_promo, factory_promo, tutorial, vlog

Usage:
    # Reference image mode (core use case)
    python pipeline.py --ref assets/product.jpg --type product_promo --topic "无刷电机绕线机"

    # AI鉴定 mode
    python pipeline.py --ref assets/ai_flaw.png --type ai_flaw_detect --topic "AI文字破绽"

    # Episode mode (backward compatible)
    python pipeline.py --ep 2 --title "文字乱码"

    # With preview
    python pipeline.py --ref assets/img.png --preview
"""

import argparse, json, os, shutil, subprocess, sys, time, threading, webbrowser
from http.server import HTTPServer, SimpleHTTPRequestHandler
from pathlib import Path

# Force UTF-8 on Windows
if sys.platform == "win32":
    sys.stdout.reconfigure(encoding="utf-8", errors="replace")

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
ROOT = os.path.dirname(os.path.abspath(__file__))

# Load .env
_env_path = os.path.join(ROOT, ".env")
if os.path.exists(_env_path):
    with open(_env_path, encoding="utf-8") as _f:
        for _line in _f:
            _line = _line.strip()
            if _line and not _line.startswith("#") and "=" in _line:
                _k, _v = _line.split("=", 1)
                os.environ.setdefault(_k.strip(), _v.strip())

OUTPUT = os.path.join(ROOT, "output")

from generators.script_engine import ScriptEngine, Script
from generators.quality_checker import QualityChecker, QualityReport


# ---------------------------------------------------------------------------
# Pipeline
# ---------------------------------------------------------------------------

class VideoPipeline:
    """Orchestrates the full video production flow — v2 architecture.

    Uses multi-provider dispatch for both LLM (script generation) and TTS.
    Providers auto-fallback if the primary is unavailable.
    """

    def __init__(self):
        self.script_engine = ScriptEngine()
        self.quality_checker = QualityChecker()

        # Initialize multi-provider dispatchers
        from generators.llm_providers import get_dispatcher
        from generators.tts_providers import get_tts_dispatcher
        self.llm_dispatcher = get_dispatcher()
        self.tts_dispatcher = get_tts_dispatcher()

        # Wire dispatchers into the engines
        self.script_engine._dispatcher = self.llm_dispatcher

        # Show provider status
        self.llm_dispatcher.print_status()
        self.tts_dispatcher.print_status()
        print()

    # ─── Public API ──────────────────────────────────────────

    def run(self, ref_image: str = None, video_type: str = "ai_flaw_detect",
            topic: str = "", brand_dna: dict = None, output_dir: str = None,
            tts_voice: str = "zh-CN-YunxiNeural", tts_speed: float = 1.1,
            skip_tts: bool = False, skip_jianying: bool = False,
            bgm: bool = False, style_hint: str = "") -> dict:
        """Run the complete pipeline. Returns result metadata."""

        t0 = time.time()
        output_dir = output_dir or os.path.join(OUTPUT, "latest")
        os.makedirs(output_dir, exist_ok=True)

        print("=" * 60)
        print("Auto Video Platform v2 — Starting")
        print("=" * 60)
        print(f"  Type:     {video_type}")
        print(f"  Topic:    {topic or '(from ref)'}")
        print(f"  Ref:      {ref_image or '(none)'}")
        print(f"  Output:   {output_dir}")
        print()

        # ── Stage 1: Reference Analysis ──
        print("━" * 40)
        print("Stage 1/6: Reference Analysis")
        print("━" * 40)
        ref_analysis = {}
        if ref_image and os.path.exists(ref_image):
            ref_analysis = self._analyze_ref(ref_image)
            print(f"  Ref image: {ref_image}")
            print(f"  Description: {ref_analysis.get('description', '(none)')[:120]}...")
            if not topic:
                topic = ref_analysis.get("suggested_topic", "")
                print(f"  Auto topic: {topic}")
        else:
            print("  No reference image (script will be generated from topic only)")
        print()

        # ── Stage 2: Script Generation ──
        print("━" * 40)
        print("Stage 2/6: Script Generation (Beat-level)")
        print("━" * 40)
        script = self._generate_script(video_type, topic, ref_analysis,
                                       brand_dna, style_hint)
        self._print_script(script)
        script_path = os.path.join(output_dir, "script.json")
        self._save_script_json(script, script_path)
        print(f"  Script saved: {script_path}\n")

        # ── Stage 3: Quality Check ──
        print("━" * 40)
        print("Stage 3/6: Quality Check")
        print("━" * 40)
        report = self.quality_checker.check(script, video_type)
        print(f"  Score: {report.score}/100")
        for v in report.violations:
            print(f"  [{v.severity.upper()}] {v.rule}: {v.detail[:80]}")
        if report.warnings:
            for w in report.warnings[:3]:
                print(f"  [WARN] {w.rule}: {w.detail[:80]}")
        if not report.passed:
            print(f"\n  ⚠ Auto-fixing {len(report.violations)} violations...")
            script = self.quality_checker.auto_fix(script, report)
            # Re-check after fix
            report2 = self.quality_checker.check(script, video_type)
            print(f"  After fix: Score {report2.score}/100, Passed={report2.passed}")

        # Optional AI review (suggestions only)
        ai_suggestions = self.quality_checker.ai_review(script, video_type)
        if ai_suggestions:
            print(f"  AI suggestions: {len(ai_suggestions)}")
            for s in ai_suggestions:
                print(f"  [AI] {s.detail[:80]}")
        print()

        # ── Stage 4: TTS Narration ──
        print("━" * 40)
        print("Stage 4/6: TTS Narration")
        print("━" * 40)
        tts = None
        audio_src = ""
        if not skip_tts:
            try:
                tts = self._build_tts(script, output_dir, voice=tts_voice, speed=tts_speed)
                audio_src = tts.audio_path
                print(f"  Audio: {tts.audio_path}")
                print(f"  SRT:   {tts.srt_path}")
                print(f"  Duration: {tts.total_duration_s:.1f}s")
            except Exception as e:
                print(f"  TTS failed: {e} — continuing without audio")
        else:
            print("  TTS skipped (--skip-tts)")
        print()

        # ── BGM (optional) ──
        bgm_path = ""
        bgm_tracks = None
        if bgm:
            print("━" * 40)
            print("BGM Multi-Track Mix")
            print("━" * 40)
            total_sec = sum(b.duration_s for b in script.beats) + script.outro.duration_s
            bgm_tracks = self._build_bgm_tracks(script, total_sec, output_dir)
            if not bgm_tracks:
                # Fallback to single-track download
                bgm_path = self.download_bgm(output_dir, total_sec)
            print()

        # ── Stage 5: Asset Pipeline ──
        print("━" * 40)
        print("Stage 5/6: Asset Pipeline")
        print("━" * 40)
        asset_plan = self._resolve_assets(script, ref_analysis, ref_image)
        from builders.asset_pipeline import AssetPipeline
        ap = AssetPipeline(assets_dir=os.path.join(output_dir, "assets"))
        summary = ap.summary(asset_plan)
        print(f"  Total beats: {summary['total_beats']}")
        print(f"  Local assets: {summary['local_assets']}")
        print(f"  AI generated: {summary['ai_generated']}")
        print(f"  Stock fallback: {summary['stock_fallback']}")
        print(f"  Generation needed: {summary['generation_needed']}")

        # Actually generate missing assets
        if summary['generation_needed'] > 0:
            print(f"  Generating {summary['generation_needed']} assets...")
            asset_plan = ap.generate_missing(asset_plan)
        print()

        # ── Stage 6: Assembly ──
        print("━" * 40)
        print("Stage 6/7: Assembly & HTML")
        print("━" * 40)
        from builders.assembly_engine import AssemblyEngine
        assembler = AssemblyEngine(output_dir=output_dir,
                                   tts_voice=tts_voice, tts_speed=tts_speed)
        result = assembler.assemble(script, asset_plan, bgm_path, bgm_tracks, ref_analysis)
        print(f"  HTML:  {result.html_path}")
        print(f"  Audio: {result.audio_path}")
        print(f"  SRT:   {result.srt_path}")

        # ── Stage 7: Chromium MP4 Render ──
        print()
        print("━" * 40)
        print("Stage 7/7: Chromium MP4 Render")
        print("━" * 40)
        mp4_path = ""
        mp4_error = ""
        try:
            from builders.chromium_renderer import ChromiumRenderer
            renderer = ChromiumRenderer()
            mp4_path = renderer.render(
                html_dir=output_dir,
                audio_path=result.audio_path if os.path.exists(result.audio_path) else "",
                bgm_path=result.bgm_path if result.bgm_path and os.path.exists(result.bgm_path) else "",
                duration_s=result.total_duration_s,
                output_path=os.path.join(output_dir, "output.mp4"),
            )
            print(f"  MP4: {mp4_path}")
            file_size_mb = os.path.getsize(mp4_path) / (1024 * 1024)
            print(f"  Size: {file_size_mb:.1f}MB")
        except FileNotFoundError as e:
            mp4_error = f"Browser not found: {e}"
            print(f"  Chromium render skipped: {mp4_error}")
        except Exception as e:
            mp4_error = f"{type(e).__name__}: {e}"
            print(f"  Chromium render failed: {mp4_error}")
            import traceback
            traceback.print_exc()

        # Jianying export
        jy_path = ""
        if not skip_jianying:
            jy_path = assembler.export_jianying(result) or ""
            if jy_path:
                print(f"  Jianying: {jy_path}")
            else:
                print(f"  Jianying: skipped (exporter not available)")

        elapsed = time.time() - t0
        print()
        print("=" * 60)
        print(f"Pipeline complete in {elapsed:.1f}s")
        print(f"  Output: {output_dir}")
        print(f"  HTML:   {result.html_path}")
        print(f"  MP4:    {mp4_path or '(skipped)'}")
        print(f"  Audio:  {result.audio_path}")
        print("=" * 60)

        return {
            "html_path": result.html_path,
            "audio_path": result.audio_path,
            "srt_path": result.srt_path,
            "mp4_path": mp4_path,
            "mp4_error": mp4_error,
            "duration_s": result.total_duration_s,
            "output_dir": output_dir,
            "jianying_draft": jy_path,
            "script": script,
        }

    # ─── Stage Implementations ───────────────────────────────

    def _analyze_ref(self, ref_image: str) -> dict:
        """Analyze reference image — uses Qwen-VL if available, falls back to local."""
        # Try Qwen-VL via API first
        try:
            result = self._call_vision_model(ref_image)
            if result:
                return result
        except Exception:
            pass

        # Fallback: basic metadata
        return {
            "image_path": os.path.abspath(ref_image),
            "filename": os.path.basename(ref_image),
            "description": f"Reference image: {os.path.basename(ref_image)}",
            "suggested_topic": "",
        }

    def _call_vision_model(self, image_path: str) -> dict:
        """Call Qwen-VL vision model for image analysis."""
        import base64
        import urllib.request, urllib.error

        api_key = os.environ.get("DASHSCOPE_API_KEY") or os.environ.get("DEEPSEEK_API_KEY")
        if not api_key:
            return {}

        # Read and encode image
        with open(image_path, "rb") as f:
            img_b64 = base64.b64encode(f.read()).decode()

        payload = json.dumps({
            "model": "qwen-vl-max",
            "messages": [{
                "role": "user",
                "content": [
                    {"type": "image_url", "image_url": {"url": f"data:image/png;base64,{img_b64}"}},
                    {"type": "text", "text": "分析这张图片。如果是产品图：列出产品名称、核心卖点（3-5个）、使用场景。如果是AI生成图：列出所有可识别的破绽（手指、文字、光影、纹理等），每个破绽给出具体位置描述。如果是工业设备图：列出设备名称、技术参数、应用场景。返回JSON格式。"},
                ],
            }],
            "temperature": 0.3,
            "max_tokens": 2000,
        }).encode("utf-8")

        # Try DashScope first, then DeepSeek
        for base_url, headers in [
            ("https://dashscope.aliyuncs.com/compatible-mode/v1/chat/completions",
             {"Authorization": f"Bearer {api_key}", "Content-Type": "application/json"}),
            ("https://api.deepseek.com/v1/chat/completions",
             {"Authorization": f"Bearer {api_key}", "Content-Type": "application/json"}),
        ]:
            try:
                req = urllib.request.Request(base_url, data=payload, headers=headers)
                with urllib.request.urlopen(req, timeout=60) as resp:
                    result = json.loads(resp.read())
                    content = result["choices"][0]["message"]["content"]
                    # Try to parse as JSON
                    try:
                        parsed = json.loads(content)
                    except json.JSONDecodeError:
                        # Extract JSON from text
                        import re
                        m = re.search(r'```(?:json)?\s*\n?(.*?)\n?```', content, re.DOTALL)
                        if m:
                            parsed = json.loads(m.group(1))
                        else:
                            parsed = {"description": content[:500]}
                    parsed["image_path"] = os.path.abspath(image_path)
                    parsed["raw_analysis"] = content
                    return parsed
            except Exception:
                continue

        return {}

    def _generate_script(self, video_type: str, topic: str,
                         ref_analysis: dict, brand_dna: dict,
                         style_hint: str) -> Script:
        """Generate Beat-level script via ScriptEngine."""
        return self.script_engine.generate(
            video_type=video_type,
            topic=topic,
            ref_analysis=ref_analysis if ref_analysis else None,
            brand_dna=brand_dna,
            style_hint=style_hint,
        )

    def _build_tts(self, script: Script, output_dir: str,
                   voice: str = "zh-CN-YunxiNeural", speed: float = 1.1):
        """Generate TTS from Script object — uses multi-provider dispatcher."""
        from generators.tts_builder import TTSBuilder

        tts_output = os.path.join(output_dir, "audio")
        tts = TTSBuilder(
            voice=voice,
            speed=speed,
            pause_between_shots=0.35,
            output_dir=tts_output,
            dispatcher=self.tts_dispatcher,
        )
        return tts.build_from_script(script)

    def _resolve_assets(self, script: Script, ref_analysis: dict,
                        ref_image: str = None) -> dict:
        """Resolve assets for each beat using AssetPipeline."""
        from builders.asset_pipeline import AssetPipeline

        # Determine assets directory
        if ref_analysis and ref_analysis.get("image_path"):
            assets_dir = os.path.dirname(ref_analysis["image_path"])
        elif ref_image:
            assets_dir = os.path.dirname(os.path.abspath(ref_image))
        else:
            assets_dir = os.path.join(ROOT, "output", "assets")

        pipeline = AssetPipeline(assets_dir=assets_dir)

        # Collect user assets
        user_assets = []
        if ref_image and os.path.exists(ref_image):
            user_assets.append(ref_image)
        if ref_analysis.get("image_path") and os.path.exists(ref_analysis["image_path"]):
            user_assets.append(ref_analysis["image_path"])

        return pipeline.resolve(script, ref_analysis, user_assets)

    # ─── Utility ──────────────────────────────────────────────

    @staticmethod
    def _print_script(script: Script):
        """Print script summary (Windows-safe encoding)."""
        def safe(s):
            return s.encode('utf-8', errors='replace').decode('utf-8', errors='replace')
        print(f"  Title:     {safe(script.title)}")
        print(f"  Hook:      {safe(script.hook_type)}")
        print(f"  Beats:     {len(script.beats)}")
        print(f"  Duration:  {script.total_duration_s:.1f}s")
        print(f"  BGM:       {safe(script.bgm_style)}")
        print(f"  Checklist: {safe(script.checklist)}")
        print(f"  Tags:      {safe(', '.join(script.tags[:5]))}")
        print()

        triggers = {"save": 0, "share": 0, "comment": 0}
        for b in script.beats:
            tag_parts = []
            if b.is_save_trigger:
                tag_parts.append("[SAVE]")
                triggers["save"] += 1
            if b.is_share_trigger:
                tag_parts.append("[SHARE]")
                triggers["share"] += 1
            if b.is_comment_trigger:
                tag_parts.append("[COMMENT]")
                triggers["comment"] += 1
            tag = " " + " ".join(tag_parts) if tag_parts else ""
            text_preview = b.text[:50]
            anim = b.animation or "none"
            emt = b.emotion or ""
            print(f"  Beat{b.index:2d} [{emt:10s}] [{anim:5s}] "
                  f"{b.duration_s:.1f}s {safe(text_preview)}{tag}")
        print(f"\n  Outro: {safe(script.outro.text[:60])}")
        print(f"  Triggers: SAVE x{triggers['save']} SHARE x{triggers['share']} COMMENT x{triggers['comment']}")
        print()

    @staticmethod
    def _save_script_json(script: Script, path: str):
        """Save Script to JSON."""
        beats_data = []
        for b in script.beats:
            beats_data.append({
                "index": b.index, "text": b.text, "visual": b.visual,
                "animation": b.animation, "emotion": b.emotion,
                "duration_s": b.duration_s,
                "is_save_trigger": b.is_save_trigger,
                "is_share_trigger": b.is_share_trigger,
                "is_comment_trigger": b.is_comment_trigger,
            })
        data = {
            "title": script.title,
            "hook_type": script.hook_type,
            "beats": beats_data,
            "outro": {
                "text": script.outro.text,
                "visual": script.outro.visual,
                "duration_s": script.outro.duration_s,
            },
            "tags": script.tags,
            "bgm_style": script.bgm_style,
            "checklist": script.checklist,
            "total_duration_s": script.total_duration_s,
        }
        with open(path, "w", encoding="utf-8") as f:
            json.dump(data, f, ensure_ascii=False, indent=2)

    # ─── BGM ─────────────────────────────────────────────────

    @staticmethod
    def _build_bgm_tracks(script, total_duration_s: float, output_dir: str) -> list | None:
        """Build per-beat BGM tracks — each beat gets its own BGM clip by emotion.

        Like a real editor placing clips on a timeline:
          - hook → stinger_hit (0-3s)
          - surprise → reveal_hit (3-8s)
          - curiosity → tense_loop (8-13s)
          - action → energy_beat (13-16s)
          ...

        Adjacent beats with the same BGM are merged. Falls back to None if
        no BGM files are found in bgm_library/.
        """
        from builders.bgm_mixer import BGMMixer

        mixer = BGMMixer()
        tracks = mixer.build_beat_tracks(script, total_duration_s)

        # Filter tracks whose sources actually exist
        valid = [t for t in tracks if mixer._resolve_src(t["src"])]
        if not valid:
            print("  [BGM] No BGM files found in bgm_library/ — falling back to download")
            return None

        print(f"  [BGM] Beat-level tracks: {len(valid)} segments over {total_duration_s:.0f}s")
        for t in valid:
            print(f"        {t['src']:18s} {t['start']:5.1f}s → {t['end']:5.1f}s  vol={t['volume']}")
        return valid

    @staticmethod
    def download_bgm(out_dir: str, duration_s: float = 60) -> str:
        """Download or generate royalty-free BGM."""
        bgm_path = os.path.join(out_dir, "bgm.mp3")
        if os.path.exists(bgm_path) and os.path.getsize(bgm_path) > 10000:
            return bgm_path

        try:
            import urllib.request
            bgm_urls = [
                "https://cdn.pixabay.com/audio/2024/01/16/audio_bf2dbfc98b.mp3",
                "https://cdn.pixabay.com/audio/2023/10/28/audio_946f3a6d2a.mp3",
            ]
            for url in bgm_urls:
                try:
                    req = urllib.request.Request(url, headers={
                        "User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64)"
                    })
                    with urllib.request.urlopen(req, timeout=30) as resp:
                        data = resp.read()
                        if len(data) > 10000:
                            with open(bgm_path, "wb") as f:
                                f.write(data)
                            return bgm_path
                except Exception:
                    continue
        except Exception:
            pass

        # FFmpeg fallback
        dur = max(duration_s, 60)
        subprocess.run([
            "ffmpeg", "-y", "-v", "error",
            "-f", "lavfi", "-i", f"anoisesrc=d={dur}:c=pink:a=0.10",
            "-c:a", "libmp3lame", "-b:a", "128k", bgm_path,
        ], check=True)
        return bgm_path

    # ─── Preview ─────────────────────────────────────────────

    @staticmethod
    def launch_preview(html_dir: str, port: int = 8765):
        """Start HTTP server and open browser for preview."""
        os.chdir(html_dir)
        server = HTTPServer(("", port), SimpleHTTPRequestHandler)
        t = threading.Thread(target=server.serve_forever, daemon=True)
        t.start()
        webbrowser.open(f"http://localhost:{port}/index.html")
        print(f"\n  Preview: http://localhost:{port}/index.html")
        print("  Press Ctrl+C to stop\n")
        try:
            while True:
                time.sleep(1)
        except KeyboardInterrupt:
            server.shutdown()


# ---------------------------------------------------------------------------
# CLI entry
# ---------------------------------------------------------------------------

def main():
    parser = argparse.ArgumentParser(
        description="Auto Video Platform v2 — Universal AI Video Pipeline",
    )

    # Reference image mode (primary)
    parser.add_argument("--ref", "-r", default=None,
                        help="Reference image path (product photo, AI flaw image, etc.)")
    parser.add_argument("--type", "-t", default="ai_flaw_detect",
                        help="Video type: ai_flaw_detect|product_promo|factory_promo|tutorial|vlog")
    parser.add_argument("--topic", default="",
                        help="Video topic/title (auto-detected from ref if not given)")

    # Episode mode (backward compatible)
    parser.add_argument("--ep", type=int, default=None,
                        help="Episode number (legacy mode)")
    parser.add_argument("--title", default="", help="Episode title slug")

    # Output
    parser.add_argument("--output", "-o", default=None,
                        help="Output directory")

    # Options
    parser.add_argument("--brand", "-b", default="AI照妖镜",
                        help="Brand name for DNA config")
    parser.add_argument("--style", default="",
                        help="Extra style hint for script generation")
    parser.add_argument("--voice", default="zh-CN-YunxiNeural",
                        help="TTS voice")
    parser.add_argument("--speed", type=float, default=1.1,
                        help="TTS speed")
    parser.add_argument("--skip-tts", action="store_true",
                        help="Skip TTS generation")
    parser.add_argument("--skip-jianying", action="store_true",
                        help="Skip Jianying draft export")
    parser.add_argument("--bgm", action="store_true",
                        help="Download/generate background music")
    parser.add_argument("--preview", action="store_true",
                        help="Launch browser preview after build")

    args = parser.parse_args()
    pipeline = VideoPipeline()

    # Determine ref image and output
    ref_image = args.ref
    video_type = args.type.replace("-", "_")
    output_dir = args.output

    # Legacy episode mode
    if args.ep is not None:
        slug = args.title or f"ep{args.ep}"
        output_dir = output_dir or os.path.join(OUTPUT, f"ep{args.ep}_{slug}")
        ep_assets = os.path.join(OUTPUT, f"ep{args.ep}_assets")
        if os.path.isdir(ep_assets):
            imgs = sorted([os.path.join(ep_assets, f) for f in os.listdir(ep_assets)
                           if f.endswith((".png", ".jpg", ".jpeg"))])
            if imgs:
                ref_image = imgs[0]  # Use first image as ref
        args.bgm = True
        args.preview = True

    if not ref_image:
        print("Error: No reference image. Use --ref PATH or --ep NUM")
        sys.exit(1)

    # Set brand DNA
    brand_dna = None
    if args.brand:
        from generators.script_engine import DEFAULT_BRAND_DNA
        brand_dna = {**DEFAULT_BRAND_DNA, "brand_name": args.brand}

    # Run
    result = pipeline.run(
        ref_image=ref_image,
        video_type=video_type,
        topic=args.topic,
        brand_dna=brand_dna,
        output_dir=output_dir,
        tts_voice=args.voice,
        tts_speed=args.speed,
        skip_tts=args.skip_tts,
        skip_jianying=args.skip_jianying,
        bgm=args.bgm,
        style_hint=args.style,
    )

    # Preview
    if args.preview and result.get("html_path"):
        pipeline.launch_preview(result["output_dir"])

    return result


if __name__ == "__main__":
    main()
