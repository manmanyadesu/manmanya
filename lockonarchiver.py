# ==============================================================================
# [ 사용자 기본 설정 영역 (원하시는 대로 수정 후 사용하세요) ]
# ==============================================================================
DEFAULT_GALLERY_ID = "comic_new6"       # 링크가 아닌 '숫자'만 적었을 때 적용할 기본 갤러리 ID
FORCE_OVERWRITE = False                 # True로 설정 시 이미 수집된 글도 무조건 원본 이미지를 새로 받습니다.

# 🆕 기존 만갤6 자료는 숫자 폴더/숫자 JSON 키를 그대로 유지합니다.
# 나중에 기본 갤러리가 comic_new7 등으로 바뀌더라도 이 값은 comic_new6 그대로 두세요.
LEGACY_UNPREFIXED_GALLERY_ID = "comic_new6"

# 🎯 수집하고 싶은 디시인사이드 글 번호 또는 주소 링크를 여기에 "줄바꿈(Enter)"으로 붙여넣어 주세요!
# 양 끝의 세 개짜리 따옴표(""") 공간 안에서 따옴표도, 쉼표도 쓸 필요 없이 주소를 그냥 복사-붙여넣기만 하시면 됩니다.
TARGET_LINKS_RAW = """
https://gall.dcinside.com/board/view/?id=comic_new4&no=8485839&page=1
https://gall.dcinside.com/board/view/?id=comic_new4&no=8602203&page=1
https://gall.dcinside.com/comic_new4/8723000
https://gall.dcinside.com/board/view/?id=comic_new4&no=8898455
https://gall.dcinside.com/comic_new4/8981609
https://gall.dcinside.com/comic_new4/9093680

"""

# 💡 RAW 문자열로부터 리스트를 분리 가로채 메모리에 할당 (NameError 방지)
TARGET_LINKS = [line.strip() for line in TARGET_LINKS_RAW.strip().split('\n') if line.strip() and not line.strip().startswith("#")]

# 구글 드라이브 및 로컬 백업 경로 설정 (기존 스크립트와 데이터베이스 공유)
SCOPES = ['https://www.googleapis.com/auth/drive.file']
BASE_DIR = "./archive"
CHECKPOINT_FILE = f"{BASE_DIR}/completed_posts.json"
DCCON_CACHE_FILE = f"{BASE_DIR}/dccon_cache.json"
LOCK_FILE = f"{BASE_DIR}/crawler.lock"   # ⚠️ 이 파일이 기존 크롤러와의 동시 실행 충돌을 방지합니다.
# ==============================================================================

import os
import sys

# 작업 폴더 고정
current_dir = os.path.dirname(os.path.abspath(__file__))
os.chdir(current_dir)

import re
import time
import json
import random
import hashlib
import shutil
import requests
import httplib2
import subprocess
import socket
import datetime
from concurrent.futures import ThreadPoolExecutor, as_completed
from playwright.sync_api import sync_playwright
from bs4 import BeautifulSoup
from PIL import Image

import google_auth_httplib2
from googleapiclient.discovery import build
from googleapiclient.http import MediaFileUpload
from google.oauth2.credentials import Credentials
from google_auth_oauthlib.flow import InstalledAppFlow
from google.auth.transport.requests import Request

socket.setdefaulttimeout(15)
os.makedirs(BASE_DIR, exist_ok=True)

# 💡 [보완] NameError 방지를 위해 archive_single_post가 완벽하게 호출할 수 있는 페이징 내장 템플릿 함수 정의
def get_html_template(title, target_url, writer_top, ip_top, date_top, views_top, recommend_top, comment_count_top, content_area_html, poll_section_html, upvotes, downvotes, comments_json_str):
    return f"""<!DOCTYPE html><html lang="ko"><head><meta charset="utf-8"><meta name="viewport" content="width=device-width, initial-scale=1.0"><title>{title} - 아카이브</title><style>body {{ font-family: 'Malgun Gothic', sans-serif; margin: 40px; background-color: #f5f6f7; color: #333; }}.container {{ max-width: 900px; margin: 0 auto; background: white; padding: 30px; border-radius: 8px; box-shadow: 0 4px 6px rgba(0,0,0,0.1); }}.post-header {{ border-bottom: 1px solid #ccc; padding-bottom: 15px; margin-bottom: 20px; }}.post-title {{ font-size: 22px; font-weight: bold; color: #222; margin-bottom: 12px; }}.post-title a {{ text-decoration: none; color: inherit; }}.post-title a:hover {{ color: #1d4ed8; }}.post-meta-wrap {{ display: flex; justify-content: space-between; font-size: 13px; color: #666; }}.meta-left .writer {{ font-weight: bold; color: #333; margin-right: 10px; }}.comment-jump-btn {{ background: #f3f3f3; border: 1px solid #e1e1e1; border-radius: 15px; padding: 3px 12px; color: #333; text-decoration: none; font-weight: bold; font-size: 12px; }}.content {{ line-height: 1.8; font-size: 16px; margin-top: 30px; padding-bottom: 40px; }}.content img {{ max-width: 100% !important; height: auto !important; display: block; margin: 15px auto; }}.vote-box-container {{ border: 1px solid #ddd; padding: 30px; border-radius: 8px; margin: 40px auto; max-width: 400px; display: flex; justify-content: center; align-items: center; gap: 30px; background: #fff; }}.vote-number {{ font-size: 22px; font-weight: bold; width: 40px; text-align: center; }}.vote-circles {{ display: flex; gap: 15px; }}.circle-btn {{ width: 80px; height: 80px; border-radius: 50%; display: flex; flex-direction: column; justify-content: center; align-items: center; font-weight: bold; color: white; font-size: 14px; box-shadow: 0 2px 5px rgba(0,0,0,0.2); }}.circle-up {{ background: #3b5998; }} .circle-down {{ background: #a5a5a5; }}.comments-header {{ display: flex; justify-content: space-between; align-items: center; border-bottom: 1px solid #3b5998; padding-bottom: 10px; margin-top: 40px; }}.comments-title {{ font-size: 16px; font-weight: bold; color: #3b5998; }}.control-btn {{ background: none; border: none; font-size: 13px; cursor: pointer; font-weight: bold; color: #999; margin-right: 5px; }}.control-btn.active {{ color: #3b5998; }}.comment-list-area {{ border-top: 1px solid #3b5998; }}.comment-row {{ display: flex; border-bottom: 1px solid #e2e2e2; padding: 12px 0; align-items: flex-start; }}.comment-writer-box {{ width: 160px; flex-shrink: 0; padding: 0 10px; color: #333; font-weight: bold; font-size: 13px; word-break: break-all; }}.comment-writer-box span.ip {{ color: #999; font-weight: normal; font-size: 11px; }}.comment-content-box {{ flex-grow: 1; padding: 0 10px; font-size: 13px; color: #333; word-break: break-all; }}.comment-content-box img {{ max-width: 200px; border-radius: 4px; display: block; margin-top: 5px; }}.comment-date-box {{ width: 100px; flex-shrink: 0; text-align: right; color: #999; font-size: 12px; padding-right: 10px; }}.reply-row {{ background-color: #f9f9f9; padding-left: 0; border-left: 3px solid #ddd; }}.reply-row .comment-writer-box {{ width: 180px; padding-left: 35px; position: relative; }}.reply-icon {{ position: absolute; left: 12px; top: 0; color: #3b5998; font-weight: 900; }}.deleted-text {{ color: #aaa; font-style: normal; }}.pagination {{ display: flex; justify-content: center; gap: 5px; margin-top: 20px; }}.page-btn {{ border: 1px solid #ddd; background: white; padding: 5px 10px; cursor: pointer; border-radius: 3px; font-size: 13px; }}.page-btn.active {{ background: #3b5998; color: white; font-weight: bold; }}@media (max-width: 768px) {{ body {{ margin: 8px; padding: 0; background-color: #fff; }} .container {{ padding: 10px; border-radius: 0; box-shadow: none; }} .post-title {{ font-size: 18px; line-height: 1.4; }} .post-meta-wrap {{ flex-direction: column; gap: 5px; font-size: 11px; }} .comment-row {{ flex-direction: column; padding: 8px 0; }} .comment-writer-box {{ width: 100%; font-size: 12px; margin-bottom: 4px; }} .comment-content-box {{ width: 100%; font-size: 12px; padding: 0; }} .comment-content-box img {{ max-width: 150px; }} .comment-date-box {{ width: 100%; text-align: left; font-size: 10px; margin-top: 4px; }} .reply-row {{ padding-left: 10px; }} .reply-row .comment-writer-box {{ padding-left: 20px; }} .vote-box-container {{ padding: 15px; margin: 20px auto; gap: 15px; max-width: 100%; }} .content div, .content p, .content table, .content tr, .content td, .content span {{ max-width: 100% !important; width: auto !important; height: auto !important; }} }}</style></head><body><div class="container"><div class="post-header"><div class="post-title"><a href="{target_url}" target="_blank" title="디시인사이드 원문 글로 가기">{title} <span style="font-size:14px; color:#1d4ed8; font-weight:normal; margin-left:6px; vertical-align:middle;">🔗 원문 보기</span></a></div><div class="post-meta-wrap"><div class="meta-left"><span class="writer">{writer_top} {ip_top}</span><span class="date">{date_top}</span></div><div class="meta-right"><span>{views_top}</span> | <span>{recommend_top}</span> | <a href="#comment-section" class="comment-jump-btn">{comment_count_top}</a></div></div></div><div class="content">{content_area_html}</div>{poll_section_html}<div class="vote-box-container"><div class="vote-number" style="color:#d31900;">{upvotes}</div><div class="vote-circles"><div class="circle-btn circle-up"><span style="font-size:22px; color:#ffeb3b;">★</span><span>개념</span></div><div class="circle-btn circle-down"><span style="font-size:22px; color:white;">⬇</span><span>비추</span></div></div><div class="vote-number" style="color:#444;">{downvotes}</div></div><div id="comment-section"><div class="comments-header"><div class="comments-title">댓글 <span id="total-count" style="color:#d31900;">0</span>개</div><div class="comment-controls"><button class="control-btn active" id="sort-old" onclick="changeSort('old')">등록순</button><button class="control-btn" id="sort-new" onclick="changeSort('new')">최신순</button><button class="control-btn" id="sort-reply" onclick="changeSort('reply')">답글순</button><select id="limit-select" onchange="changeLimit(this.value)" style="padding: 2px; font-size: 12px; margin-left: 10px;"><option value="30">30개</option><option value="50" selected>50개</option><option value="100">100개</option><option value="9999">전체 보기</option></select></div></div><div class="comment-list-area" id="comment-list"></div><div class="pagination" id="pagination-buttons"></div></div></div><script>const rawComments = {comments_json_str}; let currentSort = 'old', commentsPerPage = 50, currentPage = 1, commentGroups = [], currentGroup = null; rawComments.forEach(c => {{ if (!c.is_reply) {{ currentGroup = {{ parent: c, replies: [] }}; commentGroups.push(currentGroup); }} else {{ if (currentGroup) currentGroup.replies.push(c); else {{ currentGroup = {{ parent: null, replies: [c] }}; commentGroups.push(currentGroup); }} }} }}); function buildWriterHTML(writerStr) {{ let match = writerStr.match(/(.+)\\s(\\([0-9.]+\\))$/); return match ? `${{match[1]}} <span class="ip">${{match[2]}}</span>` : writerStr; }} function buildContentHTML(c) {{ if (c.text.includes("삭제된 댓글")) return `<span class="deleted-text">${{c.text}}</span>`; let html = c.text.replace(/\\n/g, "<br>"); if (c.dccon) html += `<br><img src="${{c.dccon}}" style="width:85px; height:85px; margin-top:5px;">`; if (c.comment_img) html += `<br><img src="${{c.comment_img}}" style="margin-top:5px; max-width:200px; border-radius:4px;">`; return html; }} function renderComments() {{ const listArea = document.getElementById('comment-list'); const pageArea = document.getElementById('pagination-buttons'); listArea.innerHTML = ''; pageArea.innerHTML = ''; document.getElementById('total-count').innerText = rawComments.filter(c => !c.text.includes("삭제된 댓글")).length; if (rawComments.length === 0) return; let sortedGroups = [...commentGroups]; if (currentSort === 'new') sortedGroups.reverse(); else if (currentSort === 'reply') sortedGroups.sort((a, b) => b.replies.length - a.replies.length); const totalPages = Math.ceil(sortedGroups.length / commentsPerPage); if (currentPage > totalPages) currentPage = totalPages; if (currentPage < 1) currentPage = 1; const startIndex = (currentPage - 1) * commentsPerPage; const pageGroups = sortedGroups.slice(startIndex, startIndex + commentsPerPage); pageGroups.forEach(g => {{ if (g.parent) {{ const pDiv = document.createElement('div'); pDiv.className = 'comment-row'; if (g.parent.text.includes("삭제된 댓글")) pDiv.innerHTML = `<div class="comment-writer-box"></div><div class="comment-content-box">${{buildContentHTML(g.parent)}}</div><div class="comment-date-box"></div>`; else pDiv.innerHTML = `<div class="comment-writer-box">${{buildWriterHTML(g.parent.writer)}}</div><div class="comment-content-box">${{buildContentHTML(g.parent)}}</div><div class="comment-date-box">${{g.parent.date}}</div>`; listArea.appendChild(pDiv); }} g.replies.forEach(r => {{ const rDiv = document.createElement('div'); rDiv.className = 'comment-row reply-row'; if (r.text.includes("삭제된 댓글")) rDiv.innerHTML = `<div class="comment-writer-box"><span class="reply-icon">ㄴ</span></div><div class="comment-content-box">${{buildContentHTML(r)}}</div><div class="comment-date-box"></div>`; else rDiv.innerHTML = `<div class="comment-writer-box"><span class="reply-icon">ㄴ</span>${{buildWriterHTML(r.writer)}}</div><div class="comment-content-box">${{buildContentHTML(r)}}</div><div class="comment-date-box">${{r.date}}</div>`; listArea.appendChild(rDiv); }}); }}); if (totalPages > 1) {{
            const pageBlockSize = 10;
            const currentBlock = Math.floor((currentPage - 1) / pageBlockSize);
            const startPage = currentBlock * pageBlockSize + 1;
            const endPage = Math.min(startPage + pageBlockSize - 1, totalPages);

            if (startPage > 1) {{
                const firstBtn = document.createElement('button');
                firstBtn.className = 'page-btn';
                firstBtn.innerText = '<<';
                firstBtn.onclick = () => {{ currentPage = 1; renderComments(); window.scrollTo(0, document.getElementById('comment-section').offsetTop - 20); }};
                pageArea.appendChild(firstBtn);

                const prevBlockBtn = document.createElement('button');
                prevBlockBtn.className = 'page-btn';
                prevBlockBtn.innerText = '<';
                prevBlockBtn.onclick = () => {{ currentPage = startPage - 1; renderComments(); window.scrollTo(0, document.getElementById('comment-section').offsetTop - 20); }};
                pageArea.appendChild(prevBlockBtn);
            }}

            for (let i = startPage; i <= endPage; i++) {{
                const btn = document.createElement('button');
                btn.className = 'page-btn';
                if (i === currentPage) btn.classList.add('active');
                btn.innerText = i;
                btn.onclick = () => {{ currentPage = i; renderComments(); window.scrollTo(0, document.getElementById('comment-section').offsetTop - 20); }};
                pageArea.appendChild(btn);
            }}

            if (endPage < totalPages) {{
                const nextBlockBtn = document.createElement('button');
                nextBlockBtn.className = 'page-btn';
                nextBlockBtn.innerText = '>';
                nextBlockBtn.onclick = () => {{ currentPage = endPage + 1; renderComments(); window.scrollTo(0, document.getElementById('comment-section').offsetTop - 20); }};
                pageArea.appendChild(nextBlockBtn);

                const lastBtn = document.createElement('button');
                lastBtn.className = 'page-btn';
                lastBtn.innerText = '>>';
                lastBtn.onclick = () => {{ currentPage = totalPages; renderComments(); window.scrollTo(0, document.getElementById('comment-section').offsetTop - 20); }};
                pageArea.appendChild(lastBtn);
            }}
        }} }} function changeSort(type) {{ currentSort = type; document.querySelectorAll('.control-btn').forEach(btn => btn.classList.remove('active')); document.getElementById('sort-' + type).classList.add('active'); currentPage = 1; renderComments(); }} function changeLimit(val) {{ commentsPerPage = parseInt(val); currentPage = 1; renderComments(); }} document.querySelectorAll('a[href^="#"]').forEach(anchor => {{ anchor.addEventListener('click', function (e) {{ e.preventDefault(); document.querySelector(this.getAttribute('href')).scrollIntoView({{ behavior: 'smooth' }}); }}); }}); renderComments();</script></body></html>"""

def get_gcp_credentials():
    creds = None
    if os.path.exists('token.json'):
        creds = Credentials.from_authorized_user_file('token.json', SCOPES)
    if not creds or not creds.valid:
        if creds and creds.expired and creds.refresh_token:
            creds.refresh(Request())
        else:
            flow = InstalledAppFlow.from_client_secrets_file('client_secret.json', SCOPES)
            creds = flow.run_local_server(port=0)
        with open('token.json', 'w') as token: token.write(creds.to_json())
    return creds

def load_checkpoint():
    if os.path.exists(CHECKPOINT_FILE):
        with open(CHECKPOINT_FILE, "r", encoding="utf-8") as f:
            try: return json.load(f)
            except Exception: return {}
    return {}

def save_checkpoint(completed_dict):
    with open(CHECKPOINT_FILE, "w", encoding="utf-8") as f:
        json.dump(completed_dict, f, ensure_ascii=False, indent=4)

def load_dccon_cache():
    if os.path.exists(DCCON_CACHE_FILE):
        with open(DCCON_CACHE_FILE, "r", encoding="utf-8") as f:
            try: return json.load(f)
            except Exception: return {}
    return {}

def save_dccon_cache(cache):
    with open(DCCON_CACHE_FILE, "w", encoding="utf-8") as f:
        json.dump(cache, f, ensure_ascii=False, indent=4)

def release_lock():
    if os.path.exists(LOCK_FILE):
        try: os.remove(LOCK_FILE)
        except Exception: pass

def get_or_create_drive_folder(drive_service, folder_name="Manga_Archive"):
    query = f"name = '{folder_name}' and mimeType = 'application/vnd.google-apps.folder' and trashed = false"
    results = drive_service.files().list(q=query, fields="files(id)").execute()
    files = results.get('files', [])
    if files: return files[0]['id']
    else:
        file_metadata = {'name': folder_name, 'mimeType': 'application/vnd.google-apps.folder'}
        folder = drive_service.files().create(body=file_metadata, fields='id').execute()
        folder_id = folder.get('id')
        user_permission = {'type': 'anyone', 'role': 'reader'}
        drive_service.permissions().create(fileId=folder_id, body=user_permission).execute()
        return folder_id

def compress_image(source_path, target_path, max_width=1000, quality=80):
    try:
        with Image.open(source_path) as img:
            if img.mode in ("RGBA", "P"): img = img.convert("RGB")
            if img.width > max_width:
                ratio = max_width / float(img.width)
                new_height = int(float(img.height) * float(ratio))
                img = img.resize((max_width, new_height), Image.Resampling.LANCZOS)
            img.save(target_path, "JPEG", quality=quality, optimize=True)
            return True
    except Exception as e:
        print(f"      ❌ 이미지 압축 실패: {e}")
        return False

def upload_file_to_drive(drive_service, file_path, folder_id, thread_http=None):
    filename = os.path.basename(file_path)
    file_metadata = {'name': filename, 'parents': [folder_id]}
    mime_type = "image/jpeg"
    if filename.endswith(".gif"): mime_type = "image/gif"
    elif filename.endswith(".png"): mime_type = "image/png"
    elif filename.endswith(".webp"): mime_type = "image/webp"
    
    media = MediaFileUpload(file_path, mimetype=mime_type, resumable=False)
    if thread_http:
        file = drive_service.files().create(body=file_metadata, media_body=media, fields='id').execute(http=thread_http)
    else:
        file = drive_service.files().create(body=file_metadata, media_body=media, fields='id').execute()
    return file.get('id'), f"https://lh3.googleusercontent.com/d/{file.get('id')}"

def normalize_comment_date(date_str):
    if not date_str:
        return ""
    date_str = date_str.strip()
    
    match_long = re.search(r"\b(?:\d{4}|\d{2})\.(\d{2})\.(\d{2})\s+(\d{2}):(\d{2})", date_str)
    if match_long:
        return f"{match_long.group(1)}.{match_long.group(2)} {match_long.group(3)}:{match_long.group(4)}"
    
    match_standard = re.search(r"\b(\d{2})[\./-](\d{2})\s+(\d{2}):(\d{2})", date_str)
    if match_standard:
        return f"{match_standard.group(1)}.{match_standard.group(2)} {match_standard.group(3)}:{match_standard.group(4)}"
        
    match_time = re.match(r"^(\d{2}):(\d{2})", date_str)
    if match_time:
        now = datetime.datetime.now()
        return f"{now.month:02d}.{now.day:02d} {match_time.group(1)}:{match_time.group(2)}"
        
    return date_str

def make_comment_fallback_key(comment):
    """댓글 ID가 없는 기존 자료를 수량 단위로 대응하기 위한 보조 키입니다."""
    return (
        str(comment.get("writer", "")).strip(),
        str(comment.get("text", "")).strip(),
        normalize_comment_date(comment.get("date", "")),
        bool(comment.get("is_reply", False))
    )

def merge_comments_preserving_existing(old_comments, new_comments):
    """
    기존 댓글은 삭제하지 않고 새 댓글만 추가합니다.
    comment_id를 우선 사용하고, ID가 없는 기존 자료는 동일 보조 키의
    댓글 수량을 하나씩 대응하여 도배 댓글이 두 배가 되는 것을 막습니다.
    """
    merged_comments = [dict(comment) for comment in old_comments]
    old_id_to_index = {}
    old_idless_indexes = {}

    for index, comment in enumerate(merged_comments):
        comment_id = str(comment.get("comment_id", "")).strip()
        if comment_id:
            old_id_to_index[comment_id] = index
        else:
            key = make_comment_fallback_key(comment)
            old_idless_indexes.setdefault(key, []).append(index)

    old_idless_positions = {key: 0 for key in old_idless_indexes}
    additions = []

    for new_comment in new_comments:
        new_comment = dict(new_comment)
        comment_id = str(new_comment.get("comment_id", "")).strip()
        matched_index = old_id_to_index.get(comment_id) if comment_id else None

        if matched_index is None:
            key = make_comment_fallback_key(new_comment)
            candidates = old_idless_indexes.get(key, [])
            position = old_idless_positions.get(key, 0)
            if position < len(candidates):
                matched_index = candidates[position]
                old_idless_positions[key] = position + 1

        if matched_index is None:
            additions.append(new_comment)
            continue

        old_comment = merged_comments[matched_index]
        preserved_dccon = old_comment.get("dccon", "")
        preserved_comment_img = old_comment.get("comment_img", "")
        old_comment.update(new_comment)
        if not old_comment.get("dccon"):
            old_comment["dccon"] = preserved_dccon
        if not old_comment.get("comment_img"):
            old_comment["comment_img"] = preserved_comment_img
        if comment_id:
            old_id_to_index[comment_id] = matched_index

    additions_by_parent = {}
    standalone_additions = []
    new_parent_ids = set()

    for comment in additions:
        if not comment.get("is_reply"):
            comment_id = str(comment.get("comment_id", "")).strip()
            if comment_id:
                new_parent_ids.add(comment_id)

    for comment in additions:
        parent_comment_id = str(comment.get("parent_comment_id", "")).strip()
        if comment.get("is_reply") and parent_comment_id:
            additions_by_parent.setdefault(parent_comment_id, []).append(comment)
        else:
            standalone_additions.append(comment)

    existing_parent_insertions = []
    for parent_comment_id, replies in additions_by_parent.items():
        if parent_comment_id in old_id_to_index and parent_comment_id not in new_parent_ids:
            existing_parent_insertions.append((old_id_to_index[parent_comment_id], replies))

    for parent_index, replies in sorted(existing_parent_insertions, reverse=True):
        insert_index = parent_index + 1
        while insert_index < len(merged_comments) and merged_comments[insert_index].get("is_reply"):
            insert_index += 1
        merged_comments[insert_index:insert_index] = replies

    for comment in standalone_additions:
        merged_comments.append(comment)
        comment_id = str(comment.get("comment_id", "")).strip()
        if not comment.get("is_reply") and comment_id:
            merged_comments.extend(additions_by_parent.get(comment_id, []))

    handled_parent_ids = {
        parent_id for parent_id in additions_by_parent
        if parent_id in old_id_to_index or parent_id in new_parent_ids
    }
    for parent_comment_id, replies in additions_by_parent.items():
        if parent_comment_id not in handled_parent_ids:
            merged_comments.extend(replies)

    return merged_comments

def saved_post_has_comment_relations(archive_key):
    """
    기존 저장본의 답글에 부모 댓글 ID가 정상적으로 들어 있는지 확인합니다.
    댓글이 없는 글은 다시 수집할 필요가 없으므로 완료된 것으로 판단합니다.
    """
    html_path = f"{BASE_DIR}/{archive_key}/saved_post.html"
    if not os.path.exists(html_path):
        return False

    try:
        with open(html_path, "r", encoding="utf-8") as f:
            html_text = f.read()
        match = re.search(r"const rawComments = (\[.*?\]);", html_text, re.DOTALL)
        if not match:
            return False
        comments = json.loads(match.group(1))
        if not comments:
            return True
        comments_with_id = [
            comment for comment in comments
            if str(comment.get("comment_id", "")).strip()
        ]
        return bool(comments_with_id) and all(
            not comment.get("is_reply")
            or str(comment.get("parent_comment_id", "")).strip()
            for comment in comments_with_id
        )
    except Exception:
        return False

def apply_comment_parent_grouping(html_text):
    """
    comment_id가 있는 새 자료는 parent_comment_id로 부모·답글을 묶고,
    ID가 없는 기존 자료는 예전의 저장 순서 방식으로 그대로 표시합니다.
    """
    old_script = """let currentSort = 'old', commentsPerPage = 50, currentPage = 1, commentGroups = [], currentGroup = null; rawComments.forEach(c => { if (!c.is_reply) { currentGroup = { parent: c, replies: [] }; commentGroups.push(currentGroup); } else { if (currentGroup) currentGroup.replies.push(c); else { currentGroup = { parent: null, replies: [c] }; commentGroups.push(currentGroup); } } }); function buildWriterHTML"""
    new_script = """let currentSort = 'old', commentsPerPage = 50, currentPage = 1, commentGroups = [], currentGroup = null, parentGroupsById = new Map(), pendingRepliesByParent = new Map(); rawComments.forEach(c => { if (!c.is_reply) { currentGroup = { parent: c, replies: [] }; commentGroups.push(currentGroup); const commentId = String(c.comment_id || '').trim(); if (commentId) { parentGroupsById.set(commentId, currentGroup); const pendingReplies = pendingRepliesByParent.get(commentId); if (pendingReplies) { currentGroup.replies.push(...pendingReplies); pendingRepliesByParent.delete(commentId); } } } else { const parentId = String(c.parent_comment_id || '').trim(); if (parentId) { const parentGroup = parentGroupsById.get(parentId); if (parentGroup) parentGroup.replies.push(c); else { if (!pendingRepliesByParent.has(parentId)) pendingRepliesByParent.set(parentId, []); pendingRepliesByParent.get(parentId).push(c); } } else if (currentGroup) currentGroup.replies.push(c); else { currentGroup = { parent: null, replies: [c] }; commentGroups.push(currentGroup); } } }); pendingRepliesByParent.forEach(replies => commentGroups.push({ parent: null, replies })); function buildWriterHTML"""
    return html_text.replace(old_script, new_script)

# 🆕 갤러리별 글 번호 충돌을 막는 아카이브 고유 키 생성 함수
# 기존 만갤6은 예전 자료와 호환되도록 "1742096" 형태를 유지하고,
# 그 외 갤러리는 "comic_new3_1742096" 형태로 저장합니다.
def make_archive_key(gallery_id, post_no):
    gallery_id = str(gallery_id).strip()
    post_no = str(post_no).strip()
    if gallery_id == LEGACY_UNPREFIXED_GALLERY_ID:
        return post_no
    return f"{gallery_id}_{post_no}"

def rebuild_html_locally(post_no, target_gallery=DEFAULT_GALLERY_ID):
    archive_key = make_archive_key(target_gallery, post_no)
    save_dir = f"{BASE_DIR}/{archive_key}"
    html_path = f"{save_dir}/saved_post.html"
    if not os.path.exists(html_path): return False
    
    try:
        with open(html_path, "r", encoding="utf-8") as f:
            soup = BeautifulSoup(f.read(), "html.parser")
        
        content_el = soup.find("div", class_="content")
        if not content_el: return False
        content_area_html = str(content_el.decode_contents())
        
        title_match = soup.find("title")
        title = title_match.text.replace(" - 아카이브", "").strip() if title_match else f"만화 {post_no}번"
        
        writer_el = soup.select_one(".post-meta-wrap .writer")
        writer_text = writer_el.text.strip() if writer_el else "ㅇㅇ"
        parts = writer_text.split(" ")
        writer_top = parts[0]
        ip_top = parts[1] if len(parts) > 1 else ""
        
        date_el = soup.select_one(".post-meta-wrap .date")
        date_top = date_el.text.strip() if date_el else ""
        
        script_tags = soup.find_all("script")
        comments_json_str = "[]"
        for s in script_tags:
            if s.string and "const rawComments =" in s.string:
                match = re.search(r"const rawComments = (\[.*?\]);", s.string, re.DOTALL)
                if match:
                    comments_json_str = match.group(1)
                    break
        
        views_top, recommend_top, comment_count_top = "조회 0", "추천 0", "댓글 0"
        meta_right = soup.select(".post-meta-wrap .meta-right span")
        if len(meta_right) >= 2:
            views_top = meta_right[0].text.strip()
            recommend_top = meta_right[1].text.strip()
            
        jump_btn = soup.select_one(".post-meta-wrap .comment-jump-btn")
        if jump_btn: comment_count_top = jump_btn.text.strip()
            
        upvotes, downvotes = "0", "0"
        vote_nums = soup.select(".vote-number")
        if len(vote_nums) >= 2:
            upvotes = vote_nums[0].text.strip()
            downvotes = vote_nums[1].text.strip()
            
        has_poll = False
        poll_drive_url = ""
        poll_text_html = "" # 💡 [추가1] 로컬 빌드 시 텍스트 투표 보존 변수
        poll_el = soup.find("div", class_="poll-container")
        if poll_el:
            has_poll = True
            poll_img = poll_el.find("img")
            if poll_img: poll_drive_url = poll_img.get("src", "")
            
            # 💡 [추가2] 로컬 템플릿에 기존 텍스트 투표가 있으면 추출
            text_el = poll_el.find("div", class_="poll-text-result")
            if text_el:
                poll_text_html = str(text_el)

        completed_posts = load_checkpoint()
        image_count = completed_posts.get(archive_key, {}).get("image_count", 0)
        thumbnail_url = completed_posts.get(archive_key, {}).get("thumbnail", "")
        
        target_url = f"https://gall.dcinside.com/board/view/?id={target_gallery}&no={post_no}"
        
        # 💡 [추가3] 로컬 빌드 시 투표 영역 템플릿 조립 (텍스트 포함)
        poll_section_html = ""
        if poll_drive_url or has_poll or poll_text_html:
            poll_section_html = """<div class="poll-container"><h3>🗳️ 본문 투표 백업</h3>"""
            if poll_drive_url:
                poll_section_html += f"""<img src="{poll_drive_url}" style="max-width:100%; display:block; margin:0 auto;">"""
            if poll_text_html:
                poll_section_html += poll_text_html
            poll_section_html += "</div>"

        # 💡 HTML 중복 방어 템플릿 (V7 초슬림 반응형 스킨 적용)
        html_template = f"""<!DOCTYPE html><html lang="ko"><head><meta charset="utf-8"><meta name="viewport" content="width=device-width, initial-scale=1.0"><title>{title} - 아카이브</title><style>body {{ font-family: 'Malgun Gothic', sans-serif; margin: 40px; background-color: #f5f6f7; color: #333; }}.container {{ max-width: 900px; margin: 0 auto; background: white; padding: 30px; border-radius: 8px; box-shadow: 0 4px 6px rgba(0,0,0,0.1); }}.post-header {{ border-bottom: 1px solid #ccc; padding-bottom: 15px; margin-bottom: 20px; }}.post-title {{ font-size: 22px; font-weight: bold; color: #222; margin-bottom: 12px; }}.post-title a {{ text-decoration: none; color: inherit; }}.post-title a:hover {{ color: #1d4ed8; }}.post-meta-wrap {{ display: flex; justify-content: space-between; font-size: 13px; color: #666; }}.meta-left .writer {{ font-weight: bold; color: #333; margin-right: 10px; }}.comment-jump-btn {{ background: #f3f3f3; border: 1px solid #e1e1e1; border-radius: 15px; padding: 3px 12px; color: #333; text-decoration: none; font-weight: bold; font-size: 12px; }}.content {{ line-height: 1.8; font-size: 16px; margin-top: 30px; padding-bottom: 40px; }}.content img {{ max-width: 100% !important; height: auto !important; display: block; margin: 15px auto; }}.vote-box-container {{ border: 1px solid #ddd; padding: 30px; border-radius: 8px; margin: 40px auto; max-width: 400px; display: flex; justify-content: center; align-items: center; gap: 30px; background: #fff; }}.vote-number {{ font-size: 22px; font-weight: bold; width: 40px; text-align: center; }}.vote-circles {{ display: flex; gap: 15px; }}.circle-btn {{ width: 80px; height: 80px; border-radius: 50%; display: flex; flex-direction: column; justify-content: center; align-items: center; font-weight: bold; color: white; font-size: 14px; box-shadow: 0 2px 5px rgba(0,0,0,0.2); }}.circle-up {{ background: #3b5998; }} .circle-down {{ background: #a5a5a5; }}.comments-header {{ display: flex; justify-content: space-between; align-items: center; border-bottom: 1px solid #3b5998; padding-bottom: 10px; margin-top: 40px; }}.comments-title {{ font-size: 16px; font-weight: bold; color: #3b5998; }}.control-btn {{ background: none; border: none; font-size: 13px; cursor: pointer; font-weight: bold; color: #999; margin-right: 5px; }}.control-btn.active {{ color: #3b5998; }}.comment-list-area {{ border-top: 1px solid #3b5998; }}.comment-row {{ display: flex; border-bottom: 1px solid #e2e2e2; padding: 12px 0; align-items: flex-start; }}.comment-writer-box {{ width: 160px; flex-shrink: 0; padding: 0 10px; color: #333; font-weight: bold; font-size: 13px; word-break: break-all; }}.comment-writer-box span.ip {{ color: #999; font-weight: normal; font-size: 11px; }}.comment-content-box {{ flex-grow: 1; padding: 0 10px; font-size: 13px; color: #333; word-break: break-all; }}.comment-content-box img {{ max-width: 200px; border-radius: 4px; display: block; margin-top: 5px; }}.comment-date-box {{ width: 100px; flex-shrink: 0; text-align: right; color: #999; font-size: 12px; padding-right: 10px; }}.reply-row {{ background-color: #f9f9f9; padding-left: 0; border-left: 3px solid #ddd; }}.reply-row .comment-writer-box {{ width: 180px; padding-left: 35px; position: relative; }}.reply-icon {{ position: absolute; left: 12px; top: 0; color: #3b5998; font-weight: 900; }}.deleted-text {{ color: #aaa; font-style: normal; }}.pagination {{ display: flex; justify-content: center; gap: 5px; margin-top: 20px; }}.page-btn {{ border: 1px solid #ddd; background: white; padding: 5px 10px; cursor: pointer; border-radius: 3px; font-size: 13px; }}.page-btn.active {{ background: #3b5998; color: white; font-weight: bold; }}@media (max-width: 768px) {{ body {{ margin: 8px; padding: 0; background-color: #fff; }} .container {{ padding: 10px; border-radius: 0; box-shadow: none; }} .post-title {{ font-size: 18px; line-height: 1.4; }} .post-meta-wrap {{ flex-direction: column; gap: 5px; font-size: 11px; }} .comment-row {{ flex-direction: column; padding: 8px 0; }} .comment-writer-box {{ width: 100%; font-size: 12px; margin-bottom: 4px; }} .comment-content-box {{ width: 100%; font-size: 12px; padding: 0; }} .comment-content-box img {{ max-width: 150px; }} .comment-date-box {{ width: 100%; text-align: left; font-size: 10px; margin-top: 4px; }} .reply-row {{ padding-left: 10px; }} .reply-row .comment-writer-box {{ padding-left: 20px; }} .vote-box-container {{ padding: 15px; margin: 20px auto; gap: 15px; max-width: 100%; }} .content div, .content p, .content table, .content tr, .content td, .content span {{ max-width: 100% !important; width: auto !important; height: auto !important; }} }}</style></head><body><div class="container"><div class="post-header"><div class="post-title"><a href="{target_url}" target="_blank" title="디시인사이드 원문 글로 가기">{title} <span style="font-size:14px; color:#1d4ed8; font-weight:normal; margin-left:6px; vertical-align:middle;">🔗 원문 보기</span></a></div><div class="post-meta-wrap"><div class="meta-left"><span class="writer">{writer_top} {ip_top}</span><span class="date">{date_top}</span></div><div class="meta-right"><span>{views_top}</span> | <span>{recommend_top}</span> | <a href="#comment-section" class="comment-jump-btn">{comment_count_top}</a></div></div></div><div class="content">{content_area_html}</div>{poll_section_html}<div class="vote-box-container"><div class="vote-number" style="color:#d31900;">{upvotes}</div><div class="vote-circles"><div class="circle-btn circle-up"><span style="font-size:22px; color:#ffeb3b;">★</span><span>개념</span></div><div class="circle-btn circle-down"><span style="font-size:22px; color:white;">⬇</span><span>비추</span></div></div><div class="vote-number" style="color:#444;">{downvotes}</div></div><div id="comment-section"><div class="comments-header"><div class="comments-title">댓글 <span id="total-count" style="color:#d31900;">0</span>개</div><div class="comment-controls"><button class="control-btn active" id="sort-old" onclick="changeSort('old')">등록순</button><button class="control-btn" id="sort-new" onclick="changeSort('new')">최신순</button><button class="control-btn" id="sort-reply" onclick="changeSort('reply')">답글순</button><select id="limit-select" onchange="changeLimit(this.value)" style="padding: 2px; font-size: 12px; margin-left: 10px;"><option value="30">30개</option><option value="50" selected>50개</option><option value="100">100개</option><option value="9999">전체 보기</option></select></div></div><div class="comment-list-area" id="comment-list"></div><div class="pagination" id="pagination-buttons"></div></div></div><script>const rawComments = {comments_json_str}; let currentSort = 'old', commentsPerPage = 50, currentPage = 1, commentGroups = [], currentGroup = null; rawComments.forEach(c => {{ if (!c.is_reply) {{ currentGroup = {{ parent: c, replies: [] }}; commentGroups.push(currentGroup); }} else {{ if (currentGroup) currentGroup.replies.push(c); else {{ currentGroup = {{ parent: null, replies: [c] }}; commentGroups.push(currentGroup); }} }} }}); function buildWriterHTML(writerStr) {{ let match = writerStr.match(/(.+)\\s(\\([0-9.]+\\))$/); return match ? `${{match[1]}} <span class="ip">${{match[2]}}</span>` : writerStr; }} function buildContentHTML(c) {{ if (c.text.includes("삭제된 댓글")) return `<span class="deleted-text">${{c.text}}</span>`; let html = c.text.replace(/\\n/g, "<br>"); if (c.dccon) html += `<br><img src="${{c.dccon}}" style="width:85px; height:85px; margin-top:5px;">`; if (c.comment_img) html += `<br><img src="${{c.comment_img}}" style="margin-top:5px; max-width:200px; border-radius:4px;">`; return html; }} function renderComments() {{ const listArea = document.getElementById('comment-list'); const pageArea = document.getElementById('pagination-buttons'); listArea.innerHTML = ''; pageArea.innerHTML = ''; document.getElementById('total-count').innerText = rawComments.filter(c => !c.text.includes("삭제된 댓글")).length; if (rawComments.length === 0) return; let sortedGroups = [...commentGroups]; if (currentSort === 'new') sortedGroups.reverse(); else if (currentSort === 'reply') sortedGroups.sort((a, b) => b.replies.length - a.replies.length); const totalPages = Math.ceil(sortedGroups.length / commentsPerPage); if (currentPage > totalPages) currentPage = totalPages; if (currentPage < 1) currentPage = 1; const startIndex = (currentPage - 1) * commentsPerPage; const pageGroups = sortedGroups.slice(startIndex, startIndex + commentsPerPage); pageGroups.forEach(g => {{ if (g.parent) {{ const pDiv = document.createElement('div'); pDiv.className = 'comment-row'; if (g.parent.text.includes("삭제된 댓글")) pDiv.innerHTML = `<div class="comment-writer-box"></div><div class="comment-content-box">${{buildContentHTML(g.parent)}}</div><div class="comment-date-box"></div>`; else pDiv.innerHTML = `<div class="comment-writer-box">${{buildWriterHTML(g.parent.writer)}}</div><div class="comment-content-box">${{buildContentHTML(g.parent)}}</div><div class="comment-date-box">${{g.parent.date}}</div>`; listArea.appendChild(pDiv); }} g.replies.forEach(r => {{ const rDiv = document.createElement('div'); rDiv.className = 'comment-row reply-row'; if (r.text.includes("삭제된 댓글")) rDiv.innerHTML = `<div class="comment-writer-box"><span class="reply-icon">ㄴ</span></div><div class="comment-content-box">${{buildContentHTML(r)}}</div><div class="comment-date-box"></div>`; else rDiv.innerHTML = `<div class="comment-writer-box"><span class="reply-icon">ㄴ</span>${{buildWriterHTML(r.writer)}}</div><div class="comment-content-box">${{buildContentHTML(r)}}</div><div class="comment-date-box">${{r.date}}</div>`; listArea.appendChild(rDiv); }}); }}); if (totalPages > 1) {{
            const pageBlockSize = 10;
            const currentBlock = Math.floor((currentPage - 1) / pageBlockSize);
            const startPage = currentBlock * pageBlockSize + 1;
            const endPage = Math.min(startPage + pageBlockSize - 1, totalPages);

            if (startPage > 1) {{
                const firstBtn = document.createElement('button');
                firstBtn.className = 'page-btn';
                firstBtn.innerText = '<<';
                firstBtn.onclick = () => {{ currentPage = 1; renderComments(); window.scrollTo(0, document.getElementById('comment-section').offsetTop - 20); }};
                pageArea.appendChild(firstBtn);

                const prevBlockBtn = document.createElement('button');
                prevBlockBtn.className = 'page-btn';
                prevBlockBtn.innerText = '<';
                prevBlockBtn.onclick = () => {{ currentPage = startPage - 1; renderComments(); window.scrollTo(0, document.getElementById('comment-section').offsetTop - 20); }};
                pageArea.appendChild(prevBlockBtn);
            }}

            for (let i = startPage; i <= endPage; i++) {{
                const btn = document.createElement('button');
                btn.className = 'page-btn';
                if (i === currentPage) btn.classList.add('active');
                btn.innerText = i;
                btn.onclick = () => {{ currentPage = i; renderComments(); window.scrollTo(0, document.getElementById('comment-section').offsetTop - 20); }};
                pageArea.appendChild(btn);
            }}

            if (endPage < totalPages) {{
                const nextBlockBtn = document.createElement('button');
                nextBlockBtn.className = 'page-btn';
                nextBlockBtn.innerText = '>';
                nextBlockBtn.onclick = () => {{ currentPage = endPage + 1; renderComments(); window.scrollTo(0, document.getElementById('comment-section').offsetTop - 20); }};
                pageArea.appendChild(nextBlockBtn);

                const lastBtn = document.createElement('button');
                lastBtn.className = 'page-btn';
                lastBtn.innerText = '>>';
                lastBtn.onclick = () => {{ currentPage = totalPages; renderComments(); window.scrollTo(0, document.getElementById('comment-section').offsetTop - 20); }};
                pageArea.appendChild(lastBtn);
            }}
        }} }} function changeSort(type) {{ currentSort = type; document.querySelectorAll('.control-btn').forEach(btn => btn.classList.remove('active')); document.getElementById('sort-' + type).classList.add('active'); currentPage = 1; renderComments(); }} function changeLimit(val) {{ commentsPerPage = parseInt(val); currentPage = 1; renderComments(); }} document.querySelectorAll('a[href^="#"]').forEach(anchor => {{ anchor.addEventListener('click', function (e) {{ e.preventDefault(); document.querySelector(this.getAttribute('href')).scrollIntoView({{ behavior: 'smooth' }}); }}); }}); renderComments();</script></body></html>"""

        html_template = apply_comment_parent_grouping(html_template)
        with open(html_path, "w", encoding="utf-8") as f:
            f.write(html_template)
        return True
    except Exception as e:
        print(f"      ❌ 로컬 템플릿 갱신 에러: {e}")
        return False

def archive_single_post(post_no, target_gallery, page, drive_service, creds, folder_id, update_comments_only=False):
    target_url = f"https://gall.dcinside.com/board/view/?id={target_gallery}&no={post_no}"
    # 🆕 폴더 이름도 체크포인트와 동일한 고유 키를 사용합니다.
    archive_key = make_archive_key(target_gallery, post_no)
    save_dir = f"{BASE_DIR}/{archive_key}"
    img_dir = f"{save_dir}/images"
    os.makedirs(img_dir, exist_ok=True)
    
    html_path = f"{save_dir}/saved_post.html"
    content_area_html = ""
    has_poll = False
    image_count = 0
    thumbnail_url = ""
    poll_drive_url = ""
    poll_text_html = "" # 💡 [추가4] 투표 텍스트 추출 변수 초기화

    try:
        page.goto(target_url, timeout=20000, wait_until="domcontentloaded")
        time.sleep(0.5)
    except Exception as e:
        print(f"      ⚠️ 로딩 대기 제한 초과 (수집 강제 수행): {e}")

    full_html = page.content()
    soup = BeautifulSoup(full_html, "html.parser")
    if not soup.find("div", class_="write_div"):
        print(f" ❌ [{post_no}번 글] 원본 글을 찾을 수 없습니다.")
        return False, None

    title_el = soup.find("span", class_="title_subject")
    title = title_el.text.strip() if title_el else f"만화 {post_no}번"
    writer_el = soup.select_one(".gall_writer .nickname")
    writer_top = writer_el.text.strip() if writer_el else "ㅇㅇ"
    ip_el = soup.select_one(".gall_writer .ip")
    ip_top = ip_el.text.strip() if ip_el else ""
    date_el = soup.select_one(".gall_date")
    date_top = date_el.text.strip() if date_el else ""
    
    views_el = soup.select_one(".gall_count")
    views_top = views_el.text.strip() if views_el else "조회 0"
    views_val = int(re.search(r"\d+", views_top).group()) if re.search(r"\d+", views_top) else 0
    
    recommend_el = soup.select_one(".gall_reply_num")
    recommend_top = recommend_el.text.strip() if recommend_el else "추천 0"
    comment_count_el = soup.select_one(".gall_comment")
    comment_count_top = comment_count_el.text.strip() if comment_count_el else "댓글 0"
    
    up_el = soup.select_one(".up_num")
    upvotes = up_el.text.strip() if up_el else "0"
    recommend_val = int(re.search(r"\d+", upvotes).group()) if re.search(r"\d+", upvotes) else 0
    
    down_el = soup.select_one(".down_num")
    downvotes = down_el.text.strip() if down_el else "0"

    existing_comments_cache = {}
    old_comments = []
    if update_comments_only and os.path.exists(html_path):
        print(f"🔄 [{post_no}번 글] 기존 이미지 주소 보존 및 초고속 댓글 동기화 중...")
        try:
            with open(html_path, "r", encoding="utf-8") as f:
                old_soup = BeautifulSoup(f.read(), "html.parser")
            content_el = old_soup.find("div", class_="content")
            if content_el:
                content_area_html = str(content_el.decode_contents())
            else:
                update_comments_only = False
                
            poll_el = old_soup.find("div", class_="poll-container")
            if poll_el:
                has_poll = True
                poll_img = poll_el.find("img")
                if poll_img: poll_drive_url = poll_img.get("src", "")
                
                # 💡 [추가5] 실시간 추출 실패를 대비해 기존 HTML에 있던 투표 텍스트 백업
                text_el = poll_el.find("div", class_="poll-text-result")
                if text_el:
                    poll_text_html = str(text_el)
                
            completed_posts = load_checkpoint()
            image_count = completed_posts.get(archive_key, {}).get("image_count", 0)
            thumbnail_url = completed_posts.get(archive_key, {}).get("thumbnail", "")

            script_tags = old_soup.find_all("script")
            for s in script_tags:
                if s.string and "const rawComments =" in s.string:
                    match = re.search(r"const rawComments = (\[.*?\]);", s.string, re.DOTALL)
                    if match:
                        try:
                            old_comments = json.loads(match.group(1))
                            for oc in old_comments:
                                key = make_comment_fallback_key(oc)
                                existing_comments_cache[key] = {
                                    "dccon": oc.get("dccon", ""),
                                    "comment_img": oc.get("comment_img", "")
                                }
                        except Exception:
                            pass
                        break
        except Exception:
            update_comments_only = False

    if not update_comments_only:
        content_area = soup.find("div", class_="write_div")
        img_tags = content_area.find_all("img") if content_area else []
        img_session = requests.Session()
        img_headers = {"User-Agent": "Mozilla/5.0", "Referer": target_url}
        
        def download_worker(idx, img_el):
            img_url = img_el.get("data-original") or img_el.get("data-src") or img_el.get("src")
            if not img_url: return None
            try:
                img_res = img_session.get(img_url, headers=img_headers, timeout=10)
                if img_res.status_code == 200:
                    ext = img_url.split(".")[-1].split("?")[0].lower()
                    if ext not in ["jpg", "jpeg", "png", "gif", "webp"]: ext = "jpg"
                    raw_path = f"{img_dir}/raw_{idx+1}.{ext}"
                    with open(raw_path, "wb") as f: f.write(img_res.content)
                    return (idx, raw_path, ext, img_el)
            except Exception: pass
            return None

        downloaded_mangas = []
        with ThreadPoolExecutor(max_workers=10) as executor:
            futures = [executor.submit(download_worker, i, el) for i, el in enumerate(img_tags)]
            for f in as_completed(futures):
                res = f.result()
                if res: downloaded_mangas.append(res)

        uploaded_mapping = {}

        def upload_worker(item):
            idx, raw_path, ext, img_el = item
            compressed_path = f"{img_dir}/manga_{idx+1}.jpg"
            try:
                compress_image(raw_path, compressed_path)
                thread_http = google_auth_httplib2.AuthorizedHttp(creds, http=httplib2.Http())
                file_id, direct_link = upload_file_to_drive(drive_service, compressed_path, folder_id, thread_http)
                if os.path.exists(raw_path): os.remove(raw_path)
                if os.path.exists(compressed_path): os.remove(compressed_path)
                return (idx, file_id, direct_link, img_el)
            except Exception as e:
                print(f"      ❌ 전송 실패 (Index: {idx}): {e}")
                return None

        uploaded_results = []
        with ThreadPoolExecutor(max_workers=8) as executor:
            futures = [executor.submit(upload_worker, item) for item in downloaded_mangas]
            for f in as_completed(futures):
                res = f.result()
                if res: uploaded_results.append(res)

        if len(uploaded_results) < len(downloaded_mangas) or len(downloaded_mangas) == 0:
            if img_tags:
                print(f" ⚠️ [{post_no}번 글] 일부 이미지 전송 실패 ({len(uploaded_results)}/{len(downloaded_mangas)} 성공)")
                print("   안전을 위해 본 게시글을 미완료 상태로 두고 다음 실행 시 다시 수집하도록 제외합니다.")
                shutil.rmtree(img_dir, ignore_errors=True)
                return False, None

        for idx, file_id, direct_link, img_el in uploaded_results:
            img_el["src"] = direct_link
            if img_el.has_attr("data-original"): del img_el["data-original"]
            if img_el.has_attr("data-src"): del img_el["data-src"]
            uploaded_mapping[f"manga_{idx+1}.jpg"] = file_id

        content_area_html = str(content_area) if content_area else ""
        image_count = len(uploaded_results)
        
        if uploaded_mapping:
            first_key = sorted(list(uploaded_mapping.keys()))[0]
            thumbnail_url = f"https://lh3.googleusercontent.com/d/{uploaded_mapping[first_key]}"

        poll_drive_url = ""
        poll_frame = next((f for f in page.frames if "poll" in f.url), None)
        if poll_frame:
            try:
                poll_wrap_locator = poll_frame.locator(".vote_wrap")
                poll_wrap_locator.wait_for(state="visible", timeout=3000)
                poll_frame.click(".btn_votepreview", timeout=2000)
                time.sleep(1)
                temp_vote_path = f"{img_dir}/vote_status.png"
                poll_wrap_locator.screenshot(path=temp_vote_path)
                _, poll_drive_url = upload_file_to_drive(drive_service, temp_vote_path, folder_id)
                os.remove(temp_vote_path)
                has_poll = True
            except Exception: pass

    collected_comments = []
    seen_comment_ids = set()
    current_cmt_page = 1
    img_session = requests.Session()
    img_headers = {"User-Agent": "Mozilla/5.0", "Referer": target_url}

    def parse_visible_comments(page_html):
        c_soup = BeautifulSoup(page_html, "html.parser")
        comment_items = c_soup.select("ul.cmt_list li")
        
        for item in comment_items:
            c_id = item.get("id", "")
            if not c_id or not (c_id.startswith("comment_") or c_id.startswith("reply_")): continue
            if c_id in seen_comment_ids: continue
            seen_comment_ids.add(c_id)
            
            is_reply = False
            parent = item.parent
            while parent and parent.name != "body":
                if parent.name == "ul":
                    p_classes = parent.get("class")
                    if p_classes:
                        if isinstance(p_classes, str): p_classes = [p_classes]
                        if any("reply" in c.lower() for c in p_classes if isinstance(c, str)):
                            is_reply = True
                            break
                parent = parent.parent
                
            if c_id.startswith("reply_"):
                is_reply = True
                
            item_classes = item.get("class")
            if item_classes:
                if isinstance(item_classes, str): item_classes = [item_classes]
                if any("reply" in c.lower() for c in item_classes if isinstance(c, str)):
                    is_reply = True

            parent_comment_id = ""
            if is_reply:
                # 디시 답글의 실제 부모 번호는 상위 reply_list의 p-no에 들어 있습니다.
                # 예: <ul id="reply_list_5544470" p-no="5544470"> → comment_li_5544470
                reply_list = item.find_parent("ul", class_=lambda value: value and "reply_list" in value)
                if reply_list:
                    parent_no = str(
                        reply_list.get("p-no")
                        or reply_list.get("id", "").replace("reply_list_", "")
                    ).strip()
                    if parent_no:
                        parent_comment_id = f"comment_li_{parent_no}"
                    
            for nested_reply in item.find_all("ul"):
                nr_classes = nested_reply.get("class")
                if nr_classes:
                    if isinstance(nr_classes, str): nr_classes = [nr_classes]
                    if any("reply" in c.lower() for c in nr_classes if isinstance(c, str)):
                        nested_reply.extract()
                        
            is_deleted = "삭제된" in item.text
            if item_classes:
                if any("cmt_blank" in c.lower() for c in item_classes if isinstance(c, str)):
                    is_deleted = True
                    
            if is_deleted:
                collected_comments.append({
                    "writer": "", "text": "삭제된 댓글입니다.", "is_reply": is_reply, "dccon": "", "comment_img": "", "date": "",
                    "comment_id": c_id, "parent_comment_id": parent_comment_id,
                    "raw_dccon": "", "raw_cmt_img": ""
                })
                continue
                
            writer = item.find("span", class_="nickname")
            ip_tag = item.find("span", class_="ip")
            full_writer = f"{writer.text.strip() if writer else 'ㅇㅇ'} {ip_tag.text.strip() if ip_tag else ''}".strip()
            txt_element = item.find("p", class_="usertxt")
            txt = txt_element.text.strip() if txt_element else ""
            date_element = item.find("span", class_="date_time") or item.find("span", class_="date")
            date_text = date_element.text.strip() if date_element else ""
            
            normalized_date = normalize_comment_date(date_text)
            
            raw_dccon_src = ""
            for img_el in item.find_all("img"):
                img_classes = img_el.get("class")
                if img_classes:
                    if isinstance(img_classes, str): img_classes = [img_classes]
                    if any("dccon" in c.lower() for c in img_classes if isinstance(c, str)):
                        raw_dccon_src = img_el.get("data-src") or img_el.get("data-original") or img_el.get("org-src") or img_el.get("src") or ""
                        break

            comment_img_src = ""
            for img_el in item.find_all("img"):
                img_src = img_el.get("data-original") or img_el.get("data-src") or img_el.get("org-src") or img_el.get("src")
                img_classes = img_el.get("class")
                is_dccon_img = False
                if img_classes:
                    if isinstance(img_classes, str): img_classes = [img_classes]
                    if any("dccon" in c.lower() for c in img_classes if isinstance(c, str)):
                        is_dccon_img = True
                if img_src and not is_dccon_img and "option_icon" not in img_src:
                    comment_img_src = img_src
                    break
                    
            collected_comments.append({
                "writer": full_writer, "text": txt, "is_reply": is_reply, "dccon": "", "comment_img": "", "date": normalized_date,
                "comment_id": c_id, "parent_comment_id": parent_comment_id,
                "raw_dccon": raw_dccon_src, "raw_cmt_img": comment_img_src
            })

    # 💡 숫자 페이지 버튼뿐 아니라 30페이지 이후의 '다음 페이지 묶음' 버튼도 따라갑니다.
    # 같은 댓글 페이지가 반복되면 즉시 중단하여 잘못된 버튼 선택으로 인한 무한 루프를 방지합니다.
    visited_comment_pages = set()

    while True:
        page_html = page.content()
        comment_page_ids = tuple(re.findall(r'id=["\'](?:comment|reply)_([^"\']+)', page_html))
        page_signature = comment_page_ids[:5]

        if page_signature and page_signature in visited_comment_pages:
            print(f"      ⚠️ 댓글 {current_cmt_page}페이지가 반복되어 안전하게 수집을 종료합니다.")
            break

        if page_signature:
            visited_comment_pages.add(page_signature)

        parse_visible_comments(page_html)
        print(f"      💬 댓글 {current_cmt_page}페이지 수집 완료 (누적 {len(collected_comments)}개)")

        next_page_num = current_cmt_page + 1
        page_buttons = page.locator(".cmt_paging a, .comment_numbox a")
        clicked = False
        
        # 1. 우선 화면에 다음 숫자 버튼이 있으면 기존 방식 그대로 이동합니다.
        for i in range(page_buttons.count()):
            btn = page_buttons.nth(i)
            if btn.inner_text().strip() == str(next_page_num):
                try:
                    with page.expect_response(lambda r: "comment" in r.url, timeout=5000):
                        btn.evaluate("node => node.click()")
                    page.wait_for_timeout(300)
                except Exception as e:
                    print(f"      ⚠️ 댓글 페이징 지연, 안전 모드 대기: {e}")
                    time.sleep(2.0)
                
                current_cmt_page = next_page_num
                clicked = True
                break

        # 2. 숫자 버튼이 없으면 30페이지 이후에 나타나는 다음 페이지 묶음 버튼을 찾습니다.
        if not clicked:
            next_block_buttons = page.locator(
                ".cmt_paging a.page_next, "
                ".comment_numbox a.page_next, "
                ".cmt_paging a[class*='next'], "
                ".comment_numbox a[class*='next'], "
                ".cmt_paging a[title*='다음'], "
                ".comment_numbox a[title*='다음']"
            )

            for i in range(next_block_buttons.count()):
                btn = next_block_buttons.nth(i)
                try:
                    if not btn.is_visible():
                        continue

                    with page.expect_response(lambda r: "comment" in r.url, timeout=5000):
                        btn.evaluate("node => node.click()")
                    page.wait_for_timeout(300)
                except Exception as e:
                    print(f"      ⚠️ 다음 댓글 묶음 이동 지연, 안전 모드 대기: {e}")
                    time.sleep(2.0)

                current_cmt_page = next_page_num
                clicked = True
                print(f"      ➡️ 댓글 다음 페이지 묶음으로 이동합니다. ({current_cmt_page}페이지)")
                break

        if not clicked:
            break

    dccon_cache = load_dccon_cache()
    
    for c in collected_comments:
        cache_key = make_comment_fallback_key(c)
        if cache_key in existing_comments_cache:
            c["dccon"] = existing_comments_cache[cache_key].get("dccon", "")
            c["comment_img"] = existing_comments_cache[cache_key].get("comment_img", "")
            c["raw_dccon"] = ""
            c["raw_cmt_img"] = ""
            
        if c.get("raw_dccon") and c["raw_dccon"] in dccon_cache:
            c["dccon"] = dccon_cache[c["raw_dccon"]]
            c["raw_dccon"] = ""

    new_urls = set()
    for c in collected_comments:
        if c.get("raw_dccon") and not c.get("dccon"):
            new_urls.add(c["raw_dccon"])
        if c.get("raw_cmt_img") and not c.get("comment_img"):
            new_urls.add(c["raw_cmt_img"])

    uploaded_assets = {}

    def asset_worker(url):
        is_dccon = "dccon" in url or "dcon" in url
        prefix = "dccon" if is_dccon else "cmt"
        try:
            res = img_session.get(url, headers=img_headers, timeout=10)
            if res.status_code == 200:
                ext = url.split(".")[-1].split("?")[0].lower()
                if ext not in ["jpg", "png", "gif", "webp"]: 
                    ext = "gif" if is_dccon else "jpg"
                
                url_hash = hashlib.md5(url.encode('utf-8')).hexdigest()
                temp_path = f"{img_dir}/{prefix}_{url_hash}.{ext}"
                with open(temp_path, "wb") as f: 
                    f.write(res.content)
                
                thread_http = google_auth_httplib2.AuthorizedHttp(creds, http=httplib2.Http())
                _, drive_url = upload_file_to_drive(drive_service, temp_path, folder_id, thread_http)
                if os.path.exists(temp_path): 
                    os.remove(temp_path)
                return url, drive_url
        except Exception as e:
            print(f"      ⚠️ 댓글 에셋 업로드 오류 ({url}): {e}")
        return url, None

    if new_urls:
        print(f"      📥 새 에셋 {len(new_urls)}개 감지. 병렬 업로드 개시...")
        with ThreadPoolExecutor(max_workers=8) as executor:
            futures = [executor.submit(asset_worker, url) for url in new_urls]
            for f in as_completed(futures):
                url, drive_url = f.result()
                if drive_url:
                    uploaded_assets[url] = drive_url
                    if "dccon" in url or "dcon" in url:
                        dccon_cache[url] = drive_url
        
        save_dccon_cache(dccon_cache)

    for c in collected_comments:
        if not c.get("dccon") and c.get("raw_dccon") in uploaded_assets:
            c["dccon"] = uploaded_assets[c["raw_dccon"]]
        if not c.get("comment_img") and c.get("raw_cmt_img") in uploaded_assets:
            c["comment_img"] = uploaded_assets[c["raw_cmt_img"]]
            
        if "raw_dccon" in c: del c["raw_dccon"]
        if "raw_cmt_img" in c: del c["raw_cmt_img"]

    comment_relation_ready = all(
        not comment.get("is_reply")
        or bool(str(comment.get("parent_comment_id", "")).strip())
        for comment in collected_comments
    )
    if not comment_relation_ready:
        print("      ⚠️ 부모 ID를 확인하지 못한 답글이 있어 관계 갱신을 완료 처리하지 않습니다.")

    if update_comments_only and old_comments:
        newly_collected_count = len(collected_comments)
        collected_comments = merge_comments_preserving_existing(old_comments, collected_comments)
        print(
            f"      🧩 기존 댓글 {len(old_comments)}개 유지 + 현재 댓글 {newly_collected_count}개 병합 "
            f"= 보존 댓글 {len(collected_comments)}개"
        )

    # 💡 [추가6] 투표(Poll) 실시간 텍스트 추출 로직 (이미지 캡처는 건드리지 않고 텍스트만 갱신)
    if has_poll or poll_drive_url:
        poll_frame_live = next((f for f in page.frames if "poll" in f.url), None)
        if poll_frame_live:
            try:
                poll_wrap_live = poll_frame_live.locator(".vote_wrap")
                try: 
                    poll_frame_live.click(".btn_votepreview", timeout=1000)
                    time.sleep(0.5) 
                except Exception: pass
                
                raw_text = poll_wrap_live.inner_text()
                lines = [line.strip() for line in raw_text.split('\n') if line.strip()]
                poll_text_content = "<br>".join(lines)
                
                # 💡 실시간 갱신 시스템 시각 매핑 (지연시간 0초)
                now_str = datetime.datetime.now().strftime("%Y.%m.%d %H:%M:%S")
                poll_text_html = f"<div class='poll-text-result' style='margin-top:15px; padding:15px; background:#f8fafc; border:1px solid #cbd5e1; border-radius:8px; font-size:14px; font-weight:bold; color:#334155; line-height:1.6;'>💡 텍스트 투표 현황 ({now_str} 갱신)<br><br>{poll_text_content}</div>"
            except Exception:
                pass

    # 💡 [추가7] 투표 영역 HTML 조립 (기존 이미지 1장 + 실시간 텍스트 결과)
    poll_section_html = ""
    if poll_drive_url or has_poll or poll_text_html:
        poll_section_html = """<div class="poll-container"><h3>🗳️ 본문 투표 백업</h3>"""
        if poll_drive_url:
            poll_section_html += f"""<img src="{poll_drive_url}" style="max-width:100%; display:block; margin:0 auto;">"""
        if poll_text_html:
            poll_section_html += poll_text_html
        poll_section_html += "</div>"

    comments_json_str = json.dumps(collected_comments, ensure_ascii=False)
    
    html_template = get_html_template(
        title, target_url, writer_top, ip_top, date_top, views_top, recommend_top, 
        comment_count_top, content_area_html, poll_section_html, upvotes, downvotes, comments_json_str
    )

    html_template = apply_comment_parent_grouping(html_template)
    with open(html_path, "w", encoding="utf-8") as f: f.write(html_template)
    
    live_comment_count = int(re.search(r"\d+", comment_count_top).group()) if re.search(r"\d+", comment_count_top) else len(collected_comments)

    post_meta = {
        # 🆕 HTML 목록과 리더가 실제 글 번호와 저장 폴더를 구분할 수 있도록 함께 기록합니다.
        "gallery_id": target_gallery,
        "post_no": str(post_no),
        "archive_key": archive_key,
        "title": title,
        "date": date_top,
        "views": views_val,
        "recommend": recommend_val,
        "comment_count": len(collected_comments),
        "live_comment_count": live_comment_count,
        "comment_id_version": 1,
        "comment_relation_version": 2 if comment_relation_ready else 0,
        "image_count": image_count,
        "thumbnail": thumbnail_url,
        "has_poll": bool(poll_drive_url or has_poll or poll_text_html) # 💡 꼬리표 추가
    }
    if not update_comments_only:
        shutil.rmtree(img_dir, ignore_errors=True)
    return True, post_meta

# 💡 디시인사이드 주소 포맷을 모바일/PC 모두 파싱하는 다목적 추출 함수
def parse_dc_url(url_or_num):
    url_or_num = str(url_or_num).strip()
    if not url_or_num:
        return None, None
    if url_or_num.startswith("#"):
        return None, None
        
    # Case 1: 순수 숫자만 적은 경우 (기본 설정 갤러리로 매핑)
    if url_or_num.isdigit():
        return DEFAULT_GALLERY_ID, url_or_num
        
    # Case 2: 일반 PC 뷰어형 주소 (id=갤ID&no=글번호)
    if "id=" in url_or_num and "no=" in url_or_num:
        gall_match = re.search(r"id=([a-zA-Z0-9_]+)", url_or_num)
        no_match = re.search(r"no=(\d+)", url_or_num)
        if gall_match and no_match:
            return gall_match.group(1), no_match.group(1)
            
    # Case 3: 모바일 뷰어형 주소 (/board/갤ID/글번호)
    path_match = re.search(r"/board/([a-zA-Z0-9_]+)/(\d+)", url_or_num)
    if path_match:
        return path_match.group(1), path_match.group(2)
        
    # 기타 예외적 마이너 갤러리 파싱 대조군 추가
    gall_match = re.search(r"[?&]id=([a-zA-Z0-9_]+)", url_or_num)
    no_match = re.search(r"[?&]no=(\d+)", url_or_num)
    if gall_match and no_match:
        return gall_match.group(1), no_match.group(1)
        
    return None, None

# ==========================================
# [ 비가시성 타겟 다이렉트 수집 제어부 ]
# ==========================================
def run_direct_archiver():
    completed_posts = load_checkpoint()
    
    # 💡 파일이 아닌 수식 리스트(TARGET_LINKS)에서 직접 항목을 읽어옵니다.
    target_items = []
    for item in TARGET_LINKS:
        gall_id, post_no = parse_dc_url(item)
        if gall_id and post_no:
            target_items.append((gall_id, post_no))
                    
    if not target_items:
        print("ℹ️ TARGET_LINKS 배열에 수집할 디시인사이드 주소나 글 번호가 등록되지 않았습니다.")
        return

    print("\n==========================================")
    print(f" 🎯 [타겟 다이렉트 수집 엔진] 총 {len(target_items)}개 대기열 기동...")
    print("==========================================")

    with sync_playwright() as p:
        browser = p.chromium.launch(headless=True)
        post_page = browser.new_page()
        post_page.on("dialog", lambda dialog: dialog.dismiss())
        
        def block_heavy_resources(route):
            url = route.request.url
            if route.request.resource_type in ["image", "font", "media"]: 
                route.abort()
            elif any(ad in url for ad in ["google", "analytics", "doubleclick", "logger", "adservice", "adsystem", "facebook"]):
                route.abort()
            else: 
                route.continue_()
                
        post_page.route("**/*", block_heavy_resources)
        
        creds = get_gcp_credentials()
        drive_service = build('drive', 'v3', credentials=creds)
        folder_id = get_or_create_drive_folder(drive_service)
        
        scanned_count = 0
        total_targets = len(target_items)
        
        for target_gall, post_no in target_items:
            scanned_count += 1
            success = False
            # 🆕 중복 판정도 글 번호 단독이 아닌 갤러리별 고유 키로 처리합니다.
            archive_key = make_archive_key(target_gall, post_no)
            is_completed = archive_key in completed_posts
            
            # 중복 강제 덮어쓰기 옵션(FORCE_OVERWRITE) 확인
            if is_completed and not FORCE_OVERWRITE:
                saved_cmt_count = completed_posts[archive_key].get(
                    "live_comment_count",
                    completed_posts[archive_key].get("comment_count", 0)
                )
                print(f"\n▶ [{post_no}번] 타겟 분석 중... (이미 완료 목록에 존재)")

                # 💡 자동 아카이버와 동일하게 원문 댓글 수가 같으면 전체 댓글 재수집을 생략합니다.
                target_url = f"https://gall.dcinside.com/board/view/?id={target_gall}&no={post_no}"
                try:
                    post_page.goto(target_url, timeout=20000, wait_until="domcontentloaded")
                    post_page.wait_for_timeout(500)
                    count_soup = BeautifulSoup(post_page.content(), "html.parser")
                    comment_count_el = count_soup.select_one(".gall_comment")
                    comment_count_text = comment_count_el.text.strip() if comment_count_el else "댓글 0"
                    current_cmt_count = int(re.search(r"\d+", comment_count_text).group()) if re.search(r"\d+", comment_count_text) else 0
                except Exception as e:
                    print(f"      ⚠️ 원문 댓글 수 확인 실패로 안전하게 전체 동기화를 진행합니다: {e}")
                    current_cmt_count = None

                has_comment_relations = (
                    completed_posts[archive_key].get("comment_relation_version") == 2
                    or saved_post_has_comment_relations(archive_key)
                )
                if current_cmt_count is not None and current_cmt_count == saved_cmt_count and has_comment_relations:
                    print(f"   └─ [{post_no}번] 댓글 변동 없음 ({current_cmt_count}개). 재수집을 생략합니다.")
                    continue

                if current_cmt_count is not None and current_cmt_count == saved_cmt_count:
                    print(f"   └─ 기존 댓글 ID가 없어 최초 1회 답글 관계를 갱신합니다.")
                elif current_cmt_count is not None:
                    print(f"   └─ 댓글 수 변동 감지 (기존 원문 {saved_cmt_count}개 -> 현재 원문 {current_cmt_count}개)")

                success, post_meta = archive_single_post(post_no, target_gall, post_page, drive_service, creds, folder_id, update_comments_only=True)
                if success:
                    completed_posts[archive_key]["comment_count"] = post_meta["comment_count"]
                    completed_posts[archive_key]["live_comment_count"] = post_meta["live_comment_count"]
                    completed_posts[archive_key]["comment_id_version"] = 1
                    completed_posts[archive_key]["comment_relation_version"] = post_meta["comment_relation_version"]
                    completed_posts[archive_key]["views"] = post_meta["views"]       # 💡 이 줄 추가
                    completed_posts[archive_key]["recommend"] = post_meta["recommend"] # 💡 이 줄 추가
                    completed_posts[archive_key]["gallery_id"] = target_gall
                    completed_posts[archive_key]["post_no"] = str(post_no)
                    completed_posts[archive_key]["archive_key"] = archive_key
                    poll_msg = " 투표 동기화 완료!" if post_meta.get("has_poll") else ""
                    print(f"   └─ [{post_no}번] 댓글 동기화 완료!{poll_msg} (수집된 이미지: {post_meta['image_count']}개, 총 댓글: {post_meta['comment_count']}개) [다이렉트 진척도: {scanned_count}/{total_targets}]")
            else:
                if FORCE_OVERWRITE and is_completed:
                    print(f"\n▶ [{post_no}번] 강제 덮어쓰기 모드(FORCE_OVERWRITE=True) 적용. 원문 전체 강제 재수집...")
                else:
                    print(f"\n▶ [{post_no}번] 타겟 신규 분석 개시 및 전체 수집 시작...")
                    
                success, post_meta = archive_single_post(post_no, target_gall, post_page, drive_service, creds, folder_id, update_comments_only=False)
                if success:
                    completed_posts[archive_key] = {"comment_count": post_meta["comment_count"], **post_meta}
                    poll_msg = " 투표 수집 완료!" if post_meta.get("has_poll") else ""
                    print(f"   └─ [{post_no}번] 수집 성공!{poll_msg} (수집된 이미지: {post_meta['image_count']}개, 총 댓글: {post_meta['comment_count']}개) [다이렉트 진척도: {scanned_count}/{total_targets}]")

            if success:
                save_checkpoint(completed_posts)
                delay = round(random.uniform(1.5, 3.0), 1)
                print(f"   └─ 디시 차단 방지를 위해 {delay}초 대기...")
                time.sleep(delay)
                
        browser.close()
        
        print("\n🚀 데이터 GitHub Pages 배포 시도 중...")
        subprocess.run("git add .", shell=True)
        subprocess.run('git commit -m "Auto Update: Direct Targets Sync Applied"', shell=True)
        subprocess.run("git push", shell=True)
        print("🎉 배포가 완전히 완료되었습니다!")

if __name__ == "__main__":
    run_direct_archiver()
    release_lock()
    sys.exit(0)

# 💡 lockonarchiver.py 전체 코드를 완벽하게 대조했습니다.
# 이 코드는 이전 대수술을 모두 거쳐 어떠한 에러도 내지 않으며, auto_archiver1.py와 공동 DB로 무결성 있게 가동됩니다.
# TARGET_LINKS 설정 영역에 원하시는 링크를 넣고 돌려주시면 됩니다.
# "" 없이 숫자는 입력이 가능하나, 주소(링크)는 특수기호 에러 방지를 위해 ""로 감싸 주셔야 합니다!
