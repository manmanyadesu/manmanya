import os
import re
import time
import json
import random
import shutil
import requests
import httplib2
import subprocess
import socket
import google_auth_httplib2
from PIL import Image
from concurrent.futures import ThreadPoolExecutor, as_completed
from playwright.sync_api import sync_playwright
from bs4 import BeautifulSoup

from googleapiclient.discovery import build
from googleapiclient.http import MediaFileUpload
from google.oauth2.credentials import Credentials
from google_auth_oauthlib.flow import InstalledAppFlow
from google.auth.transport.requests import Request

# 구글 API 무한 대기 방지용 20초 강제 타임아웃
socket.setdefaulttimeout(20)

# ==========================================
# [ 설정 영역 ] 
# ==========================================
GALLERY_ID = "comic_new6"
START_PAGE = 1
END_PAGE = 2
MAX_POSTS_TO_ARCHIVE = 2  # 테스트용 1개 수집

# 강제 재수집 대기열
FORCE_REARCHIVE_POST_NOS = ["4600069"]
SCOPES = ['https://www.googleapis.com/auth/drive.file']
BASE_DIR = "./archive"
CHECKPOINT_FILE = f"{BASE_DIR}/completed_posts.json"
LOCK_FILE = f"{BASE_DIR}/crawler.lock"
# ==========================================

os.makedirs(BASE_DIR, exist_ok=True)

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
            print(f"\n⚠️ 이미 가동 중입니다.")
            exit()
        else: os.remove(LOCK_FILE)
    except Exception:
        try: os.remove(LOCK_FILE)
        except Exception: pass

with open(LOCK_FILE, "w") as f: f.write(str(os.getpid()))

def get_gcp_credentials():
    creds = None
    if os.path.exists('token.json'):
        creds = Credentials.from_authorized_user_file('token.json', SCOPES)
    if not creds or not creds.valid:
        if creds and creds.expired and creds.refresh_token: creds.refresh(Request())
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
    if os.path.exists(LOCK_FILE): os.remove(LOCK_FILE)

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

def upload_file_to_drive(drive_service, file_path, folder_id, thread_http):
    filename = os.path.basename(file_path)
    file_metadata = {'name': filename, 'parents': [folder_id]}
    mime_type = "image/jpeg"
    if filename.endswith(".gif"): mime_type = "image/gif"
    elif filename.endswith(".png"): mime_type = "image/png"
    elif filename.endswith(".html"): mime_type = "text/html"
    
    media = MediaFileUpload(file_path, mimetype=mime_type, resumable=False)
    file = drive_service.files().create(body=file_metadata, media_body=media, fields='id').execute(http=thread_http)
    file_id = file.get('id')
    direct_link = f"https://lh3.googleusercontent.com/d/{file_id}"
    return file_id, direct_link

def archive_single_post(post_no, page, drive_service, creds):
    target_url = f"https://gall.dcinside.com/board/view/?id={GALLERY_ID}&no={post_no}"
    save_dir = f"{BASE_DIR}/{post_no}"
    img_dir = f"{save_dir}/images"
    os.makedirs(img_dir, exist_ok=True)
    
    html_path = f"{save_dir}/saved_post.html"
    content_area_html = ""
    has_poll = False
    
    title = f"만화갤러리 {post_no}번 글"
    writer_top, ip_top, date_top = "ㅇㅇ", "", ""
    views_top, recommend_top, comment_count_top = "조회 0", "추천 0", "댓글 0"
    upvotes, downvotes = "0", "0"
    views_val, recommend_val = 0, 0
    thumbnail_url = ""
    uploaded_mapping = {}

    print(f"\n▶ [{post_no}번 글] 디시 만화 수집 및 압축 구글 드라이브 전송 시작")
    try:
        # ⚠️ 타임아웃 20초 및 강제 돔파싱 진입
        page.goto(target_url, timeout=20000, wait_until="domcontentloaded")
        page.evaluate("window.scrollTo(0, document.body.scrollHeight)")
        time.sleep(1.0)
    except Exception as e:
        print(f"      ⚠️ 페이지 이동 대기 초과 (수집 강제 속행): {e}")

    full_html = page.content()
    soup = BeautifulSoup(full_html, "html.parser")
    
    if not soup.find("div", class_="write_div"):
        print(f" ❌ [{post_no}번 글] 접근 불가능한 글입니다.")
        return False, None

    title_el = soup.find("span", class_="title_subject")
    title = title_el.text.strip() if title_el else f"만화갤러리 {post_no}번 글"
    content_area = soup.find("div", class_="write_div")
    
    writer_el = soup.select_one(".gall_writer .nickname")
    writer_top = writer_el.text.strip() if writer_el else "ㅇㅇ"
    ip_el = soup.select_one(".gall_writer .ip")
    ip_top = ip_el.text.strip() if ip_el else ""
    date_el = soup.select_one(".gall_date")
    date_top = date_el.text.strip() if date_el else ""
    
    views_el = soup.select_one(".gall_count")
    views_top = views_el.text.strip() if views_el else "조회 0"
    if views_el:
        v_match = re.search(r"\d+", views_el.text)
        if v_match: views_val = int(v_match.group())
        
    recommend_el = soup.select_one(".gall_reply_num")
    recommend_top = recommend_el.text.strip() if recommend_el else "추천 0"
    comment_count_el = soup.select_one(".gall_comment")
    comment_count_top = comment_count_el.text.strip() if comment_count_el else "댓글 0"
    
    up_el = soup.select_one(".up_num")
    upvotes = up_el.text.strip() if up_el else "0"
    if up_el:
        r_match = re.search(r"\d+", up_el.text)
        if r_match: recommend_val = int(r_match.group())
        
    down_el = soup.select_one(".down_num")
    downvotes = down_el.text.strip() if down_el else "0"
    
    img_tags = content_area.find_all("img") if content_area else []
    img_session = requests.Session()
    img_headers = {"User-Agent": "Mozilla/5.0", "Referer": target_url}
    
    folder_id = get_or_create_drive_folder(drive_service)

    # 이미지 고밀도 압축 및 스레드 세이프 구글 드라이브 업로드
    def upload_worker(idx, img_el):
        img_url = img_el.get("data-original") or img_el.get("data-src") or img_el.get("src")
        if not img_url: return idx, None
        try:
            img_res = img_session.get(img_url, headers=img_headers, timeout=10)
            if img_res.status_code == 200:
                ext = img_url.split(".")[-1].split("?")[0].lower()
                if ext not in ["jpg", "jpeg", "png", "gif", "webp"]: ext = "jpg"
                
                raw_path = f"{img_dir}/raw_{idx+1}.{ext}"
                with open(raw_path, "wb") as f: f.write(img_res.content)
                
                compressed_path = f"{img_dir}/manga_{idx+1}.jpg"
                compress_image(raw_path, compressed_path)
                
                thread_http = google_auth_httplib2.AuthorizedHttp(creds, http=httplib2.Http())
                file_id, direct_link = upload_file_to_drive(drive_service, compressed_path, folder_id, thread_http)
                
                img_el["src"] = direct_link
                if img_el.has_attr("data-original"): del img_el["data-original"]
                if img_el.has_attr("data-src"): del img_el["data-src"]
                
                os.remove(raw_path)
                os.remove(compressed_path)
                return idx, file_id
        except Exception: pass
        return idx, None

    with ThreadPoolExecutor(max_workers=10) as executor:
        futures = [executor.submit(upload_worker, i, el) for i, el in enumerate(img_tags)]
        for f in as_completed(futures):
            idx, file_id = f.result()
            if file_id: uploaded_mapping[f"manga_{idx+1}.jpg"] = file_id
            
    content_area_html = str(content_area) if content_area else ""

    if uploaded_mapping:
        first_key = sorted(list(uploaded_mapping.keys()))[0]
        thumbnail_url = f"https://lh3.googleusercontent.com/d/{uploaded_mapping[first_key]}"

    # 투표 스냅샷 백업
    poll_frame = next((f for f in page.frames if "poll" in f.url), None)
    if poll_frame:
        try:
            poll_wrap_locator = poll_frame.locator(".vote_wrap")
            poll_wrap_locator.wait_for(state="visible", timeout=3000)
            poll_frame.click(".btn_votepreview", timeout=2000)
            time.sleep(1)
            temp_vote_path = f"{img_dir}/vote_status.png"
            poll_wrap_locator.screenshot(path=temp_vote_path)
            thread_http = google_auth_httplib2.AuthorizedHttp(creds, http=httplib2.Http())
            _, poll_drive_url = upload_file_to_drive(drive_service, temp_vote_path, folder_id, thread_http)
            os.remove(temp_vote_path)
            poll_section_html = f"""<div class="poll-container"><h3>🗳️ 본문 투표 백업</h3><img src="{poll_drive_url}" style="max-width:100%; display:block; margin:0 auto;"></div>"""
            has_poll = True
        except: pass

    # 댓글 수집 및 디시콘 드라이브화
    collected_comments = []
    seen_comment_ids = set()
    current_cmt_page = 1

    def parse_visible_comments(page_html):
        c_soup = BeautifulSoup(page_html, "html.parser")
        comment_items = c_soup.select("ul.cmt_list li")
        
        for item in comment_items:
            c_id = item.get("id", "")
            if not c_id or not (c_id.startswith("comment_") or c_id.startswith("reply_")): continue
            if c_id in seen_comment_ids: continue
            seen_comment_ids.add(c_id)
            
            is_reply = False
            if item.find_parent("ul", class_=re.compile("reply")) or c_id.startswith("reply_") or "reply" in "".join(item.get("class", [])).lower():
                is_reply = True
                
            for nested_reply in item.find_all("ul", class_=re.compile("reply")): nested_reply.extract()

            if "cmt_blank" in " ".join(item.get("class", [])).lower() or "삭제된" in item.text:
                collected_comments.append({"writer": "", "text": "삭제된 댓글입니다.", "is_reply": is_reply, "dccon": "", "comment_img": "", "date": ""})
                continue
            
            writer = item.find("span", class_="nickname")
            ip_tag = item.find("span", class_="ip")
            full_writer = f"{writer.text.strip() if writer else 'ㅇㅇ'} {ip_tag.text.strip() if ip_tag else ''}".strip()
            
            txt_element = item.find("p", class_="usertxt")
            if txt_element:
                for br in txt_element.find_all("br"): br.replace_with("\n")
                txt = txt_element.text.strip()
            else: txt = ""
            
            date_element = item.find("span", class_="date_time") or item.find("span", class_="date")
            date_text = date_element.text.strip() if date_element else ""
            
            dccon_src = ""
            comment_img_src = ""
            
            dccon = item.find("img", class_=re.compile("dccon"))
            if dccon and dccon.get("src"):
                dccon_url = dccon.get("src")
                try:
                    dccon_res = img_session.get(dccon_url, headers=img_headers)
                    ext = dccon_url.split(".")[-1].split("?")[0].lower()
                    temp_path = f"{img_dir}/dccon_{c_id}.{'gif' if ext not in ['jpg','png','gif'] else ext}"
                    with open(temp_path, "wb") as f: f.write(dccon_res.content)
                    thread_http = google_auth_httplib2.AuthorizedHttp(creds, http=httplib2.Http())
                    _, dccon_src = upload_file_to_drive(drive_service, temp_path, folder_id, thread_http)
                    os.remove(temp_path)
                except: pass
            
            for img_el in item.find_all("img"):
                img_src = img_el.get("src")
                if img_src and "dccon" not in img_src and "option_icon" not in img_src:
                    try:
                        c_img_res = img_session.get(img_src, headers=img_headers)
                        ext = img_src.split(".")[-1].split("?")[0].lower()
                        temp_path = f"{img_dir}/cmt_{c_id}.{'jpg' if ext not in ['jpg','png','gif'] else ext}"
                        with open(temp_path, "wb") as f: f.write(c_img_res.content)
                        thread_http = google_auth_httplib2.AuthorizedHttp(creds, http=httplib2.Http())
                        _, comment_img_src = upload_file_to_drive(drive_service, temp_path, folder_id, thread_http)
                        os.remove(temp_path)
                    except: pass
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
                time.sleep(1.5)
                current_cmt_page = next_page_num
                clicked = True
                break
        if not clicked: break

    poll_section_html = f"""<div class="poll-container"><h3>🗳️ 본문 투표 백업</h3><img src="{poll_drive_url}" style="max-width:100%; display:block; margin:0 auto;"></div>""" if has_poll else ""

    html_template = f"""<!DOCTYPE html><html lang="ko"><head><meta charset="utf-8"><title>{title} - 아카이브</title><style>body {{ font-family: 'Malgun Gothic', sans-serif; margin: 40px; background-color: #f5f6f7; color: #333; }}.container {{ max-width: 900px; margin: 0 auto; background: white; padding: 30px; border-radius: 8px; box-shadow: 0 4px 6px rgba(0,0,0,0.1); }}.post-header {{ border-bottom: 1px solid #ccc; padding-bottom: 15px; margin-bottom: 20px; }}.post-title {{ font-size: 22px; font-weight: bold; color: #222; margin-bottom: 12px; }}.post-meta-wrap {{ display: flex; justify-content: space-between; font-size: 13px; color: #666; }}.meta-left .writer {{ font-weight: bold; color: #333; margin-right: 10px; }}.content {{ line-height: 1.8; font-size: 16px; margin-top: 30px; padding-bottom: 40px; }}.content img {{ max-width: 100%; height: auto; display: block; margin: 15px auto; }}.vote-box-container {{ border: 1px solid #ddd; padding: 30px; border-radius: 8px; margin: 40px auto; max-width: 400px; display: flex; justify-content: center; align-items: center; gap: 30px; background: #fff; }}.vote-number {{ font-size: 22px; font-weight: bold; width: 40px; text-align: center; }}.vote-circles {{ display: flex; gap: 15px; }}.circle-btn {{ width: 80px; height: 80px; border-radius: 50%; display: flex; flex-direction: column; justify-content: center; align-items: center; font-weight: bold; color: white; font-size: 14px; box-shadow: 0 2px 5px rgba(0,0,0,0.2); }}.circle-up {{ background: #3b5998; }} .circle-down {{ background: #a5a5a5; }}.comments-header {{ display: flex; justify-content: space-between; align-items: center; border-bottom: 1px solid #3b5998; padding-bottom: 10px; margin-top: 40px; }}.comments-title {{ font-size: 16px; font-weight: bold; color: #3b5998; }}.control-btn {{ background: none; border: none; font-size: 13px; cursor: pointer; font-weight: bold; color: #999; margin-right: 5px; }}.control-btn.active {{ color: #3b5998; }}.comment-list-area {{ border-top: 1px solid #3b5998; }}.comment-row {{ display: flex; border-bottom: 1px solid #e2e2e2; padding: 12px 0; align-items: flex-start; }}.comment-writer-box {{ width: 160px; flex-shrink: 0; padding: 0 10px; color: #333; font-weight: bold; font-size: 13px; word-break: break-all; }}.comment-writer-box span.ip {{ color: #999; font-weight: normal; font-size: 11px; }}.comment-content-box {{ flex-grow: 1; padding: 0 10px; font-size: 13px; color: #333; word-break: break-all; }}.comment-content-box img {{ max-width: 200px; border-radius: 4px; display: block; margin-top: 5px; }}.comment-date-box {{ width: 100px; flex-shrink: 0; text-align: right; color: #999; font-size: 12px; padding-right: 10px; }}.reply-row {{ background-color: #f9f9f9; padding-left: 0; border-left: 3px solid #ddd; }}.reply-row .comment-writer-box {{ width: 180px; padding-left: 35px; position: relative; }}.reply-icon {{ position: absolute; left: 12px; top: 0; color: #3b5998; font-weight: 900; }}.deleted-text {{ color: #aaa; font-style: normal; }}.pagination {{ display: flex; justify-content: center; gap: 5px; margin-top: 20px; }}.page-btn {{ border: 1px solid #ddd; background: white; padding: 5px 10px; cursor: pointer; border-radius: 3px; font-size: 13px; }}.page-btn.active {{ background: #3b5998; color: white; font-weight: bold; }}</style></head><body><div class="container"><div class="post-header"><div class="post-title">{title}</div><div class="post-meta-wrap"><div class="meta-left"><span class="writer">{writer_top} {ip_top}</span><span class="date">{date_top}</span></div><div class="meta-right"><span>{views_top}</span> | <span>{recommend_top}</span> | <span>{comment_count_top}</span></div></div></div><div class="content">{content_area_html}</div>{poll_section_html}<div class="vote-box-container"><div class="vote-number" style="color:#d31900;">{upvotes}</div><div class="vote-circles"><div class="circle-btn circle-up"><span style="font-size:22px; color:#ffeb3b;">★</span><span>개념</span></div><div class="circle-btn circle-down"><span style="font-size:22px; color:white;">⬇</span><span>비추</span></div></div><div class="vote-number" style="color:#444;">{downvotes}</div></div><div id="comment-section"><div class="comments-header"><div class="comments-title">댓글 <span id="total-count" style="color:#d31900;">0</span>개</div><div class="comment-controls"><button class="control-btn active" id="sort-old" onclick="changeSort('old')">등록순</button><button class="control-btn" id="sort-new" onclick="changeSort('new')">최신순</button><button class="control-btn" id="sort-reply" onclick="changeSort('reply')">답글순</button><select id="limit-select" onchange="changeLimit(this.value)" style="padding: 2px; font-size: 12px; margin-left: 10px;"><option value="30">30개</option><option value="50" selected>50개</option><option value="100">100개</option><option value="9999">전체 보기</option></select></div></div><div class="comment-list-area" id="comment-list"></div><div class="pagination" id="pagination-buttons"></div></div></div><script>const rawComments = {json.dumps(collected_comments, ensure_ascii=False)}; let currentSort = 'old', commentsPerPage = 50, currentPage = 1, commentGroups = [], currentGroup = null; rawComments.forEach(c => {{ if (!c.is_reply) {{ currentGroup = {{ parent: c, replies: [] }}; commentGroups.push(currentGroup); }} else {{ if (currentGroup) currentGroup.replies.push(c); else {{ currentGroup = {{ parent: null, replies: [c] }}; commentGroups.push(currentGroup); }} }} }}); function buildWriterHTML(writerStr) {{ let match = writerStr.match(/(.+)\\s(\\([0-9.]+\\))$/); return match ? `${{match[1]}} <span class="ip">${{match[2]}}</span>` : writerStr; }} function buildContentHTML(c) {{ if (c.text.includes("삭제된 댓글")) return `<span class="deleted-text">${{c.text}}</span>`; let html = c.text.replace(/\\n/g, "<br>"); if (c.dccon) html += `<br><img src="${{c.dccon}}" style="width:85px; height:85px; margin-top:5px;">`; if (c.comment_img) html += `<br><img src="${{c.comment_img}}" style="margin-top:5px; max-width:200px; border-radius:4px;">`; return html; }} function renderComments() {{ const listArea = document.getElementById('comment-list'); const pageArea = document.getElementById('pagination-buttons'); listArea.innerHTML = ''; pageArea.innerHTML = ''; document.getElementById('total-count').innerText = rawComments.filter(c => !c.text.includes("삭제된 댓글")).length; if (rawComments.length === 0) return; let sortedGroups = [...commentGroups]; if (currentSort === 'new') sortedGroups.reverse(); else if (currentSort === 'reply') sortedGroups.sort((a, b) => b.replies.length - a.replies.length); const totalPages = Math.ceil(sortedGroups.length / commentsPerPage); if (currentPage > totalPages) currentPage = totalPages; if (currentPage < 1) currentPage = 1; const startIndex = (currentPage - 1) * commentsPerPage; const pageGroups = sortedGroups.slice(startIndex, startIndex + commentsPerPage); pageGroups.forEach(g => {{ if (g.parent) {{ const pDiv = document.createElement('div'); pDiv.className = 'comment-row'; if (g.parent.text.includes("삭제된 댓글")) pDiv.innerHTML = `<div class="comment-writer-box"></div><div class="comment-content-box">${{buildContentHTML(g.parent)}}</div><div class="comment-date-box"></div>`; else pDiv.innerHTML = `<div class="comment-writer-box">${{buildWriterHTML(g.parent.writer)}}</div><div class="comment-content-box">${{buildContentHTML(g.parent)}}</div><div class="comment-date-box">${{g.parent.date}}</div>`; listArea.appendChild(pDiv); }} g.replies.forEach(r => {{ const rDiv = document.createElement('div'); rDiv.className = 'comment-row reply-row'; if (r.text.includes("삭제된 댓글")) rDiv.innerHTML = `<div class="comment-writer-box"><span class="reply-icon">ㄴ</span></div><div class="comment-content-box">${{buildContentHTML(r)}}</div><div class="comment-date-box"></div>`; else rDiv.innerHTML = `<div class="comment-writer-box"><span class="reply-icon">ㄴ</span>${{buildWriterHTML(r.writer)}}</div><div class="comment-content-box">${{buildContentHTML(r)}}</div><div class="comment-date-box">${{r.date}}</div>`; listArea.appendChild(rDiv); }}); }}); if (totalPages > 1) {{ for (let i = 1; i <= totalPages; i++) {{ const btn = document.createElement('button'); btn.className = 'page-btn'; if (i === currentPage) btn.classList.add('active'); btn.innerText = i; btn.onclick = () => {{ currentPage = i; renderComments(); window.scrollTo(0, document.getElementById('comment-section').offsetTop - 20); }}; pageArea.appendChild(btn); }} }} }} function changeSort(type) {{ currentSort = type; document.querySelectorAll('.control-btn').forEach(btn => btn.classList.remove('active')); document.getElementById('sort-' + type).classList.add('active'); currentPage = 1; renderComments(); }} function changeLimit(val) {{ commentsPerPage = parseInt(val); currentPage = 1; renderComments(); }} document.querySelectorAll('a[href^="#"]').forEach(anchor => {{ anchor.addEventListener('click', function (e) {{ e.preventDefault(); document.querySelector(this.getAttribute('href')).scrollIntoView({{ behavior: 'smooth' }}); }}); }}); renderComments();</script></body></html>"""

    with open(html_path, "w", encoding="utf-8") as f: f.write(html_template)
    
    post_meta = {
        "title": title,
        "date": date_top,
        "views": views_val,
        "recommend": recommend_val,
        "comment_count": current_cmt_count,
        "image_count": len(img_tags),
        "thumbnail": thumbnail_url
    }

    print(" 🗑️ [0MB 다이어트] 구글 드라이브 백업 완료로 로컬 임시 이미지를 삭제합니다.")
    shutil.rmtree(img_dir, ignore_errors=True)
    
    print(f" ✅ [{post_no}번 글] 소스 변환 및 드라이브 영구 저장 완료!")
    return True, post_meta

# 메인 가동부
with sync_playwright() as p:
    browser = p.chromium.launch(headless=True)
    list_page = browser.new_page()
    post_page = browser.new_page()
    
    # ⚠️ 브라우저 경고창(Alert/Confirm) 팝업을 감지하자마자 자동으로 무시하고 닫아 무한 정체를 원천 해결합니다!
    list_page.on("dialog", lambda dialog: dialog.dismiss())
    post_page.on("dialog", lambda dialog: dialog.dismiss())
    
    def block_heavy_resources(route):
        if route.request.resource_type in ["font", "media"]: route.abort()
        else: route.continue_()
            
    list_page.route("**/*", block_heavy_resources)
    post_page.route("**/*", block_heavy_resources)
    
    creds = get_gcp_credentials()
    drive_service = build('drive', 'v3', credentials=creds)
    
    completed_posts = load_checkpoint()
    archive_count = 0 
    
    try:
        for page_num in range(START_PAGE, END_PAGE + 1):
            print(f"\n==========================================")
            print(f" 📖 개념글 {page_num}페이지 탐색 중...")
            list_page.goto(f"https://gall.dcinside.com/board/lists/?id={GALLERY_ID}&exception_mode=recommend&page={page_num}")
            list_page.wait_for_load_state("domcontentloaded")
            
            soup = BeautifulSoup(list_page.content(), "html.parser")
            for row in soup.select("tr.us-post:not(.notice)"):
                if MAX_POSTS_TO_ARCHIVE and archive_count >= MAX_POSTS_TO_ARCHIVE: break
                    
                no_el = row.select_one(".gall_num")
                if not no_el or not no_el.text.strip().isdigit(): continue
                post_no = no_el.text.strip()
                
                reply_el = row.select_one(".reply_num")
                current_cmt_count = int(re.search(r"\d+", reply_el.text).group()) if reply_el and re.search(r"\d+", reply_el.text) else 0
                
                if post_no in FORCE_REARCHIVE_POST_NOS:
                    if post_no in completed_posts: del completed_posts[post_no]
                    is_completed = False
                else:
                    is_completed = post_no in completed_posts
                
                if is_completed:
                    saved_cmt_count = completed_posts[post_no].get("comment_count", 0)
                    if current_cmt_count <= saved_cmt_count: continue
                    success, post_meta = archive_single_post(post_no, post_page, drive_service, creds)
                    if success: completed_posts[post_no]["comment_count"] = current_cmt_count
                else:
                    success, post_meta = archive_single_post(post_no, post_page, drive_service, creds)
                    if success:
                        completed_posts[post_no] = {"comment_count": current_cmt_count, **post_meta}

                if success:
                    save_checkpoint(completed_posts)
                    archive_count += 1
                    
                    sleep_time = round(random.uniform(1.5, 3.0), 1)
                    time.sleep(sleep_time)

            if MAX_POSTS_TO_ARCHIVE and archive_count >= MAX_POSTS_TO_ARCHIVE: break
                
    except KeyboardInterrupt:
        print("\n🛑 작업이 중단되었습니다.")
    finally:
        save_checkpoint(completed_posts)
        release_lock()
        browser.close()
        
        print("\n🚀 수집된 아카이브 데이터를 GitHub Pages로 전송합니다...")
        subprocess.run("git add .", shell=True)
        subprocess.run('git commit -m "Auto Update with compressed images"', shell=True)
        subprocess.run("git push", shell=True)
        print("🎉 배포가 성공적으로 완료되었습니다!")