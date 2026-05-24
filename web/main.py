"""Auto Video Platform — Visual Editor Backend (FastAPI).

REST API wrapping the full pipeline: upload → analyze → script → preview → export.
"""
import os, sys, json, uuid, tempfile, subprocess, shutil, asyncio
from concurrent.futures import ThreadPoolExecutor
from pathlib import Path
from fastapi import FastAPI, UploadFile, File, Form, HTTPException
from fastapi.responses import FileResponse, HTMLResponse, JSONResponse
from fastapi.staticfiles import StaticFiles
from fastapi.templating import Jinja2Templates
from starlette.requests import Request
import yaml

# Ensure project root is on path
PROJECT_ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, PROJECT_ROOT)

# Load .env before any module that reads environment variables
_env_path = os.path.join(PROJECT_ROOT, ".env")
if os.path.exists(_env_path):
    with open(_env_path, encoding="utf-8") as _f:
        for _line in _f:
            _line = _line.strip()
            if _line and not _line.startswith("#") and "=" in _line:
                _k, _v = _line.split("=", 1)
                os.environ.setdefault(_k.strip(), _v.strip())

from analyzers import (
    AssetAnalyzer, save_report,
    SharpnessDetector, ColorDetector, CompositionDetector,
    FaceDetector, HandDetector, TextureDetector, TextDetector,
)
from generators import ScriptGenerator, ConfigLoader, TTSBuilder

app = FastAPI(title="Auto Video Platform — AI短视频自动剪辑平台", version="0.2.0")

# Static & templates
static_dir = os.path.join(os.path.dirname(__file__), "static")
templates_dir = os.path.join(os.path.dirname(__file__), "templates")
os.makedirs(static_dir, exist_ok=True)
os.makedirs(templates_dir, exist_ok=True)
app.mount("/static", StaticFiles(directory=static_dir), name="static")
templates = Jinja2Templates(directory=templates_dir)

# Workspace for uploaded files & outputs
WORKSPACE = os.path.join(PROJECT_ROOT, "workspace")
os.makedirs(WORKSPACE, exist_ok=True)

# Config loader
config_loader = ConfigLoader()


# ═══════════════════════════════════════════════════════
# Pages
# ═══════════════════════════════════════════════════════

@app.get("/", response_class=HTMLResponse)
async def editor_page(request: Request):
    """Main visual editor page."""
    return templates.TemplateResponse("editor.html", {})


# ═══════════════════════════════════════════════════════
# API: Upload & Analyze
# ═══════════════════════════════════════════════════════

@app.post("/api/upload")
async def upload_and_analyze(
    files: list[UploadFile] = File(...),
    video_type: str = Form("ai_flaw_detect"),
):
    """Upload images, run all detectors, return analysis report."""
    project_id = uuid.uuid4().hex[:8]
    project_dir = os.path.join(WORKSPACE, project_id)
    assets_dir = os.path.join(project_dir, "assets")
    os.makedirs(assets_dir, exist_ok=True)

    # Save uploaded files
    image_paths = []
    for f in files:
        safe_name = f.filename.replace(" ", "_")
        path = os.path.join(assets_dir, safe_name)
        with open(path, "wb") as out:
            content = await f.read()
            out.write(content)
        image_paths.append(path)

    # Choose detectors based on video type
    detectors = _get_detectors(video_type)

    # Analyze
    analyzer = AssetAnalyzer(detectors)
    results = analyzer.scan_batch(image_paths)
    report_path = os.path.join(project_dir, "analysis_report.json")
    save_report(results, report_path)

    # Load report as dict for response
    with open(report_path, encoding="utf-8") as f:
        report = json.load(f)

    return {
        "project_id": project_id,
        "report": report,
        "assets": [os.path.basename(p) for p in image_paths],
    }


# ═══════════════════════════════════════════════════════
# API: Generate Script
# ═══════════════════════════════════════════════════════

@app.post("/api/generate")
async def generate_script(
    project_id: str = Form(...),
    video_type: str = Form("ai_flaw_detect"),
    brand_name: str = Form(""),
):
    """Generate production script from analysis report."""
    report_path = os.path.join(WORKSPACE, project_id, "analysis_report.json")
    if not os.path.exists(report_path):
        raise HTTPException(404, "Analysis report not found. Run /api/upload first.")

    with open(report_path, encoding="utf-8") as f:
        report = json.load(f)

    api_key = os.environ.get("DEEPSEEK_API_KEY") or os.environ.get("ANTHROPIC_API_KEY")
    gen = ScriptGenerator(api_key=api_key)
    script = gen.generate(
        report,
        video_type=video_type,
        brand_name=brand_name or None,
    )

    # Save script
    script_path = os.path.join(WORKSPACE, project_id, "script.json")
    with open(script_path, "w", encoding="utf-8") as f:
        json.dump(script, f, ensure_ascii=False, indent=2)

    return {"project_id": project_id, "script": script}


# ═══════════════════════════════════════════════════════
# API: TTS
# ═══════════════════════════════════════════════════════

@app.post("/api/tts")
async def generate_tts(
    project_id: str = Form(...),
    voice: str = Form("zh-CN-YunxiNeural"),
    speed: float = Form(1.0),
):
    """Generate TTS narration from script storyboard."""
    script_path = os.path.join(WORKSPACE, project_id, "script.json")
    if not os.path.exists(script_path):
        raise HTTPException(404, "Script not found. Run /api/generate first.")

    with open(script_path, encoding="utf-8") as f:
        script = json.load(f)

    tts_dir = os.path.join(WORKSPACE, project_id, "tts")
    builder = TTSBuilder(voice=voice, speed=speed, output_dir=tts_dir)
    timeline = builder.build(script)

    # Copy audio to project root for easier access
    audio_dest = os.path.join(WORKSPACE, project_id, "narration.mp3")
    shutil.copy(timeline.audio_path, audio_dest)

    # Read SRT
    with open(timeline.srt_path, encoding="utf-8") as f:
        srt_content = f.read()

    return {
        "project_id": project_id,
        "audio_url": f"/api/audio/{project_id}/narration.mp3",
        "srt": srt_content,
        "duration_s": timeline.total_duration_s,
        "segments": [
            {"shot": s.shot, "text": s.text, "duration_s": s.duration_s}
            for s in timeline.segments
        ],
    }


@app.get("/api/audio/{project_id}/{filename}")
async def serve_audio(project_id: str, filename: str):
    """Serve generated audio file."""
    path = os.path.join(WORKSPACE, project_id, filename)
    if not os.path.exists(path):
        raise HTTPException(404, "Audio not found")
    return FileResponse(path, media_type="audio/mpeg")


# ═══════════════════════════════════════════════════════
# API: Preview (HyperFrames HTML)
# ═══════════════════════════════════════════════════════

@app.post("/api/preview", response_class=HTMLResponse)
async def build_preview(
    project_id: str = Form(...),
):
    """Generate HyperFrames HTML for real-time preview."""
    script_path = os.path.join(WORKSPACE, project_id, "script.json")
    if not os.path.exists(script_path):
        raise HTTPException(404, "Script not found.")

    with open(script_path, encoding="utf-8") as f:
        script = json.load(f)

    # Get asset paths
    assets_dir = os.path.join(WORKSPACE, project_id, "assets")
    asset_files = []
    if os.path.exists(assets_dir):
        asset_files = sorted(os.listdir(assets_dir))

    # Build HyperFrames HTML
    html = _build_hyperframes_html(script, asset_files, project_id)
    return HTMLResponse(html)


@app.get("/api/preview/{project_id}", response_class=HTMLResponse)
async def get_preview(project_id: str):
    """Get the latest preview HTML for a project."""
    script_path = os.path.join(WORKSPACE, project_id, "script.json")
    if not os.path.exists(script_path):
        raise HTTPException(404, "Script not found.")

    with open(script_path, encoding="utf-8") as f:
        script = json.load(f)

    assets_dir = os.path.join(WORKSPACE, project_id, "assets")
    asset_files = sorted(os.listdir(assets_dir)) if os.path.exists(assets_dir) else []

    html = _build_hyperframes_html(script, asset_files, project_id)
    return HTMLResponse(html)


# ═══════════════════════════════════════════════════════
# API: Export MP4 (via real Chromium + BGM pipeline)
# ═══════════════════════════════════════════════════════

@app.post("/api/export")
async def export_mp4(
    project_id: str = Form(...),
    fps: int = Form(24),
):
    """Export assembled HTML to MP4 via ChromiumRenderer + bgm."""
    html_path = os.path.join(WORKSPACE, project_id, "index.html")
    if not os.path.exists(html_path):
        raise HTTPException(404, "HTML not found. Run /api/preview first.")

    output_path = os.path.join(WORKSPACE, project_id, "output.mp4")
    narration_path = os.path.join(WORKSPACE, project_id, "narration.mp3")
    bgm_path = os.path.join(WORKSPACE, project_id, "bgm_mixed.mp3")

    def _do_render():
        from builders.chromium_renderer import ChromiumRenderer
        renderer = ChromiumRenderer()
        return renderer.render(
            html_dir=os.path.join(WORKSPACE, project_id),
            audio_path=narration_path if os.path.exists(narration_path) else "",
            bgm_path=bgm_path if os.path.exists(bgm_path) else "",
            duration_s=30,
            output_path=output_path,
        )

    try:
        loop = asyncio.get_event_loop()
        with ThreadPoolExecutor(max_workers=1) as pool:
            await loop.run_in_executor(pool, _do_render)
    except Exception as e:
        return {"error": f"Render failed: {e}"}

    return {
        "project_id": project_id,
        "video_url": f"/api/video/{project_id}/output.mp4",
        "size_mb": round(os.path.getsize(output_path) / (1024 * 1024), 1) if os.path.exists(output_path) else 0,
    }


# ═══════════════════════════════════════════════════════
# API: One-Click Full Pipeline
# ═══════════════════════════════════════════════════════

@app.post("/api/pipeline/run")
async def run_full_pipeline(
    files: list[UploadFile] = File(...),
    video_type: str = Form("ai_flaw_detect"),
    topic: str = Form(""),
    bgm: bool = Form(True),
):
    """One-click: upload ref images → analyze → script → TTS → assets → BGM → MP4.

    Returns the full pipeline result including video URL and metadata.
    """
    # Save uploaded files to a temp project dir
    project_id = uuid.uuid4().hex[:8]
    output_dir = os.path.join(WORKSPACE, "pipelines", project_id)
    os.makedirs(output_dir, exist_ok=True)

    ref_paths = []
    for f in files:
        safe_name = f.filename.replace(" ", "_")
        path = os.path.join(output_dir, safe_name)
        with open(path, "wb") as out:
            out.write(await f.read())
        ref_paths.append(path)

    if not ref_paths:
        raise HTTPException(400, "At least one reference image required")

    ref_image = ref_paths[0]

    # Run blocking pipeline in thread pool (Playwright sync API + asyncio don't mix)
    def _run_pipeline():
        from pipeline import VideoPipeline
        pipeline_runner = VideoPipeline()
        return pipeline_runner.run(
            ref_image=ref_image,
            video_type=video_type,
            topic=topic or None,
            output_dir=output_dir,
            bgm=bgm,
            skip_jianying=True,
        )

    try:
        loop = asyncio.get_event_loop()
        with ThreadPoolExecutor(max_workers=1) as pool:
            result = await loop.run_in_executor(pool, _run_pipeline)
    except Exception as e:
        import traceback
        return {"error": str(e), "traceback": traceback.format_exc()[-2000:]}

    return {
        "project_id": project_id,
        "video_url": f"/api/video-pipeline/{project_id}/output.mp4",
        "html_url": f"/api/video-pipeline/{project_id}/index.html",
        "duration_s": result.get("duration_s", 0),
        "mp4_path": result.get("mp4_path", ""),
        "mp4_error": result.get("mp4_error", ""),
        "output_dir": output_dir,
    }


@app.get("/api/video-pipeline/{project_id}/{filename}")
async def serve_pipeline_file(project_id: str, filename: str):
    """Serve files from pipeline output directory."""
    path = os.path.join(WORKSPACE, "pipelines", project_id, filename)
    if not os.path.exists(path):
        raise HTTPException(404, f"File not found: {filename}")
    media_type = "video/mp4" if filename.endswith(".mp4") else "text/html"
    return FileResponse(path, media_type=media_type)


@app.get("/api/video/{project_id}/{filename}")
async def serve_video(project_id: str, filename: str):
    """Serve rendered video file."""
    path = os.path.join(WORKSPACE, project_id, filename)
    if not os.path.exists(path):
        raise HTTPException(404, "Video not found")
    return FileResponse(path, media_type="video/mp4")


# ═══════════════════════════════════════════════════════
# API: Export to Jianying (剪映)
# ═══════════════════════════════════════════════════════

@app.post("/api/export-jianying")
async def export_jianying(
    project_id: str = Form(...),
):
    """Export the project as a Jianying (剪映) editable draft."""
    script_path = os.path.join(WORKSPACE, project_id, "script.json")
    report_path = os.path.join(WORKSPACE, project_id, "analysis_report.json")

    if not os.path.exists(script_path):
        raise HTTPException(404, "Script not found. Run /api/generate first.")
    if not os.path.exists(report_path):
        raise HTTPException(404, "Analysis report not found. Run /api/upload first.")

    with open(script_path, encoding="utf-8") as f:
        script = json.load(f)
    with open(report_path, encoding="utf-8") as f:
        report = json.load(f)

    try:
        from builders.storyboard_mapper import StoryboardMapper
        mapper = StoryboardMapper(video_type="ai-flaw-detect")
        component_sb = mapper.map(script=script, analysis_report=report)

        from builders.jianying_exporter import JianyingDraftExporter
        exporter = JianyingDraftExporter()
        assets_dir = os.path.join(WORKSPACE, project_id, "assets")
        draft_path = exporter.export(
            component_sb,
            draft_name=f"AI_{project_id}",
            assets_dir=assets_dir,
        )

        return {
            "project_id": project_id,
            "draft_path": draft_path,
            "message": "Draft exported. Open Jianying → Create from draft folder.",
        }
    except ImportError as e:
        raise HTTPException(500, f"pyJianYingDraft not installed: {e}")
    except Exception as e:
        raise HTTPException(500, f"Export failed: {e}")


# ═══════════════════════════════════════════════════════
# API: Assets
# ═══════════════════════════════════════════════════════

@app.get("/api/asset/{project_id}/{filename}")
async def serve_asset(project_id: str, filename: str):
    """Serve uploaded asset image."""
    path = os.path.join(WORKSPACE, project_id, "assets", filename)
    if not os.path.exists(path):
        raise HTTPException(404, "Asset not found")
    return FileResponse(path)


# ═══════════════════════════════════════════════════════
# Helpers
# ═══════════════════════════════════════════════════════

def _get_detectors(video_type: str) -> list:
    """Instantiate detectors based on video type config."""
    try:
        cfg = config_loader.load_video_type(video_type)
        names = cfg.get("asset_analysis", {}).get("detectors", [])
    except Exception:
        names = ["sharpness", "color", "face", "hand", "texture", "text"]

    registry = {
        "sharpness": SharpnessDetector,
        "color": ColorDetector,
        "composition": CompositionDetector,
        "face": FaceDetector,
        "hand": HandDetector,
        "texture": TextureDetector,
        "text": TextDetector,
    }
    return [registry[n]() for n in names if n in registry]


def _build_hyperframes_html(script: dict, asset_files: list, project_id: str) -> str:
    """Build HyperFrames HTML using the component pipeline when possible."""
    # Try full component pipeline first
    report_path = os.path.join(WORKSPACE, project_id, "analysis_report.json")
    if os.path.exists(report_path):
        try:
            with open(report_path, encoding="utf-8") as f:
                report = json.load(f)
            from builders import CompositionBuilder, StoryboardConfig, StoryboardMapper

            mapper = StoryboardMapper(video_type="ai-flaw-detect")
            component_sb = mapper.map(script=script, analysis_report=report)
            builder = CompositionBuilder()
            return builder.build_from_dict(component_sb)
        except Exception:
            pass  # Fall back to simplified version

    # Fallback: simplified shot-by-shot preview
    storyboard = script.get("storyboard", [])
    titles = script.get("titles", [])
    total_duration = sum(_parse_duration(s.get("duration", "5s")) for s in storyboard)

    # Asset URLs (served by this server)
    asset_urls = [f"/api/asset/{project_id}/{f}" for f in asset_files]

    # Parse annotations from storyboard visual descriptions
    import re as _re
    shots_data = []
    cursor = 0.0
    for i, shot in enumerate(storyboard):
        dur = _parse_duration(shot.get("duration", "5s"))
        caption = shot.get("caption", "")
        audio_text = shot.get("audio", "")
        img_url = asset_urls[i % len(asset_urls)] if asset_urls else ""

        # Extract annotation coords from visual description
        annotations = []
        visual = shot.get("visual", "")
        for m in _re.finditer(r'@\s*\((\d+\.?\d*)\s*,\s*(\d+\.?\d*)\)', visual):
            cx = float(m.group(1))
            cy = float(m.group(2))
            if cx > 1.0:
                cx = cx / 1080.0 * 100
            else:
                cx = cx * 100
            if cy > 1.0:
                cy = cy / 1920.0 * 100
            else:
                cy = cy * 100
            annotations.append({"cx": cx, "cy": cy, "size": 60})

        shots_data.append({
            "num": i + 1,
            "start": cursor,
            "end": cursor + dur,
            "img_url": img_url,
            "caption": caption,
            "audio_text": audio_text,
            "annotations": annotations,
        })
        cursor += dur

    # Build shot HTML
    shots_html = ""
    for sd in shots_data:
        ann_html = ""
        for a in sd["annotations"]:
            ann_html += f'<div class="annotation" style="left:{a["cx"]}%;top:{a["cy"]}%;width:{a["size"]}px;height:{a["size"]}px;transform:translate(-50%,-50%)"></div>'

        shots_html += f"""
    <!-- Shot {sd["num"]} -->
    <div class="shot" data-shot="{sd["num"]}" data-start="{sd['start']:.2f}" data-end="{sd['end']:.2f}">
      <img class="shot-bg" src="{sd['img_url']}" />
      {ann_html}
      <div class="caption">{sd['caption']}</div>
      <div class="audio-text" style="display:none">{sd['audio_text']}</div>
    </div>"""

    title_text = titles[0]["text"] if titles else "Auto Video"

    return f"""<!DOCTYPE html>
<html lang="zh-CN">
<head>
<meta charset="UTF-8">
<meta name="viewport" content="width=1080, height=1920">
<title>{title_text}</title>
<style>
  :root {{
    --bg: #0d1b2a;
    --accent: #00e676;
    --text: #ffffff;
  }}
  * {{ margin: 0; padding: 0; box-sizing: border-box; }}
  body {{
    width: 1080px; height: 1920px;
    background: radial-gradient(circle at center, var(--bg), #06060b);
    color: var(--text);
    font-family: 'Noto Sans SC', 'PingFang SC', 'Microsoft YaHei', sans-serif;
    overflow: hidden;
    position: relative;
  }}
  .shot {{
    position: absolute;
    inset: 0;
    display: flex;
    align-items: center;
    justify-content: center;
    flex-direction: column;
    opacity: 0;
    transition: opacity 0.3s;
  }}
  .shot.active {{ opacity: 1; }}
  .shot-bg {{
    position: absolute;
    width: 100%;
    height: 100%;
    object-fit: cover;
  }}
  .caption {{
    position: absolute;
    bottom: 200px;
    left: 60px;
    right: 60px;
    font-size: 36px;
    font-weight: 700;
    text-align: center;
    text-shadow: 0 0 20px rgba(0,0,0,0.7), 0 4px 8px rgba(0,0,0,0.5);
    z-index: 10;
    padding: 16px 24px;
    background: rgba(0,0,0,0.5);
    border-radius: 12px;
  }}
  .annotation {{
    position: absolute;
    border: 4px solid #ff1744;
    border-radius: 50%;
    animation: pulse 1s ease-in-out infinite;
    z-index: 20;
    pointer-events: none;
  }}
  @keyframes pulse {{
    0%, 100% {{ transform: translate(-50%,-50%) scale(1); box-shadow: 0 0 20px rgba(255,23,68,0.5); }}
    50% {{ transform: translate(-50%,-50%) scale(1.08); box-shadow: 0 0 40px rgba(255,23,68,0.8); }}
  }}
  .watermark {{
    position: absolute;
    bottom: 60px;
    right: 60px;
    font-size: 20px;
    color: rgba(255,255,255,0.4);
    z-index: 5;
  }}
  .progress {{
    position: absolute;
    bottom: 0;
    left: 0;
    height: 4px;
    background: var(--accent);
    z-index: 30;
    transition: width 0.1s linear;
  }}
</style>
</head>
<body>

{shots_html}

<div class="watermark">Auto Video Platform</div>
<div class="progress" id="progress"></div>

<script>
  var shots = document.querySelectorAll('.shot');
  var progress = document.getElementById('progress');
  var TOTAL = {total_duration:.1f};

  // Build GSAP timeline if available (for HyperFrames CLI render)
  if (typeof gsap !== 'undefined') {{
    var tl = gsap.timeline({{ paused: true }});
    shots.forEach(function(shot) {{
      var start = parseFloat(shot.dataset.start);
      tl.call(function() {{
        shots.forEach(function(s) {{ s.classList.remove('active'); }});
        shot.classList.add('active');
      }}, null, start);
    }});
    window.__timelines = [tl];
  }}

  // Standalone preview engine
  var currentTime = 0;
  var playing = false;
  var animFrame = null;

  function updatePreview(time) {{
    currentTime = Math.max(0, Math.min(TOTAL, time));
    progress.style.width = (currentTime / TOTAL * 100) + '%';

    var activeShot = null;
    for (var i = 0; i < shots.length; i++) {{
      var s = parseFloat(shots[i].dataset.start);
      var e = parseFloat(shots[i].dataset.end);
      if (currentTime >= s && currentTime < e) activeShot = shots[i];
      shots[i].classList.remove('active');
    }}
    if (activeShot) activeShot.classList.add('active');

    if (window.__timelines && window.__timelines[0]) {{
      window.__timelines[0].seek(currentTime);
    }}
  }}

  function play() {{
    if (playing) return;
    playing = true;
    var startWall = Date.now() / 1000 - currentTime;
    function tick() {{
      if (!playing) return;
      updatePreview(Date.now() / 1000 - startWall);
      if (currentTime >= TOTAL) {{ playing = false; }}
      animFrame = requestAnimationFrame(tick);
    }}
    tick();
  }}

  function pause() {{
    playing = false;
    if (animFrame) {{ cancelAnimationFrame(animFrame); animFrame = null; }}
  }}

  window.previewControls = {{
    play: play,
    pause: pause,
    seek: updatePreview,
    getTime: function() {{ return currentTime; }},
    getTotal: function() {{ return TOTAL; }},
  }};

  updatePreview(0);
</script>
</body>
</html>"""


def _parse_duration(dur_str: str) -> float:
    """Parse '5s' or '3.5s' to float seconds."""
    if isinstance(dur_str, (int, float)):
        return float(dur_str)
    return float(str(dur_str).rstrip("s").strip())


# ═══════════════════════════════════════════════════════
# Main
# ═══════════════════════════════════════════════════════

if __name__ == "__main__":
    import uvicorn
    uvicorn.run(app, host="0.0.0.0", port=9000, log_level="info")
