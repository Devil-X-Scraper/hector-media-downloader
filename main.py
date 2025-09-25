"""
YouTube Downloader API using FastAPI + yt-dlp
Author: Matrix

"""

import os
import sys
import argparse
import subprocess
import asyncio
import logging
import requests
from bs4 import BeautifulSoup
from logging.handlers import RotatingFileHandler
from urllib.parse import unquote
from contextlib import asynccontextmanager
from concurrent.futures import ThreadPoolExecutor
from fastapi.responses import FileResponse 

import yt_dlp
from fastapi import FastAPI, Query, HTTPException, Request
from fastapi.responses import JSONResponse, FileResponse, HTMLResponse
from fastapi.middleware.cors import CORSMiddleware
from fastapi.staticfiles import StaticFiles


# ---------------------- Configuration ----------------------
HOST = "144.91.87.159"
PORT = 25566
CACHE_DIR = "cache"
MAX_CACHE_FILES = 10

DEBUG = False  # set via CLI when running directly
download_queue = asyncio.Queue()
executor = ThreadPoolExecutor(max_workers=3) # Increased workers for info fetching

# ---------------------- Logging ----------------------
for handler in logging.root.handlers[:]:
    logging.root.removeHandler(handler)

LOG_FILE = "logs.txt"
LOG_FORMAT = "%(asctime)s [%(levelname)s] %(message)s"

log_formatter = logging.Formatter(LOG_FORMAT)

file_handler = RotatingFileHandler(LOG_FILE, maxBytes=5 * 1024 * 1024, backupCount=5)
file_handler.setFormatter(log_formatter)
file_handler.setLevel(logging.DEBUG)

console_handler = logging.StreamHandler(sys.stdout)
console_handler.setFormatter(log_formatter)
console_handler.setLevel(logging.INFO)

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


def search_xnxx_videos(query: str, page: int = 1):
    """
    Searches XNXX for a keyword on a specific page and returns a list of results,
    plus a flag indicating if there are more pages, and the next page URL.
    """
    import re
    import time
    import random
    from urllib.parse import quote

    # Enhanced headers to look like a real browser
    headers = {
        'User-Agent': 'Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/120.0.0.0 Safari/537.36',
        'Accept': 'text/html,application/xhtml+xml,application/xml;q=0.9,image/avif,image/webp,image/apng,*/*;q=0.8,application/signed-exchange;v=b3;q=0.7',
        'Accept-Language': 'en-US,en;q=0.9',
        'Accept-Encoding': 'gzip, deflate, br',
        'DNT': '1',
        'Connection': 'keep-alive',
        'Upgrade-Insecure-Requests': '1',
        'Sec-Fetch-Dest': 'document',
        'Sec-Fetch-Mode': 'navigate',
        'Sec-Fetch-Site': 'none',
        'Cache-Control': 'max-age=0',
    }

    # Use the correct URL format
    if page > 1:
        search_url = f"https://www.xnxx.com/search/{quote(query)}/{page}"
    else:
        search_url = f"https://www.xnxx.com/search/{quote(query)}"

    try:
        session = requests.Session()
        session.headers.update(headers)

        # Add random delay to look more human
        time.sleep(random.uniform(1.0, 3.0))

        response = session.get(search_url, timeout=30)
        response.raise_for_status()

        # Check if we got redirected (sign of bot detection)
        if 'search' not in response.url.lower():
            logger.warning(f"Possible redirect detected. Response URL: {response.url}")

        soup = BeautifulSoup(response.text, 'html.parser')

        # Look for actual video results
        video_blocks = soup.select('div.thumb-block')

        logger.info(f"Found {len(video_blocks)} video blocks for query '{query}' on page {page}.")

        results = []
        seen_ids = set()

        for block in video_blocks:
            title_tag = block.select_one('div.thumb-under p a')
            if not title_tag:
                continue

            href = title_tag.get('href', '')

            match = re.search(r'/video-([a-zA-Z0-9]+)/', href)
            if not match:
                continue

            video_id = match.group(1)
            if video_id in seen_ids:
                continue

            seen_ids.add(video_id)

            page_url = "https://www.xnxx.com" + href
            title = title_tag.get('title', 'No Title')

            results.append({
                "title": title,
                "page_url": page_url
            })

        # --- UPDATED PAGINATION DETECTION ---
        next_page_button = soup.select_one('.pagination ul li a.no-page.next')
        has_more = next_page_button is not None

        next_page_url = None
        if next_page_button:
            next_page_url = requests.compat.urljoin(search_url, next_page_button.get('href'))

        logger.info(f"Successfully scraped {len(results)} results for '{query}' on page {page}. Has more pages: {has_more}")

        return {"videos": results, "has_more": has_more, "next_page_url": next_page_url}

    except Exception as e:
        logger.error(f"Error in search_xnxx_videos: {e}", exc_info=True)
        return {"error": f"The search operation failed: {str(e)}"}


@app.get("/search/xnxx", summary="Search XNXX by keyword with pagination")
async def search_xnxx_endpoint(query: str = Query(..., description="The keyword to search for."), page: int = Query(1, description="The page number to search.")):
    try:
        search_results = await asyncio.get_event_loop().run_in_executor(
            executor, 
            search_xnxx_videos, 
            query,
            page
        )
        
        if isinstance(search_results, dict) and "error" in search_results:
             return create_response(False, message=search_results["error"], status=500)
             
        return create_response(True, result=search_results)
    except Exception as e:
        logger.error(f"XNXX search failed for query '{query}' on page {page}: {e}", exc_info=True)
        return create_response(False, message=str(e), status=500)


def get_universal_media_info(url: str, debug: bool = False):
    """
    Universal media info extractor that gets all available formats with their real format_id.
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
        # Loop through all available formats from yt-dlp
        for f in info.get('formats', []):
            filesize = f.get('filesize') or f.get('filesize_approx')
            
            if not filesize:
                duration = info.get('duration')
                tbr = f.get('tbr')
                if duration and tbr:
                    filesize = int((tbr * 1000 / 8) * duration)

            # Add every available video format to the list
            if f.get('vcodec') != 'none':
                processed_formats.append({
                    "quality": f.get('format_note', f.get('height', 'unknown')),
                    "format_id": f.get('format_id'),  # This is the REAL ID you need
                    "filesize_mb": round(filesize / (1024 * 1024), 2) if filesize else "N/A",
                    "ext": f.get('ext', 'mp4')
                })

        # Add a separate option for downloading audio only
        best_audio = next((f for f in sorted(info.get('formats', []), key=lambda x: x.get('abr') or 0, reverse=True) if f.get('acodec') != 'none'), None)
        if best_audio:
            filesize = best_audio.get('filesize') or best_audio.get('filesize_approx')
            if not filesize:
                duration, abr = info.get('duration'), best_audio.get('abr')
                if duration and abr:
                    filesize = int((abr * 1000 / 8) * duration)
            
            processed_formats.append({
                "quality": "audio_mp3",
                "format_id": best_audio.get('format_id'), # The ID for the best audio
                "filesize_mb": round(filesize / (1024 * 1024), 2) if filesize else "N/A",
                "ext": "mp3"
            })

        logger.info(f"SUCCESS! Got info for '{url}'.")
        
        return {
            "title": info.get("title", "No Title Found"),
            "thumbnail_url": info.get("thumbnail"),
            "description": info.get("description"),
            "formats": processed_formats
        }



def download_media(url: str, format_id: str, is_audio: bool, debug: bool = False):
    """Universal downloader for any platform supported by yt-dlp."""
    ydl_opts = {
        "noplaylist": True,
        # This is the corrected part: Always use the format_id from the user.
        "format": format_id,
        "outtmpl": os.path.join(CACHE_DIR, "%(id)s.%(ext)s"),
        "cookiefile": "cookies.txt" if os.path.exists("cookies.txt") else None,
        "logger": YTDLPLogger(),
        "progress_hooks": [lambda d: logger.debug(d)],
        "quiet": not debug,
        "verbose": debug,
    }
    # The is_audio flag is now only used to decide if we should convert to MP3.
    if is_audio:
        ydl_opts["postprocessors"] = [{
            "key": "FFmpegExtractAudio",
            "preferredcodec": "mp3",
            "preferredquality": "320",
        }]
    
    with yt_dlp.YoutubeDL(ydl_opts) as ydl:
        logger.info(f"Starting download for {'audio' if is_audio else 'video'} with options: {ydl_opts}")
        info = ydl.extract_info(url, download=True)
        video_id = info.get("id")
        
        # When extracting audio with the post-processor, the final extension is always 'mp3'.
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


# In your main.py, REPLACE the entire @app.get("/") function with this corrected version:

@app.get("/", response_class=HTMLResponse, include_in_schema=False)
async def landing(request: Request):
    # This line requires 'quote' to be imported from 'urllib.parse'
    from urllib.parse import quote
    
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



#  @app.get("/info") route with this new one.
@app.get("/info", summary="Get Universal Media Info")
async def get_info_endpoint(url: str = Query(..., description="The media URL to get information for.")):
    """
    Fetches video metadata for any supported platform (YouTube, Facebook, etc.).
    """
    try:
        # Use the new universal info extractor
        info = await asyncio.get_event_loop().run_in_executor(
            executor,
            get_universal_media_info, # Call the new function
            unquote(url),
            DEBUG
        )
        return create_response(True, result=info)
    except Exception as e:
        logger.error(f"/info endpoint error for URL {url}: {e}", exc_info=True)
        return create_response(False, message=str(e), status=500)


@app.get("/download/ytmp4fhd")
async def ytmp4fhd(url: str = Query(...), format_id: str = Query("best")):
    """
    Downloads a YouTube video for a specific format and streams it directly.
    """
    logger.info(f"Received YouTube video stream request for URL: {url} with format: {format_id}")
    try:
        # Use a flexible format string that prioritizes the user's choice
        final_format = f"{format_id}[ext=mp4]/best[ext=mp4]"
        info = await handle_download(unquote(url), format_id=final_format, is_audio=False)
        
        filepath = info.get("filepath")
        title = info.get("title", "youtube_video")
        safe_filename = "".join(c for c in title if c.isalnum() or c in (' ', '_')).rstrip()[:50] + ".mp4"

        if not filepath or not os.path.exists(filepath):
            raise HTTPException(status_code=404, detail="Downloaded video file not found.")

        return FileResponse(
            path=filepath,
            media_type='video/mp4',
            filename=safe_filename
        )
    except Exception as e:
        logger.error(f"YouTube video stream failed for URL {url}", exc_info=True)
        raise HTTPException(status_code=500, detail=f"Failed to stream YouTube video: {str(e)}")


@app.get("/download/ytmp3")
async def ytmp3(url: str = Query(...)):
    """
    Downloads YouTube audio and streams it directly as an MP3 file.
    """
    logger.info(f"Received YouTube MP3 stream request for URL: {url}")
    try:
        info = await handle_download(unquote(url), is_audio=True)
        
        filepath = info.get("filepath")
        title = info.get("title", "youtube_audio")
        safe_filename = "".join(c for c in title if c.isalnum() or c in (' ', '_')).rstrip()[:50] + ".mp3"

        if not filepath or not os.path.exists(filepath):
            raise HTTPException(status_code=404, detail="Downloaded audio file not found.")

        return FileResponse(
            path=filepath,
            media_type='audio/mpeg',
            filename=safe_filename
        )
    except Exception as e:
        logger.error(f"YouTube MP3 stream failed for URL {url}", exc_info=True)
        raise HTTPException(status_code=500, detail=f"Failed to stream YouTube audio: {str(e)}")


@app.get("/download/facebook", summary="Download Facebook Video")
async def download_facebook(url: str = Query(...)):
    try:
        format_string = "bestvideo[ext=mp4]+bestaudio[ext=m4a]/best[ext=mp4]/best"
        info = await handle_download(unquote(url), format_id=format_string, is_audio=False)
        
        return create_response(True, {
            "type": "video", "title": info.get("title"),
            "thumbnail": info.get("thumbnail"), "download_url": f"http://{HOST}:{PORT}/cache/{info['video_id']}.{info['ext']}"
        })
    except Exception as e:
        logger.error(f"Facebook download failed for URL {url}: {e}", exc_info=True)
        raise HTTPException(status_code=500, detail=f"Failed to process Facebook URL: {e}")
        
        

@app.get("/download/facebookmp3", summary="Download Facebook Video as MP3")
async def download_facebook_mp3(url: str = Query(...)):
    """
    Downloads the audio track from a Facebook video as a 320kbps MP3 file.
    """
    logger.info(f"Received Facebook MP3 download request for URL: {url}")
    try:
        info = await handle_download(url, is_audio=True)
        return create_response(
            success=True,
            result={
                "type": "audio",
                "quality": "320kbps",
                "title": info.get("title"),
                "thumbnail": info.get("thumbnail"),
                "download_url": f"http://{HOST}:{PORT}/cache/{info['video_id']}.{info['ext']}",
            },
        )
    except Exception as e:
        logger.error(f"Facebook MP3 download failed for URL {url}: {e}", exc_info=True)
        raise HTTPException(status_code=500, detail=f"Failed to extract audio from Facebook URL: {e}")


@app.get("/download/tiktok", summary="Download TikTok Video As Video")
async def download_tiktok(url: str = Query(..., description="The URL-encoded TikTok link.")):
    """
    Downloads a TikTok video and returns the file directly as a streaming response.
    """
    logger.info(f"Received direct TikTok Video stream request for URL: {url}")
    try:
        # We specify a format that works well for Telegram
        format_string = "bestvideo[ext=mp4]+bestaudio[ext=m4a]/best[ext=mp4]/best"
        info = await handle_download(unquote(url), format_id=format_string, is_audio=False)
        
        filepath = info.get("filepath")
        
        # Create a clean filename for the user
        title = info.get("title", "tiktok_video").replace("'", "")
        safe_filename = "".join(c for c in title if c.isalnum() or c in (' ', '_')).rstrip()[:50] + ".mp4"

        if not filepath or not os.path.exists(filepath):
            logger.error(f"Downloaded video file could not be found at path: {filepath}")
            raise HTTPException(status_code=404, detail="Downloaded video file not found on server.")

        # Return the actual video file as a streaming response
        return FileResponse(
            path=filepath,
            media_type='video/mp4',
            filename=safe_filename
        )

    except Exception as e:
        logger.error(f"TikTok video direct download failed for URL {url}", exc_info=True)
        raise HTTPException(
            status_code=500, detail=f"Failed to process and stream TikTok video: {str(e)}")



@app.get("/download/tiktokmp3", summary="Download TikTok Video as MP3")
async def download_tiktok_mp3(url: str = Query(..., description="The URL-encoded TikTok link.")):

    logger.info(f"Received direct TikTok MP3 download stream request for URL: {url}")
    try:
        # This function downloads the file and returns its info, including the local filepath
        info = await handle_download(unquote(url), is_audio=True)
        
        filepath = info.get("filepath")
        
        # Create a clean filename for the user
        title = info.get("title", "tiktok_audio").replace("'", "")
        safe_filename = "".join(c for c in title if c.isalnum() or c in (' ', '_')).rstrip()[:50] + ".mp3"

        if not filepath or not os.path.exists(filepath):
            logger.error(f"Downloaded file could not be found at path: {filepath}")
            raise HTTPException(status_code=404, detail="Downloaded file not found on server.")

        # Return the actual file as a streaming response
        return FileResponse(
            path=filepath,
            media_type='audio/mpeg',
            filename=safe_filename
        )

    except Exception as e:
        logger.error(f"TikTok MP3 direct download failed for URL {url}", exc_info=True)
        raise HTTPException(
            status_code=500, detail=f"Failed to process and stream TikTok audio: {str(e)}"
        )


# In main.py

# REPLACE the existing /download/instagram route with this one
@app.get("/download/instagram", summary="Download Instagram Media as Video")
async def download_instagram(url: str = Query(..., description="The URL of the Instagram post.")):
    """
    Downloads an Instagram video and returns it as a direct file stream.
    """
    logger.info(f"Received direct Instagram Video stream request for URL: {url}")
    try:
        format_string = "bestvideo[ext=mp4]+bestaudio[ext=m4a]/best[ext=mp4]/best"
        info = await handle_download(unquote(url), format_id=format_string, is_audio=False)
        
        filepath = info.get("filepath")
        title = info.get("title", "instagram_video")
        safe_filename = "".join(c for c in title if c.isalnum() or c in (' ', '_')).rstrip()[:50] + ".mp4"

        if not filepath or not os.path.exists(filepath):
            raise HTTPException(status_code=404, detail="Downloaded video file not found.")

        return FileResponse(
            path=filepath,
            media_type='video/mp4',
            filename=safe_filename
        )
    except Exception as e:
        logger.error(f"Instagram video stream failed for URL {url}", exc_info=True)
        raise HTTPException(status_code=500, detail=f"Failed to stream Instagram video: {str(e)}")


# REPLACE the existing /download/instagrammp3 route with this one
@app.get("/download/instagrammp3", summary="Download Instagram Media as MP3")
async def download_instagram_mp3(url: str = Query(..., description="The URL of the Instagram post.")):
    """
    Downloads audio from an Instagram post and returns it as a direct file stream.
    """
    logger.info(f"Received direct Instagram MP3 stream request for URL: {url}")
    try:
        info = await handle_download(unquote(url), is_audio=True)
        
        filepath = info.get("filepath")
        title = info.get("title", "instagram_audio")
        safe_filename = "".join(c for c in title if c.isalnum() or c in (' ', '_')).rstrip()[:50] + ".mp3"

        if not filepath or not os.path.exists(filepath):
            raise HTTPException(status_code=404, detail="Downloaded audio file not found.")

        return FileResponse(
            path=filepath,
            media_type='audio/mpeg',
            filename=safe_filename
        )
    except Exception as e:
        logger.error(f"Instagram MP3 stream failed for URL {url}", exc_info=True)
        raise HTTPException(status_code=500, detail=f"Failed to stream Instagram audio: {str(e)}")


@app.get("/download/xnxx", summary="Get All Info and Download Links for a Single XNXX URL")
async def download_xnxx(request: Request, url: str = Query(..., description="The direct URL of the XNXX video.")):

    from urllib.parse import quote
    logger.info(f"Received direct info and download link request for XNXX URL: {url}")
    
    try:
        # Step 1: Get all media information for the given URL.
        media_info = await asyncio.get_event_loop().run_in_executor(
            executor,
            get_universal_media_info,
            unquote(url),
            DEBUG
        )

        host = request.headers.get('x-forwarded-host', request.headers.get('host', f"{HOST}:{PORT}"))
        scheme = request.headers.get('x-forwarded-proto', 'http')
        API_BASE_URL = f"{scheme}://{host}"
        
        enhanced_formats = []
        page_url_encoded = quote(unquote(url))

        for f in media_info.get("formats", []):
            format_id_encoded = quote(str(f.get('format_id', '')))

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


@app.get("/download", summary="Universal Media Downloader by Specific Format ID")
async def download_specific_format(
    url: str = Query(..., description="The URL of the media to download"),
    format_id: str = Query(..., description="The specific format_id obtained from the /info endpoint"),
    audio_only: bool = Query(False, description="Set to true if the format is audio")
):
    """
    This is the universal endpoint that downloads any media using a specific format_id
    and streams the file directly back to the client.
    """
    logger.info(f"Received universal download request for URL: {url} with format_id: {format_id}")
    try:
        # Use the existing handle_download queue to download the file
        info = await handle_download(unquote(url), format_id=format_id, is_audio=audio_only)
        
        filepath = info.get("filepath")
        
        if not filepath or not os.path.exists(filepath):
            logger.error(f"Downloaded file could not be found at path: {filepath}")
            raise HTTPException(status_code=404, detail="Downloaded media file not found on server.")

        # Determine the correct media type and create a safe filename
        title = info.get("title", "downloaded_media")
        if audio_only:
            media_type = "audio/mpeg"
            extension = ".mp3"
        else:
            media_type = "video/mp4"
            extension = ".mp4"
            
        safe_filename = "".join(c for c in title if c.isalnum() or c in (' ', '_')).rstrip()[:50] + extension

        # Return the actual file as a streaming response
        return FileResponse(
            path=filepath,
            media_type=media_type,
            filename=safe_filename
        )

    except Exception as e:
        logger.error(f"Universal download failed for URL {url} with format_id {format_id}", exc_info=True)
        raise HTTPException(
            status_code=500, detail=f"Failed to process and stream the requested media: {str(e)}"
        )




@app.get("/cache/{filename}")
async def serve_cache(filename: str):
    filepath = os.path.join(CACHE_DIR, filename)
    if not os.path.exists(filepath):
        raise HTTPException(status_code=404, detail="File not found")
    return FileResponse(filepath, media_type="application/octet-stream", filename=filename)



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


# ---------------------- CLI ----------------------
if __name__ == "__main__":
    parser = argparse.ArgumentParser(prog="python main.py")
    parser.add_argument("--u", "--update", dest="update",
                        choices=["n", "s", "m", "nightly", "stable", "master"],
                        help="Update yt-dlp build")
    parser.add_argument("--v", "--version", action="store_true", dest="version",
                        help="Show yt-dlp version")
    parser.add_argument("--d", "--debug", action="store_true", dest="debug",
                        help="Enable verbose debug logging")
    parsed = parser.parse_args()

    DEBUG = bool(parsed.debug)

    if parsed.version:
        from yt_dlp import version as ytdlp_version
        print(f"\n>>> yt-dlp version: {ytdlp_version.__version__}\n")
        sys.exit(0)

    if parsed.update:
        channel_map = {"n": "nightly", "s": "stable", "m": "master"}
        update_yt_dlp(channel_map.get(parsed.update, parsed.update))

    import uvicorn
    uvicorn.run(app, host="0.0.0.0", port=PORT)
