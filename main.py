"""
YouTube Downloader API using FastAPI + yt-dlp
Author: Matrix

"""

import os
import sys
import argparse
import subprocess
import asyncio
import requests
from bs4 import BeautifulSoup
from urllib.parse import quote
import logging
from logging.handlers import RotatingFileHandler
from urllib.parse import unquote
from contextlib import asynccontextmanager
from concurrent.futures import ThreadPoolExecutor
import yt_dlp
from fastapi import FastAPI, Query, HTTPException, Request
from fastapi.responses import JSONResponse, FileResponse, HTMLResponse
from fastapi.middleware.cors import CORSMiddleware
from fastapi.staticfiles import StaticFiles


# ---------------------- Configuration ----------------------
CACHE_DIR = "cache"
MAX_CACHE_FILES = 10

DEBUG = False  # set via CLI when running directly
download_queue = asyncio.Queue()
executor = ThreadPoolExecutor(max_workers=3) # Increased workers for info fetching

# ---------------------- Logging ----------------------
# Clear any existing root handlers
for handler in logging.root.handlers[:]:
    logging.root.removeHandler(handler)

LOG_FILE = "logs.txt"
LOG_FORMAT = "%(asctime)s [%(levelname)s] %(message)s"

log_formatter = logging.Formatter(LOG_FORMAT)

# File handler (keeps saving all logs to a file)
file_handler = RotatingFileHandler(LOG_FILE, maxBytes=5 * 1024 * 1024, backupCount=5)
file_handler.setFormatter(log_formatter)
file_handler.setLevel(logging.DEBUG)

# --- THIS IS THE FIX ---
# Console handler (now shows DEBUG logs in Render)
console_handler = logging.StreamHandler(sys.stdout)
console_handler.setFormatter(log_formatter)
console_handler.setLevel(logging.DEBUG) # Changed from INFO to DEBUG

logger = logging.getLogger(__name__)
logger.setLevel(logging.DEBUG)
logger.addHandler(file_handler)
logger.addHandler(console_handler)



class YTDLPLogger:
    def debug(self, msg): logger.debug(msg)
    def info(self, msg): logger.info(msg)
    def warning(self, msg): logger.warning(msg)
    def error(self, msg): logger.error(msg)


# ---------------------- FastAPI Setup ----------------------
@asynccontextmanager
async def lifespan(app: FastAPI):
    logger.info("========== YT-DOWNLOADER STARTED ==========")
    asyncio.create_task(process_queue())
    yield
    logger.info("========== YT-DOWNLOADER SHUTTING DOWN ==========")


app = FastAPI(title="Universal Media Downloader API Backend Developed by Matrix-King", lifespan=lifespan)


app.add_middleware(
    CORSMiddleware,
    allow_origins=["*"],
    allow_credentials=True,
    allow_methods=["*"],
    allow_headers=["*"],
)

os.makedirs(CACHE_DIR, exist_ok=True)
app.mount("/cache", StaticFiles(directory=CACHE_DIR), name="cache")

# ---------------------- Utility Functions ----------------------
def create_response(success: bool, result=None, message=None, status=200):
    response = {"creator": "Matrix - King", "status": status, "success": success, "result": result}
    if message:
        response["message"] = message
    return JSONResponse(status_code=status, content=response)


def clean_cache():
    files = sorted(
        [os.path.join(CACHE_DIR, f)
         for f in os.listdir(CACHE_DIR)
         if os.path.isfile(os.path.join(CACHE_DIR, f))],
        key=os.path.getmtime,
    )
    while len(files) > MAX_CACHE_FILES:
        try:
            os.remove(files.pop(0))
        except Exception as e:
            logger.warning(f"Failed to remove cache file: {e}")


def get_cache_path(video_id: str, ext: str) -> str:
    return os.path.join(CACHE_DIR, f"{video_id}.{ext}")


def get_universal_media_info(url: str, debug: bool = False):
    """
    Universal media info extractor that uses a single cookies.txt file.
    This version includes filesize calculation for video formats.
    """
    logger.info(f"Attempting to get info for '{url}' using cookies.txt")

    ydl_opts = {
        "noplaylist": True,
        "cookiefile": "cookies.txt" if os.path.exists("cookies.txt") else None,
        "logger": YTDLPLogger(),
        "quiet": not debug,
        "verbose": debug,
    }

    with yt_dlp.YoutubeDL(ydl_opts) as ydl:
        info = ydl.extract_info(url, download=False)
        
        processed_formats = []
        seen_formats = set()

        # Handle video and audio formats from any source
        for f in info.get('formats', []):
            filesize = f.get('filesize') or f.get('filesize_approx')
            
            # Calculate filesize if missing
            if not filesize:
                duration = info.get('duration')
                tbr = f.get('tbr')
                if duration and tbr:
                    filesize = int((tbr * 1000 / 8) * duration)

            # Add video formats based on height
            height = f.get('height')
            original_format_id = f.get('format_id')  # Keep the original format ID
            
            if height and f.get('vcodec') != 'none' and f.get('ext') in ['mp4', 'webm']:
                display_name = f"{height}p"  # Use this for display only
                if original_format_id not in seen_formats:
                    processed_formats.append({
                        "format_id": original_format_id,  # Use the REAL format ID
                        "quality": display_name,  # Display name for the user
                        "filesize_mb": round(filesize / (1024 * 1024), 2) if filesize else "N/A",
                        "ext": f.get('ext', 'mp4')
                    })
                    seen_formats.add(original_format_id)

        # Find the best audio format
        best_audio = next((f for f in sorted(info.get('formats', []), key=lambda x: x.get('abr') or 0, reverse=True) if f.get('acodec') != 'none'), None)
        if best_audio:
            filesize = best_audio.get('filesize') or best_audio.get('filesize_approx')
            if not filesize:
                duration = info.get('duration')
                abr = best_audio.get('abr')
                if duration and abr:
                    filesize = int((abr * 1000 / 8) * duration)
            
            processed_formats.append({
                "format_id": best_audio.get('format_id'),  # Use the REAL format ID
                "quality": "mp3",  # Display name
                "filesize_mb": round(filesize / (1024 * 1024), 2) if filesize else "N/A",
                "ext": "mp3"
            })

        logger.info(f"SUCCESS! Got info for '{url}'.")
        
        return {
            "title": info.get("title", "No Title Found"),
            "thumbnail_url": info.get("thumbnail"),
            "description": info.get("description"),
            "formats": sorted(processed_formats, key=lambda x: (isinstance(x['filesize_mb'], str), x['filesize_mb']), reverse=True)
        }




# In main.py, REPLACE the entire search_xnxx_videos function with this:

def search_xnxx_videos(query: str, request: Request):
    """
    Searches XNXX and fetches the full list of formats and file sizes for each video,
    replicating the behavior of the old Pterodactyl backend.
    """
    from urllib.parse import quote
    
    headers = {
        'User-Agent': 'Mozilla/5.0 (Linux; Android 10; K) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/114.0.0.0 Mobile Safari/537.36',
    }
    
    API_BASE_URL = get_public_url(request)
    search_url = f"https://www.xnxx.com/search/{query.replace(' ', '+')}"
    
    try:
        session = requests.Session()
        session.headers.update(headers)
        response = session.get(search_url, timeout=20)
        response.raise_for_status()
            
        soup = BeautifulSoup(response.text, 'html.parser')
        video_blocks = soup.select('div.thumb-block')
        
        logger.info(f"Found {len(video_blocks)} video blocks for query '{query}'. Starting detailed info extraction...")
        
        results = []
        
        # We will process results one by one to avoid overwhelming the server
        for block in video_blocks[:10]: # Limit to 10 results to keep it reasonably fast
            title_tag = block.select_one('div.thumb-under p a')
            img_tag = block.select_one('div.thumb img')
            
            if not title_tag or not img_tag:
                continue

            page_url = "https://www.xnxx.com" + title_tag.get('href', '')
            title = title_tag.get('title', 'No Title')
            thumbnail = img_tag.get('data-src', '')
            
            try:
                # --- THIS IS THE KEY CHANGE ---
                # Call the universal info extractor for each video to get all formats.
                # This requires FFmpeg to be installed in the Docker container.
                media_info = get_universal_media_info(page_url, debug=False)

                # Now, build the download URLs for each format found
                enhanced_formats = []
                for f in media_info.get("formats", []):
                    page_url_encoded = quote(page_url)
                    format_id_encoded = quote(str(f.get('format_id', '')))
                    
                    # This is the generic download endpoint that works for any format_id
                    download_url = (
                        f"{API_BASE_URL}/download?"
                        f"url={page_url_encoded}"
                        f"&format_id={format_id_encoded}"
                    )
                    
                    new_format = f.copy()
                    # Add the ready-to-use download URL for the bot
                    new_format['download_url'] = download_url
                    # Use a consistent name for quality
                    new_format['quality'] = f.get('format_id') 
                    enhanced_formats.append(new_format)
                
                results.append({
                    "title": title,
                    "thumbnail": thumbnail,
                    "page_url": page_url,
                    "formats": enhanced_formats # This now contains the full list of qualities and sizes
                })

            except Exception as e:
                logger.warning(f"Could not process one of the videos ('{title}') in the search results: {e}")
                # If one video fails, we skip it and continue with the others
                continue

        logger.info(f"Successfully processed {len(results)} videos with full format details.")
        return results
        
    except Exception as e:
        logger.error(f"Error in search_xnxx_videos: {e}", exc_info=True)
        return {"error": f"The search operation failed: {str(e)}"}




def download_media(url: str, format_id: str, is_audio: bool, debug: bool = False):
    """Universal downloader for any platform supported by yt-dlp."""
    ydl_opts = {
        "noplaylist": True,
        # --- FINAL FIX ---
        # For audio, request the best audio format available.
        # For video, use the specific format_id passed from the route handler.
        "format": "bestaudio/best" if is_audio else format_id,
        "outtmpl": os.path.join(CACHE_DIR, "%(id)s.%(ext)s"),
        "cookiefile": "cookies.txt" if os.path.exists("cookies.txt") else None,
        "logger": YTDLPLogger(),
        "progress_hooks": [lambda d: logger.debug(d)],
        "quiet": not debug,
        "verbose": debug,
    }
    if is_audio:
        # This post-processor configuration requires FFmpeg to be installed on the server.
        ydl_opts["postprocessors"] = [{
            "key": "FFmpegExtractAudio",
            "preferredcodec": "mp3",
            "preferredquality": "320",

        }]
    
    with yt_dlp.YoutubeDL(ydl_opts) as ydl:
        logger.info(f"Starting download for {'audio' if is_audio else 'video'} with options: {ydl_opts}")
        info = ydl.extract_info(url, download=True)
        video_id = info.get("id")
        
        # When extracting audio, the final extension is always 'mp3'.
        ext = "mp3" if is_audio else info.get("ext", "mp4")
        
        filepath = get_cache_path(video_id, ext)
        logger.info(f"Download complete. File saved at: {filepath}")

        return {
            "video_id": video_id,
            "title": info.get("title"),
            "thumbnail": info.get("thumbnail"),
            "filepath": filepath,
            "ext": ext,
        }




# --- REPLACE the existing process_queue function with this ---
async def process_queue():
    while True:
        task = await download_queue.get()
        try:
            # Use the new universal download_media function
            info = await asyncio.get_event_loop().run_in_executor(
                executor,
                download_media,
                task["url"],
                task["format_id"],
                task["is_audio"],
                DEBUG
            )
            task["future"].set_result(info)
        except Exception as e:
            logger.error("Error in process_queue", exc_info=True)
            if not task["future"].done():
                task["future"].set_exception(e)
        finally:
            download_queue.task_done()



async def handle_download(url: str, format_id: str = "best", is_audio: bool = False):
    clean_cache()
    future = asyncio.Future()
    
    await download_queue.put({"url": url, "format_id": format_id, "is_audio": is_audio, "future": future})
    return await future


# ---------------------- API Routes ----------------------

@app.get("/", response_class=HTMLResponse, include_in_schema=False)
async def landing(request: Request):
  
    # This block dynamically detects the public URL (your worker URL)
    host = request.headers.get('x-forwarded-host', request.headers.get('host'))
    scheme = request.headers.get('x-forwarded-proto', 'http')
    PUBLIC_URL = f"{scheme}://{host}"

    # Define example URLs for the documentation
    yt_example_url = quote("https://www.youtube.com/watch?v=dQw4w9WgXcQ")
    fb_example_url = quote("https://www.facebook.com/share/v/19mZnNxWaP/")
    tt_example_url = quote("https://www.tiktok.com/@therock/video/7323383323204996398")
    ig_example_url = quote("https://www.instagram.com/p/C3_q2w_rT1d/")
    xnxx_url_example = quote("https://www.xnxx.com/video-13mztfa8/her_ass_is_fat_and_her_pussy_is_wet")
    xnxx_query_example = "big ass"

    html = f"""
<!DOCTYPE html>
<html lang="en">
<head>
    <meta charset="UTF-8">
    <meta name="viewport" content="width=device-width, initial-scale=1.0">
    <title>Universal Media Downloader API</title>
    <link rel="preconnect" href="https://fonts.googleapis.com">
    <link rel="preconnect" href="https://fonts.gstatic.com" crossorigin>
    <link href="https://fonts.googleapis.com/css2?family=Montserrat:wght@400;700&family=Fira+Code&display=swap" rel="stylesheet">
    <style>
        :root {{
            --bg-color: #0d0d1a;
            --card-color: #1a1a2e;
            --border-color: #2c2c4d;
            --primary-color: #8a7cff;
            --secondary-color: #ff7c8a;
            --text-color: #e0e0e0;
            --text-secondary-color: #a0a0b0;
            --green-color: #4caf50;
            --purple-glow: rgba(138, 124, 255, 0.5);
        }}
        body {{
            background-color: var(--bg-color);
            color: var(--text-color);
            font-family: 'Montserrat', sans-serif;
            display: flex;
            justify-content: center;
            align-items: flex-start;
            min-height: 100vh;
            margin: 0;
            padding: 2rem;
            line-height: 1.6;
            position: relative;
        }}
        body::before {{
            content: '';
            position: fixed;
            top: 0;
            left: 0;
            width: 100%;
            height: 100%;
            background: 
                radial-gradient(circle at 10% 20%, rgba(138, 124, 255, 0.2) 0%, transparent 50%),
                radial-gradient(circle at 90% 80%, rgba(255, 124, 138, 0.2) 0%, transparent 50%),
                radial-gradient(circle, rgba(255, 255, 255, 0.1) 1px, transparent 1px),
                radial-gradient(circle, rgba(255, 255, 255, 0.05) 1px, transparent 1px);
            background-size: 50vw 50vh, 50vw 50vh, 20px 20px, 40px 40px;
            background-position: 0 0, 10px 10px, 0 0, 10px 10px;
            z-index: -1;
            animation: move-background 100s linear infinite;
        }}
        @keyframes move-background {{
            0% {{ background-position: 0% 0%, 100% 100%, 0 0, 10px 10px; }}
            100% {{ background-position: 100% 100%, 0% 0%, 100% 100%, 110% 110%; }}
        }}
        .container {{
            width: 100%;
            max-width: 700px;
            text-align: center;
            position: relative;
            z-index: 10;
        }}
        .header {{ margin-bottom: 2rem; }}
        .main-title {{
            font-size: 2.2rem;
            font-weight: 700;
            margin: 0;
            color: #fff;
            animation: pulse-glow 2.5s ease-in-out infinite;
        }}
        @keyframes pulse-glow {{
            0% {{ text-shadow: 0 0 5px var(--primary-color), 0 0 10px var(--primary-color); }}
            50% {{ text-shadow: 0 0 15px var(--primary-color), 0 0 25px var(--primary-color); }}
            100% {{ text-shadow: 0 0 5px var(--primary-color), 0 0 10px var(--primary-color); }}
        }}
        .animated-text {{
            font-size: 1.1rem;
            font-weight: 400;
            margin-top: 0.5rem;
            color: var(--text-secondary-color);
        }}
        .status-container {{
            display: flex;
            justify-content: center;
            align-items: center;
            gap: 1rem;
            margin-top: 1rem;
        }}
        .status {{
            display: inline-flex;
            align-items: center;
            gap: 8px;
            padding: 6px 14px;
            border-radius: 20px;
            background-color: rgba(76, 175, 80, 0.1);
            color: var(--green-color);
            font-weight: 600;
            font-size: 0.9rem;
        }}
        .status-dot {{
            width: 8px; height: 8px;
            background-color: var(--green-color);
            border-radius: 50%;
            animation: pulse 1.5s infinite;
        }}
        @keyframes pulse {{
            0% {{ box-shadow: 0 0 0 0 rgba(76, 175, 80, 0.7); }}
            70% {{ box-shadow: 0 0 0 8px rgba(76, 175, 80, 0); }}
            100% {{ box-shadow: 0 0 0 0 rgba(76, 175, 80, 0); }}
        }}
        h2 {{
            font-size: 1.6rem;
            margin-top: 3rem;
            margin-bottom: 1.5rem;
            color: #fff;
            font-weight: 700;
            text-align: left;
        }}
        .endpoint-card {{
            background-color: var(--card-color);
            border: 1px solid var(--border-color);
            border-radius: 12px;
            margin-bottom: 1.5rem;
            overflow: hidden;
            box-shadow: 0 4px 15px rgba(0, 0, 0, 0.2);
            text-align: left;
            transition: transform 0.3s ease, box-shadow 0.3s ease;
        }}
        .endpoint-card:hover {{
            transform: translateY(-5px);
            box-shadow: 0 8px 25px rgba(0, 0, 0, 0.3);
        }}
        .endpoint-header {{
            padding: 1rem;
            background-color: rgba(0,0,0,0.1);
            border-bottom: 1px solid var(--border-color);
            display: flex;
            align-items: center;
            gap: 1rem;
        }}
        .endpoint-method {{
            display: inline-block;
            padding: 5px 12px;
            border-radius: 8px;
            font-weight: 700;
            font-size: 0.8rem;
            color: #fff;
        }}
        .method-get {{ background-color: var(--green-color); }}
        .method-info {{ background-color: var(--primary-color); }}
        .endpoint-path {{
            font-family: 'Fira Code', monospace;
            font-size: 1rem;
            color: var(--text-color);
            flex-grow: 1;
            word-break: break-all;
        }}
        .endpoint-body {{ padding: 1.5rem; }}
        p {{
            color: var(--text-secondary-color);
            margin: 0 0 1rem 0;
            font-size: 0.95rem;
        }}
        b {{ color: var(--text-color); font-weight: 700; }}
        code {{
            font-family: 'Fira Code', monospace;
            background-color: var(--bg-color);
            padding: 0.2em 0.4em;
            border-radius: 4px;
            font-size: 0.9em;
            border: 1px solid var(--border-color);
            word-break: break-all;
        }}
        pre {{
            background-color: #050508;
            padding: 1rem;
            border-radius: 8px;
            border: 1px solid var(--border-color);
            white-space: pre-wrap;
            word-wrap: break-word;
            font-family: 'Fira Code', monospace;
            font-size: 0.9rem;
            overflow-x: auto;
        }}
        footer {{
            text-align: center;
            margin-top: 3rem;
            padding-bottom: 2rem;
            color: var(--text-secondary-color);
            font-size: 0.8rem;
        }}
        footer a {{
            color: var(--primary-color);
            text-decoration: none;
            font-weight: 700;
        }}
        footer a:hover {{ text-decoration: underline; }}
    </style>
</head>
<body>
    <div class="container">
        <header class="header">
            <h1 class="main-title">Universal Media Downloader</h1>
            <p class="animated-text">Developed By Matrix X Scraper</p>
        </header>

        <div class="status-container">
            <div class="status">
                <div class="status-dot"></div><span>API Operational</span>
            </div>
        </div>

        <main>
            <h2>General Info</h2>
            <div class="endpoint-card">
                <div class="endpoint-header">
                    <span class="endpoint-method method-info">GET</span>
                    <p class="endpoint-path">/info</p>
                </div>
                <div class="endpoint-body">
                    <p>Fetches media metadata and a list of available formats for any supported URL.</p>
                    <p><b>Example:</b></p>
                    <pre><code>{PUBLIC_URL}/info?url={yt_example_url}</code></pre>
                </div>
            </div>

            <h2>XNXX</h2>
            <div class="endpoint-card">
                <div class="endpoint-header">
                    <span class="endpoint-method method-get">GET</span>
                    <p class="endpoint-path">/search/xnxx</p>
                </div>
                <div class="endpoint-body">
                    <p>Searches XNXX by keyword and returns a list of results with formats and download URLs.</p>
                    <p><b>Example:</b></p>
                    <pre><code>{PUBLIC_URL}/search/xnxx?query={xnxx_query_example}</code></pre>
                </div>
            </div>
            <div class="endpoint-card">
                <div class="endpoint-header">
                    <span class="endpoint-method method-get">GET</span>
                    <p class="endpoint-path">/download/xnxx</p>
                </div>
                <div class="endpoint-body">
                    <p>Fetches all formats and download links for a single XNXX video URL.</p>
                    <p><b>Example:</b></p>
                    <pre><code>{PUBLIC_URL}/download/xnxx?url={xnxx_url_example}</code></pre>
                </div>
            </div>
            
            <h2>Social Media & YouTube</h2>
            <div class="endpoint-card">
                <div class="endpoint-header">
                    <span class="endpoint-method method-get">GET</span>
                    <p class="endpoint-path">/download/facebook</p>
                </div>
                <div class="endpoint-body"><pre><code>{PUBLIC_URL}/download/facebook?url={fb_example_url}</code></pre></div>
            </div>
            <div class="endpoint-card">
                <div class="endpoint-header">
                    <span class="endpoint-method method-get">GET</span>
                    <p class="endpoint-path">/download/facebookmp3</p>
                </div>
                <div class="endpoint-body"><pre><code>{PUBLIC_URL}/download/facebookmp3?url={fb_example_url}</code></pre></div>
            </div>
            <div class="endpoint-card">
                <div class="endpoint-header">
                    <span class="endpoint-method method-get">GET</span>
                    <p class="endpoint-path">/download/instagram</p>
                </div>
                <div class="endpoint-body"><pre><code>{PUBLIC_URL}/download/instagram?url={ig_example_url}</code></pre></div>
            </div>
            <div class="endpoint-card">
                <div class="endpoint-header">
                    <span class="endpoint-method method-get">GET</span>
                    <p class="endpoint-path">/download/instagrammp3</p>
                </div>
                <div class="endpoint-body"><pre><code>{PUBLIC_URL}/download/instagrammp3?url={ig_example_url}</code></pre></div>
            </div>
             <div class="endpoint-card">
                <div class="endpoint-header">
                    <span class="endpoint-method method-get">GET</span>
                    <p class="endpoint-path">/download/tiktok</p>
                </div>
                <div class="endpoint-body"><pre><code>{PUBLIC_URL}/download/tiktok?url={tt_example_url}</code></pre></div>
            </div>
             <div class="endpoint-card">
                <div class="endpoint-header">
                    <span class="endpoint-method method-get">GET</span>
                    <p class="endpoint-path">/download/tiktokmp3</p>
                </div>
                <div class="endpoint-body"><pre><code>{PUBLIC_URL}/download/tiktokmp3?url={tt_example_url}</code></pre></div>
            </div>
            <div class="endpoint-card">
                <div class="endpoint-header">
                    <span class="endpoint-method method-get">GET</span>
                    <p class="endpoint-path">/download/ytmp4fhd</p>
                </div>
                <div class="endpoint-body"><pre><code>{PUBLIC_URL}/download/ytmp4fhd?url={yt_example_url}</code></pre></div>
            </div>
            <div class="endpoint-card">
                <div class="endpoint-header">
                    <span class="endpoint-method method-get">GET</span>
                    <p class="endpoint-path">/download/ytmp3</p>
                </div>
                <div class="endpoint-body"><pre><code>{PUBLIC_URL}/download/ytmp3?url={yt_example_url}</code></pre></div>
            </div>
        </main>
        <footer>
            <p>View Full API Spec: <a href="/docs" target="_blank">Interactive Docs</a></p>
        </footer>
    </div>
</body>
</html>
"""
    return HTMLResponse(content=html)




# ---------------------- Helper for Dynamic URL ----------------------
def get_public_url(request: Request) -> str:
    """Dynamically determines the public-facing URL of the server from request headers."""
    host = request.headers.get('x-forwarded-host', request.headers.get('host'))
    scheme = request.headers.get('x-forwarded-proto', 'http')
    return f"{scheme}://{host}"

# ---------------------- API Routes ----------------------

@app.get("/info", summary="Get Universal Media Info")
async def get_info_endpoint(url: str = Query(..., description="The media URL to get information for.")):
    """
    Fetches video metadata for any supported platform (YouTube, Facebook, etc.).
    """
    try:
        info = await asyncio.get_event_loop().run_in_executor(
            executor,
            get_universal_media_info,
            unquote(url),
            DEBUG
        )
        return create_response(True, result=info)
    except Exception as e:
        logger.error(f"/info endpoint error for URL {url}: {e}", exc_info=True)
        return create_response(False, message=str(e), status=500)

@app.get("/download/ytmp4fhd")
async def ytmp4fhd(request: Request, url: str = Query(...)):
    try:
        PUBLIC_URL = get_public_url(request)
        format_id = "bestvideo[height<=1080][ext=mp4]+bestaudio[ext=m4a]/best[height<=1080][ext=mp4]"
        info = await handle_download(unquote(url), format_id=format_id, is_audio=False)
        return create_response(True, {
            "type": "video", "quality": "best <=1080p", "title": info["title"],
            "thumbnail": info["thumbnail"], 
            "download_url": f"{PUBLIC_URL}/cache/{info['video_id']}.{info['ext']}"
        })
    except Exception as e:
        logger.error("ytmp4fhd error", exc_info=True)
        return create_response(False, message=str(e), status=500)

@app.get("/download/ytmp3")
async def ytmp3(request: Request, url: str = Query(...)):
    try:
        PUBLIC_URL = get_public_url(request)
        info = await handle_download(unquote(url), is_audio=True)
        return create_response(True, {
            "type": "audio", "quality": "320kbps", "title": info["title"],
            "thumbnail": info["thumbnail"],
            "download_url": f"{PUBLIC_URL}/cache/{info['video_id']}.{info['ext']}",
        })
    except Exception as e:
        logger.error("ytmp3 error", exc_info=True)
        return create_response(False, message=str(e), status=500)

@app.get("/download/facebook", summary="Download Facebook Video")
async def download_facebook(request: Request, url: str = Query(...)):
    try:
        PUBLIC_URL = get_public_url(request)
        format_string = "bestvideo[ext=mp4]+bestaudio[ext=m4a]/best[ext=mp4]/best"
        info = await handle_download(unquote(url), format_id=format_string, is_audio=False)
        return create_response(True, {
            "type": "video", "title": info.get("title"),
            "thumbnail": info.get("thumbnail"), 
            "download_url": f"{PUBLIC_URL}/cache/{info['video_id']}.{info['ext']}"
        })
    except Exception as e:
        logger.error(f"Facebook download failed for URL {url}: {e}", exc_info=True)
        raise HTTPException(status_code=500, detail=f"Failed to process Facebook URL: {e}")

@app.get("/download/facebookmp3", summary="Download Facebook Video as MP3")
async def download_facebook_mp3(request: Request, url: str = Query(...)):
    logger.info(f"Received Facebook MP3 download request for URL: {url}")
    try:
        PUBLIC_URL = get_public_url(request)
        info = await handle_download(url, is_audio=True)
        return create_response(
            success=True,
            result={
                "type": "audio", "quality": "320kbps", "title": info.get("title"),
                "thumbnail": info.get("thumbnail"),
                "download_url": f"{PUBLIC_URL}/cache/{info['video_id']}.{info['ext']}",
            },
        )
    except Exception as e:
        logger.error(f"Facebook MP3 download failed for URL {url}: {e}", exc_info=True)
        raise HTTPException(status_code=500, detail=f"Failed to extract audio from Facebook URL: {e}")

@app.get("/download/tiktok", summary="Download TikTok Video As Video")
async def download_tiktok(url: str = Query(..., description="The URL-encoded TikTok video link")):
    logger.info(f"Received TikTok Video download request for URL: {url}")
    try:
        format_string = "bestvideo[ext=mp4]+bestaudio[ext=m4a]/best[ext=mp4]/best"
        info = await handle_download(unquote(url), format_id=format_string, is_audio=False)
        filepath = info.get("filepath")
        if not filepath or not os.path.exists(filepath):
            logger.error(f"Downloaded file not found in cache for URL: {url}")
            raise HTTPException(status_code=404, detail="Downloaded file not found in cache.")
        return FileResponse(filepath, media_type="video/mp4", filename=f"{info.get('title', 'tiktok_video')}.mp4")
    except Exception as e:
        logger.error(f"TikTok video download failed for URL {url}: {e}", exc_info=True)
        raise HTTPException(status_code=500, detail=f"Failed to process TikTok video: {str(e)}")

@app.get("/download/tiktokmp3", summary="Download TikTok Video as MP3")
async def download_tiktok_mp3(url: str = Query(..., description="The URL-encoded TikTok video link")):
    logger.info(f"Received TikTok MP3 download request for URL: {url}")
    try:
        info = await handle_download(unquote(url), is_audio=True)
        filepath = info.get("filepath")
        if not filepath or not os.path.exists(filepath):
            logger.error(f"Downloaded MP3 file not found in cache for URL: {url}")
            raise HTTPException(status_code=404, detail="Downloaded file not found in cache.")
        return FileResponse(filepath, media_type="audio/mpeg", filename=f"{info.get('title', 'tiktok_audio')}.mp3")
    except Exception as e:
        logger.error(f"TikTok MP3 download failed for URL {url}: {e}", exc_info=True)
        raise HTTPException(status_code=500, detail=f"Failed to extract audio from TikTok: {str(e)}")

@app.get("/download/instagram", summary="Download Instagram Media")
async def download_instagram(request: Request, url: str = Query(...)):
    try:
        PUBLIC_URL = get_public_url(request)
        format_string = "bestvideo[ext=mp4]+bestaudio[ext=m4a]/best[ext=mp4]/best"
        info = await handle_download(unquote(url), format_id=format_string, is_audio=False)
        return create_response(True, {
            "type": "video", "quality": "best", "title": info["title"],
            "thumbnail": info["thumbnail"], 
            "download_url": f"{PUBLIC_URL}/cache/{info['video_id']}.{info['ext']}"
        })
    except Exception as e:
        logger.error(f"Instagram download failed for URL {url}: {e}", exc_info=True)
        raise HTTPException(status_code=500, detail=f"Failed to process Instagram URL: {e}")

@app.get("/download/instagrammp3", summary="Download Instagram Media as MP3")
async def download_instagram_mp3(request: Request, url: str = Query(...)):
    logger.info(f"Received Instagram MP3 download request for URL: {url}")
    try:
        PUBLIC_URL = get_public_url(request)
        info = await handle_download(unquote(url), is_audio=True)
        return create_response(
            success=True,
            result={
                "type": "audio", "quality": "320kbps", "title": info.get("title"),
                "thumbnail": info.get("thumbnail"),
                "download_url": f"{PUBLIC_URL}/cache/{info['video_id']}.{info['ext']}",
            },
        )
    except Exception as e:
        logger.error(f"Instagram MP3 download failed for URL {url}: {e}", exc_info=True)
        raise HTTPException(status_code=500, detail=f"Failed to extract audio from Instagram URL: {e}")
        
        
@app.get("/download/mediafire", summary="Download MediaFire File")
async def download_mediafire(request: Request, url: str = Query(..., description="MediaFire file URL")):
    try:
        PUBLIC_URL = get_public_url(request)
        format_string = "best"  # yt-dlp format string for best available file
        info = await handle_download(unquote(url), format_id=format_string, is_audio=False)
        return create_response(True, {
            "type": "file",
            "title": info["title"],
            "download_url": f"{PUBLIC_URL}/cache/{info['video_id']}.{info['ext']}",
        })
    except Exception as e:
        logger.error(f"MediaFire download failed for URL {url}: {e}", exc_info=True)
        raise HTTPException(status_code=500, detail=f"Failed to process MediaFire URL: {e}")
                
        
        


@app.get("/download/xnxx", summary="Get All Info and Download Links for a Single XNXX URL")
async def download_xnxx(request: Request, url: str = Query(..., description="The direct URL of the XNXX video.")):

    logger.info(f"Received direct info and download link request for XNXX URL: {url}")
    
    try:
        # Step 1: Get all media information for the given URL.
        media_info = await asyncio.get_event_loop().run_in_executor(
            executor,
            get_universal_media_info,
            unquote(url),
            DEBUG
        )

        # Step 2: For each format found, create a direct and ready-to-use download URL.
        API_BASE_URL = get_public_url(request)
        enhanced_formats = []
        page_url_encoded = quote(unquote(url))

        for f in media_info.get("formats", []):
            format_id_encoded = quote(str(f.get('format_id', '')))
            
            # This is the generic download link that your bot or app can use immediately.
            download_link = (
                f"{API_BASE_URL}/download?"
                f"url={page_url_encoded}"
                f"&format_id={format_id_encoded}"
            )
            
            # Add the direct download link to the format information.
            new_format = f.copy()
            new_format['download_url'] = download_link
            enhanced_formats.append(new_format)
        
        # Step 3: Combine the title, thumbnail, and the list of formats into one final result.
        final_result = {
            "title": media_info.get("title"),
            "thumbnail_url": media_info.get("thumbnail_url"),
            "formats": enhanced_formats
        }

        # Return everything in a single, successful response.
        return create_response(True, result=final_result)

    except Exception as e:
        logger.error(f"Failed to process direct XNXX URL {url}: {e}", exc_info=True)
        # Provide a helpful error message if something goes wrong.
        raise HTTPException(status_code=500, detail=f"Failed to process the XNXX URL: {str(e)}")



@app.get("/search/xnxx", summary="Search XNXX by keyword")
async def search_xnxx_endpoint(request: Request, query: str = Query(...)):
    """
    Searches XNXX and returns results with ready-to-use download links.
    """
    try:
        # Pass the request object down to the search function so it can build correct URLs
        search_results = await asyncio.get_event_loop().run_in_executor(
            executor, 
            search_xnxx_videos, 
            query,
            request  # Pass the request object here
        )
        
        if isinstance(search_results, dict) and "error" in search_results:
             return create_response(False, message=search_results["error"], status=500)
             
        return create_response(True, result=search_results)
    except Exception as e:
        logger.error(f"XNXX search failed for query '{query}': {e}", exc_info=True)
        return create_response(False, message=str(e), status=500)


@app.get("/download", summary="Download Media by Specific format_id")
async def download_specific_format(
    request: Request,
    url: str = Query(..., description="The URL of the media"),
    format_id: str = Query(..., description="The format_id obtained from the /info endpoint"),
    audio_only: bool = Query(False, description="Set to true if the format_id is for audio")
):
    """
    Downloads media using a specific format_id, generating a public URL for the cached file.
    """
    logger.info(f"Received specific download request for URL: {url} with format_id: {format_id}")
    try:
        info = await handle_download(unquote(url), format_id=format_id, is_audio=audio_only)
        
        # Dynamically get the public URL from the request
        PUBLIC_URL = get_public_url(request)
        
        return create_response(True, {
            "title": info.get("title"),
            "download_url": f"{PUBLIC_URL}/cache/{info['video_id']}.{info['ext']}"
        })
    except Exception as e:
        logger.error(f"Specific format download failed for URL {url}: {e}", exc_info=True)
        raise HTTPException(status_code=500, detail=f"Failed to process download request: {e}")



@app.get("/cache/{filename}")
async def serve_cache(filename: str):
    filepath = os.path.join(CACHE_DIR, filename)
    if not os.path.exists(filepath):
        raise HTTPException(status_code=404, detail="File not found")
    return FileResponse(filepath, media_type="application/octet-stream", filename=filename)


@app.get("/healthz", summary="Health Check")
async def healthcheck():
    return {"status": "ok"}


@app.get("/logs")
async def get_logs():
    if not os.path.exists(LOG_FILE):
        raise HTTPException(status_code=404, detail="Log file not found")
    return FileResponse(LOG_FILE, media_type="text/plain", filename=LOG_FILE)


# ---------------------- yt-dlp Updater ----------------------
def update_yt_dlp(channel: str):
    if channel == "nightly":
        url = "https://github.com/yt-dlp/yt-dlp-nightly-builds/releases/latest/download/yt-dlp.tar.gz"
    elif channel == "master":
        url = "git+https://github.com/yt-dlp/yt-dlp.git"
    else:
        url = "yt-dlp"

    subprocess.run([sys.executable, "-m", "pip", "install", "--upgrade", "--force-reinstall", url])
    logger.info(f"yt-dlp updated to {channel} build. Restarting...")
    os.execv(sys.executable, [sys.executable] + sys.argv)


# ---------------------- CLI / Server Start ----------------------
if __name__ == "__main__":
    import uvicorn
        
    port = int(os.environ.get("PORT", 10000))
    
    
    uvicorn.run(app, host="0.0.0.0", port=port)
