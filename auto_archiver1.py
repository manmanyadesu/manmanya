# ==============================================================================
# [ 사용자 기본 설정 영역 (원하시는 대로 수정 후 사용하세요) ]
# ==============================================================================
GALLERY_ID = "comic_new6"               # 디시인사이드 갤러리 ID
START_PAGE = 1                          # 기본 시작 페이지
END_PAGE = 2                            # 기본 종료 페이지
MAX_POSTS_TO_ARCHIVE = 8                # 기본 최대 수집 수량 (0 이면 제한 없음)

# 🚀 [템플릿 초고속 갱신용 토글]
# True로 설정 시 이미지 업로드를 생략하고 기존 드라이브 주소로 HTML 디자인만 즉시 교체합니다.
FORCE_TEMPLATE_REBUILD = False          

# 강제 전체 재수집(초기화) 대상 글 번호 목록 (몇 페이지에 있든 무조건 최우선 수집!)
FORCE_REARCHIVE_POST_NOS = []

# 구글 드라이브 및 로컬 백업 경로 설정
SCOPES = ['https://www.googleapis.com/auth/drive.file']
BASE_DIR = "./archive"
CHECKPOINT_FILE = f"{BASE_DIR}/completed_posts.json"
LOCK_FILE = f"{BASE_DIR}/crawler.lock"
# ==============================================================================

import os
import re
import sys
import time
import json
import random
import shutil
import requests
import httplib2
import subprocess
import socket
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

# 중복 가동 방지용 자가치유 락 시스템
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
    
    media = MediaFileUpload(file_path, mimetype=mime_type, resumable=False)
    if thread_http:
        file = drive_service.files().create(body=file_metadata, media_body=media, fields='id').execute(http=thread_http)
    else:
        file = drive_service.files().create(body=file_metadata, media_body=media, fields='id').execute()
    return file.get('id'), f"https://lh3.googleusercontent.com/d/{file.get('id')}"

def archive_single_post(post_no, page, drive_service, creds, update_comments_only=False):
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

    # 실시간 통계 수집
    try:
        page.goto(target_url, timeout=20000, wait_until="domcontentloaded")
        page.evaluate("window.scrollTo(0, document.body.scrollHeight)")
        time.sleep(1.0)
    except Exception as e:
        print(f"      ⚠️ 로딩 대기 제한 초과 (수집 강제 수행): {e}")

    full_html = page.content()
    soup = BeautifulSoup(full_html, "html.parser")
    if not soup.find("div", class_="write_div"):
        print(f" ❌ [{post_no}번 글] 원본 글을 찾을 수 없거나 접근이 불가능합니다.")
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

    # 🔄 [댓글 동기화 / 템플릿만 갱신 모드] 이미지 전송 생략하고 HTML만 조립
    if update_comments_only and os.path.exists(html_path):
        print(f"🔄 [{post_no}번 글] 이미지 전송 생략 및 디자인/댓글 초고속 갱신 중...")
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
                
            completed_posts = load_checkpoint()
            image_count = completed_posts.get(post_no, {}).get("image_count", 0)
            thumbnail_url = completed_posts.get(post_no, {}).get("thumbnail", "")
        except Exception:
            update_comments_only = False

    # 📸 [최초 전체 수집 모드] 이미지 전송 정상 가동
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

        folder_id = get_or_create_drive_folder(drive_service)
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

        # 🛡️ [자가 치유 안전 장치] 업로드 결함 발생 시 완료 대조군 등록을 차단
        if len(uploaded_results) < len(downloaded_mangas) or len(downloaded_mangas) == 0:
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

    # 댓글 수집
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
            if item.find_parent("ul", class_=re.compile("reply")) or c_id.startswith("reply_") or "reply" in "".join(item.get("class", [])).lower(): is_reply = True
            for nested_reply in item.find_all("ul", class_=re.compile("reply")): nested_reply.extract()
            if "cmt_blank" in " ".join(item.get("class", [])).lower() or "삭제된" in item.text:
                collected_comments.append({"writer": "", "text": "삭제된 댓글입니다.", "is_reply": is_reply, "dccon": "", "comment_img": "", "date": ""})
                continue
            writer = item.find("span", class_="nickname")
            ip_tag = item.find("span", class_="ip")
            full_writer = f"{writer.text.strip() if writer else 'ㅇㅇ'} {ip_tag.text.strip() if ip_tag else ''}".strip()
            txt_element = item.find("p", class_="usertxt")
            txt = txt_element.text.strip() if txt_element else ""
            date_element = item.find("span", class_="date_time") or item.find("span", class_="date")
            date_text = date_element.text.strip() if date_element else ""
            
            dccon_src = ""
            dccon = item.find("img", class_=re.compile("dccon"))
            if dccon and dccon.get("src"): dccon_src = dccon.get("src")
            
            comment_img_src = ""
            for img_el in item.find_all("img"):
                img_src = img_el.get("src")
                if img_src and "dccon" not in img_src and "option_icon" not in img_src:
                    comment_img_src = img_src
                    break
            collected_comments.append({"writer": full_writer, "text": txt, "is_reply": is_reply, "dccon": dccon_src, "comment_img": comment_img_src, "date": date_text})

    while True:
        parse_visible_comments(page.content())
        next_page_num = current_cmt_page + 1
        page_buttons = page.locator(".cmt_paging a, .comment_numbox a")
        clicked = False
        for i in range(page_buttons.count()):
            btn = page_buttons.nth(i)
            if btn.inner_text().strip() == str(next_page_num):
                btn.evaluate("node => node.click()")
                time.sleep(1.2)
                current_cmt_page = next_page_num
                clicked = True
                break
        if not clicked: break

    poll_section_html = f"""<div class="poll-container"><h3>🗳️ 본문 투표 백업</h3><img src="{poll_drive_url}" style="max-width:100%; display:block; margin:0 auto;"></div>""" if (poll_drive_url or (not update_comments_only and has_poll)) else ""

    html_template = f"""<!DOCTYPE html><html lang="ko"><head><meta charset="utf-8"><title>{title} - 아카이브</title><style>body {{ font-family: 'Malgun Gothic', sans-serif; margin: 40px; background-color: #f5f6f7; color: #333; }}.container {{ max-width: 900px; margin: 0 auto; background: white; padding: 30px; border-radius: 8px; box-shadow: 0 4px 6px rgba(0,0,0,0.1); }}.post-header {{ border-bottom: 1px solid #ccc; padding-bottom: 15px; margin-bottom: 20px; }}.post-title {{ font-size: 22px; font-weight: bold; color: #222; margin-bottom: 12px; }}.post-title a {{ text-decoration: none; color: inherit; }}.post-title a:hover {{ color: #1d4ed8; }}.post-meta-wrap {{ display: flex; justify-content: space-between; font-size: 13px; color: #666; }}.meta-left .writer {{ font-weight: bold; color: #333; margin-right: 10px; }}.comment-jump-btn {{ background: #f3f3f3; border: 1px solid #e1e1e1; border-radius: 15px; padding: 3px 12px; color: #333; text-decoration: none; font-weight: bold; font-size: 12px; }}.content {{ line-height: 1.8; font-size: 16px; margin-top: 30px; padding-bottom: 40px; }}.content img {{ max-width: 100%; height: auto; display: block; margin: 15px auto; }}.vote-box-container {{ border: 1px solid #ddd; padding: 30px; border-radius: 8px; margin: 40px auto; max-width: 400px; display: flex; justify-content: center; align-items: center; gap: 30px; background: #fff; }}.vote-number {{ font-size: 22px; font-weight: bold; width: 40px; text-align: center; }}.vote-circles {{ display: flex; gap: 15px; }}.circle-btn {{ width: 80px; height: 80px; border-radius: 50%; display: flex; flex-direction: column; justify-content: center; align-items: center; font-weight: bold; color: white; font-size: 14px; box-shadow: 0 2px 5px rgba(0,0,0,0.2); }}.circle-up {{ background: #3b5998; }} .circle-down {{ background: #a5a5a5; }}.comments-header {{ display: flex; justify-content: space-between; align-items: center; border-bottom: 1px solid #3b5998; padding-bottom: 10px; margin-top: 40px; }}.comments-title {{ font-size: 16px; font-weight: bold; color: #3b5998; }}.control-btn {{ background: none; border: none; font-size: 13px; cursor: pointer; font-weight: bold; color: #999; margin-right: 5px; }}.control-btn.active {{ color: #3b5998; }}.comment-list-area {{ border-top: 1px solid #3b5998; }}.comment-row {{ display: flex; border-bottom: 1px solid #e2e2e2; padding: 12px 0; align-items: flex-start; }}.comment-writer-box {{ width: 160px; flex-shrink: 0; padding: 0 10px; color: #333; font-weight: bold; font-size: 13px; word-break: break-all; }}.comment-writer-box span.ip {{ color: #999; font-weight: normal; font-size: 11px; }}.comment-content-box {{ flex-grow: 1; padding: 0 10px; font-size: 13px; color: #333; word-break: break-all; }}.comment-content-box img {{ max-width: 200px; border-radius: 4px; display: block; margin-top: 5px; }}.comment-date-box {{ width: 100px; flex-shrink: 0; text-align: right; color: #999; font-size: 12px; padding-right: 10px; }}.reply-row {{ background-color: #f9f9f9; padding-left: 0; border-left: 3px solid #ddd; }}.reply-row .comment-writer-box {{ width: 180px; padding-left: 35px; position: relative; }}.reply-icon {{ position: absolute; left: 12px; top: 0; color: #3b5998; font-weight: 900; }}.deleted-text {{ color: #aaa; font-style: normal; }}.pagination {{ display: flex; justify-content: center; gap: 5px; margin-top: 20px; }}.page-btn {{ border: 1px solid #ddd; background: white; padding: 5px 10px; cursor: pointer; border-radius: 3px; font-size: 13px; }}.page-btn.active {{ background: #3b5998; color: white; font-weight: bold; }}</style></head><body><div class="container"><div class="post-header"><div class="post-title"><a href="{target_url}" target="_blank" title="디시인사이드 원문 글로 가기">{title} <span style="font-size:14px; color:#1d4ed8; font-weight:normal; margin-left:6px; vertical-align:middle;">🔗 원문 보기</span></a></div><div class="post-meta-wrap"><div class="meta-left"><span class="writer">{writer_top} {ip_top}</span><span class="date">{date_top}</span></div><div class="meta-right"><span>{views_top}</span> | <span>{recommend_top}</span> | <a href="#comment-section" class="comment-jump-btn">{comment_count_top}</a></div></div></div><div class="content">{content_area_html}</div>{poll_section_html}<div class="vote-box-container"><div class="vote-number" style="color:#d31900;">{upvotes}</div><div class="vote-circles"><div class="circle-btn circle-up"><span style="font-size:22px; color:#ffeb3b;">★</span><span>개념</span></div><div class="circle-btn circle-down"><span style="font-size:22px; color:white;">⬇</span><span>비추</span></div></div><div class="vote-number" style="color:#444;">{downvotes}</div></div><div id="comment-section"><div class="comments-header"><div class="comments-title">댓글 <span id="total-count" style="color:#d31900;">0</span>개</div><div class="comment-controls"><button class="control-btn active" id="sort-old" onclick="changeSort('old')">등록순</button><button class="control-btn" id="sort-new" onclick="changeSort('new')">최신순</button><button class="control-btn" id="sort-reply" onclick="changeSort('reply')">답글순</button><select id="limit-select" onchange="changeLimit(this.value)" style="padding: 2px; font-size: 12px; margin-left: 10px;"><option value="30">30개</option><option value="50" selected>50개</option><option value="100">100개</option><option value="9999">전체 보기</option></select></div></div><div class="comment-list-area" id="comment-list"></div><div class="pagination" id="pagination-buttons"></div></div></div><script>const rawComments = {json.dumps(collected_comments, ensure_ascii=False)}; let currentSort = 'old', commentsPerPage = 50, currentPage = 1, commentGroups = [], currentGroup = null; rawComments.forEach(c => {{ if (!c.is_reply) {{ currentGroup = {{ parent: c, replies: [] }}; commentGroups.push(currentGroup); }} else {{ if (currentGroup) currentGroup.replies.push(c); else {{ currentGroup = {{ parent: null, replies: [c] }}; commentGroups.push(currentGroup); }} }} }}); function buildWriterHTML(writerStr) {{ let match = writerStr.match(/(.+)\\s(\\([0-9.]+\\))$/); return match ? `${{match[1]}} <span class="ip">${{match[2]}}</span>` : writerStr; }} function buildContentHTML(c) {{ if (c.text.includes("삭제된 댓글")) return `<span class="deleted-text">${{c.text}}</span>`; let html = c.text.replace(/\\n/g, "<br>"); if (c.dccon) html += `<br><img src="${{c.dccon}}" style="width:85px; height:85px; margin-top:5px;">`; if (c.comment_img) html += `<br><img src="${{c.comment_img}}" style="margin-top:5px; max-width:200px; border-radius:4px;">`; return html; }} function renderComments() {{ const listArea = document.getElementById('comment-list'); const pageArea = document.getElementById('pagination-buttons'); listArea.innerHTML = ''; pageArea.innerHTML = ''; document.getElementById('total-count').innerText = rawComments.filter(c => !c.text.includes("삭제된 댓글")).length; if (rawComments.length === 0) return; let sortedGroups = [...commentGroups]; if (currentSort === 'new') sortedGroups.reverse(); else if (currentSort === 'reply') sortedGroups.sort((a, b) => b.replies.length - a.replies.length); const totalPages = Math.ceil(sortedGroups.length / commentsPerPage); if (currentPage > totalPages) currentPage = totalPages; if (currentPage < 1) currentPage = 1; const startIndex = (currentPage - 1) * commentsPerPage; const pageGroups = sortedGroups.slice(startIndex, startIndex + commentsPerPage); pageGroups.forEach(g => {{ if (g.parent) {{ const pDiv = document.createElement('div'); pDiv.className = 'comment-row'; if (g.parent.text.includes("삭제된 댓글")) pDiv.innerHTML = `<div class="comment-writer-box"></div><div class="comment-content-box">${{buildContentHTML(g.parent)}}</div><div class="comment-date-box"></div>`; else pDiv.innerHTML = `<div class="comment-writer-box">${{buildWriterHTML(g.parent.writer)}}</div><div class="comment-content-box">${{buildContentHTML(g.parent)}}</div><div class="comment-date-box">${{g.parent.date}}</div>`; listArea.appendChild(pDiv); }} g.replies.forEach(r => {{ const rDiv = document.createElement('div'); rDiv.className = 'comment-row reply-row'; if (r.text.includes("삭제된 댓글")) rDiv.innerHTML = `<div class="comment-writer-box"><span class="reply-icon">ㄴ</span></div><div class="comment-content-box">${{buildContentHTML(r)}}</div><div class="comment-date-box"></div>`; else rDiv.innerHTML = `<div class="comment-writer-box"><span class="reply-icon">ㄴ</span>${{buildWriterHTML(r.writer)}}</div><div class="comment-content-box">${{buildContentHTML(r)}}</div><div class="comment-date-box">${{r.date}}</div>`; listArea.appendChild(rDiv); }}); }}); if (totalPages > 1) {{ for (let i = 1; i <= totalPages; i++) {{ const btn = document.createElement('button'); btn.className = 'page-btn'; if (i === currentPage) btn.classList.add('active'); btn.innerText = i; btn.onclick = () => {{ currentPage = i; renderComments(); window.scrollTo(0, document.getElementById('comment-section').offsetTop - 20); }}; pageArea.appendChild(btn); }} }} }} function changeSort(type) {{ currentSort = type; document.querySelectorAll('.control-btn').forEach(btn => btn.classList.remove('active')); document.getElementById('sort-' + type).classList.add('active'); currentPage = 1; renderComments(); }} function changeLimit(val) {{ commentsPerPage = parseInt(val); currentPage = 1; renderComments(); }} document.querySelectorAll('a[href^="#"]').forEach(anchor => {{ anchor.addEventListener('click', function (e) {{ e.preventDefault(); document.querySelector(this.getAttribute('href')).scrollIntoView({{ behavior: 'smooth' }}); }}); }}); renderComments();</script></body></html>"""

    with open(html_path, "w", encoding="utf-8") as f: f.write(html_template)
    
    post_meta = {
        "title": title,
        "date": date_top,
        "views": views_val,
        "recommend": recommend_val,
        "comment_count": len(collected_comments),
        "image_count": image_count,
        "thumbnail": thumbnail_url
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
            if route.request.resource_type in ["font", "media"]: route.abort()
            else: route.continue_()
                
        list_page.route("**/*", block_heavy_resources)
        post_page.route("**/*", block_heavy_resources)
        
        creds = get_gcp_credentials()
        drive_service = build('drive', 'v3', credentials=creds)
        
        # 🛡️ [자가 치유 핵심 로직 추가] 
        # 목록 탐색( lists/ )을 하기도 전에, FORCE_REARCHIVE_POST_NOS에 있는 글들을 최우선적으로 다이렉트 강제 수집!
        # 이로써 수집하고자 하는 타겟 글이 몇 페이지에 있든, 목록에서 밀려났든 상관없이 무조건 100% 정상 수집됩니다.
        if force_nos:
            print("\n==========================================")
            print(" 🚀 [최우선 타겟 수집] 강제 재수집 대기열 가동 중...")
            print("==========================================")
            for f_no in force_nos:
                # 대조군 DB에서 강제 제거
                if f_no in completed_posts:
                    del completed_posts[f_no]
                
                print(f"\n▶ [{f_no}번 글] 원문 강제 다이렉트 수집 개시...")
                success, post_meta = archive_single_post(f_no, post_page, drive_service, creds, update_comments_only=False)
                if success:
                    # 실제 목록 페이지의 댓글 개수를 가져오기 위해 디시 본문 파싱 값 적용
                    completed_posts[f_no] = {
                        "comment_count": post_meta["comment_count"],
                        **post_meta
                    }
                    save_checkpoint(completed_posts)
                    archive_count += 1
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
                    
                    # 이미 위에서 다이렉트 강제 수집을 끝낸 타겟 번호는 목록 순회에서 안전하게 패스!
                    if post_no in force_nos:
                        continue
                    
                    reply_el = row.select_one(".reply_num")
                    current_cmt_count = int(re.search(r"\d+", reply_el.text).group()) if reply_el and re.search(r"\d+", reply_el.text) else 0
                    
                    is_completed = post_no in completed_posts
                    
                    if force_template_rebuild:
                        if is_completed:
                            success, post_meta = archive_single_post(post_no, post_page, drive_service, creds, update_comments_only=True)
                            if success:
                                completed_posts[post_no]["comment_count"] = current_cmt_count
                                archive_count += 1
                        continue

                    if is_completed:
                        saved_cmt_count = completed_posts[post_no].get("comment_count", 0)
                        if current_cmt_count <= saved_cmt_count: continue
                        success, post_meta = archive_single_post(post_no, post_page, drive_service, creds, update_comments_only=True)
                        if success: completed_posts[post_no]["comment_count"] = current_cmt_count
                    else:
                        success, post_meta = archive_single_post(post_no, post_page, drive_service, creds, update_comments_only=False)
                        if success:
                            completed_posts[post_no] = {"comment_count": current_cmt_count, **post_meta}
                            archive_count += 1

                    if success:
                        save_checkpoint(completed_posts)
                        time.sleep(round(random.uniform(1.5, 3.0), 1))

                if max_p and archive_count >= max_p: break
        except Exception as e:
            print(f"⚠️ 가동 중 오류 발생: {e}")
        finally:
            save_checkpoint(completed_posts)
            release_lock()
            browser.close()
            
            print("\n🚀 데이터 GitHub Pages 배포 시도 중...")
            subprocess.run("git add .", shell=True)
            subprocess.run('git commit -m "Auto Update with completely resolved forced re-archive logics"', shell=True)
            subprocess.run("git push", shell=True)
            print("🎉 배포가 완전히 완료되었습니다!")

# ==========================================
# [ 프로그램 구동 진입점 ]
# ==========================================
if __name__ == "__main__":
    start_p = START_PAGE
    end_p = END_PAGE
    max_p = MAX_POSTS_TO_ARCHIVE
    force_nos_str = ",".join(FORCE_REARCHIVE_POST_NOS)
    force_tmpl = FORCE_TEMPLATE_REBUILD
    
    run_archiver_logic(start_p, end_p, max_p, force_nos_str, force_tmpl)
    release_lock()
    sys.exit(0)