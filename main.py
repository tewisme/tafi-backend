import os
import uuid
import re
from datetime import datetime
from fastapi import FastAPI, HTTPException, Header, Depends
from pydantic import BaseModel
import sqlite3
from google import genai
from google.genai import types
from dotenv import load_dotenv

load_dotenv()
app = FastAPI(title="VideoTool License & Proxy Server")

GEMINI_API_KEY = os.getenv("GEMINI_API_KEY", "DUMMY")
import random
def get_random_backend_key():
    keys = [k.strip() for k in GEMINI_API_KEY.split(',') if k.strip()]
    if not keys: return "DUMMY"
    return random.choice(keys)


generation_config = {
    "temperature": 1,
    "top_p": 0.95,
    "top_k": 64,
    "max_output_tokens": 8192,
    "response_mime_type": "text/plain",
}

DB_FILE = "licenses.db"

TIER_LIMITS = {
    "free": 10,
    "standard": 50,
    "vip": 9999999
}

def init_db():
    conn = sqlite3.connect(DB_FILE, check_same_thread=False)
    c = conn.cursor()
    c.execute('''
        CREATE TABLE IF NOT EXISTS users (
            license_key TEXT PRIMARY KEY,
            hwid TEXT,
            tier TEXT DEFAULT 'free',
            api_credits INTEGER DEFAULT 0,
            tool_used_this_month INTEGER DEFAULT 0,
            last_reset_month TEXT,
            expires_at DATETIME
        )
    ''')
    current_month = datetime.now().strftime("%Y-%m")
    c.execute("INSERT OR IGNORE INTO users (license_key, hwid, tier, api_credits, tool_used_this_month, last_reset_month, expires_at) VALUES ('TEST-FREE-KEY', NULL, 'free', 0, 0, ?, '2030-01-01')", (current_month,))
    c.execute("INSERT OR IGNORE INTO users (license_key, hwid, tier, api_credits, tool_used_this_month, last_reset_month, expires_at) VALUES ('TEST-BASIC-KEY', NULL, 'basic', 2000, 0, ?, '2030-01-01')", (current_month,))
    c.execute("INSERT OR IGNORE INTO users (license_key, hwid, tier, api_credits, tool_used_this_month, last_reset_month, expires_at) VALUES ('TEST-VIP-KEY', NULL, 'vip', 10000, 0, ?, '2030-01-01')", (current_month,))
    conn.commit()
    conn.close()

init_db()

def get_db():
    conn = sqlite3.connect(DB_FILE, check_same_thread=False)
    conn.row_factory = sqlite3.Row
    try:
        yield conn
    finally:
        conn.close()

class AuthRequest(BaseModel):
    license_key: str
    hwid: str

class TranslateRequest(BaseModel):
    srt_content: str

class MetadataRequest(BaseModel):
    prompt: str

@app.post("/auth/login")
def login(req: AuthRequest, db: sqlite3.Connection = Depends(get_db)):
    c = db.cursor()
    c.execute("SELECT * FROM users WHERE license_key = ?", (req.license_key,))
    user = c.fetchone()
    if not user:
        raise HTTPException(status_code=401, detail="License Key không hợp lệ!")
    
    if user['expires_at']:
        try:
            if len(user['expires_at']) <= 10:
                exp = datetime.strptime(user['expires_at'], "%Y-%m-%d")
            else:
                exp = datetime.strptime(user['expires_at'], "%Y-%m-%d %H:%M:%S")
            if exp < datetime.now():
                raise HTTPException(status_code=403, detail="License Key đã hết hạn!")
        except Exception:
            pass
        
    # Reset monthly quota if needed
    current_month = datetime.now().strftime("%Y-%m")
    if user['last_reset_month'] != current_month:
        c.execute("UPDATE users SET tool_used_this_month = 0, last_reset_month = ? WHERE license_key = ?", (current_month, req.license_key))
        db.commit()
        # Refetch
        c.execute("SELECT * FROM users WHERE license_key = ?", (req.license_key,))
        user = c.fetchone()

    # Hardware ID check
    if not user['hwid']:
        c.execute("UPDATE users SET hwid = ? WHERE license_key = ?", (req.hwid, req.license_key))
        db.commit()
    elif user['hwid'] != req.hwid:
        raise HTTPException(status_code=403, detail="License Key này đã được kích hoạt trên một máy tính khác!")
        
    limit = TIER_LIMITS.get(user['tier'], 10)
    return {
        "status": "success",
        "tier": user['tier'],
        "api_credits": user['api_credits'],
        "tool_used": user['tool_used_this_month'],
        "tool_limit": limit,
        "expires_at": user['expires_at']
    }

def get_srt_duration_seconds(srt_text: str) -> int:
    matches = re.findall(r'-->\s*(\d{2}):(\d{2}):(\d{2}),(\d{3})', srt_text)
    if not matches: return 0
    last_time = matches[-1]
    h, m, s = int(last_time[0]), int(last_time[1]), int(last_time[2])
    return h * 3600 + m * 60 + s

@app.post("/api/translate")
def translate_proxy(req: TranslateRequest, x_license_key: str = Header(None), x_hwid: str = Header(None), db: sqlite3.Connection = Depends(get_db)):
    if not x_license_key or not x_hwid:
        raise HTTPException(status_code=401, detail="Thiếu thông tin xác thực!")
    c = db.cursor()
    c.execute("SELECT * FROM users WHERE license_key = ? AND hwid = ?", (x_license_key, x_hwid))
    user = c.fetchone()
    if not user:
        raise HTTPException(status_code=401, detail="Xác thực thất bại!")
        
    # Tính thời lượng và tính tiền (1 giây = 1 credit)
    duration_seconds = get_srt_duration_seconds(req.srt_content)
    # Tối thiểu 10 giây (tránh srt lỗi)
    if duration_seconds < 10: duration_seconds = 10
    
    cost = duration_seconds
    
    if user['api_credits'] < cost and user['tier'] != 'vip':
        raise HTTPException(status_code=402, detail=f"Bạn cần {cost} API Credits để dịch video {duration_seconds}s này, nhưng số dư chỉ còn {user['api_credits']} Credits. Vui lòng nạp thêm hoặc dùng API Key cá nhân.")

    # Check monthly tool limit
    limit = TIER_LIMITS.get(user['tier'], 10)
    if user['tool_used_this_month'] >= limit and user['tier'] != 'vip':
        raise HTTPException(status_code=402, detail=f"Bạn đã đạt giới hạn {limit} video/tháng của hạng {user['tier'].upper()}. Vui lòng nâng cấp!")

    try:
        import random
        keys_pool = [k.strip() for k in GEMINI_API_KEY.split(',') if k.strip()]
        if not keys_pool: keys_pool = ["DUMMY"]
        random.shuffle(keys_pool)
        
        success = False
        last_error = None
        for backend_key in keys_pool:
            try:
                if backend_key == "DUMMY":
                    translated_text = req.srt_content.replace("[Speaker 1]", "[Speaker 1] [Test DUMMY]")
                else:
                    client = genai.Client(api_key=backend_key)
                    response = client.models.generate_content(
                        model="gemini-3.6-flash",
                        contents=req.srt_content,
                        config=types.GenerateContentConfig(
                            temperature=1,
                            top_p=0.95,
                            top_k=40,
                            max_output_tokens=8192,
                            system_instruction="Bạn là chuyên gia dịch phụ đề. Dịch sang tiếng Việt, giữ nguyên cấu trúc SRT, không thêm markdown. TUYỆT ĐỐI GIỮ NGUYÊN các thẻ [Speaker 1], [Speaker 2]... ở đầu câu nếu có, KHÔNG ĐƯỢC XÓA HOẶC DỊCH CHÚNG."
                        )
                    )
                    translated_text = response.text
                success = True
                break
            except Exception as e:
                last_error = e
                continue
                
        if not success:
            raise Exception(f"Tất cả các Key đều lỗi. Lỗi cuối: {last_error}")
        
        # Deduct credits & increase usage counter
        if user['tier'] != 'vip':
            c.execute("UPDATE users SET api_credits = api_credits - ?, tool_used_this_month = tool_used_this_month + 1 WHERE license_key = ?", (cost, x_license_key))
            db.commit()
            
        new_credits = user['api_credits'] - cost if user['tier'] != 'vip' else user['api_credits']
        return {"status": "success", "translated_srt": translated_text, "api_credits_remaining": new_credits, "cost": cost}
    except Exception as e:
        raise HTTPException(status_code=500, detail=f"Lỗi AI Server: {str(e)}")


from fastapi import UploadFile, File

@app.post("/api/transcribe")
async def transcribe_proxy(
    audio: UploadFile = File(...),
    x_license_key: str = Header(None),
    x_hwid: str = Header(None),
    db: sqlite3.Connection = Depends(get_db)
):
    if not x_license_key or not x_hwid:
        raise HTTPException(status_code=401, detail="Thiếu thông tin xác thực!")
    c = db.cursor()
    c.execute("SELECT * FROM users WHERE license_key = ? AND hwid = ?", (x_license_key, x_hwid))
    user = c.fetchone()
    if not user:
        raise HTTPException(status_code=401, detail="Xác thực thất bại!")
        
    temp_file_path = f"temp_{uuid.uuid4()}.wav"
    with open(temp_file_path, "wb") as f:
        f.write(await audio.read())
        
    import wave
    import contextlib
    try:
        with contextlib.closing(wave.open(temp_file_path, 'r')) as f:
            frames = f.getnframes()
            rate = f.getframerate()
            duration_seconds = int(frames / float(rate))
    except Exception:
        duration_seconds = 60

    cost = duration_seconds
    
    if user['api_credits'] < cost and user['tier'] != 'vip':
        os.remove(temp_file_path)
        raise HTTPException(status_code=402, detail=f"Cần {cost} Credits để bóc băng, số dư {user['api_credits']}")

    try:
        
        import random
        keys_pool = [k.strip() for k in GEMINI_API_KEY.split(',') if k.strip()]
        if not keys_pool: keys_pool = ["DUMMY"]
        random.shuffle(keys_pool)
        
        success = False
        last_error = None
        for backend_key in keys_pool:
            try:
                if backend_key == "DUMMY":
                    transcription = "1\n00:00:00,000 --> 00:00:05,000\n[Speaker 1] Đây là phụ đề test vì chưa có API Key."
                else:
                    client = genai.Client(api_key=backend_key)
                    gemini_audio = client.files.upload(file=temp_file_path)
                    response = client.models.generate_content(
                        model="gemini-3.6-flash",
                        contents=["Transcribe this audio verbatim with speaker diarization and precise timestamps.", gemini_audio],
                        config=types.GenerateContentConfig(
                            response_mime_type="application/json",
                            response_schema={
                                "type": "array",
                                "items": {
                                    "type": "object",
                                    "properties": {
                                        "start_time": {"type": "string", "description": "Thời gian bắt đầu (VD: 00:01)"},
                                        "end_time": {"type": "string", "description": "Thời gian kết thúc (VD: 00:05)"},
                                        "speaker": {"type": "string", "description": "Người nói (VD: Speaker 1)"},
                                        "transcript": {"type": "string", "description": "Nội dung phiên âm chính xác"},
                                    },
                                    "required": ["start_time", "end_time", "speaker", "transcript"],
                                },
                            }
                        )
                    )
                    
                    try:
                        client.files.delete(name=gemini_audio.name)
                    except:
                        pass
                        
                    raw_json = response.text
                    if not raw_json:
                        raise Exception("API returned empty JSON")
                    
                    import json
                    json_data = json.loads(raw_json)
                    srt_lines = []
                    for i, item in enumerate(json_data, 1):
                        def format_time(t):
                            t = t.replace('[', '').replace(']', '')
                            parts = t.strip().split(':')
                            if len(parts) == 2:
                                return f"00:{parts[0].zfill(2)}:{parts[1].zfill(2)},000"
                            elif len(parts) == 3:
                                return f"{parts[0].zfill(2)}:{parts[1].zfill(2)}:{parts[2].zfill(2)},000"
                            return "00:00:00,000"
                            
                        start = format_time(item.get("start_time", "00:00"))
                        end = format_time(item.get("end_time", "00:00"))
                        speaker = item.get("speaker", "Speaker 1")
                        text = item.get("transcript", "")
                        srt_lines.append(f"{i}\n{start} --> {end}\n[{speaker}] {text}\n")
                    
                    transcription = "\n".join(srt_lines)
                if not transcription:
                    raise Exception("API returned empty string")
                success = True
                break
            except Exception as e:
                last_error = e
                continue
                
        if not success:
            raise Exception(f"Tất cả các Key đều lỗi. Lỗi cuối: {last_error}")

        
        if user['tier'] != 'vip':
            c.execute("UPDATE users SET api_credits = api_credits - ? WHERE license_key = ?", (cost, x_license_key))
            db.commit()
            
        new_credits = user['api_credits'] - cost if user['tier'] != 'vip' else user['api_credits']
        os.remove(temp_file_path)
        return {"status": "success", "srt_content": transcription, "api_credits_remaining": new_credits, "cost": cost}
    except Exception as e:
        if os.path.exists(temp_file_path):
            os.remove(temp_file_path)
        raise HTTPException(status_code=500, detail=f"Lỗi AI Server: {str(e)}")


@app.post("/api/metadata")
def metadata_proxy(req: MetadataRequest, x_license_key: str = Header(None), x_hwid: str = Header(None), db: sqlite3.Connection = Depends(get_db)):
    # Bỏ qua trừ tiền metadata cho đơn giản, hoặc thu 5 credit
    try:
        backend_key = get_random_backend_key()
        client = genai.Client(api_key=backend_key)
        resp = client.models.generate_content(model="gemini-3.6-flash", contents=req.prompt)
        return {"status": "success", "result": resp.text}
    except Exception as e:
        raise HTTPException(status_code=500, detail=f"Lỗi AI Server: {str(e)}")

if __name__ == "__main__":
    import uvicorn
    print("[Server] Starting Backend...")
    uvicorn.run(app, host="0.0.0.0", port=8000)



# ==========================================
# WEBHOOK THANH TOÁN TỰ ĐỘNG (Ví dụ: SePay/Casso)
# ==========================================
from pydantic import BaseModel
import re
from datetime import datetime, timedelta

class PaymentWebhook(BaseModel):
    id: int = 0
    gateway: str = ""
    transactionDate: str = ""
    accountNumber: str = ""
    amountIn: int = 0
    amountOut: int = 0
    transactionContent: str = ""
    referenceCode: str = ""
    
# Cấu hình tỷ giá: 1 VNĐ = 1 Credit (VD: 100k VNĐ = 100,000 Credits)
EXCHANGE_RATE = 1

@app.post("/api/webhook/payment")
async def auto_payment_webhook(payload: PaymentWebhook, db: sqlite3.Connection = Depends(get_db)):
    """
    Endpoint nhận Webhook từ ngân hàng (SePay/Casso).
    Cú pháp mua Token: NAP TOKEN <LICENSE_KEY>
    Cú pháp Gia hạn: GIAHAN <LICENSE_KEY>
    Cú pháp Mua mới: MUA <LICENSE_KEY>
    """
    if payload.amountIn <= 0:
        return {"status": "ignored", "msg": "Không có tiền vào"}

    content = payload.transactionContent.upper()
    
    # Tìm mã Key (VD: TAFI-XXXX-XXXX)
    import re
    match = re.search(r'(TAFI-[A-Z0-9\-]+)', content)
    if not match:
        return {"status": "ignored", "msg": "Không tìm thấy mã Key"}
        
    license_key = match.group(1).strip()
    
    c = db.cursor()
    c.execute("SELECT * FROM users WHERE license_key = ?", (license_key,))
    user = c.fetchone()
    
    # XỬ LÝ MUA KEY MỚI TINH
    if "MUA" in content and not user:
        days_to_add = int((payload.amountIn / 100000) * 30)
        if days_to_add <= 0: days_to_add = 1
        
        from datetime import datetime, timedelta
        new_expiry = datetime.now() + timedelta(days=days_to_add)
        new_expiry_str = new_expiry.strftime("%Y-%m-%d %H:%M:%S")
        
        # Tạo user mới với 10,000 credit tặng kèm
        c.execute("INSERT INTO users (license_key, tier, api_credits, expires_at) VALUES (?, ?, ?, ?)", 
                  (license_key, 'vip', 10000, new_expiry_str))
        db.commit()
        print(f"🎉 [AUTO-PAYMENT] Khách MUA MỚI Key {license_key}. Hạn: {new_expiry_str}")
        return {"status": "success", "msg": f"Tạo mới thành công Key {license_key}"}

    if not user:
        return {"status": "error", "msg": f"Key {license_key} không tồn tại (Không phải lệnh MUA)"}
        
    if "GIAHAN" in content:
        # XỬ LÝ GIA HẠN BẢN QUYỀN
        days_to_add = int((payload.amountIn / 100000) * 30)
        if days_to_add <= 0: days_to_add = 1 
        
        current_expiry_str = user[6] # expires_at index
        try:
            from datetime import datetime, timedelta
            current_expiry = datetime.strptime(current_expiry_str, "%Y-%m-%d %H:%M:%S")
            if current_expiry < datetime.now():
                current_expiry = datetime.now()
        except:
            current_expiry = datetime.now()
            
        new_expiry = current_expiry + timedelta(days=days_to_add)
        new_expiry_str = new_expiry.strftime("%Y-%m-%d %H:%M:%S")
        
        c.execute("UPDATE users SET expires_at = ? WHERE license_key = ?", (new_expiry_str, license_key))
        db.commit()
        print(f"⌛ [AUTO-PAYMENT] Gia hạn thêm {days_to_add} ngày cho Key {license_key}. Hạn mới: {new_expiry_str}")
        return {"status": "success", "msg": f"Gia hạn thành công {days_to_add} ngày"}
        
    else:
        # MẶC ĐỊNH LÀ MUA TOKEN (NAP TOKEN TAFI-...)
        EXCHANGE_RATE = 1
        added_credits = payload.amountIn * EXCHANGE_RATE
        
        c.execute("UPDATE users SET api_credits = api_credits + ? WHERE license_key = ?", (added_credits, license_key))
        db.commit()
        print(f"💰 [AUTO-PAYMENT] Đã nạp {added_credits} Credits cho Key {license_key}.")
        return {"status": "success", "msg": f"Nạp thành công {added_credits} credits"}
