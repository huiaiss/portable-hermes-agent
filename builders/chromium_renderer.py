"""Chromium Renderer — HyperFrames HTML + TTS audio → MP4 video.

Uses Playwright (Chromium/Edge) to record the animated HTML page,
then combines with TTS narration audio via ffmpeg.

Usage:
    from builders.chromium_renderer import ChromiumRenderer
    renderer = ChromiumRenderer()
    mp4_path = renderer.render(html_path, audio_path, duration_s=45)
"""

import os, subprocess, time, threading, socket
from pathlib import Path
from http.server import HTTPServer, SimpleHTTPRequestHandler
from typing import Optional


class ChromiumRenderer:
    """Playwright-based HTML → MP4 renderer."""

    def __init__(self, browser_executable: str = None):
        """
        Args:
            browser_executable: Path to Chromium/Chrome/Edge executable.
                                Auto-detected if not provided.
        """
        self.browser_exec = browser_executable or self._find_browser()
        self._server = None
        self._server_thread = None

    # ─── Public API ─────────────────────────────────────

    def render(self, html_dir: str, audio_path: str = "",
               bgm_path: str = "", duration_s: float = 30,
               output_path: str = None, port: int = 8765) -> str:
        """Render HTML animation → MP4 video.

        Args:
            html_dir: directory containing index.html + assets
            audio_path: narration MP3 path
            bgm_path: background music MP3 path (optional, mixed with narration)
            duration_s: total video duration
            output_path: destination MP4 path
        """
        html_dir = os.path.abspath(html_dir)
        if audio_path:
            audio_path = os.path.abspath(audio_path)
        if bgm_path:
            bgm_path = os.path.abspath(bgm_path)
        output_path = os.path.abspath(output_path or os.path.join(html_dir, "output.mp4"))

        # Ensure gsap.min.js is available locally (no CDN dependency)
        self._ensure_local_gsap(html_dir)

        # 1. Start HTTP server
        self._start_server(html_dir, port)
        time.sleep(0.5)

        try:
            # 2. Record page via Playwright
            temp_video = os.path.join(html_dir, "_temp_video.webm")
            self._record_page(port, duration_s, temp_video)

            # 3. Mux video + audio(s) → MP4
            if audio_path and os.path.exists(audio_path):
                self._mux_video_audio(temp_video, audio_path, output_path,
                                       duration_s, bgm_path)
            else:
                self._convert_webm_to_mp4(temp_video, output_path)

            # Cleanup temp
            if os.path.exists(temp_video):
                os.remove(temp_video)

        finally:
            self._stop_server()

        return output_path

    # ─── Playwright Recording ────────────────────────────

    def _record_page(self, port: int, duration_s: float, output_path: str):
        """Use Playwright to record the animated page as webm video."""
        from playwright.sync_api import sync_playwright

        url = f"http://127.0.0.1:{port}/index.html?autoplay=1"
        print(f"  Recording: {url}")
        print(f"  Duration: {duration_s:.0f}s")
        print(f"  Browser: {os.path.basename(self.browser_exec)}")

        with sync_playwright() as pw:
            browser = pw.chromium.launch(
                headless=True,
                executable_path=self.browser_exec,
                args=[
                    "--no-sandbox",
                    "--disable-gpu",
                    "--disable-software-rasterizer",
                    "--autoplay-policy=no-user-gesture-required",
                ],
            )

            context = browser.new_context(
                viewport={"width": 1080, "height": 1920},
                record_video_dir=os.path.dirname(output_path),
                record_video_size={"width": 1080, "height": 1920},
            )

            page = context.new_page()
            # Use domcontentloaded — GSAP is now local (no CDN), so this is fast
            page.goto(url, wait_until="domcontentloaded", timeout=30000)
            # Wait for GSAP to be available (local file loads quickly)
            page.wait_for_function("typeof gsap !== 'undefined'", timeout=10000)

            # Click once to unlock audio (autoplay parameter handles overlay + timeline)
            page.click("body", timeout=5000)

            # Wait for GSAP timeline to complete + buffer for final frames
            wait_ms = int(duration_s * 1000) + 3000  # +3s buffer
            page.wait_for_timeout(wait_ms)

            # Close context to flush video
            context.close()
            browser.close()

        # Playwright saves video with a generated name — find it
        video_dir = os.path.dirname(output_path)
        for f in os.listdir(video_dir):
            if f.endswith(".webm") and os.path.getsize(os.path.join(video_dir, f)) > 1000:
                src = os.path.join(video_dir, f)
                # Remove stale output file if it exists (WinError 183)
                if os.path.exists(output_path):
                    os.remove(output_path)
                os.rename(src, output_path)
                print(f"  Video saved: {output_path} ({os.path.getsize(output_path)//1024}KB)")
                return

        raise FileNotFoundError(f"No .webm video found in {video_dir}")

    # ─── FFmpeg Operations ───────────────────────────────

    def _mux_video_audio(self, video_path: str, audio_path: str,
                         output_path: str, duration_s: float,
                         bgm_path: str = ""):
        """Combine video + narration (+ optional BGM) into final MP4.

        When bgm_path is present, narration and BGM are mixed with
        ffmpeg's amix filter. BGM is lowered to -14dB (vol=0.2) to stay
        in the background while narration remains at full volume.
        """
        if bgm_path and os.path.exists(bgm_path):
            print(f"  Muxing video + narration + BGM → MP4...")
            # Mix narration + BGM using amix, then combine with video
            cmd = [
                "ffmpeg", "-y", "-v", "error",
                "-i", video_path,
                "-i", audio_path,
                "-i", bgm_path,
                "-filter_complex",
                "[1:a]volume=1.0[a1];[2:a]volume=0.15[a2];"
                "[a1][a2]amix=inputs=2:duration=first:dropout_transition=0,"
                "volume=1.2[amix]",
                "-map", "0:v:0",
                "-map", "[amix]",
                "-c:v", "libx264", "-preset", "fast", "-crf", "23",
                "-c:a", "aac", "-b:a", "128k",
                "-pix_fmt", "yuv420p",
                "-t", str(duration_s),
                "-shortest",
                output_path,
            ]
        else:
            print(f"  Muxing video + audio → MP4...")
            cmd = [
                "ffmpeg", "-y", "-v", "error",
                "-i", video_path,
                "-i", audio_path,
                "-c:v", "libx264", "-preset", "fast", "-crf", "23",
                "-c:a", "aac", "-b:a", "128k",
                "-pix_fmt", "yuv420p",
                "-t", str(duration_s),
                "-shortest",
                output_path,
            ]

        subprocess.run(cmd, check=True)
        print(f"  MP4 saved: {output_path} ({os.path.getsize(output_path)//1024}KB)")

    def _convert_webm_to_mp4(self, webm_path: str, output_path: str):
        """Convert webm to mp4 without audio."""
        print(f"  Converting webm → mp4...")
        cmd = [
            "ffmpeg", "-y", "-v", "error",
            "-i", webm_path,
            "-c:v", "libx264", "-preset", "fast", "-crf", "23",
            "-pix_fmt", "yuv420p",
            output_path,
        ]
        subprocess.run(cmd, check=True)
        print(f"  MP4 saved: {output_path} ({os.path.getsize(output_path)//1024}KB)")

    # ─── HTTP Server ─────────────────────────────────────

    def _start_server(self, serve_dir: str, port: int):
        """Start a background HTTP server (does NOT change CWD)."""
        serve_dir = os.path.abspath(serve_dir)

        while self._port_in_use(port):
            port += 1

        # Create handler that serves from the right directory
        handler = lambda *args, **kwargs: SimpleHTTPRequestHandler(
            *args, directory=serve_dir, **kwargs)

        self._server = HTTPServer(("127.0.0.1", port), handler)
        self._server_thread = threading.Thread(target=self._server.serve_forever, daemon=True)
        self._server_thread.start()
        print(f"  HTTP server: http://127.0.0.1:{port}")

    def _stop_server(self):
        if self._server:
            self._server.shutdown()
            self._server = None

    @staticmethod
    def _port_in_use(port: int) -> bool:
        try:
            with socket.socket(socket.AF_INET, socket.SOCK_STREAM) as s:
                s.bind(("127.0.0.1", port))
                return False
        except OSError:
            return True

    # ─── Browser Detection ───────────────────────────────

    @staticmethod
    def _ensure_local_gsap(html_dir: str):
        """Copy gsap.min.js into html_dir if not already present."""
        gsap_dst = os.path.join(html_dir, "gsap.min.js")
        if os.path.exists(gsap_dst):
            return
        # Try the builders/static copy first
        gsap_src = os.path.join(os.path.dirname(__file__), "static", "gsap.min.js")
        if os.path.exists(gsap_src):
            import shutil as _shutil
            _shutil.copy2(gsap_src, gsap_dst)

    @staticmethod
    def _find_browser() -> str:
        """Auto-detect available Chromium browser."""
        candidates = [
            "C:/Program Files (x86)/Microsoft/Edge/Application/msedge.exe",
            "C:/Program Files/Google/Chrome/Application/chrome.exe",
            "C:/Program Files (x86)/Google/Chrome/Application/chrome.exe",
        ]
        # Also check PATH
        for name in ["chromium", "chromium-browser", "google-chrome", "chrome", "msedge"]:
            result = subprocess.run(["where", name], capture_output=True, text=True)
            if result.returncode == 0:
                path = result.stdout.strip().split('\n')[0]
                if os.path.exists(path):
                    candidates.insert(0, path)

        for c in candidates:
            if os.path.exists(c):
                return c

        raise FileNotFoundError(
            "No Chromium browser found. Install Chrome or Edge, or pass browser_executable path."
        )


# ─── Convenience ────────────────────────────────────────────

def render_html_to_mp4(html_dir: str, audio_path: str = "",
                       duration_s: float = 30) -> str:
    """Quick render: HTML directory → MP4 video."""
    renderer = ChromiumRenderer()
    return renderer.render(html_dir, audio_path, duration_s)
