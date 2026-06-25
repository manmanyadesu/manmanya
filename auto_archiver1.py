# ==============================================================================
# [ 사용자 기본 설정 영역 (원하시는 대로 수정 후 사용하세요) ]
# ==============================================================================
GALLERY_ID = "comic_new6"               # 디시인사이드 갤러리 ID
START_PAGE = 1                          # 기본 시작 페이지
END_PAGE = 1                            # 기본 종료 페이지
MAX_POSTS_TO_ARCHIVE = 0               # 기본 최대 수집 수량 (0 이면 제한 없음)

# 🚀 [템플릿 디자인 초고속 갱신용 토글]
#  False /True로 설정 시 크롬창과 드라이브 API 호출 없이 로컬에서 단 1초 만에 모바일 반응형 템플릿으로 일괄 교체합니다.
FORCE_TEMPLATE_REBUILD = False          

# 강제 전체 재수집(초기화) 대상 글 번호 목록 (몇 페이지에 있든 무조건 최우선 수집!)
FORCE_REARCHIVE_POST_NOS = []

# 구글 드라이브 및 로컬 백업 경로 설정
SCOPES = ['https://www.googleapis.com/auth/drive.file']
BASE_DIR = "./archive"
CHECKPOINT_FILE = f"{BASE_DIR}/completed_posts.json"
DCCON_CACHE_FILE = f"{BASE_DIR}/dccon_cache.json"
LOCK_FILE = f"{BASE_DIR}/crawler.lock"
# ==============================================================================

import os
import sys

# 🛡️ [스케줄러 백그라운드 가동 대비] 작업 폴더가 System32로 잡히는 것을 원천 차단
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

# 깃허브 Pages Jekyll 우회 파일 자동 생성
if not os.path.exists(".nojekyll"):
    try:
        with open(".nojekyll", "w") as f: pass
        print("ℹ️ 깃허브 Pages 차단 방지용 .nojekyll 파일을 생성했습니다.")
    except Exception: pass

# 중복 실행 방지용 락 시스템
if os.path.exists(LOCK_FILE):
    try:
        with open(LOCK_FILE, "r") as f:
            old_pid = int(f.read().strip())
        is_running = False
        try:
            out = subprocess.check_output(f'tasklist /FI "PID eq {old_pid}"', shell=True, stderr=subprocess.DEVNULL)
            out_str = out.decode('utf-8', errors='ignore') + out.decode('cp949', errors='ignore')
            for line in out_str.splitlines():
                if str(old_pid) in line:
                    is_running = True
                    break
        except Exception: is_running = False
        if is_running:
            print(f"⚠️ 이미 다른 크롤러 인스턴스(PID: {old_pid})가 작동 중입니다. 실행을 중단합니다.")
            exit()
        else:
            os.remove(LOCK_FILE)
    except Exception:
        try: os.remove(LOCK_FILE)
        except Exception: pass

with open(LOCK_FILE, "w") as f: f.write(str(os.getpid()))

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

def saved_post_has_comment_ids(post_no):
    """
    기존 저장본의 댓글에 comment_id가 이미 들어 있는지 확인합니다.
    댓글이 없는 글은 다시 수집할 필요가 없으므로 완료된 것으로 판단합니다.
    """
    html_path = f"{BASE_DIR}/{post_no}/saved_post.html"
    if not os.path.exists(html_path):
        return False

    try:
        with open(html_path, "r", encoding="utf-8") as f:
            html_text = f.read()
        match = re.search(r"const rawComments = (\[.*?\]);", html_text, re.DOTALL)
        if not match:
            return False
        comments = json.loads(match.group(1))
        # 삭제되어 원문에서 사라진 옛 댓글은 ID를 복원할 수 있으므로,
        # 현재 확인 가능한 댓글에 ID가 하나라도 채워졌으면 전환 완료로 봅니다.
        return not comments or any(str(comment.get("comment_id", "")).strip() for comment in comments)
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

def rebuild_html_locally(post_no):
    save_dir = f"{BASE_DIR}/{post_no}"
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
        image_count = completed_posts.get(post_no, {}).get("image_count", 0)
        thumbnail_url = completed_posts.get(post_no, {}).get("thumbnail", "")
        
        target_url = f"https://gall.dcinside.com/board/view/?id={GALLERY_ID}&no={post_no}"
        
        # 💡 [추가3] 로컬 빌드 시 투표 영역 템플릿 조립 (텍스트 포함)
        poll_section_html = ""
        if poll_drive_url or has_poll or poll_text_html:
            poll_section_html = """<div class="poll-container"><h3>🗳️ 본문 투표 백업</h3>"""
            if poll_drive_url:
                poll_section_html += f"""<img src="{poll_drive_url}" style="max-width:100%; display:block; margin:0 auto;">"""
            if poll_text_html:
                poll_section_html += poll_text_html
            poll_section_html += "</div>"

        html_template = f"""<!DOCTYPE html><html lang="ko"><head><meta charset="utf-8"><meta name="viewport" content="width=device-width, initial-scale=1.0"><title>{title} - 아카이브</title><style>body {{ font-family: 'Malgun Gothic', sans-serif; margin: 40px; background-color: #f5f6f7; color: #333; }}.container {{ max-width: 900px; margin: 0 auto; background: white; padding: 30px; border-radius: 8px; box-shadow: 0 4px 6px rgba(0,0,0,0.1); }}.post-header {{ border-bottom: 1px solid #ccc; padding-bottom: 15px; margin-bottom: 20px; }}.post-title {{ font-size: 22px; font-weight: bold; color: #222; margin-bottom: 12px; }}.post-title a {{ text-decoration: none; color: inherit; }}.post-title a:hover {{ color: #1d4ed8; }}.post-meta-wrap {{ display: flex; justify-content: space-between; font-size: 13px; color: #666; }}.meta-left .writer {{ font-weight: bold; color: #333; margin-right: 10px; }}.comment-jump-btn {{ background: #f3f3f3; border: 1px solid #e1e1e1; border-radius: 15px; padding: 3px 12px; color: #333; text-decoration: none; font-weight: bold; font-size: 12px; }}.content {{ line-height: 1.8; font-size: 16px; margin-top: 30px; padding-bottom: 40px; }}.content img {{ max-width: 100% !important; height: auto !important; display: block; margin: 15px auto; }}.vote-box-container {{ border: 1px solid #ddd; padding: 30px; border-radius: 8px; margin: 40px auto; max-width: 400px; display: flex; justify-content: center; align-items: center; gap: 30px; background: #fff; }}.vote-number {{ font-size: 22px; font-weight: bold; width: 40px; text-align: center; }}.vote-circles {{ display: flex; gap: 15px; }}.circle-btn {{ width: 80px; height: 80px; border-radius: 50%; display: flex; flex-direction: column; justify-content: center; align-items: center; font-weight: bold; color: white; font-size: 14px; box-shadow: 0 2px 5px rgba(0,0,0,0.2); }}.circle-up {{ background: #3b5998; }} .circle-down {{ background: #a5a5a5; }}.comments-header {{ display: flex; justify-content: space-between; align-items: center; border-bottom: 1px solid #3b5998; padding-bottom: 10px; margin-top: 40px; }}.comments-title {{ font-size: 16px; font-weight: bold; color: #3b5998; }}.control-btn {{ background: none; border: none; font-size: 13px; cursor: pointer; font-weight: bold; color: #999; margin-right: 5px; }}.control-btn.active {{ color: #3b5998; }}.comment-list-area {{ border-top: 1px solid #3b5998; }}.comment-row {{ display: flex; border-bottom: 1px solid #e2e2e2; padding: 12px 0; align-items: flex-start; }}.comment-writer-box {{ width: 160px; flex-shrink: 0; padding: 0 10px; color: #333; font-weight: bold; font-size: 13px; word-break: break-all; }}.comment-writer-box span.ip {{ color: #999; font-weight: normal; font-size: 11px; }}.comment-content-box {{ flex-grow: 1; padding: 0 10px; font-size: 13px; color: #333; word-break: break-all; }}.comment-content-box img {{ max-width: 200px; border-radius: 4px; display: block; margin-top: 5px; }}.comment-date-box {{ width: 100px; flex-shrink: 0; text-align: right; color: #999; font-size: 12px; padding-right: 10px; }}.reply-row {{ background-color: #f9f9f9; padding-left: 0; border-left: 3px solid #ddd; }}.reply-row .comment-writer-box {{ width: 180px; padding-left: 35px; position: relative; }}.reply-icon {{ position: absolute; left: 12px; top: 0; color: #3b5998; font-weight: 900; }}.deleted-text {{ color: #aaa; font-style: normal; }}.pagination {{ display: flex; justify-content: center; gap: 5px; margin-top: 20px; }}.page-btn {{ border: 1px solid #ddd; background: white; padding: 5px 10px; cursor: pointer; border-radius: 3px; font-size: 13px; }}.page-btn.active {{ background: #3b5998; color: white; font-weight: bold; }}@media (max-width: 768px) {{ body {{ margin: 8px; padding: 0; background-color: #fff; }} .container {{ padding: 10px; border-radius: 0; box-shadow: none; }} .post-title {{ font-size: 18px; line-height: 1.4; }} .post-meta-wrap {{ flex-direction: column; gap: 5px; font-size: 11px; }} .comment-row {{ flex-direction: column; padding: 8px 0; }} .comment-writer-box {{ width: 100%; font-size: 12px; margin-bottom: 4px; }} .comment-content-box {{ width: 100%; font-size: 12px; padding: 0; }} .comment-content-box img {{ max-width: 150px; }} .comment-date-box {{ width: 100%; text-align: left; font-size: 10px; margin-top: 4px; }} .reply-row {{ padding-left: 10px; }} .reply-row .comment-writer-box {{ padding-left: 20px; }} .vote-box-container {{ padding: 15px; margin: 20px auto; gap: 15px; max-width: 100%; }} .content div, .content p, .content table, .content tr, .content td, .content span {{ max-width: 100% !important; width: auto !important; height: auto !important; }} }}</style></head><body><div class="container"><div class="post-header"><div class="post-title"><a href="{target_url}" target="_blank" title="디시인사이드 원문 글로 가기">{title} <span style="font-size:14px; color:#1d4ed8; font-weight:normal; margin-left:6px; vertical-align:middle;">🔗 원문 보기</span></a></div><div class="post-meta-wrap"><div class="meta-left"><span class="writer">{writer_top} {ip_top}</span><span class="date">{date_top}</span></div><div class="meta-right"><span>{views_top}</span> | <span>{recommend_top}</span> | <a href="#comment-section" class="comment-jump-btn">{comment_count_top}</a></div></div></div><div class="content">{content_area_html}</div>{poll_section_html}<div class="vote-box-container"><div class="vote-number" style="color:#d31900;">{upvotes}</div><div class="vote-circles"><div class="circle-btn circle-up"><span style="font-size:22px; color:#ffeb3b;">★</span><span>개념</span></div><div class="circle-btn circle-down"><span style="font-size:22px; color:white;">⬇</span><span>비추</span></div></div><div class="vote-number" style="color:#444;">{downvotes}</div></div><div id="comment-section"><div class="comments-header"><div class="comments-title">댓글 <span id="total-count" style="color:#d31900;">0</span>개</div><div class="comment-controls"><button class="control-btn active" id="sort-old" onclick="changeSort('old')">등록순</button><button class="control-btn" id="sort-new" onclick="changeSort('new')">최신순</button><button class="control-btn" id="sort-reply" onclick="changeSort('reply')">답글순</button><select id="limit-select" onchange="changeLimit(this.value)" style="padding: 2px; font-size: 12px; margin-left: 10px;"><option value="30">30개</option><option value="50" selected>50개</option><option value="100">100개</option><option value="9999">전체 보기</option></select></div></div><div class="comment-list-area" id="comment-list"></div><div class="pagination" id="pagination-buttons"></div></div></div><script>const rawComments = {comments_json_str}; let currentSort = 'old', commentsPerPage = 50, currentPage = 1, commentGroups = [], currentGroup = null; rawComments.forEach(c => {{ if (!c.is_reply) {{ currentGroup = {{ parent: c, replies: [] }}; commentGroups.push(currentGroup); }} else {{ if (currentGroup) currentGroup.replies.push(c); else {{ currentGroup = {{ parent: null, replies: [c] }}; commentGroups.push(currentGroup); }} }} }}); function buildWriterHTML(writerStr) {{ let match = writerStr.match(/(.+)\\s(\\([0-9.]+\\))$/); return match ? `${{match[1]}} <span class="ip">${{match[2]}}</span>` : writerStr; }} function buildContentHTML(c) {{ if (c.text.includes("삭제된 댓글")) return `<span class="deleted-text">${{c.text}}</span>`; let html = c.text.replace(/\\n/g, "<br>"); if (c.dccon) html += `<br><img src="${{c.dccon}}" style="width:85px; height:85px; margin-top:5px;">`; if (c.comment_img) html += `<br><img src="${{c.comment_img}}" style="margin-top:5px; max-width:200px; border-radius:4px;">`; return html; }} function renderComments() {{ const listArea = document.getElementById('comment-list'); const pageArea = document.getElementById('pagination-buttons'); listArea.innerHTML = ''; pageArea.innerHTML = ''; document.getElementById('total-count').innerText = rawComments.filter(c => !c.text.includes("삭제된 댓글")).length; if (rawComments.length === 0) return; let sortedGroups = [...commentGroups]; if (currentSort === 'new') sortedGroups.reverse(); else if (currentSort === 'reply') sortedGroups.sort((a, b) => b.replies.length - a.replies.length); const totalPages = Math.ceil(sortedGroups.length / commentsPerPage); if (currentPage > totalPages) currentPage = totalPages; if (currentPage < 1) currentPage = 1; const startIndex = (currentPage - 1) * commentsPerPage; const pageGroups = sortedGroups.slice(startIndex, startIndex + commentsPerPage); pageGroups.forEach(g => {{ if (g.parent) {{ const pDiv = document.createElement('div'); pDiv.className = 'comment-row'; if (g.parent.text.includes("삭제된 댓글")) pDiv.innerHTML = `<div class="comment-writer-box"></div><div class="comment-content-box">${{buildContentHTML(g.parent)}}</div><div class="comment-date-box"></div>`; else pDiv.innerHTML = `<div class="comment-writer-box">${{buildWriterHTML(g.parent.writer)}}</div><div class="comment-content-box">${{buildContentHTML(g.parent)}}</div><div class="comment-date-box">${{g.parent.date}}</div>`; listArea.appendChild(pDiv); }} g.replies.forEach(r => {{ const rDiv = document.createElement('div'); rDiv.className = 'comment-row reply-row'; if (r.text.includes("삭제된 댓글")) rDiv.innerHTML = `<div class="comment-writer-box"><span class="reply-icon">ㄴ</span></div><div class="comment-content-box">${{buildContentHTML(r)}}</div><div class="comment-date-box"></div>`; else rDiv.innerHTML = `<div class="comment-writer-box"><span class="reply-icon">ㄴ</span>${{buildWriterHTML(r.writer)}}</div><div class="comment-content-box">${{buildContentHTML(r)}}</div><div class="comment-date-box">${{r.date}}</div>`; listArea.appendChild(rDiv); }}); }}); if (totalPages > 1) {{ for (let i = 1; i <= totalPages; i++) {{ const btn = document.createElement('button'); btn.className = 'page-btn'; if (i === currentPage) btn.classList.add('active'); btn.innerText = i; btn.onclick = () => {{ currentPage = i; renderComments(); window.scrollTo(0, document.getElementById('comment-section').offsetTop - 20); }}; pageArea.appendChild(btn); }} }} }} function changeSort(type) {{ currentSort = type; document.querySelectorAll('.control-btn').forEach(btn => btn.classList.remove('active')); document.getElementById('sort-' + type).classList.add('active'); currentPage = 1; renderComments(); }} function changeLimit(val) {{ commentsPerPage = parseInt(val); currentPage = 1; renderComments(); }} document.querySelectorAll('a[href^="#"]').forEach(anchor => {{ anchor.addEventListener('click', function (e) {{ e.preventDefault(); document.querySelector(this.getAttribute('href')).scrollIntoView({{ behavior: 'smooth' }}); }}); }}); renderComments();</script></body></html>"""

        html_template = apply_comment_parent_grouping(html_template)
        with open(html_path, "w", encoding="utf-8") as f:
            f.write(html_template)
        return True
    except Exception as e:
        print(f"      ❌ 로컬 템플릿 갱신 에러: {e}")
        return False

def archive_single_post(post_no, page, drive_service, creds, folder_id, update_comments_only=False):
    target_url = f"https://gall.dcinside.com/board/view/?id={GALLERY_ID}&no={post_no}"
    save_dir = f"{BASE_DIR}/{post_no}"
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
            image_count = completed_posts.get(post_no, {}).get("image_count", 0)
            thumbnail_url = completed_posts.get(post_no, {}).get("thumbnail", "")

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

    # 💡 신규 BS4 버그 회피를 위해, 내부 모든 탐색 엔진을 vanilla 방식으로 교체했습니다.
    def parse_visible_comments(page_html):
        c_soup = BeautifulSoup(page_html, "html.parser")
        comment_items = c_soup.select("ul.cmt_list li")
        
        for item in comment_items:
            c_id = item.get("id", "")
            if not c_id or not (c_id.startswith("comment_") or c_id.startswith("reply_")): continue
            if c_id in seen_comment_ids: continue
            seen_comment_ids.add(c_id)
            
            # vanilla parent ul search (re.compile 버그 방지)
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
                parent_comment = item.find_parent("li", id=re.compile(r"^comment_"))
                if parent_comment:
                    parent_comment_id = parent_comment.get("id", "")
                    
            # vanilla extract nested replies
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
                # 결과 보기 버튼 클릭 (투표가 있을 때만 0.5초 대기 발생)
                try: 
                    poll_frame_live.click(".btn_votepreview", timeout=1000)
                    time.sleep(0.5) 
                except Exception: pass
                
                # 텍스트화 추출 (드라이브 업로드 없이 실시간 갱신)
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

    html_template = f"""<!DOCTYPE html><html lang="ko"><head><meta charset="utf-8"><meta name="viewport" content="width=device-width, initial-scale=1.0"><title>{title} - 아카이브</title><style>body {{ font-family: 'Malgun Gothic', sans-serif; margin: 40px; background-color: #f5f6f7; color: #333; }}.container {{ max-width: 900px; margin: 0 auto; background: white; padding: 30px; border-radius: 8px; box-shadow: 0 4px 6px rgba(0,0,0,0.1); }}.post-header {{ border-bottom: 1px solid #ccc; padding-bottom: 15px; margin-bottom: 20px; }}.post-title {{ font-size: 22px; font-weight: bold; color: #222; margin-bottom: 12px; }}.post-title a {{ text-decoration: none; color: inherit; }}.post-title a:hover {{ color: #1d4ed8; }}.post-meta-wrap {{ display: flex; justify-content: space-between; font-size: 13px; color: #666; }}.meta-left .writer {{ font-weight: bold; color: #333; margin-right: 10px; }}.comment-jump-btn {{ background: #f3f3f3; border: 1px solid #e1e1e1; border-radius: 15px; padding: 3px 12px; color: #333; text-decoration: none; font-weight: bold; font-size: 12px; }}.content {{ line-height: 1.8; font-size: 16px; margin-top: 30px; padding-bottom: 40px; }}.content img {{ max-width: 100% !important; height: auto !important; display: block; margin: 15px auto; }}.vote-box-container {{ border: 1px solid #ddd; padding: 30px; border-radius: 8px; margin: 40px auto; max-width: 400px; display: flex; justify-content: center; align-items: center; gap: 30px; background: #fff; }}.vote-number {{ font-size: 22px; font-weight: bold; width: 40px; text-align: center; }}.vote-circles {{ display: flex; gap: 15px; }}.circle-btn {{ width: 80px; height: 80px; border-radius: 50%; display: flex; flex-direction: column; justify-content: center; align-items: center; font-weight: bold; color: white; font-size: 14px; box-shadow: 0 2px 5px rgba(0,0,0,0.2); }}.circle-up {{ background: #3b5998; }} .circle-down {{ background: #a5a5a5; }}.comments-header {{ display: flex; justify-content: space-between; align-items: center; border-bottom: 1px solid #3b5998; padding-bottom: 10px; margin-top: 40px; }}.comments-title {{ font-size: 16px; font-weight: bold; color: #3b5998; }}.control-btn {{ background: none; border: none; font-size: 13px; cursor: pointer; font-weight: bold; color: #999; margin-right: 5px; }}.control-btn.active {{ color: #3b5998; }}.comment-list-area {{ border-top: 1px solid #3b5998; }}.comment-row {{ display: flex; border-bottom: 1px solid #e2e2e2; padding: 12px 0; align-items: flex-start; }}.comment-writer-box {{ width: 160px; flex-shrink: 0; padding: 0 10px; color: #333; font-weight: bold; font-size: 13px; word-break: break-all; }}.comment-writer-box span.ip {{ color: #999; font-weight: normal; font-size: 11px; }}.comment-content-box {{ flex-grow: 1; padding: 0 10px; font-size: 13px; color: #333; word-break: break-all; }}.comment-content-box img {{ max-width: 200px; border-radius: 4px; display: block; margin-top: 5px; }}.comment-date-box {{ width: 100px; flex-shrink: 0; text-align: right; color: #999; font-size: 12px; padding-right: 10px; }}.reply-row {{ background-color: #f9f9f9; padding-left: 0; border-left: 3px solid #ddd; }}.reply-row .comment-writer-box {{ width: 180px; padding-left: 35px; position: relative; }}.reply-icon {{ position: absolute; left: 12px; top: 0; color: #3b5998; font-weight: 900; }}.deleted-text {{ color: #aaa; font-style: normal; }}.pagination {{ display: flex; justify-content: center; gap: 5px; margin-top: 20px; }}.page-btn {{ border: 1px solid #ddd; background: white; padding: 5px 10px; cursor: pointer; border-radius: 3px; font-size: 13px; }}.page-btn.active {{ background: #3b5998; color: white; font-weight: bold; }}@media (max-width: 768px) {{ body {{ margin: 8px; padding: 0; background-color: #fff; }} .container {{ padding: 10px; border-radius: 0; box-shadow: none; }} .post-title {{ font-size: 18px; line-height: 1.4; }} .post-meta-wrap {{ flex-direction: column; gap: 5px; font-size: 11px; }} .comment-row {{ flex-direction: column; padding: 8px 0; }} .comment-writer-box {{ width: 100%; font-size: 12px; margin-bottom: 4px; }} .comment-content-box {{ width: 100%; font-size: 12px; padding: 0; }} .comment-content-box img {{ max-width: 150px; }} .comment-date-box {{ width: 100%; text-align: left; font-size: 10px; margin-top: 4px; }} .reply-row {{ padding-left: 10px; }} .reply-row .comment-writer-box {{ padding-left: 20px; }} .vote-box-container {{ padding: 15px; margin: 20px auto; gap: 15px; max-width: 100%; }} .content div, .content p, .content table, .content tr, .content td, .content span {{ max-width: 100% !important; width: auto !important; height: auto !important; }} }}</style></head><body><div class="container"><div class="post-header"><div class="post-title"><a href="{target_url}" target="_blank" title="디시인사이드 원문 글로 가기">{title} <span style="font-size:14px; color:#1d4ed8; font-weight:normal; margin-left:6px; vertical-align:middle;">🔗 원문 보기</span></a></div><div class="post-meta-wrap"><div class="meta-left"><span class="writer">{writer_top} {ip_top}</span><span class="date">{date_top}</span></div><div class="meta-right"><span>{views_top}</span> | <span>{recommend_top}</span> | <a href="#comment-section" class="comment-jump-btn">{comment_count_top}</a></div></div></div><div class="content">{content_area_html}</div>{poll_section_html}<div class="vote-box-container"><div class="vote-number" style="color:#d31900;">{upvotes}</div><div class="vote-circles"><div class="circle-btn circle-up"><span style="font-size:22px; color:#ffeb3b;">★</span><span>개념</span></div><div class="circle-btn circle-down"><span style="font-size:22px; color:white;">⬇</span><span>비추</span></div></div><div class="vote-number" style="color:#444;">{downvotes}</div></div><div id="comment-section"><div class="comments-header"><div class="comments-title">댓글 <span id="total-count" style="color:#d31900;">0</span>개</div><div class="comment-controls"><button class="control-btn active" id="sort-old" onclick="changeSort('old')">등록순</button><button class="control-btn" id="sort-new" onclick="changeSort('new')">최신순</button><button class="control-btn" id="sort-reply" onclick="changeSort('reply')">답글순</button><select id="limit-select" onchange="changeLimit(this.value)" style="padding: 2px; font-size: 12px; margin-left: 10px;"><option value="30">30개</option><option value="50" selected>50개</option><option value="100">100개</option><option value="9999">전체 보기</option></select></div></div><div class="comment-list-area" id="comment-list"></div><div class="pagination" id="pagination-buttons"></div></div></div><script>const rawComments = {json.dumps(collected_comments, ensure_ascii=False)}; let currentSort = 'old', commentsPerPage = 50, currentPage = 1, commentGroups = [], currentGroup = null; rawComments.forEach(c => {{ if (!c.is_reply) {{ currentGroup = {{ parent: c, replies: [] }}; commentGroups.push(currentGroup); }} else {{ if (currentGroup) currentGroup.replies.push(c); else {{ currentGroup = {{ parent: null, replies: [c] }}; commentGroups.push(currentGroup); }} }} }}); function buildWriterHTML(writerStr) {{ let match = writerStr.match(/(.+)\\s(\\([0-9.]+\\))$/); return match ? `${{match[1]}} <span class="ip">${{match[2]}}</span>` : writerStr; }} function buildContentHTML(c) {{ if (c.text.includes("삭제된 댓글")) return `<span class="deleted-text">${{c.text}}</span>`; let html = c.text.replace(/\\n/g, "<br>"); if (c.dccon) html += `<br><img src="${{c.dccon}}" style="width:85px; height:85px; margin-top:5px;">`; if (c.comment_img) html += `<br><img src="${{c.comment_img}}" style="margin-top:5px; max-width:200px; border-radius:4px;">`; return html; }} function renderComments() {{ const listArea = document.getElementById('comment-list'); const pageArea = document.getElementById('pagination-buttons'); listArea.innerHTML = ''; pageArea.innerHTML = ''; document.getElementById('total-count').innerText = rawComments.filter(c => !c.text.includes("삭제된 댓글")).length; if (rawComments.length === 0) return; let sortedGroups = [...commentGroups]; if (currentSort === 'new') sortedGroups.reverse(); else if (currentSort === 'reply') sortedGroups.sort((a, b) => b.replies.length - a.replies.length); const totalPages = Math.ceil(sortedGroups.length / commentsPerPage); if (currentPage > totalPages) currentPage = totalPages; if (currentPage < 1) currentPage = 1; const startIndex = (currentPage - 1) * commentsPerPage; const pageGroups = sortedGroups.slice(startIndex, startIndex + commentsPerPage); pageGroups.forEach(g => {{ if (g.parent) {{ const pDiv = document.createElement('div'); pDiv.className = 'comment-row'; if (g.parent.text.includes("삭제된 댓글")) pDiv.innerHTML = `<div class="comment-writer-box"></div><div class="comment-content-box">${{buildContentHTML(g.parent)}}</div><div class="comment-date-box"></div>`; else pDiv.innerHTML = `<div class="comment-writer-box">${{buildWriterHTML(g.parent.writer)}}</div><div class="comment-content-box">${{buildContentHTML(g.parent)}}</div><div class="comment-date-box">${{g.parent.date}}</div>`; listArea.appendChild(pDiv); }} g.replies.forEach(r => {{ const rDiv = document.createElement('div'); rDiv.className = 'comment-row reply-row'; if (r.text.includes("삭제된 댓글")) rDiv.innerHTML = `<div class="comment-writer-box"><span class="reply-icon">ㄴ</span></div><div class="comment-content-box">${{buildContentHTML(r)}}</div><div class="comment-date-box"></div>`; else rDiv.innerHTML = `<div class="comment-writer-box"><span class="reply-icon">ㄴ</span>${{buildWriterHTML(r.writer)}}</div><div class="comment-content-box">${{buildContentHTML(r)}}</div><div class="comment-date-box">${{r.date}}</div>`; listArea.appendChild(rDiv); }}); }}); if (totalPages > 1) {{ for (let i = 1; i <= totalPages; i++) {{ const btn = document.createElement('button'); btn.className = 'page-btn'; if (i === currentPage) btn.classList.add('active'); btn.innerText = i; btn.onclick = () => {{ currentPage = i; renderComments(); window.scrollTo(0, document.getElementById('comment-section').offsetTop - 20); }}; pageArea.appendChild(btn); }} }} }} function changeSort(type) {{ currentSort = type; document.querySelectorAll('.control-btn').forEach(btn => btn.classList.remove('active')); document.getElementById('sort-' + type).classList.add('active'); currentPage = 1; renderComments(); }} function changeLimit(val) {{ commentsPerPage = parseInt(val); currentPage = 1; renderComments(); }} document.querySelectorAll('a[href^="#"]').forEach(anchor => {{ anchor.addEventListener('click', function (e) {{ e.preventDefault(); document.querySelector(this.getAttribute('href')).scrollIntoView({{ behavior: 'smooth' }}); }}); }}); renderComments();</script></body></html>"""

    html_template = apply_comment_parent_grouping(html_template)
    with open(html_path, "w", encoding="utf-8") as f: f.write(html_template)
    
    live_comment_count = int(re.search(r"\d+", comment_count_top).group()) if re.search(r"\d+", comment_count_top) else len(collected_comments)

    post_meta = {
        "title": title,
        "date": date_top,
        "views": views_val,
        "recommend": recommend_val,
        "comment_count": len(collected_comments),
        "live_comment_count": live_comment_count,
        "comment_id_version": 1,
        "image_count": image_count,
        "thumbnail": thumbnail_url,
        "has_poll": bool(poll_drive_url or has_poll or poll_text_html) # 💡 꼬리표 추가
    }
    if not update_comments_only:
        shutil.rmtree(img_dir, ignore_errors=True)
    return True, post_meta

# ==========================================
# [ 비가시성 수집 제어부 ]
# ==========================================
def run_archiver_logic(start_p, end_p, max_p, force_nos_str, force_template_rebuild=False):
    completed_posts = load_checkpoint()
    force_nos = [n.strip() for n in force_nos_str.split(",") if n.strip()]
    archive_count = 0

    with sync_playwright() as p:
        browser = p.chromium.launch(headless=True)
        list_page = browser.new_page()
        post_page = browser.new_page()
        
        list_page.on("dialog", lambda dialog: dialog.dismiss())
        post_page.on("dialog", lambda dialog: dialog.dismiss())
        
        def block_heavy_resources(route):
            url = route.request.url
            if route.request.resource_type in ["image", "font", "media"]: 
                route.abort()
            elif any(ad in url for ad in ["google", "analytics", "doubleclick", "logger", "adservice", "adsystem", "facebook"]):
                route.abort()
            else: 
                route.continue_()
                
        list_page.route("**/*", block_heavy_resources)
        post_page.route("**/*", block_heavy_resources)
        
        creds = get_gcp_credentials()
        drive_service = build('drive', 'v3', credentials=creds)
        
        folder_id = get_or_create_drive_folder(drive_service)
        
        if force_nos:
            print("\n==========================================")
            print(" 🚀 [최우선 타겟 수집] 강제 재수집 대기열 가동 중...")
            print("==========================================")
            for f_no in force_nos:
                if f_no in completed_posts:
                    del completed_posts[f_no]
                
                print(f"\n▶ [{f_no}번 글] 원문 강제 다이렉트 수집 개시...")
                success, post_meta = archive_single_post(f_no, post_page, drive_service, creds, folder_id, update_comments_only=False)
                if success:
                    completed_posts[f_no] = {
                        "comment_count": post_meta["comment_count"],
                        **post_meta
                    }
                    save_checkpoint(completed_posts)
                    time.sleep(3.0)

        try:
            for page_num in range(start_p, end_p + 1):
                print(f"\n==========================================")
                print(f" 📖 개념글 {page_num}페이지 탐색 중...")
                list_page.goto(f"https://gall.dcinside.com/board/lists/?id={GALLERY_ID}&exception_mode=recommend&page={page_num}")
                list_page.wait_for_load_state("domcontentloaded")
                
                soup = BeautifulSoup(list_page.content(), "html.parser")
                for row in soup.select("tr.us-post:not(.notice)"):
                    if max_p and archive_count >= max_p: break
                        
                    no_el = row.select_one(".gall_num")
                    if not no_el or not no_el.text.strip().isdigit(): continue
                    post_no = no_el.text.strip()
                    
                    archive_count += 1
                    
                    if post_no in force_nos:
                        archive_count -= 1
                        continue
                    
                    reply_el = row.select_one(".reply_num")
                    current_cmt_count = int(re.search(r"\d+", reply_el.text).group()) if reply_el and re.search(r"\d+", reply_el.text) else 0
                    
                    is_completed = post_no in completed_posts
                    
                    if force_template_rebuild:
                        if is_completed:
                            print(f"⚡ [{post_no}번 글] 로컬 템플릿 초고속 갱신 중... (인스펙션 누적: {archive_count}/{max_p})")
                            rebuild_html_locally(post_no)
                        continue

                    if is_completed:
                        # 💡 보존 댓글 수가 아니라 마지막 원문 댓글 수를 비교합니다.
                        # 기존 체크포인트에는 live_comment_count가 없으므로 최초 1회는 comment_count를 대신 사용합니다.
                        saved_cmt_count = completed_posts[post_no].get(
                            "live_comment_count",
                            completed_posts[post_no].get("comment_count", 0)
                        )
                        has_comment_ids = (
                            completed_posts[post_no].get("comment_id_version") == 1
                            or saved_post_has_comment_ids(post_no)
                        )
                        if current_cmt_count == saved_cmt_count and has_comment_ids:
                            print(f"   └─ [{post_no}번] 이미 수집됨 (댓글 변동 없음: {current_cmt_count}개) [인스펙션 누적: {archive_count}/{max_p}]")
                            continue

                        if current_cmt_count == saved_cmt_count:
                            print(f"\n▶ [{post_no}번] 기존 댓글 ID가 없어 최초 1회 답글 관계 갱신 시작...")
                        else:
                            print(f"\n▶ [{post_no}번] 댓글 수 변동 감지 (기존 원문 {saved_cmt_count}개 -> 현재 원문 {current_cmt_count}개) 병합 시작...")
                        
                        success, post_meta = archive_single_post(post_no, post_page, drive_service, creds, folder_id, update_comments_only=True)
                        if success: 
                            completed_posts[post_no]["comment_count"] = post_meta["comment_count"]
                            completed_posts[post_no]["live_comment_count"] = current_cmt_count
                            completed_posts[post_no]["comment_id_version"] = 1
                            completed_posts[post_no]["views"] = post_meta["views"]       # 💡 이 줄 추가
                            completed_posts[post_no]["recommend"] = post_meta["recommend"] # 💡 이 줄 추가
                            poll_msg = " 투표 동기화 완료!" if post_meta.get("has_poll") else ""
                            print(f"   └─ [{post_no}번] 댓글 동기화 완료!{poll_msg} (수집된 이미지: {post_meta['image_count']}개, 총 댓글: {post_meta['comment_count']}개) [인스펙션 누적: {archive_count}/{max_p}]")
                    else:
                        print(f"\n▶ [{post_no}번] 신규 글 발견! 전체 수집 시작...")
                        success, post_meta = archive_single_post(post_no, post_page, drive_service, creds, folder_id, update_comments_only=False)
                        if success:
                            completed_posts[post_no] = {"comment_count": current_cmt_count, **post_meta}
                            poll_msg = " 투표 수집 완료!" if post_meta.get("has_poll") else ""
                            print(f"   └─ [{post_no}번] 수집 성공!{poll_msg} (수집된 이미지: {post_meta['image_count']}개, 총 댓글: {post_meta['comment_count']}개) [인스펙션 누적: {archive_count}/{max_p}]")

                    if success:
                        save_checkpoint(completed_posts)
                        delay = round(random.uniform(1.5, 3.0), 1)
                        print(f"   └─ 디시 차단 방지를 위해 {delay}초 대기...")
                        time.sleep(delay)

                if max_p and archive_count >= max_p: break
        except Exception as e:
            print(f"⚠️ 가동 중 오류 발생: {e}")
        finally:
            save_checkpoint(completed_posts)
            release_lock()
            browser.close()
            
            print("\n🚀 데이터 GitHub Pages 배포 시도 중...")
            subprocess.run("git add .", shell=True)
            subprocess.run('git commit -m "Auto Update: Fast Sync & Inspection Limit Applied"', shell=True)
            subprocess.run("git push", shell=True)
            print("🎉 배포가 완전히 완료되었습니다!")

if __name__ == "__main__":
    start_p = START_PAGE
    end_p = END_PAGE
    max_p = MAX_POSTS_TO_ARCHIVE
    force_nos_str = ",".join(FORCE_REARCHIVE_POST_NOS)
    force_tmpl = FORCE_TEMPLATE_REBUILD
    
    run_archiver_logic(start_p, end_p, max_p, force_nos_str, force_tmpl)
    release_lock()
    sys.exit(0)
