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

load_dotenv()
DISCORD_TOKEN = os.getenv("DISCORD_BOT_TOKEN")
ANTHROPIC_API_KEY = os.getenv("ANTHROPIC_API_KEY")
group_id = os.getenv("group_id")
tts_api_key = os.getenv("tts_api_key")

client = anthropic.Anthropic(api_key=ANTHROPIC_API_KEY)

# Auto-detect FFmpeg path
def find_ffmpeg():
    """시스템에서 FFmpeg 실행파일을 찾습니다."""
    # 1. Try shutil.which first
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
chat_memory_file = "chat_memory_dahlia.json"
chat_memory_dahlia = {}

def load_memory():
    global chat_memory_dahlia
    try:
        with open(chat_memory_file, "r", encoding="utf-8") as f:
            chat_memory_dahlia = json.load(f)
        clean_memory()  # Auto-clean memory after loading
        save_memory()   # Save cleaned memory
    except (FileNotFoundError, json.JSONDecodeError, UnicodeDecodeError):
        print("Memory loading error. Starting fresh.")

def save_memory():
    try:
        with open(chat_memory_file, "w", encoding="utf-8") as f:
            json.dump(chat_memory_dahlia, f, ensure_ascii=False, indent=2)
    except Exception as e:
        print(f"Error saving chat memory: {e}")

def clean_memory():
    """메모리에서 빈 메시지들을 정리합니다."""
    global chat_memory_dahlia
    cleaned_memory = {}
    
    for user_id, messages in chat_memory_dahlia.items():
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
    
    chat_memory_dahlia = cleaned_memory
    print(f"✅ Memory cleaned. Users with valid messages: {len(chat_memory_dahlia)}")

MAX_MEMORY_LENGTH = 1000

def update_memory(user_id, message):
    if user_id not in chat_memory_dahlia:
        chat_memory_dahlia[user_id] = []
    chat_memory_dahlia[user_id].append(message)
    if len(chat_memory_dahlia[user_id]) > MAX_MEMORY_LENGTH:
        chat_memory_dahlia[user_id].pop(0)

def generate_messages(user_id, new_message):
    memory = chat_memory_dahlia.get(user_id, [])

    # Claude handles system messages separately
    system_prompt = """ THIS IS WHERE THE SYSTEM PROMPT GOES """ # THIS IS WHERE THE SYSTEM PROMPT GOES

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
            "voice_id": " XXXXXX ", # THIS IS WHERE THE VOICE ID GOES
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

load_memory()

intents = discord.Intents.default()
intents.message_content = True

bot = commands.Bot(command_prefix="!", intents=intents)

@bot.event
async def on_ready():
    print(f"✅ Logged in as {bot.user.name}")
    print(f"🔑 TTS API Key configured: {'Yes' if tts_api_key else 'No'}")
    print(f"🔑 Group ID configured: {'Yes' if group_id else 'No'}")
    bot.loop.create_task(periodic_chat())

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

@bot.event
async def on_message(message):
    if message.author.bot:
        return

    user_id = str(message.author.id)
    user_input = message.content.strip()

    if user_input.startswith("!"):
        await bot.process_commands(message)
        return

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

        # Claude API
        response = client.messages.create(
            model="claude-sonnet-4-20250514",
            max_tokens=1000,
            temperature=0.8,
            system=system_prompt,
            messages=messages
        )

        assistant_reply = response.content[0].text.strip()
        await message.channel.send(assistant_reply)

        # If connected to a voice channel and not playing, respond with voice
        voice_client = discord.utils.get(bot.voice_clients, guild=message.guild)
        if voice_client and not voice_client.is_playing() and tts_api_key:
            try:
                audio_file = await generate_audio_file(assistant_reply)
                
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

        update_memory(user_id, {"user": user_input, "assistant": assistant_reply})
        save_memory()

    except Exception as e:
        await message.channel.send(f"❗ Error: {e}")

# Periodic message from Dahlia (Claude automatically generates)
async def periodic_chat():
    await bot.wait_until_ready()
    user = await bot.fetch_user(000000000000000000)  # Insert actual user ID here
    while not bot.is_closed():
        try:
            system_prompt = """ THIS IS WHERE THE SYSTEM PROMPT GOES """ # THIS IS WHERE THE SYSTEM PROMPT GOES 
            
            user_prompt = " THIS IS WHERE THE USER PROMPT GOES " # THIS IS WHERE THE USER PROMPT GOES
            
            response = client.messages.create(
                model="claude-3-5-sonnet-20241022",
                max_tokens=200,
                temperature=0.8,
                system=system_prompt,
                messages=[{"role": "user", "content": user_prompt}]
            )
            
            generated_question = response.content[0].text.strip()
            await user.send(generated_question)

            # 빈 assistant 메시지는 저장하지 않음 - periodic_chat은 단방향이므로 메모리에 저장하지 않음
            # update_memory(str(user.id), {"user": generated_question, "assistant": ""})
            # save_memory()

        except Exception as e:
            print(f"Error sending periodic chat: {e}")
        await asyncio.sleep(1800)  # 30 minutes interval

bot.run(DISCORD_TOKEN)
