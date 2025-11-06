import discord
from discord.ext import commands
import anthropic
from dotenv import load_dotenv
import os
import json
import asyncio
import requests
import subprocess
from typing import Iterator
import tempfile
import shutil
import base64
from datetime import datetime, timedelta
import re
import pytz
import pyvts

load_dotenv()
DISCORD_TOKEN = os.getenv("DISCORD_BOT_TOKEN_F")
ANTHROPIC_API_KEY = os.getenv("ANTHROPIC_API_KEY")
group_id = os.getenv("group_id")
tts_api_key = os.getenv("tts_api_key")

client = anthropic.Anthropic(api_key=ANTHROPIC_API_KEY)

# Auto-detect FFmpeg path
def find_ffmpeg():
    """시스템에서 FFmpeg 실행파일을 찾습니다."""
    # 1. shutil.which로 먼저 시도
    ffmpeg_path = shutil.which("ffmpeg")
    if ffmpeg_path and os.path.isfile(ffmpeg_path):
        try:
            result = subprocess.run([ffmpeg_path, "-version"], 
                                  capture_output=True, text=True, timeout=5)
            if result.returncode == 0:
                print(f"✅ FFmpeg found via PATH: {ffmpeg_path}")
                return ffmpeg_path
        except:
            pass
    
    # 2. Use PowerShell Get-Command
    try:
        result = subprocess.run([
            "powershell", "-Command", 
            "(Get-Command ffmpeg -ErrorAction SilentlyContinue).Source"
        ], capture_output=True, text=True, timeout=10)
        
        if result.returncode == 0 and result.stdout.strip():
            ffmpeg_path = result.stdout.strip()
            if os.path.isfile(ffmpeg_path):
                print(f"✅ FFmpeg found via PowerShell: {ffmpeg_path}")
                return ffmpeg_path
    except:
        pass
    
    # 3. Check common installation paths
    possible_paths = [
        r"C:\ffmpeg\bin\ffmpeg.exe",
        r"C:\Program Files\ffmpeg\bin\ffmpeg.exe",
        r"C:\Program Files (x86)\ffmpeg\bin\ffmpeg.exe",
    ]
    
    # 4. Add winget installation paths
    winget_base = os.path.join(os.path.expanduser("~"), "AppData", "Local", "Microsoft", "WinGet", "Packages")
    if os.path.exists(winget_base):
        try:
            for folder in os.listdir(winget_base):
                if "ffmpeg" in folder.lower():
                    package_path = os.path.join(winget_base, folder)
                    for root, dirs, files in os.walk(package_path):
                        if "ffmpeg.exe" in files:
                            full_path = os.path.join(root, "ffmpeg.exe")
                            possible_paths.append(full_path)
        except:
            pass
    
    # 5. Test all paths
    for path in possible_paths:
        if path and os.path.isfile(path):
            try:
                result = subprocess.run([path, "-version"], 
                                      capture_output=True, text=True, timeout=5)
                if result.returncode == 0:
                    print(f"✅ FFmpeg found at: {path}")
                    return path
            except:
                continue
    
    print("⚠️ FFmpeg not found in any common locations")
    return None

# Set FFmpeg path
FFMPEG_PATH = find_ffmpeg()
if FFMPEG_PATH:
    discord.FFmpegPCMAudio.executable = FFMPEG_PATH
else:
    print("⚠️ FFmpeg not found. Voice functionality may not work.")

# Chat memory
chat_memory_file = "chat_memory_fiona.json"
chat_memory_fiona = {}

# Schedule management system
schedule_file = "fiona_schedule.json"
schedules = {}

# Korean timezone setup
KST = pytz.timezone('Asia/Seoul')

# VTube Studio 설정
VTS_PLUGIN_INFO = {
    "plugin_name": "Fiona VTS Extension",
    "developer": "Fiona Secretary",
    "authentication_token_path": "./vts_token.txt"
}

# VTube Studio 인스턴스
vts = None
vts_connected = False

# 감정별 핫키 매핑 (실제 VTube Studio에서 설정한 핫키 이름으로 변경 필요)
EMOTION_HOTKEYS = {
    "happy": "eye_kirakira",
    "sad": "eye_ulmuck", 
    "angry": "eye_angry",
    "surprised": "eye_surprised",
    "pathetic": "hansim",
    "neutral": "reset"
}

def get_korea_time():
    """한국 시간을 정확하게 가져옵니다."""
    return datetime.now(KST)

def get_korea_date():
    """한국 시간 기준 오늘 날짜를 가져옵니다."""
    return get_korea_time().date()

async def init_vts_connection():
    """VTube Studio 연결을 초기화합니다."""
    global vts, vts_connected
    try:
        vts = pyvts.vts(plugin_info=VTS_PLUGIN_INFO)
        await vts.connect()
        await vts.request_authenticate_token()
        await vts.request_authenticate()
        vts_connected = True
        print("✅ VTube Studio connected successfully")
        return True
    except Exception as e:
        print(f"❌ VTube Studio connection failed: {e}")
        vts_connected = False
        return False

async def close_vts_connection():
    """VTube Studio 연결을 종료합니다."""
    global vts, vts_connected
    try:
        if vts and vts_connected:
            await vts.close()
            vts_connected = False
            print("✅ VTube Studio connection closed")
    except Exception as e:
        print(f"❌ Error closing VTS connection: {e}")

async def analyze_emotion_from_text(text):
    """텍스트에서 감정을 분석합니다."""
    
    system_prompt = """You are an emotion analysis expert for a character named Fiona.
    
    Analyze the emotional tone of Fiona's message and classify it into one of these categories:
    - happy: 기쁨, 즐거움, 만족감
    - sad: 슬픔, 우울함, 실망감
    - angry: 화남, 짜증, 분노
    - surprised: 놀람, 경악, 충격
    - neutral: 평범함, 무감정, 일반적
    - pathetic: 안타까움, 조금 불쌍함, 창피함
    
    Respond with ONLY the emotion category name (e.g., "happy", "sad", etc.).
    Do not include any explanation or additional text."""
    
    user_prompt = f"""다음 Fiona의 메시지에서 가장 적절한 감정을 분석해주세요:
    
    "{text}"
    
    감정 카테고리만 응답해주세요."""
    
    try:
        response = client.messages.create(
            model="claude-sonnet-4-20250514",
            max_tokens=50,
            temperature=0.3,
            system=system_prompt,
            messages=[{"role": "user", "content": user_prompt}]
        )
        
        emotion = response.content[0].text.strip().lower()
        
        # 유효한 감정인지 확인
        if emotion in EMOTION_HOTKEYS:
            return emotion
        else:
            return "neutral"  # 기본값
            
    except Exception as e:
        print(f"❌ Emotion analysis error: {e}")
        return "neutral"  # 오류 시 기본값

async def trigger_vts_emotion(emotion):
    """감정에 따라 VTube Studio 핫키를 트리거합니다."""
    global vts, vts_connected
    
    if not vts_connected or not vts:
        print("⚠️ VTube Studio not connected, skipping emotion trigger")
        return False
    
    try:
        hotkey_name = EMOTION_HOTKEYS.get(emotion, EMOTION_HOTKEYS["neutral"])
        
        # 핫키 목록 가져오기
        response_data = await vts.request(vts.vts_request.requestHotKeyList())
        available_hotkeys = [hotkey['name'] for hotkey in response_data['data']['availableHotkeys']]
        
        # 요청한 핫키가 존재하는지 확인
        if hotkey_name in available_hotkeys:
            # 핫키 트리거
            trigger_request = vts.vts_request.requestTriggerHotKey(hotkey_name)
            await vts.request(trigger_request)
            print(f"✅ VTS emotion triggered: {emotion} -> {hotkey_name}")
            return True
        else:
            print(f"⚠️ Hotkey '{hotkey_name}' not found in VTube Studio")
            return False
            
    except Exception as e:
        print(f"❌ VTS emotion trigger error: {e}")
        # 연결이 끊어진 경우 재연결 시도
        if "connection" in str(e).lower():
            vts_connected = False
            print("🔄 Attempting VTS reconnection...")
            await asyncio.sleep(1)
            await init_vts_connection()
        return False

def load_memory():
    global chat_memory_fiona
    try:
        with open(chat_memory_file, "r", encoding="utf-8") as f:
            chat_memory_fiona = json.load(f)
        clean_memory()  # Auto-clean memory after loading
        save_memory()   # Save cleaned memory
    except (FileNotFoundError, json.JSONDecodeError, UnicodeDecodeError):
        print("Memory loading error. Starting fresh.")

def save_memory():
    try:
        with open(chat_memory_file, "w", encoding="utf-8") as f:
            json.dump(chat_memory_fiona, f, ensure_ascii=False, indent=2)
    except Exception as e:
        print(f"Error saving chat memory: {e}")

def clean_memory():
    """메모리에서 빈 메시지들을 정리합니다."""
    global chat_memory_fiona
    cleaned_memory = {}
    
    for user_id, messages in chat_memory_fiona.items():
        cleaned_messages = []
        for msg in messages:
            user_content = msg.get("user", "").strip()
            assistant_content = msg.get("assistant", "").strip()
            
            # Keep only messages where both user and assistant are not empty
            if user_content and assistant_content:
                cleaned_messages.append({
                    "user": user_content,
                    "assistant": assistant_content
                })
        
        if cleaned_messages:  # Save only if there are valid messages
            cleaned_memory[user_id] = cleaned_messages
    
    chat_memory_fiona = cleaned_memory
    print(f"✅ Memory cleaned. Users with valid messages: {len(chat_memory_fiona)}")

def load_schedules():
    """일정 데이터를 로드합니다."""
    global schedules
    try:
        with open(schedule_file, "r", encoding="utf-8") as f:
            schedules = json.load(f)
        print(f"✅ Schedules loaded: {len(schedules)} items")
    except (FileNotFoundError, json.JSONDecodeError, UnicodeDecodeError):
        schedules = {}
        print("📅 Starting with empty schedule.")

def save_schedules():
    """일정 데이터를 저장합니다."""
    try:
        with open(schedule_file, "w", encoding="utf-8") as f:
            json.dump(schedules, f, ensure_ascii=False, indent=2)
    except Exception as e:
        print(f"❌ Error saving schedules: {e}")

def add_schedule(user_id, schedule_data):
    """새로운 일정을 추가합니다."""
    if user_id not in schedules:
        schedules[user_id] = []
    
    schedule_item = {
        "id": f"{user_id}_{len(schedules[user_id])}_{int(datetime.now().timestamp())}",
        "title": schedule_data.get("title", ""),
        "datetime": schedule_data.get("datetime", ""),
        "description": schedule_data.get("description", ""),
        "reminder_sent": False,
        "created_at": get_korea_time().isoformat()
    }
    
    schedules[user_id].append(schedule_item)
    save_schedules()
    return schedule_item

def get_upcoming_schedules(user_id, hours_ahead=24):
    """다가오는 일정들을 가져옵니다."""
    if user_id not in schedules:
        return []
    
    now = get_korea_time()
    upcoming = []
    
    for schedule in schedules[user_id]:
        try:
            schedule_time = datetime.fromisoformat(schedule["datetime"])
            # 시간대 정보가 없으면 한국 시간으로 가정
            if schedule_time.tzinfo is None:
                schedule_time = KST.localize(schedule_time)
            
            if now <= schedule_time <= now + timedelta(hours=hours_ahead):
                upcoming.append(schedule)
        except (ValueError, KeyError):
            continue
    
    return sorted(upcoming, key=lambda x: x["datetime"])

def get_today_schedules(user_id):
    """오늘의 일정들을 가져옵니다."""
    if user_id not in schedules:
        return []
    
    today = get_korea_date()
    today_schedules = []
    
    for schedule in schedules[user_id]:
        try:
            schedule_time = datetime.fromisoformat(schedule["datetime"])
            # 시간대 정보가 없으면 한국 시간으로 가정
            if schedule_time.tzinfo is None:
                schedule_time = KST.localize(schedule_time)
            
            if schedule_time.date() == today:
                today_schedules.append(schedule)
        except (ValueError, KeyError):
            continue
    
    return sorted(today_schedules, key=lambda x: x["datetime"])

async def parse_schedule_from_text(text):
    """자연어 텍스트에서 일정 정보를 추출합니다."""
    
    system_prompt = """You are Fiona, a professional secretary with expertise in parsing schedule information from natural language.
    
    CRITICAL: You must ONLY respond with valid JSON. Do not include any explanatory text, comments, or other content.
    
    Extract schedule information from the user's message and return it as JSON.
    
    Parse Korean text and extract:
    1. Date and time information (convert to ISO format: YYYY-MM-DDTHH:MM:SS)
    2. Event title/description
    3. Additional details
    
    For relative time expressions:
    - "오늘" = today's date
    - "내일" = tomorrow's date
    - "저녁 10시" = 22:00 today
    - "밤까지" = 23:59 of that day
    - "다음주" = next week
    
    Return ONLY this JSON format:
    {
        "schedules": [
            {
                "title": "brief title",
                "datetime": "2025-01-XX-THXX:XX:XX",
                "description": "detailed description"
            }
        ]
    }
    
    If no schedule information is found, return ONLY: {"schedules": []}
    
    IMPORTANT: Response must be valid JSON only. No additional text allowed.
    
    Current date and time for reference: """ + get_korea_time().strftime("%Y-%m-%d %H:%M:%S")
    
    user_prompt = f"""
    다음 메시지에서 일정 정보를 추출해주세요:
    "{text}"
    
    JSON 형식으로만 응답해주세요."""
    
    try:
        response = client.messages.create(
            model="claude-sonnet-4-20250514",
            max_tokens=500,
            temperature=0.3,
            system=system_prompt,
            messages=[{"role": "user", "content": user_prompt}]
        )
        
        # Extract text content from response
        response_text = ""  
        for content_block in response.content:
            if hasattr(content_block, 'text'):
                response_text += content_block.text
        
        # Parse JSON response with better error handling
        response_text = response_text.strip()
        
        # Claude가 가끔 일반 텍스트로 응답하는 경우 처리
        if not response_text:
            return []
            
        # JSON 형식인지 확인
        if not (response_text.startswith('{') and response_text.endswith('}')):
            print(f"⚠️ Non-JSON response from Claude: {response_text[:100]}...")
            return []
        
        try:
            schedule_data = json.loads(response_text)
            return schedule_data.get("schedules", [])
        except json.JSONDecodeError as json_error:
            print(f"❌ JSON parsing failed: {json_error}")
            print(f"📝 Raw response: {response_text[:200]}...")
            return []
        
    except Exception as e:
        print(f"❌ Schedule parsing error: {e}")
        return []

async def check_reminders():
    """백그라운드에서 리마인더를 체크하고 알림을 보냅니다."""
    while True:
        try:
            now = get_korea_time()
            
            for user_id, user_schedules in schedules.items():
                for schedule in user_schedules:
                    if schedule.get("reminder_sent", False):
                        continue
                    
                    try:
                        # 저장된 일정 시간을 한국 시간으로 변환
                        schedule_time = datetime.fromisoformat(schedule["datetime"])
                        if schedule_time.tzinfo is None:
                            schedule_time = KST.localize(schedule_time)
                        
                        # 15분 전에 리마인더 발송
                        reminder_time = schedule_time - timedelta(minutes=15)
                        
                        if now >= reminder_time and now < schedule_time:
                            # 리마인더 메시지 생성
                            time_str = schedule_time.strftime("%H:%M")
                            reminder_msg = f"🔔 **리마인더**\n\n사장님, {time_str}에 {schedule['title']} 일정이 있습니다!\n\n📝 {schedule['description']}"
                            
                            # 사장님께 DM으로 리마인더 발송
                            try:
                                user = await bot.fetch_user(int(user_id))
                                await user.send(reminder_msg)
                                
                                # 리마인더 발송 플래그 업데이트
                                schedule["reminder_sent"] = True
                                save_schedules()
                                
                                print(f"✅ Reminder sent to {user_id} for: {schedule['title']}")
                                
                            except Exception as e:
                                print(f"❌ Failed to send reminder to {user_id}: {e}")
                        
                        # 지난 일정 정리 (7일 후)
                        elif now > schedule_time + timedelta(days=7):
                            user_schedules.remove(schedule)
                            save_schedules()
                            print(f"🗑️ Cleaned old schedule: {schedule['title']}")
                            
                    except (ValueError, KeyError) as e:
                        print(f"❌ Schedule parsing error: {e}")
                        continue
            
        except Exception as e:
            print(f"❌ Reminder check error: {e}")
        
        # 1분마다 체크
        await asyncio.sleep(60)

MAX_MEMORY_LENGTH = 1000

def update_memory(user_id, message):
    if user_id not in chat_memory_fiona:
        chat_memory_fiona[user_id] = []
    chat_memory_fiona[user_id].append(message)
    if len(chat_memory_fiona[user_id]) > MAX_MEMORY_LENGTH:
        chat_memory_fiona[user_id].pop(0)

def generate_messages(user_id, new_message):
    memory = chat_memory_fiona.get(user_id, [])

    # Claude handles system messages separately
    current_time = get_korea_time()
    current_time_str = current_time.strftime("%Y년 %m월 %d일 (%A) %H시 %M분 %S초 (KST)")
    
    system_prompt = f"""You are having a Discord chat conversation. Respond around 2 sentences. IMPORTANT: Only respond with direct dialogue/speech. Do not include any action descriptions, emotional descriptions, facial expressions, body language, or narrative elements. Do not use phrases like 'with a smile', 'while blushing', 'face turns red', etc. Respond only with what Fiona would actually say out loud in text chat.

CURRENT TIME INFORMATION: {current_time_str}
You can access this current time information to answer time-related questions accurately.
    
프롬프트 기타등등이 여기 들어감"""

    messages = []

    # Add conversation history from memory (excluding empty messages)
    for msg in memory:
        user_content = msg.get("user", "").strip()
        assistant_content = msg.get("assistant", "").strip()
        
        # Add only if not empty
        if user_content:
            messages.append({"role": "user", "content": user_content})
        if assistant_content:
            messages.append({"role": "assistant", "content": assistant_content})

    messages.append({"role": "user", "content": new_message})

    return system_prompt, messages

# TTS functions
def build_tts_stream_headers() -> dict:
    headers = {
        'accept': 'application/json, text/plain, */*',
        'content-type': 'application/json',
        'authorization': "Bearer " + tts_api_key,
    }
    return headers

def build_tts_stream_body(text: str) -> dict:
    body = json.dumps({
        "model": "speech-01-turbo",
        "text": text,
        "stream": True,
        "voice_setting": {
            "voice_id": "Japanese_ColdQueen",
            "speed": 1.0,
            "vol": 1.0,
            "pitch": 0
        },
        "audio_setting": {
            "sample_rate": 32000,
            "bitrate": 128000,
            "format": "mp3",
            "channel": 1
        }
    })
    return body

def call_tts_stream(text: str) -> Iterator[bytes]:
    url = "https://api.minimaxi.chat/v1/t2a_v2?GroupId=" + group_id
    tts_headers = build_tts_stream_headers()
    tts_body = build_tts_stream_body(text)

    try:
        response = requests.post(url, stream=True, headers=tts_headers, data=tts_body)
        
        if response.status_code != 200:
            print(f"❌ TTS API Error: {response.status_code}")
            return
        
        for chunk in response.iter_lines():
            if chunk and chunk.startswith(b'data:'):
                try:
                    data = json.loads(chunk[5:])
                    if "data" in data and "extra_info" not in data:
                        if "audio" in data["data"]:
                            audio = data["data"]["audio"]
                            if audio and audio != '\n':
                                yield audio
                except json.JSONDecodeError:
                    continue
        
    except Exception as e:
        print(f"❌ TTS API call failed: {e}")

async def generate_audio_file(text: str) -> str:
    """TTS로 음성을 생성하고 임시 파일로 저장"""
    if not tts_api_key or not group_id:
        raise Exception("TTS API key or Group ID not configured")
    
    audio_stream = call_tts_stream(text)
    
    # Create temporary file
    temp_file = tempfile.NamedTemporaryFile(delete=False, suffix='.mp3')
    temp_filename = temp_file.name
    
    total_bytes = 0
    try:
        for chunk in audio_stream:
            if chunk and chunk.strip():
                try:
                    decoded_hex = bytes.fromhex(chunk)
                    temp_file.write(decoded_hex)
                    total_bytes += len(decoded_hex)
                except ValueError:
                    continue
        
        temp_file.close()
        
        # File validation check
        if os.path.exists(temp_filename) and os.path.getsize(temp_filename) > 1000:
            return temp_filename
        else:
            raise Exception("Generated audio file is too small or empty")
            
    except Exception as e:
        temp_file.close()
        if os.path.exists(temp_filename):
            os.unlink(temp_filename)
        raise e

async def download_image_as_base64(url):
    """이미지를 다운로드하고 base64로 변환"""
    try:
        response = requests.get(url)
        response.raise_for_status()
        
        # Check image type
        content_type = response.headers.get('content-type', '')
        if 'jpeg' in content_type or 'jpg' in content_type:
            media_type = 'image/jpeg'
        elif 'png' in content_type:
            media_type = 'image/png'
        elif 'gif' in content_type:
            media_type = 'image/gif'
        elif 'webp' in content_type:
            media_type = 'image/webp'
        else:
            media_type = 'image/jpeg'  # Default value
        
        base64_data = base64.b64encode(response.content).decode('utf-8')
        return media_type, base64_data
    except Exception as e:
        print(f"이미지 다운로드 오류: {e}")
        return None, None

# 연락처 데이터베이스
contacts = {
    "이윤혁": {"id": 448361357176471552, "name": "이윤혁"},
    "장현준": {"id": 424555845104435201, "name": "장현준"},
    "채범규": {"id": 498438715128021034, "name": "채범규"},
    "장연우": {"id": 756426406732365825, "name": "장연우"}
    # 실제 사용시 여기에 진짜 Discord ID를 입력하세요
}

# 사장님 ID
BOSS_ID = 752514887980286012

# 답장 대기 중인 메시지들을 추적하기 위한 딕셔너리
pending_replies = {}  # {user_id: {"original_message": "...", "timestamp": "..."}}

def get_contact_name_by_id(user_id):
    """Discord ID로 연락처 이름을 찾는 함수"""
    for name, contact_info in contacts.items():
        if contact_info["id"] == user_id:
            return name
    return None

async def send_message_to_contact(sender_name, recipient_name, original_request, recipient_id):
    """Claude가 전달할 메시지를 생성하고 발송"""
    
    # Claude에게 전달 메시지 생성 요청
    system_prompt = """You are Fiona, a professional secretary creating a message to forward to a contact.
    
    IMPORTANT: You are creating a message that will be sent TO the contact, NOT a report back to the boss.
    
    Create brief, clear messages that convey requests directly without excessive politeness.
    Write in Korean with a professional but natural tone. Keep messages short and businesslike.
    
    DO NOT include any commentary about the request or the recipient. 
    DO NOT write anything that would be said to the boss.
    ONLY write the message content that should be sent to the recipient."""
    
    user_prompt = f"""
    사장님이 {recipient_name}님에게 다음 요청을 전달해달라고 했습니다:
    "{original_request}"
    
    {recipient_name}님에게 보낼 전달 메시지만 작성해주세요:
    - 받는 사람: {recipient_name}님
    - 비즈니스 톤, 과도한 정중함 피하기  
    - 핵심 내용만 직접적으로 전달
    - 마지막에 "- Fiona 대신 전달" 추가
    
    응답 형식: {recipient_name}님에게 보낼 메시지 내용만 작성"""
    
    try:
        response = client.messages.create(
            model="claude-sonnet-4-20250514",
            max_tokens=300,
            temperature=0.7,
            system=system_prompt,
            messages=[{"role": "user", "content": user_prompt}]
        )
        
        generated_message = response.content[0].text.strip()
        
        # 생성된 메시지를 해당 유저에게 DM으로 발송
        user = await bot.fetch_user(recipient_id)
        await user.send(generated_message)
        
        # 답장 대기 목록에 추가
        pending_replies[recipient_id] = {
            "original_message": original_request,
            "recipient_name": recipient_name,
            "timestamp": asyncio.get_event_loop().time()
        }
        
        return f"✅ {recipient_name}님에게 메시지를 전달했습니다!"
        
    except discord.NotFound:
        return f"❌ {recipient_name}님을 찾을 수 없습니다. (잘못된 Discord ID)"
    except discord.Forbidden:
        return f"❌ {recipient_name}님에게 DM을 보낼 수 없습니다. (DM 차단됨)"
    except Exception as e:
        return f"❌ 메시지 전달 실패: {e}"

async def check_if_work_related(message_content):
    """메시지가 업무 관련 내용인지 판별"""
    
    system_prompt = """You are Fiona, a professional secretary. 
    Determine if a message is work-related or personal/casual conversation.
    
    Work-related messages include: requests, updates, reports, scheduling, project discussions, business matters, etc.
    Personal messages include: greetings, casual chat, personal topics, small talk, etc.
    
    Respond only with 'WORK' or 'PERSONAL'."""
    
    user_prompt = f"""
    다음 메시지가 업무 관련인지 개인적인 대화인지 판별해주세요:
    
    메시지: "{message_content}"
    
    업무 관련이면 'WORK', 개인적인 대화면 'PERSONAL'로만 답변해주세요.
    """
    
    try:
        response = client.messages.create(
            model="claude-sonnet-4-20250514",
            max_tokens=50,
            temperature=0.3,
            system=system_prompt,
            messages=[{"role": "user", "content": user_prompt}]
        )
        
        result = response.content[0].text.strip().upper()
        return result == "WORK"
        
    except Exception as e:
        # 판별 실패 시 안전하게 업무 관련으로 처리
        return True

async def summarize_and_report_to_boss(contact_name, original_request, reply_content):
    """답장 내용을 요약해서 사장님께 전달"""
    
    # Claude에게 요약 요청
    system_prompt = """You are Fiona, a professional secretary creating a concise report for your boss.
    
    Create natural, conversational summaries in Korean. Write as if speaking directly to your boss.
    Keep the tone professional but warm, and include a brief personal observation or comment at the end.
    
    Structure: Brief context + main response + small personal insight"""
    
    user_prompt = f"""
    전달했던 요청: "{original_request}"
    받은 답장: "{reply_content}"
    
    {contact_name}님의 답장을 자연스럽게 요약해주세요:
    
    형식:
    사장님, {contact_name}님께 [요청 내용] 전달한 결과를 보고드립니다.
    {contact_name}님께서 [답장 내용 요약]고 합니다.
    [간단한 개인적 의견이나 관찰]
    
    요구사항:
    - 자연스러운 대화체로 작성
    - 핵심 내용만 간결하게
    - 마지막에 상황에 대한 간단한 사견 추가"""
    
    try:
        response = client.messages.create(
            model="claude-sonnet-4-20250514",
            max_tokens=300,
            temperature=0.7,
            system=system_prompt,
            messages=[{"role": "user", "content": user_prompt}]
        )
        
        summary = response.content[0].text.strip()
        
        # 사장님께 DM으로 요약 전달
        boss = await bot.fetch_user(BOSS_ID)
        await boss.send(f"📋 **답장 보고**\n{summary}")
        
    except Exception as e:
        # 실패 시 원본 그대로라도 전달
        try:
            boss = await bot.fetch_user(BOSS_ID)
            await boss.send(f"📋 **{contact_name}님 답장** (요약 실패)\n원본: {reply_content}")
        except:
            pass

async def summarize_additional_message(contact_name, message_content):
    """추가 업무 메시지를 요약해서 사장님께 전달"""
    
    system_prompt = """You are Fiona, a professional secretary reporting additional messages to your boss.
    
    Create brief, natural summaries in Korean. Write as if casually informing your boss about additional communication.
    Keep it conversational but professional."""
    
    user_prompt = f"""
    {contact_name}님으로부터 추가 메시지가 왔습니다:
    "{message_content}"
    
    사장님께 간단히 알려드릴 요약을 작성해주세요:
    
    형식:
    사장님, {contact_name}님에게서 추가 연락이 왔습니다.
    [메시지 내용 요약]
    
    요구사항:
    - 자연스러운 대화체
    - 간결한 요약
    - 필요시 간단한 의견 추가
    """
    
    try:
        response = client.messages.create(
            model="claude-sonnet-4-20250514",
            max_tokens=200,
            temperature=0.7,
            system=system_prompt,
            messages=[{"role": "user", "content": user_prompt}]
        )
        
        summary = response.content[0].text.strip()
        
        # 사장님께 DM으로 요약 전달
        boss = await bot.fetch_user(BOSS_ID)
        await boss.send(f"📬 **추가 연락**\n{summary}")
        
    except Exception as e:
        # 실패 시 원본 그대로라도 전달
        try:
            boss = await bot.fetch_user(BOSS_ID)
            await boss.send(f"📬 **{contact_name}님 추가 메시지**\n원본: {message_content}")
        except:
            pass

load_memory()
load_schedules()

intents = discord.Intents.default()
intents.message_content = True

bot = commands.Bot(command_prefix="!", intents=intents)

@bot.event
async def on_ready():
    korea_time = get_korea_time()
    print(f"✅ Logged in as {bot.user.name}")
    print(f"🔑 TTS API Key configured: {'Yes' if tts_api_key else 'No'}")
    print(f"🔑 Group ID configured: {'Yes' if group_id else 'No'}")
    print(f"📅 Schedule system initialized")
    print(f"🇰🇷 Korean time: {korea_time.strftime('%Y-%m-%d %H:%M:%S %Z')}")
    
    # VTube Studio 연결 시도
    print("🎭 Initializing VTube Studio connection...")
    vts_success = await init_vts_connection()
    print(f"🎭 VTube Studio: {'Connected' if vts_success else 'Failed to connect'}")
    
    # 리마인더 백그라운드 태스크 시작
    bot.loop.create_task(check_reminders())
    # bot.loop.create_task(periodic_chat())  # periodic_chat이 주석처리되어 있으므로 비활성화

@bot.command(name="join")
async def join_voice(ctx):
    """음성 채널에 참가"""
    if ctx.author.voice is None:
        await ctx.send("❗ 먼저 음성 채널에 참가해주세요!")
        return
    
    channel = ctx.author.voice.channel
    if ctx.voice_client is None:
        await channel.connect()
        await ctx.send(f"🎤 {channel.name}에 참가했습니다!")
    else:
        await ctx.send("❗ 이미 음성 채널에 있습니다!")

@bot.command(name="leave")
async def leave_voice(ctx):
    """음성 채널에서 나가기"""
    if ctx.voice_client:
        await ctx.voice_client.disconnect()
        await ctx.send("👋 음성 채널에서 나갔습니다!")
    else:
        await ctx.send("❗ 음성 채널에 있지 않습니다!")

@bot.command(name="speak")
async def speak_text(ctx, *, text: str):
    """텍스트를 음성으로 출력"""
    if not ctx.voice_client:
        await ctx.send("❗ 먼저 `!join` 명령어로 음성 채널에 참가해주세요!")
        return
    
    if ctx.voice_client.is_playing():
        await ctx.send("❗ 현재 음성을 재생 중입니다. 잠시 후 다시 시도해주세요!")
        return
    
    await ctx.send("🎵 음성을 생성하고 있습니다...")
    
    try:
        audio_file = await generate_audio_file(text)
        
        if FFMPEG_PATH:
            source = discord.FFmpegPCMAudio(audio_file, executable=FFMPEG_PATH)
        else:
            source = discord.FFmpegPCMAudio(audio_file)
        
        await ctx.send("🎤 음성 재생을 시작했습니다!")
        
        def cleanup(error):
            if error:
                print(f'❌ Player error: {error}')
            try:
                os.unlink(audio_file)
            except:
                pass
        
        ctx.voice_client.play(source, after=cleanup)
        
    except Exception as e:
        await ctx.send(f"❗ 음성 생성 오류: {e}")

@bot.command(name="debug_voice")
async def debug_voice(ctx):
    """음성 설정 상태를 확인"""
    debug_info = []
    debug_info.append(f"🔧 **Voice Debug Information**")
    debug_info.append(f"📍 Voice Client: {'Connected' if ctx.voice_client else 'Not connected'}")
    debug_info.append(f"🔑 TTS API Key: {'Configured' if tts_api_key else 'Missing'}")
    debug_info.append(f"🆔 Group ID: {'Configured' if group_id else 'Missing'}")
    debug_info.append(f"⚙️ FFmpeg Path: {FFMPEG_PATH if FFMPEG_PATH else 'Not found'}")
    
    if ctx.voice_client:
        debug_info.append(f"🎵 Currently Playing: {'Yes' if ctx.voice_client.is_playing() else 'No'}")
        debug_info.append(f"🔗 Channel: {ctx.voice_client.channel.name}")
    
    await ctx.send("\n".join(debug_info))

@bot.command(name="debug_vts")
async def debug_vts(ctx):
    """VTube Studio 연결 상태를 확인"""
    debug_info = []
    debug_info.append(f"🎭 **VTube Studio Debug Information**")
    debug_info.append(f"🔗 VTS Connected: {'Yes' if vts_connected else 'No'}")
    debug_info.append(f"📦 VTS Instance: {'Available' if vts else 'Not initialized'}")
    debug_info.append(f"🔑 Token File: {VTS_PLUGIN_INFO['authentication_token_path']}")
    
    if vts_connected and vts:
        try:
            # 사용 가능한 핫키 목록 가져오기
            response_data = await vts.request(vts.vts_request.requestHotKeyList())
            available_hotkeys = [hotkey['name'] for hotkey in response_data['data']['availableHotkeys']]
            debug_info.append(f"🎯 Available Hotkeys: {len(available_hotkeys)}")
            
            # 감정 매핑된 핫키 확인
            mapped_hotkeys = []
            for emotion, hotkey_name in EMOTION_HOTKEYS.items():
                if hotkey_name in available_hotkeys:
                    mapped_hotkeys.append(f"{emotion}✅")
                else:
                    mapped_hotkeys.append(f"{emotion}❌")
            
            debug_info.append(f"😊 Emotion Mapping: {', '.join(mapped_hotkeys)}")
            
        except Exception as e:
            debug_info.append(f"❌ Error checking VTS status: {e}")
    
    await ctx.send("\n".join(debug_info))

@bot.command(name="vts_reconnect")
async def vts_reconnect(ctx):
    """VTube Studio 재연결"""
    if ctx.author.id != BOSS_ID:
        await ctx.send("❌ VTS 재연결은 사장님만 사용할 수 있습니다.")
        return
    
    await ctx.send("🔄 VTube Studio 재연결을 시도합니다...")
    
    # 기존 연결 종료
    await close_vts_connection()
    await asyncio.sleep(1)
    
    # 재연결
    success = await init_vts_connection()
    
    if success:
        await ctx.send("✅ VTube Studio 재연결 성공!")
    else:
        await ctx.send("❌ VTube Studio 재연결 실패. VTube Studio가 실행 중인지 확인해주세요.")

@bot.command(name="test_emotion")
async def test_emotion(ctx, emotion: str = "happy"):
    """감정 표현 테스트"""
    if ctx.author.id != BOSS_ID:
        await ctx.send("❌ 감정 테스트는 사장님만 사용할 수 있습니다.")
        return
    
    if emotion not in EMOTION_HOTKEYS:
        available_emotions = ", ".join(EMOTION_HOTKEYS.keys())
        await ctx.send(f"❌ 올바르지 않은 감정입니다. 사용 가능한 감정: {available_emotions}")
        return
    
    await ctx.send(f"🎭 {emotion} 감정을 테스트합니다...")
    
    success = await trigger_vts_emotion(emotion)
    if success:
        await ctx.send(f"✅ {emotion} 감정 표현이 실행되었습니다!")
    else:
        await ctx.send(f"❌ {emotion} 감정 표현 실행에 실패했습니다.")

@bot.command(name="연락처")
async def show_contacts(ctx):
    """등록된 연락처 목록을 보여줍니다"""
    if not contacts:
        await ctx.send("📱 등록된 연락처가 없습니다.")
        return
    
    contact_list = ["📱 **등록된 연락처 목록**"]
    for name, info in contacts.items():
        contact_list.append(f"• {name} ({info['name']}) - ID: {info['id']}")
    
    contact_list.append("\n💬 **사용법**: `{이름}에게 {메시지} 연락해줘`")
    contact_list.append("📝 **예시**: `Alice에게 내일까지 보고서 완료해달라고 연락해줘`")
    
    await ctx.send("\n".join(contact_list))

@bot.command(name="연락처추가")
async def add_contact(ctx, name: str, user_id: int, *, display_name: str = None):
    """새로운 연락처를 추가합니다"""
    if display_name is None:
        display_name = name
    
    contacts[name] = {"id": user_id, "name": display_name}
    await ctx.send(f"✅ {name} ({display_name}) 연락처가 추가되었습니다!")

@bot.command(name="연락처삭제")
async def remove_contact(ctx, name: str):
    """연락처를 삭제합니다"""
    if name in contacts:
        del contacts[name]
        await ctx.send(f"✅ {name} 연락처가 삭제되었습니다!")
    else:
        await ctx.send(f"❌ {name} 연락처를 찾을 수 없습니다.")

@bot.command(name="일정")
async def show_schedule(ctx, *, period: str = "오늘"):
    """일정을 조회합니다"""
    user_id = str(ctx.author.id)
    
    if period in ["오늘", "today"]:
        today_schedules = get_today_schedules(user_id)
        if not today_schedules:
            await ctx.send("📅 오늘 일정이 없습니다!")
            return
        
        schedule_text = ["📅 **오늘의 일정**"]
        for schedule in today_schedules:
            time_str = datetime.fromisoformat(schedule["datetime"]).strftime("%H:%M")
            schedule_text.append(f"🕐 {time_str} - {schedule['title']}")
            if schedule.get("description"):
                schedule_text.append(f"   📝 {schedule['description']}")
        
        await ctx.send("\n".join(schedule_text))
    
    elif period in ["다가오는", "upcoming"]:
        upcoming = get_upcoming_schedules(user_id, 72)  # 3일간
        if not upcoming:
            await ctx.send("📅 다가오는 일정이 없습니다!")
            return
        
        schedule_text = ["📅 **다가오는 일정**"]
        for schedule in upcoming:
            schedule_time = datetime.fromisoformat(schedule["datetime"])
            time_str = schedule_time.strftime("%m/%d %H:%M")
            schedule_text.append(f"🕐 {time_str} - {schedule['title']}")
            if schedule.get("description"):
                schedule_text.append(f"   📝 {schedule['description']}")
        
        await ctx.send("\n".join(schedule_text))

@bot.command(name="일정추가")
async def add_schedule_manual(ctx, datetime_str: str, *, title_desc: str):
    """수동으로 일정을 추가합니다
    예시: !일정추가 "2025-01-20 15:30" 팀 미팅 - 프로젝트 리뷰
    """
    user_id = str(ctx.author.id)
    
    try:
        # 날짜 파싱
        schedule_datetime = datetime.fromisoformat(datetime_str.replace('"', ''))
        
        # 제목과 설명 분리
        if " - " in title_desc:
            title, description = title_desc.split(" - ", 1)
        else:
            title = title_desc
            description = ""
        
        # 일정 추가
        schedule_data = {
            "title": title.strip(),
            "datetime": schedule_datetime.isoformat(),
            "description": description.strip()
        }
        
        added_schedule = add_schedule(user_id, schedule_data)
        time_str = schedule_datetime.strftime("%m/%d %H:%M")
        
        await ctx.send(f"✅ 일정이 추가되었습니다!\n🕐 {time_str} - {title}")
        
    except ValueError:
        await ctx.send("❌ 날짜 형식이 올바르지 않습니다. 예시: \"2025-01-20 15:30\"")
    except Exception as e:
        await ctx.send(f"❌ 일정 추가 중 오류가 발생했습니다: {e}")

@bot.command(name="시간")
async def show_current_time(ctx):
    """현재 한국 시간을 표시합니다"""
    korea_time = get_korea_time()
    time_info = [
        "🇰🇷 **현재 한국 시간**",
        f"📅 날짜: {korea_time.strftime('%Y년 %m월 %d일 (%A)')}",
        f"🕐 시간: {korea_time.strftime('%H:%M:%S')}",
        f"🌏 타임존: {korea_time.strftime('%Z %z')}"
    ]
    await ctx.send("\n".join(time_info))

@bot.command(name="검색")
async def web_search_command(ctx, *, query: str):
    """웹 검색을 수행합니다"""
    if ctx.author.id != BOSS_ID:
        await ctx.send("❌ 검색 기능은 사장님만 사용할 수 있습니다.")
        return
    
    await ctx.channel.typing()
    
    try:
        user_id = str(ctx.author.id)
        system_prompt, messages = generate_messages(user_id, query)
        
        # Claude API with web search tool
        response = client.messages.create(
            model="claude-sonnet-4-20250514",
            max_tokens=1000,
            temperature=0.8,
            system=system_prompt,
            messages=messages,
            tools=[{
                "type": "web_search_20250305",
                "name": "web_search",
                "max_uses": 3
            }]
        )

        # Handle response content with potential tool use blocks
        assistant_reply = ""
        for content_block in response.content:
            if hasattr(content_block, 'text'):
                assistant_reply += content_block.text
        
        if assistant_reply.strip():
            await ctx.send(assistant_reply.strip())
            
            # VTS 감정 분석 및 트리거
            try:
                emotion = await analyze_emotion_from_text(assistant_reply.strip())
                await trigger_vts_emotion(emotion)
            except Exception as e:
                print(f"❌ VTS emotion trigger failed: {e}")
        else:
            await ctx.send("죄송해요, 검색 중 문제가 발생했습니다.")

        # If connected to a voice channel and not playing, respond with voice
        voice_client = discord.utils.get(bot.voice_clients, guild=ctx.guild)
        if voice_client and not voice_client.is_playing() and tts_api_key:
            try:
                audio_file = await generate_audio_file(assistant_reply.strip())
                
                if FFMPEG_PATH:
                    source = discord.FFmpegPCMAudio(audio_file, executable=FFMPEG_PATH)
                else:
                    source = discord.FFmpegPCMAudio(audio_file)
                
                def cleanup(error):
                    if error:
                        print(f'❌ Auto-voice error: {error}')
                    try:
                        os.unlink(audio_file)
                    except:
                        pass
                
                voice_client.play(source, after=cleanup)
            except Exception as e:
                print(f"❌ Voice playback error: {e}")

        update_memory(user_id, {"user": query, "assistant": assistant_reply.strip()})
        save_memory()

    except Exception as e:
        await ctx.send(f"❗ 검색 오류: {e}")

@bot.event
async def on_message(message):
    if message.author.bot:
        return

    user_id = str(message.author.id)
    user_input = message.content.strip()

    # DM 메시지인지 확인
    is_dm = isinstance(message.channel, discord.DMChannel)
    
    # DM 답장 처리 (연락처에 있는 사람이 DM으로 답장한 경우)
    if is_dm and message.author.id != BOSS_ID and message.author.id in pending_replies:
        contact_name = get_contact_name_by_id(message.author.id)
        if contact_name:
            # 자동 응답 메시지 전송
            await message.channel.send("네, 확인했습니다. 사장님께 그대로 전해드리겠습니다.")
            
            # 답장 정보 가져오기
            reply_info = pending_replies[message.author.id]
            
            # 사장님께 요약 전달
            await summarize_and_report_to_boss(
                contact_name=contact_name,
                original_request=reply_info["original_message"],
                reply_content=user_input
            )
            
            # 답장 대기 목록에서 제거
            del pending_replies[message.author.id]
            
            return
    
    # 추가 업무 메시지 처리 (연락처에 있는 사람이 답장 후 추가로 DM을 보낸 경우)
    if is_dm and message.author.id != BOSS_ID:
        contact_name = get_contact_name_by_id(message.author.id)
        if contact_name:
            # 업무 관련 내용인지 확인
            is_work_related = await check_if_work_related(user_input)
            
            if is_work_related:
                # 자동 응답 메시지 전송
                await message.channel.send("네, 확인했습니다. 사장님께 전해드리겠습니다.")
                
                # 사장님께 추가 메시지 요약 전달
                await summarize_additional_message(contact_name, user_input)
                
            return

    if user_input.startswith("!"):
        await bot.process_commands(message)
        return

    # 연락 요청 패턴 감지 및 처리 (사장님만)
    if message.author.id == BOSS_ID:
        for name, contact_info in contacts.items():
            if f"{name}에게" in user_input and any(keyword in user_input for keyword in ["연락해줘", "말해줘", "전달해줘", "보내줘", "알려줘", "전해", "보내"]):
                await message.channel.typing()
                
                # Claude가 전달 메시지 생성 및 발송
                result = await send_message_to_contact(
                    sender_name=message.author.display_name,
                    recipient_name=name,
                    original_request=user_input,
                    recipient_id=contact_info["id"]
                )
                
                await message.channel.send(result)
                
                # 메모리에도 저장 (사용자 요청과 봇의 응답)
                update_memory(user_id, {"user": user_input, "assistant": result})
                save_memory()
                return

    # 사장님이 아닌 경우 일반 채팅 차단
    if message.author.id != BOSS_ID:
        return

    # 일정 정보가 포함된 메시지인지 확인 (사장님만)
    parsed_schedules = await parse_schedule_from_text(user_input)
    if parsed_schedules:
        for schedule_info in parsed_schedules:
            try:
                added_schedule = add_schedule(user_id, schedule_info)
                schedule_time = datetime.fromisoformat(schedule_info["datetime"])
                time_str = schedule_time.strftime("%m/%d %H:%M")
                
                # 일정 추가 확인 메시지
                confirm_msg = f"✅ 일정을 등록했습니다!\n🕐 {time_str} - {schedule_info['title']}"
                if schedule_info.get("description"):
                    confirm_msg += f"\n📝 {schedule_info['description']}"
                
                await message.channel.send(confirm_msg)
                
            except Exception as e:
                print(f"❌ Error adding schedule: {e}")

    await message.channel.typing()

    try:
        system_prompt, messages = generate_messages(user_id, user_input)

        # Process attached image files
        if message.attachments:
            for attachment in message.attachments:
                if attachment.content_type and attachment.content_type.startswith("image/"):
                    media_type, base64_data = await download_image_as_base64(attachment.url)
                    if media_type and base64_data:
                        # Change to Claude's image format
                        messages[-1] = {
                            "role": "user",
                            "content": [
                                {"type": "text", "text": user_input},
                                {
                                    "type": "image",
                                    "source": {
                                        "type": "base64",
                                        "media_type": media_type,
                                        "data": base64_data
                                    }
                                }
                            ]
                        }
                    break

        # Claude API without web search tool for regular chat
        response = client.messages.create(
            model="claude-sonnet-4-20250514",
            max_tokens=1000,
            temperature=0.8,
            system=system_prompt,
            messages=messages
        )

        # Handle response content with potential tool use blocks
        assistant_reply = ""
        for content_block in response.content:
            if hasattr(content_block, 'text'):
                assistant_reply += content_block.text
        
        if assistant_reply.strip():
            await message.channel.send(assistant_reply.strip())
            
            # VTS 감정 분석 및 트리거
            try:
                emotion = await analyze_emotion_from_text(assistant_reply.strip())
                await trigger_vts_emotion(emotion)
            except Exception as e:
                print(f"❌ VTS emotion trigger failed: {e}")
        else:
            await message.channel.send("죄송해요, 검색 중 문제가 발생했습니다.")

        # If connected to a voice channel and not playing, respond with voice
        voice_client = discord.utils.get(bot.voice_clients, guild=message.guild)
        if voice_client and not voice_client.is_playing() and tts_api_key:
            try:
                audio_file = await generate_audio_file(assistant_reply.strip())
                
                if FFMPEG_PATH:
                    source = discord.FFmpegPCMAudio(audio_file, executable=FFMPEG_PATH)
                else:
                    source = discord.FFmpegPCMAudio(audio_file)
                
                def cleanup(error):
                    if error:
                        print(f'❌ Auto-voice error: {error}')
                    try:
                        os.unlink(audio_file)
                    except:
                        pass
                
                voice_client.play(source, after=cleanup)
            except Exception as e:
                print(f"❌ Voice playback error: {e}")

        update_memory(user_id, {"user": user_input, "assistant": assistant_reply.strip()})
        save_memory()

    except Exception as e:
        await message.channel.send(f"❗ Error: {e}")


# 봇 종료 시 VTS 연결 정리
@bot.event
async def on_disconnect():
    """봇이 종료될 때 VTS 연결을 정리합니다."""
    print("🔄 Bot disconnecting, cleaning up VTS connection...")
    await close_vts_connection()

if __name__ == "__main__":
    try:
        bot.run(DISCORD_TOKEN)
    except KeyboardInterrupt:
        print("\n⏹️ Bot stopped by user")
    finally:
        # 프로그램 종료 시에도 VTS 연결 정리
        if vts_connected:
            asyncio.run(close_vts_connection())
