from typing import Optional, List, Dict, Any
from datetime import datetime, timedelta
import os
import json
from fastapi import FastAPI, HTTPException, Depends, File, UploadFile, WebSocket, WebSocketDisconnect, BackgroundTasks, Request, Query, Form
from fastapi.security import HTTPBearer, HTTPAuthorizationCredentials
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import JSONResponse, FileResponse
from jose import JWTError
from pydantic import BaseModel, EmailStr, validator, Field, field_validator, model_validator
from passlib.context import CryptContext
import jwt
import mysql.connector
from mysql.connector import pooling, errorcode
from dotenv import load_dotenv
import secrets
import string
from email.mime.text import MIMEText
from email.mime.multipart import MIMEMultipart
import smtplib
import firebase_admin
from firebase_admin import credentials, auth as firebase_auth
import uuid
from pathlib import Path
import shutil
import bleach
from PIL import Image
import io
import asyncio
from collections import defaultdict
import math
import random
import geopy.distance
import redis
from enum import Enum
import logging
import traceback
from contextlib import contextmanager
import hashlib
import time

# Load environment variables
load_dotenv()

# Setup logging
logging.basicConfig(level=logging.INFO)
logger = logging.getLogger(__name__)

# Initialize FastAPI
app = FastAPI(
    title="meiXuP Premium Social Platform API",
    version="2.0.0",
    description="Premium social media platform combining social networking and dating features",
    docs_url="/api/docs",
    redoc_url="/api/redoc"
)

# CORS Configuration
app.add_middleware(
    CORSMiddleware,
    allow_origins=["*"],
    allow_credentials=True,
    allow_methods=["*"],
    allow_headers=["*"],
)

# Security Configuration
pwd_context = CryptContext(schemes=["bcrypt"], deprecated="auto")
security = HTTPBearer()

# JWT Configuration
SECRET_KEY = os.getenv("SECRET_KEY", "your-secret-key-change-this-in-production")
ALGORITHM = "HS256"
ACCESS_TOKEN_EXPIRE_MINUTES = 60
REFRESH_TOKEN_EXPIRE_DAYS = 30

# Email Configuration
SMTP_SERVER = os.getenv("SMTP_SERVER", "smtp.gmail.com")
SMTP_PORT = int(os.getenv("SMTP_PORT", "587"))
SMTP_EMAIL = os.getenv("SMTP_EMAIL")
SMTP_PASSWORD = os.getenv("SMTP_PASSWORD")
EMAIL_FROM_NAME = "meiXuP ADMIN"

# Database Configuration
DB_CONFIG = {
    "host": os.getenv("DB_HOST", "localhost"),
    "user": os.getenv("DB_USER", "root"),
    "password": os.getenv("DB_PASSWORD", ""),
    "database": os.getenv("DB_NAME", "meixup_premium"),
    "pool_name": "meixup_pool",
    "pool_size": 20,
    "pool_reset_session": True,
}

# Redis Configuration (for caching and real-time features)
REDIS_HOST = os.getenv("REDIS_HOST", "localhost")
REDIS_PORT = int(os.getenv("REDIS_PORT", "6379"))
redis_client = None
try:
    redis_client = redis.Redis(host=REDIS_HOST, port=REDIS_PORT, decode_responses=True)
    redis_client.ping()
    logger.info("Redis connected successfully")
except Exception as e:
    logger.warning(f"Redis connection failed: {e}. Continuing without Redis.")
    redis_client = None

# Firebase Configuration
FIREBASE_CREDS_PATH = os.getenv("FIREBASE_CREDS_PATH", "firebase-credentials.json")
if os.path.exists(FIREBASE_CREDS_PATH):
    try:
        cred = credentials.Certificate(FIREBASE_CREDS_PATH)
        firebase_admin.initialize_app(cred)
        logger.info("Firebase initialized successfully")
    except Exception as e:
        logger.error(f"Firebase initialization failed: {e}")
        firebase_admin = None

# Premium Subscription Plans
# PREMIUM_PLANS = {
#     "basic": {
#         "price_monthly": 9.99,
#         "features": ["unlimited_likes", "see_who_liked_you", "5_super_likes_per_day", "basic_analytics"]
#     },
#     "premium": {
#         "price_monthly": 19.99,
#         "features": ["unlimited_likes", "see_who_liked_you", "unlimited_super_likes", 
#                     "read_receipts", "travel_mode", "advanced_analytics", "priority_support"]
#     },
#     "elite": {
#         "price_monthly": 49.99,
#         "features": ["unlimited_likes", "see_who_liked_you", "unlimited_super_likes", 
#                     "read_receipts", "travel_mode", "advanced_analytics", "priority_support",
#                     "profile_boost", "verified_badge", "exclusive_events"]
#     }
# }

# File Upload Configuration
UPLOAD_DIR = Path("assets")
UPLOAD_DIR.mkdir(exist_ok=True)
for folder in ["profiles", "posts", "stories", "chat", "verification", "premium"]:
    (UPLOAD_DIR / folder).mkdir(exist_ok=True)

MAX_FILE_SIZE = 50 * 1024 * 1024  # 50MB for premium users
ALLOWED_IMAGE_TYPES = {"image/jpeg", "image/png", "image/gif", "image/webp", "image/heic"}
ALLOWED_VIDEO_TYPES = {"video/mp4", "video/webm", "video/mov", "video/avi", "video/mkv"}
ALLOWED_AUDIO_TYPES = {"audio/mpeg", "audio/wav", "audio/ogg"}

# Database Connection Pool
@contextmanager
def get_db_connection():
    """Context manager for database connections"""
    conn = None
    try:
        conn = pooling.MySQLConnectionPool(**DB_CONFIG).get_connection()
        yield conn
    except mysql.connector.Error as err:
        logger.error(f"Database error: {err}")
        raise HTTPException(status_code=500, detail="Database connection error")
    finally:
        if conn:
            conn.close()

@contextmanager
def get_db_cursor(conn):
    """Context manager for database cursors"""
    cursor = None
    try:
        cursor = conn.cursor(dictionary=True)
        yield cursor
    finally:
        if cursor:
            cursor.close()

# WebSocket Connection Manager
class ConnectionManager:
    def __init__(self):
        self.active_connections: Dict[int, WebSocket] = {}
        self.user_status: Dict[int, Dict] = {}

    async def connect(self, websocket: WebSocket, user_id: int):
        # ❌ DO NOT CALL websocket.accept() HERE
        self.active_connections[user_id] = websocket
        self.user_status[user_id] = {
            "status": "online",
            "last_seen": datetime.now().isoformat(),
        }
        await self.broadcast_status(user_id, "online")

    def disconnect(self, user_id: int):
        if user_id in self.active_connections:
            del self.active_connections[user_id]

        if user_id in self.user_status:
            self.user_status[user_id]["status"] = "offline"
            self.user_status[user_id]["last_seen"] = datetime.now().isoformat()

    async def send_personal_message(self, message: dict, user_id: int):
        ws = self.active_connections.get(user_id)
        if ws:
            try:
                await ws.send_json(message)
            except Exception as e:
                logger.error(f"WebSocket send error: {e}")
                self.disconnect(user_id)

    async def broadcast_status(self, user_id: int, status: str):
        message = {
            "type": "user_status",
            "user_id": user_id,
            "status": status,
            "timestamp": datetime.now().isoformat()
        }

        for uid, connection in list(self.active_connections.items()):
            if uid != user_id:
                try:
                    await connection.send_json(message)
                except Exception as e:
                    logger.error(f"Broadcast error: {e}")
                    self.disconnect(uid)


manager = ConnectionManager()


# Cache functions
def cache_get(key: str, default=None):
    if redis_client:
        try:
            value = redis_client.get(key)
            if value:
                return json.loads(value)
        except Exception as e:
            logger.error(f"Cache get error: {e}")
    return default

def cache_set(key: str, value: Any, expire: int = 300):
    if redis_client:
        try:
            redis_client.setex(key, expire, json.dumps(value))
        except Exception as e:
            logger.error(f"Cache set error: {e}")

def cache_delete(key: str):
    if redis_client:
        try:
            redis_client.delete(key)
        except Exception as e:
            logger.error(f"Cache delete error: {e}")

# Temporary OTP Storage (use Redis in production)
class OTPStorage:
    def __init__(self):
        self.storage = {}
    
    def set(self, email: str, otp: str, data: dict, expires_minutes: int = 10):
        self.storage[email] = {
            "otp": otp,
            "data": data,
            "expires": datetime.now() + timedelta(minutes=expires_minutes)
        }
    
    def get(self, email: str):
        data = self.storage.get(email)
        if data and datetime.now() < data["expires"]:
            return data
        if email in self.storage:
            del self.storage[email]
        return None
    
    def delete(self, email: str):
        if email in self.storage:
            del self.storage[email]

otp_storage = OTPStorage()

# ==================== USER AUTH & PROFILE ====================

class UserRegister(BaseModel):
    email: EmailStr

    password: str = Field(min_length=8)
    username: str = Field(min_length=3, max_length=30)
    full_name: str = Field(min_length=2, max_length=100)

    date_of_birth: str

    gender: Optional[str] = Field(
        default=None,
        pattern="^(male|female|non_binary|prefer_not_to_say)$"
    )

    bio: Optional[str] = Field(default=None, max_length=500)
    interests: Optional[List[str]] = None

    location_lat: Optional[float] = Field(None, ge=-90, le=90)
    location_lng: Optional[float] = Field(None, ge=-180, le=180)

    looking_for: Optional[str] = Field(
        default=None,
        pattern="^(male|female|both|any)$"
    )

    max_distance: int = Field(default=100, ge=1, le=1000)
    age_range_min: int = Field(default=18, ge=18, le=100)
    age_range_max: int = Field(default=60, ge=18, le=100)

    @field_validator("age_range_max", mode="after")
    @classmethod
    def validate_age_range(cls, v, info):
        min_age = info.data.get("age_range_min", 18)
        if v < min_age:
            raise ValueError("age_range_max must be >= age_range_min")
        return v

    @validator("password")
    @classmethod
    def validate_password(cls, v: str):
        if not any(c.isupper() for c in v):
            raise ValueError("Password must contain at least one uppercase letter")
        if not any(c.islower() for c in v):
            raise ValueError("Password must contain at least one lowercase letter")
        if not any(c.isdigit() for c in v):
            raise ValueError("Password must contain at least one number")
        return v

    @model_validator(mode="after")
    def check_age_range(self):
        if self.age_range_max < self.age_range_min:
            raise ValueError("age_range_max must be >= age_range_min")
        return self


# ==================== OTP & AUTH ====================

class OTPVerify(BaseModel):
    email: EmailStr
    otp: str = Field(min_length=6, max_length=6)


class GoogleAuthToken(BaseModel):
    id_token: str


class TokenRefresh(BaseModel):
    refresh_token: str


class LoginRequest(BaseModel):
    email: EmailStr
    password: str
    
    
class ForgotPassword(BaseModel):
    email: str
    
class VerifyResetOTP(BaseModel):
    email: str
    otp: str
    
class ResetPassword(BaseModel):
    email: str
    new_password: str


# ==================== POSTS & COMMENTS ====================

class PostCreate(BaseModel):
    content: str = Field(max_length=5000)
    post_type: str = Field(
        default="post",
        pattern="^(post|story|reel|live)$"
    )
    privacy: str = Field(
        default="public",
        pattern="^(public|friends|private)$"
    )
    tags: Optional[List[str]] = None
    location_lat: Optional[float] = None
    location_lng: Optional[float] = None



    
    
class CommentCreate(BaseModel):
    content: str
    parent_comment_id: Optional[int] = None



class CommentResponse(BaseModel):
    id: int
    post_id: int
    user_id: int
    username: str
    profile_picture: Optional[str]
    is_verified: bool
    content: str
    likes_count: int
    replies_count: int
    created_at: datetime
    is_liked: bool


# ==================== MESSAGING ====================

class MessageCreate(BaseModel):
    receiver_id: int
    content: str = Field(max_length=5000)
    message_type: str = Field(
        default="text",
        pattern="^(text|image|video|audio|gift|location)$"
    )


# ==================== MATCHING / DATING ====================

class SwipeAction(BaseModel):
    target_user_id: int
    action: str = Field(pattern="^(like|pass|super_like)$")


# ==================== ADMIN ====================

class AdminLogin(BaseModel):
    email: EmailStr
    password: str


# ==================== LOCATION ====================

class LocationUpdate(BaseModel):
    lat: float
    lng: float
    accuracy: Optional[float] = None


# ==================== PREMIUM ====================

class PremiumPurchase(BaseModel):
    plan: str = Field(pattern="^(basic|premium|elite)$")
    duration_months: int = Field(default=1, ge=1, le=12)
    payment_method: str = Field(
        pattern="^(credit_card|paypal|stripe|crypto)$"
    )


# ==================== REPORTING ====================

class ReportCreate(BaseModel):
    target_type: str = Field(
        pattern="^(user|post|comment|message)$"
    )
    target_id: int
    reason: str
    description: Optional[str] = None


# ==================== PROFILE UPDATE ====================

class ProfileUpdate(BaseModel):
    full_name: Optional[str] = None
    bio: Optional[str] = None
    gender: Optional[str] = None
    looking_for: Optional[str] = None
    max_distance: Optional[int] = None
    age_range_min: Optional[int] = None
    age_range_max: Optional[int] = None
    interests: Optional[List[str]] = None

    is_incognito: Optional[bool] = None
    show_distance: bool = True
    show_age: bool = True
    show_online_status: bool = True

# ==================== UTILITY FUNCTIONS ====================

def hash_password(password: str) -> str:
    return pwd_context.hash(password)

def verify_password(plain_password: str, hashed_password: str) -> bool:
    return pwd_context.verify(plain_password, hashed_password)

def create_access_token(data: dict, expires_delta: Optional[timedelta] = None) -> str:
    to_encode = data.copy()
    expire = datetime.utcnow() + (expires_delta or timedelta(minutes=ACCESS_TOKEN_EXPIRE_MINUTES))
    to_encode.update({"exp": expire, "type": "access", "iat": datetime.utcnow()})
    return jwt.encode(to_encode, SECRET_KEY, algorithm=ALGORITHM)

def create_refresh_token(data: dict) -> str:
    to_encode = data.copy()
    expire = datetime.utcnow() + timedelta(days=REFRESH_TOKEN_EXPIRE_DAYS)
    to_encode.update({"exp": expire, "type": "refresh", "iat": datetime.utcnow()})
    return jwt.encode(to_encode, SECRET_KEY, algorithm=ALGORITHM)

def verify_token(credentials: HTTPAuthorizationCredentials = Depends(security)) -> int:
    try:
        token = credentials.credentials
        payload = jwt.decode(token, SECRET_KEY, algorithms=[ALGORITHM])
        
        if payload.get("type") != "access":
            raise HTTPException(status_code=401, detail="Invalid token type")
        
        user_id = payload.get("user_id")
        if user_id is None:
            raise HTTPException(status_code=401, detail="Invalid token")
        
        # Check if user is banned
        with get_db_connection() as conn:
            with get_db_cursor(conn) as cursor:
                cursor.execute("SELECT is_banned FROM users WHERE id = %s", (user_id,))
                user = cursor.fetchone()
                if user and user.get("is_banned"):
                    raise HTTPException(status_code=403, detail="Account suspended")
        
        return user_id
    except jwt.ExpiredSignatureError:
        raise HTTPException(status_code=401, detail="Token expired")
    except jwt.JWTError as e:
        raise HTTPException(status_code=401, detail=f"Invalid token: {str(e)}")

def verify_admin(credentials: HTTPAuthorizationCredentials = Depends(security)) -> int:
    try:
        user_id = verify_token(credentials)
        
        with get_db_connection() as conn:
            with get_db_cursor(conn) as cursor:
                cursor.execute("SELECT is_admin, is_super_admin FROM users WHERE id = %s", (user_id,))
                user = cursor.fetchone()
                
                if not user or not user.get("is_admin"):
                    raise HTTPException(status_code=403, detail="Admin access required")
                
                return {
                    "user_id": user_id,
                    "is_super_admin": user.get("is_super_admin", False)
                }
    except Exception as e:
        raise HTTPException(status_code=401, detail=str(e))

def sanitize_input(text: str, allow_html: bool = False) -> str:
    """Sanitize user input to prevent XSS attacks"""
    if allow_html:
        allowed_tags = ['b', 'i', 'u', 'strong', 'em', 'p', 'br', 'a', 'img']
        allowed_attrs = {
            'a': ['href', 'title'],
            'img': ['src', 'alt', 'title', 'width', 'height']
        }
        return bleach.clean(text, tags=allowed_tags, attributes=allowed_attrs, strip=True)
    return bleach.clean(text, strip=True)

def generate_otp(length: int = 6) -> str:
    """Generate secure random OTP"""
    return ''.join(secrets.choice(string.digits) for _ in range(length))

def calculate_distance(lat1: float, lng1: float, lat2: float, lng2: float) -> float:
    """Calculate distance between two coordinates in kilometers"""
    try:
        return geopy.distance.geodesic((lat1, lng1), (lat2, lng2)).km
    except:
        return 0.0

def get_user_preferences(user_id: int) -> Dict:
    """Get user dating preferences"""
    with get_db_connection() as conn:
        with get_db_cursor(conn) as cursor:
            cursor.execute("""
                SELECT gender, looking_for, max_distance, age_range_min, age_range_max,
                       location_lat, location_lng, interests, is_premium, is_verified
                FROM users WHERE id = %s
            """, (user_id,))
            return cursor.fetchone() or {}

def calculate_match_score(user1: Dict, user2: Dict) -> float:
    """Calculate compatibility score between two users"""
    score = 0.0

    # ---------------- AGE COMPATIBILITY ----------------
    # user1 = current user preferences
    # user2 = discovered profile

    age1 = user1.get("age")  # may or may not exist
    age2 = user2.get("age")  # comes from SQL

    min_age = user1.get("age_range_min", 18)
    max_age = user1.get("age_range_max", 60)

    if age2 is not None and min_age <= age2 <= max_age:
        score += 30

    # ---------------- GENDER PREFERENCE ----------------
    looking_for1 = user1.get("looking_for", "any")
    gender2 = user2.get("gender")

    if looking_for1 == "any" or gender2 == looking_for1:
        score += 30

    # ---------------- DISTANCE COMPATIBILITY ----------------
    # distance_km already calculated in SQL
    distance = user2.get("distance_km")

    max_distance = user1.get("max_distance", 100)

    if distance is not None and max_distance > 0 and distance <= max_distance:
        score += 20 * (1 - (distance / max_distance))

    # ---------------- INTEREST MATCHING ----------------
    interests1 = set(user1.get("interests", []) or [])
    interests2 = set(user2.get("interests", []) or [])

    if interests1 and interests2:
        common = len(interests1 & interests2)
        total = len(interests1 | interests2)
        score += 20 * (common / total)

    return round(min(100, score), 2)




# ==================== EMAIL HANDLING ====================
def send_email(to_email: str, subject: str, html_content: str) -> bool:
    """Send HTML email with error handling"""
    try:
        message = MIMEMultipart("alternative")
        message["Subject"] = subject
        message["From"] = f"{EMAIL_FROM_NAME} <{SMTP_EMAIL}>"
        message["To"] = to_email
        
        text_part = MIMEText(html_content, "html")
        message.attach(text_part)
        
        with smtplib.SMTP(SMTP_SERVER, SMTP_PORT, timeout=10) as server:
            server.starttls()
            server.login(SMTP_EMAIL, SMTP_PASSWORD)
            server.send_message(message)
        
        logger.info(f"Email sent to {to_email}")
        return True
    except Exception as e:
        logger.error(f"Email sending failed: {e}")
        return False

def get_otp_email_template(otp: str, username: str) -> str:
    return f"""
    <!DOCTYPE html>
    <html>
    <head>
        <style>
            body {{ font-family: 'Arial', sans-serif; background: linear-gradient(135deg, #667eea 0%, #764ba2 100%); margin: 0; padding: 40px; }}
            .container {{ max-width: 600px; margin: 0 auto; background: white; padding: 40px; border-radius: 20px; box-shadow: 0 20px 60px rgba(0,0,0,0.3); }}
            .header {{ text-align: center; margin-bottom: 40px; }}
            .logo {{ font-size: 36px; font-weight: 800; background: linear-gradient(135deg, #667eea 0%, #764ba2 100%); -webkit-background-clip: text; -webkit-text-fill-color: transparent; }}
            .otp-box {{ background: linear-gradient(135deg, #667eea 0%, #764ba2 100%); color: white; padding: 30px; text-align: center; border-radius: 15px; margin: 40px 0; }}
            .otp-code {{ font-size: 48px; font-weight: bold; letter-spacing: 10px; margin: 20px 0; font-family: monospace; }}
            .footer {{ text-align: center; color: #666; font-size: 12px; margin-top: 40px; padding-top: 20px; border-top: 1px solid #eee; }}
        </style>
    </head>
    <body>
        <div class="container">
            <div class="header">
                <div class="logo">meiXuP</div>
                <h1 style="color: #333;">🔐 Verify Your Email</h1>
            </div>
            <p style="color: #555; line-height: 1.6;">Hello <strong>{username}</strong>,</p>
            <p style="color: #555; line-height: 1.6;">Welcome to meiXuP! Please use the following OTP to verify your email address:</p>
            <div class="otp-box">
                <div class="otp-code">{otp}</div>
                <p style="margin: 20px 0 0 0; opacity: 0.9;">This code expires in 10 minutes</p>
            </div>
            <p style="color: #555; line-height: 1.6;">If you didn't request this code, please ignore this email.</p>
            <div class="footer">
                <p>© 2025 meiXuP Premium Social Platform. All rights reserved.</p>
                <p>This is an automated message, please do not reply.</p>
            </div>
        </div>
    </body>
    </html>
    """

def get_welcome_email_template(username: str) -> str:
    return f"""
    <!DOCTYPE html>
    <html>
    <head>
        <style>
            body {{ font-family: 'Arial', sans-serif; background: linear-gradient(135deg, #667eea 0%, #764ba2 100%); margin: 0; padding: 40px; }}
            .container {{ max-width: 600px; margin: 0 auto; background: white; padding: 40px; border-radius: 20px; box-shadow: 0 20px 60px rgba(0,0,0,0.3); }}
            .header {{ text-align: center; margin-bottom: 40px; }}
            .logo {{ font-size: 36px; font-weight: 800; background: linear-gradient(135deg, #667eea 0%, #764ba2 100%); -webkit-background-clip: text; -webkit-text-fill-color: transparent; }}
            .welcome-banner {{ background: linear-gradient(135deg, #667eea 0%, #764ba2 100%); color: white; padding: 50px 40px; text-align: center; border-radius: 15px; margin: 30px 0; }}
            .feature-grid {{ display: grid; grid-template-columns: repeat(2, 1fr); gap: 20px; margin: 40px 0; }}
            .feature {{ background: #f8f9fa; padding: 20px; border-radius: 10px; text-align: center; }}
            .feature-icon {{ font-size: 30px; margin-bottom: 15px; }}
            .btn-primary {{ display: inline-block; background: linear-gradient(135deg, #667eea 0%, #764ba2 100%); color: white; padding: 15px 40px; text-decoration: none; border-radius: 10px; font-weight: bold; margin: 20px 0; }}
            .footer {{ text-align: center; color: #666; font-size: 12px; margin-top: 40px; padding-top: 20px; border-top: 1px solid #eee; }}
        </style>
    </head>
    <body>
        <div class="container">
            <div class="header">
                <div class="logo">meiXuP</div>
                <h1 style="color: #333;">🎉 Welcome to Premium!</h1>
            </div>
            <div class="welcome-banner">
                <h2 style="margin: 0 0 15px 0;">Hello {username}!</h2>
                <p style="margin: 0; opacity: 0.9;">Your premium account has been successfully activated.</p>
            </div>
            <p style="color: #555; line-height: 1.6; text-align: center;">Get ready to experience exclusive features:</p>
            <div class="feature-grid">
                <div class="feature">
                    <div class="feature-icon">🚀</div>
                    <h3 style="margin: 0 0 10px 0;">AI Matchmaking</h3>
                    <p style="margin: 0; color: #666;">Smart compatibility scoring</p>
                </div>
                <div class="feature">
                    <div class="feature-icon">💎</div>
                    <h3 style="margin: 0 0 10px 0;">Verified Badge</h3>
                    <p style="margin: 0; color: #666;">Stand out with verification</p>
                </div>
                <div class="feature">
                    <div class="feature-icon">🔍</div>
                    <h3 style="margin: 0 0 10px 0;">Advanced Search</h3>
                    <p style="margin: 0; color: #666;">Filter by interests & location</p>
                </div>
                <div class="feature">
                    <div class="feature-icon">📈</div>
                    <h3 style="margin: 0 0 10px 0;">Profile Analytics</h3>
                    <p style="margin: 0; color: #666;">Track your profile views</p>
                </div>
            </div>
            <div style="text-align: center;">
                <a href="#" class="btn-primary">Start Exploring Now</a>
            </div>
            <div class="footer">
                <p>© 2025 meiXuP Premium Social Platform. All rights reserved.</p>
                <p>Need help? Contact support@meixup.com</p>
            </div>
        </div>
    </body>
    </html>
    """
    
    
    
    
    
    # ==================== FILE UPLOAD HANDLING ====================

async def save_upload_file(file: UploadFile, folder: str, user_id: int) -> Dict[str, str]:
    """Save uploaded file with compression and thumbnail generation"""
    try:
        # Validate file size
        if file.size > MAX_FILE_SIZE:
            raise HTTPException(status_code=400, detail="File too large")
        
        # Generate unique filename
        file_extension = os.path.splitext(file.filename)[1].lower()
        unique_filename = f"{user_id}_{uuid.uuid4()}{file_extension}"
        file_path = UPLOAD_DIR / folder / unique_filename
        
        # Save file
        with file_path.open("wb") as buffer:
            content = await file.read()
            buffer.write(content)
        
        result = {
            "filename": unique_filename,
            "url": f"/assets/{folder}/{unique_filename}",
            "size": file.size,
            "content_type": file.content_type
        }
        
        # Generate thumbnail for images
        if file.content_type in ALLOWED_IMAGE_TYPES:
            try:
                img = Image.open(file_path)
                # Resize if too large
                if max(img.size) > 2000:
                    img.thumbnail((2000, 2000), Image.Resampling.LANCZOS)
                    img.save(file_path, optimize=True, quality=85)
                
                # Generate thumbnail
                img.thumbnail((300, 300), Image.Resampling.LANCZOS)
                thumb_path = UPLOAD_DIR / folder / f"thumb_{unique_filename}"
                img.save(thumb_path, optimize=True, quality=80)
                
                result["thumbnail"] = f"/assets/{folder}/thumb_{unique_filename}"
            except Exception as e:
                logger.error(f"Image processing error: {e}")
        
        return result
        
    except Exception as e:
        logger.error(f"File upload error: {e}")
        raise HTTPException(status_code=500, detail="File upload failed")
    
    
    
    
    
# ==================== API ENDPOINTS ====================

@app.get("/")
async def root():
    return {
        "success": True,
        "message": "🚀 meiXuP Premium Social Platform API",
        "version": "2.0.0",
        "status": "running",
        "timestamp": datetime.now().isoformat()
    }

@app.get("/health")
async def health_check():
    # """Health check endpoint"""
    health_status = {
        "status": "HEALTHY",
        "timestamp": datetime.now().isoformat(),
        "database": "MYSQL",
        "redis": "CONNECTED" if redis_client else "NOT_CONFIGURED",
        "firebase": "CONNECTED" if firebase_admin else "NOT_CONFIGURED"
    }
    
    
    # Check database
    try:
        with get_db_connection() as conn:
            health_status["database"] = "connected"
    except Exception as e:
        health_status["database"] = f"error: {str(e)}"
    
    # Check Redis
    if redis_client:
        try:
            redis_client.ping()
            health_status["redis"] = "connected"
        except Exception as e:
            health_status["redis"] = f"error: {str(e)}"
    
    # Check Firebase
    health_status["firebase"] = "not_configured" if firebase_admin is None else "connected"
    
    return health_status




# ==================== AUTHENTICATION ====================

@app.post("/auth/register")
async def register_user(user: UserRegister, background_tasks: BackgroundTasks):
    # """Register new user with OTP verification"""
    # body = await user.email
    # print(body)
    email = user.email.lower()
    username = sanitize_input(user.username)
    
    with get_db_connection() as conn:
        with get_db_cursor(conn) as cursor:
            # Check existing user
            cursor.execute(
                "SELECT id FROM users WHERE email = %s OR username = %s",
                (email, username)
            )
            if cursor.fetchone():
                raise HTTPException(
                    status_code=400,
                    detail="Email or username already registered"
                )
            
            # Generate OTP
            otp = generate_otp()
            
            # Hash password
            hashed_password = hash_password(user.password)
            
            # Store OTP data
            otp_storage.set(email, otp, {
                **user.dict(exclude={"password"}),
                "password_hash": hashed_password
            })
            
            # Send OTP email
            html_content = get_otp_email_template(otp, username)
            background_tasks.add_task(
                send_email,
                email,
                "🔐 Verify Your Email - meiXuP",
                html_content
            )
            
            return {
                "success": True,
                "message": "OTP sent to your email",
                "email": email,
                "expires_in": "10 minutes"
            }
            
@app.post("/auth/verify-otp")
async def verify_user_otp(
    data: OTPVerify,
    background_tasks: BackgroundTasks
):
    """Verify OTP and create user account"""

    email = data.email.lower()
    stored_data = otp_storage.get(email)

    if not stored_data:
        raise HTTPException(
            status_code=400,
            detail="No OTP request found or expired"
        )

    if stored_data["otp"] != data.otp:
        raise HTTPException(
            status_code=400,
            detail="Invalid OTP"
        )

    user_data = stored_data["data"]

    # ---------- SAFE DATA EXTRACTION ----------
    try:
        dob = (
            datetime.strptime(user_data["date_of_birth"], "%Y-%m-%d").date()
            if isinstance(user_data["date_of_birth"], str)
            else user_data["date_of_birth"]
        )
    except Exception:
        raise HTTPException(
            status_code=400,
            detail="Invalid date of birth format"
        )

    interests = user_data.get("interests") or []
    if not isinstance(interests, list):
        interests = []

    with get_db_connection() as conn:
        with get_db_cursor(conn) as cursor:
            try:
                # ---------- FINAL DUPLICATE CHECK ----------
                cursor.execute(
                    "SELECT id FROM users WHERE email = %s OR username = %s",
                    (email, user_data["username"])
                )
                if cursor.fetchone():
                    raise HTTPException(
                        status_code=400,
                        detail="User already exists"
                    )

                # ---------- CREATE USER ----------
                cursor.execute("""
                    INSERT INTO users (
                        email,
                        username,
                        full_name,
                        password_hash,
                        date_of_birth,
                        gender,
                        bio,
                        interests,
                        location_lat,
                        location_lng,
                        looking_for,
                        max_distance,
                        age_range_min,
                        age_range_max,
                        is_verified,
                        created_at
                    ) VALUES (%s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s)
                """, (
                    email,
                    user_data["username"],
                    user_data["full_name"],
                    user_data["password_hash"],
                    dob,
                    user_data.get("gender"),
                    user_data.get("bio"),
                    json.dumps(interests),
                    user_data.get("location_lat"),
                    user_data.get("location_lng"),
                    user_data.get("looking_for", "any"),
                    user_data.get("max_distance", 100),
                    user_data.get("age_range_min", 18),
                    user_data.get("age_range_max", 60),
                    True,
                    datetime.utcnow()
                ))

                conn.commit()
                user_id = cursor.lastrowid

            except HTTPException:
                conn.rollback()
                raise
            except Exception as e:
                conn.rollback()
                raise HTTPException(
                    status_code=500,
                    detail="Failed to create user account"
                )

    # ---------- TOKEN GENERATION ----------
    access_token = create_access_token({"user_id": user_id})
    refresh_token = create_refresh_token({"user_id": user_id})

    # ---------- CLEAN OTP ----------
    otp_storage.delete(email)

    # ---------- SEND WELCOME EMAIL ----------
    html_content = get_welcome_email_template(user_data["username"])
    background_tasks.add_task(
        send_email,
        email,
        "🎉 Welcome to meiXuP Premium!",
        html_content
    )

    return {
        "success": True,
        "message": "Account created successfully",
        "access_token": access_token,
        "refresh_token": refresh_token,
        "token_type": "bearer",
        "user": {
            "id": user_id,
            "email": email,
            "username": user_data["username"],
            "full_name": user_data["full_name"],
            "is_verified": True,
            "is_premium": False
        }
    }

@app.post("/auth/login")
async def user_login(login_data: LoginRequest):
    """User login with email/password"""
    email = login_data.email.lower()
    
    with get_db_connection() as conn:
        with get_db_cursor(conn) as cursor:
            cursor.execute("""
                SELECT id, email, username, password_hash, is_verified, is_premium, is_banned
                FROM users WHERE email = %s
            """, (email,))
            
            user = cursor.fetchone()
            if not user:
                raise HTTPException(status_code=401, detail="Invalid credentials")
            
            if user["is_banned"]:
                raise HTTPException(status_code=403, detail="Account suspended")
            
            if not verify_password(login_data.password, user["password_hash"]):
                raise HTTPException(status_code=401, detail="Invalid credentials")
            
            # Generate tokens
            access_token = create_access_token({"user_id": user["id"]})
            refresh_token = create_refresh_token({"user_id": user["id"]})
            
            # Update last login
            cursor.execute("UPDATE users SET last_login = %s WHERE id = %s", 
                         (datetime.now(), user["id"]))
            conn.commit()
            
            return {
                "success": True,
                "message": "Login successful",
                "access_token": access_token,
                "refresh_token": refresh_token,
                "token_type": "bearer",
                "user": {
                    "id": user["id"],
                    "email": user["email"],
                    "username": user["username"],
                    "is_verified": user["is_verified"],
                    "is_premium": user["is_premium"]
                }
            }

@app.post("/auth/google")
async def google_auth(data: GoogleAuthToken):
    """Authenticate with Google via Firebase"""
    if firebase_admin is None:
        raise HTTPException(status_code=503, detail="Google auth not configured")
    
    try:
        # Verify Google token
        decoded_token = firebase_auth.verify_id_token(data.id_token)
        email = decoded_token["email"].lower()
        google_uid = decoded_token["uid"]
        
        with get_db_connection() as conn:
            with get_db_cursor(conn) as cursor:
                # Check if user exists
                cursor.execute("SELECT * FROM users WHERE email = %s", (email,))
                user = cursor.fetchone()
                
                if not user:
                    # Create new user
                    username = email.split("@")[0] + str(uuid.uuid4())[:4]
                    full_name = decoded_token.get("name", "User")
                    profile_pic = decoded_token.get("picture")
                    
                    cursor.execute("""
                        INSERT INTO users (
                            email, username, full_name, google_uid, profile_picture,
                            is_verified, created_at
                        ) VALUES (%s, %s, %s, %s, %s, %s, %s)
                    """, (email, username, full_name, google_uid, profile_pic, True, datetime.now()))
                    
                    conn.commit()
                    user_id = cursor.lastrowid
                else:
                    user_id = user["id"]
                    
                    # Update google_uid if not set
                    if not user.get("google_uid"):
                        cursor.execute("UPDATE users SET google_uid = %s WHERE id = %s", 
                                     (google_uid, user_id))
                        conn.commit()
                
                # Generate tokens
                access_token = create_access_token({"user_id": user_id})
                refresh_token = create_refresh_token({"user_id": user_id})
                
                return {
                    "success": True,
                    "access_token": access_token,
                    "refresh_token": refresh_token,
                    "token_type": "bearer",
                    "user": {
                        "id": user_id,
                        "email": email,
                        "username": user["username"] if user else username,
                        "is_verified": True,
                        "is_premium": user["is_premium"] if user else False
                    }
                }
    
    except Exception as e:
        logger.error(f"Google auth error: {e}")
        raise HTTPException(status_code=401, detail=f"Google authentication failed: {str(e)}")

@app.post("/auth/refresh")
async def refresh_token(data: TokenRefresh):
    """Refresh access token using refresh token"""
    try:
        payload = jwt.decode(data.refresh_token, SECRET_KEY, algorithms=[ALGORITHM])
        
        if payload.get("type") != "refresh":
            raise HTTPException(status_code=401, detail="Invalid token type")
        
        user_id = payload.get("user_id")
        
        # Generate new access token
        access_token = create_access_token({"user_id": user_id})
        
        return {
            "success": True,
            "access_token": access_token,
            "token_type": "bearer"
        }
    
    except jwt.ExpiredSignatureError:
        raise HTTPException(status_code=401, detail="Refresh token expired")
    except jwt.JWTError as e:
        raise HTTPException(status_code=401, detail=f"Invalid refresh token: {str(e)}")
    
    
@app.post("/auth/logout")
async def user_logout(
    user_id: int = Depends(verify_token),
    credentials: HTTPAuthorizationCredentials = Depends(security)
):
    """User logout - invalidate refresh token"""
    # In a real implementation, you might want to:
    # 1. Add the refresh token to a blacklist
    # 2. Store blacklist in Redis
    # 3. Check blacklist during token refresh
    
    # For now, we just disconnect WebSocket if connected
    if user_id in manager.active_connections:
        manager.disconnect(user_id)
    
    return {
        "success": True,
        "message": "Logged out successfully"
    }
    
    
OTP_EXPIRY = int(os.getenv("OTP_EXPIRY_MINUTES", 10))
password_reset_otp: dict = {}

@app.post("/auth/forgot-password")
async def forgot_password(data: ForgotPassword):
    email = data.email.lower()

    # 🔍 Fetch user first
    with get_db_connection() as conn:
        with get_db_cursor(conn) as cursor:
            cursor.execute(
                "SELECT username FROM users WHERE email = %s",
                (email,)
            )
            user = cursor.fetchone()

            if not user:
                # Security best practice: do NOT reveal account existence
                return {"success": True}

    username = user["username"]

    # 🔐 Generate OTP
    otp = str(random.randint(100000, 999999))
    expiry = datetime.utcnow() + timedelta(minutes=OTP_EXPIRY)

    password_reset_otp[email] = {
        "otp": otp,
        "expiry": expiry,
        "verified": False
    }

    # 📩 Send professional email
    send_email(
        email,
        "🔐 Reset Your meiXuP Password",
        f"""
<!DOCTYPE html>
<html lang="en">
<head>
  <meta charset="UTF-8" />
  <meta name="viewport" content="width=device-width, initial-scale=1.0" />
</head>
<body style="margin:0; padding:0; background-color:#f5f7fb; font-family:Arial, Helvetica, sans-serif;">

<table width="100%" cellpadding="0" cellspacing="0" style="padding:20px;">
<tr>
<td align="center">

<table width="520" cellpadding="0" cellspacing="0"
style="background:#ffffff; border-radius:12px; padding:32px;
box-shadow:0 10px 30px rgba(0,0,0,0.08);">

<!-- Header -->
<tr>
<td align="center" style="padding-bottom:20px;">
  <h1 style="margin:0; font-size:28px; color:#FF6B9D;">meiXuP</h1>
  <p style="margin-top:6px; font-size:14px; color:#6b7280;">
    Secure Account Services
  </p>
</td>
</tr>

<!-- Content -->
<tr>
<td style="font-size:16px; color:#111827; line-height:24px;">
  <p>Hello <strong>{username}</strong>,</p>

  <p>
    We received a request to reset your <strong>meiXuP</strong> account password.
    Use the one-time password (OTP) below to proceed.
  </p>

  <!-- OTP -->
  <div style="margin:30px 0; text-align:center;">
    <div style="
      display:inline-block;
      background:#FFF5F7;
      border:2px dashed #FF6B9D;
      padding:18px 28px;
      border-radius:12px;
      font-size:32px;
      letter-spacing:6px;
      font-weight:700;
      color:#FF6B9D;">
      {otp}
    </div>
  </div>

  <p style="text-align:center; font-size:14px; color:#6b7280;">
    This OTP is valid for <strong>{OTP_EXPIRY} minutes</strong>.
  </p>

  <p>
    If you did not request this password reset, you can safely ignore this email.
    No changes will be made to your account.
  </p>

  <p style="margin-top:24px;">
    Regards,<br />
    <strong>meiXuP Security Team</strong>
  </p>
</td>
</tr>

<!-- Footer -->
<tr>
<td style="border-top:1px solid #e5e7eb; padding-top:16px;
font-size:12px; color:#9ca3af; text-align:center;">
© {datetime.utcnow().year} meiXuP · All rights reserved<br />
This is an automated security message. Please do not reply.
</td>
</tr>

</table>

</td>
</tr>
</table>

</body>
</html>
"""
    )

    return {"success": True}


    
@app.post("/auth/verify-reset-otp")
async def verify_reset_otp(data: VerifyResetOTP):
    record = password_reset_otp.get(data.email)

    if not record:
        raise HTTPException(400, "OTP not found")

    if record["otp"] != data.otp:
        raise HTTPException(400, "Invalid OTP")

    if record["expiry"] < datetime.utcnow():
        raise HTTPException(400, "OTP expired")

    # 🔥 Mark OTP as verified
    record["verified"] = True

    return {
        "success": True,
        "message": "OTP verified"
    }




@app.post("/auth/reset-password")
async def reset_password(data: ResetPassword):
    email = data.email.lower()

    # Ensure OTP was verified
    record = password_reset_otp.get(email)
    if not record or not record.get("verified"):
        raise HTTPException(
            status_code=403,
            detail="OTP verification required"
        )

    # Hash password using SAME logic as login
    hashed_password = hash_password(data.new_password)

    with get_db_connection() as conn:
        with get_db_cursor(conn) as cursor:
            cursor.execute(
                "SELECT id, username FROM users WHERE email = %s",
                (email,)
            )
            user = cursor.fetchone()

            if not user:
                raise HTTPException(
                    status_code=404,
                    detail="User not found"
                )

            cursor.execute(
                "UPDATE users SET password_hash = %s WHERE email = %s",
                (hashed_password, email)
            )
            conn.commit()

    # Cleanup OTP
    password_reset_otp.pop(email, None)

    # 📩 Send confirmation email
    send_email(
        email,
        "✅ Your meiXuP Password Has Been Reset",
        f"""
<!DOCTYPE html>
<html>
<head>
  <meta charset="UTF-8" />
</head>
<body style="margin:0; padding:0; background:#f5f7fb; font-family:Arial, Helvetica, sans-serif;">
  <table width="100%" cellpadding="0" cellspacing="0" style="padding:20px;">
    <tr>
      <td align="center">
        <table width="520" cellpadding="0" cellspacing="0"
          style="background:#ffffff; border-radius:12px; padding:32px; box-shadow:0 10px 30px rgba(0,0,0,0.08);">

          <!-- Header -->
          <tr>
            <td align="center" style="padding-bottom:20px;">
              <h1 style="margin:0; color:#FF6B9D;">meiXuP</h1>
              <p style="margin:6px 0 0; color:#6b7280; font-size:14px;">
                Account Security Notification
              </p>
            </td>
          </tr>

          <!-- Content -->
          <tr>
            <td style="color:#111827; font-size:16px; line-height:24px;">
              <p>Hello <strong>{user["username"]}</strong>,</p>

              <p>
                This is a confirmation that your <strong>meiXuP</strong> account password
                was successfully reset.
              </p>

              <div style="
                margin:24px 0;
                padding:16px;
                background:#ecfeff;
                border-left:4px solid #22d3ee;
                border-radius:8px;
                color:#0f172a;
              ">
                🕒 <strong>Reset Time:</strong> {datetime.utcnow().strftime("%d %b %Y, %H:%M UTC")}
              </div>

              <p>
                If you made this change, no further action is required.
              </p>

              <p style="color:#b91c1c;">
                <strong>Didn't reset your password?</strong><br />
                Please secure your account immediately by contacting our support team.
              </p>

              <p style="margin-top:24px;">
                Stay safe,<br />
                <strong>meiXuP Security Team</strong>
              </p>
            </td>
          </tr>

          <!-- Footer -->
          <tr>
            <td style="border-top:1px solid #e5e7eb; padding-top:16px;
              font-size:12px; color:#9ca3af; text-align:center;">
              © {datetime.utcnow().year} meiXuP · All rights reserved<br />
              This is an automated security message. Please do not reply.
            </td>
          </tr>

        </table>
      </td>
    </tr>
  </table>
</body>
</html>
"""
    )

    return {
        "success": True,
        "message": "Password reset successful"
    }




# ==================== USER PROFILE ====================
@app.get("/auth/me")
async def get_current_user(user_id: int = Depends(verify_token)):
    """Get current user profile"""
    with get_db_connection() as conn:
        with get_db_cursor(conn) as cursor:
            cursor.execute("""
                SELECT 
                    u.id,
                    u.email,
                    u.username,
                    u.full_name,
                    u.date_of_birth,
                    u.gender,
                    u.bio,

                    -- ✅ FIXED PROFILE PICTURE PATH
                    CASE
                        WHEN u.profile_picture IS NULL OR u.profile_picture = ''
                            THEN NULL
                        WHEN u.profile_picture LIKE '/assets/%'
                            THEN u.profile_picture
                        ELSE CONCAT('/assets/profiles/', u.profile_picture)
                    END AS profile_picture,

                    u.cover_photo,
                    u.is_verified,
                    u.is_premium,
                    u.location_lat,
                    u.location_lng,
                    u.looking_for,
                    u.max_distance,
                    u.age_range_min,
                    u.age_range_max,
                    u.interests,
                    u.created_at,
                    u.last_login,
                    u.is_incognito,
                    u.show_distance,
                    u.show_age,
                    u.show_online_status,
                    u.premium_expires_at,

                    (SELECT COUNT(*) FROM posts WHERE user_id = u.id) AS posts_count,
                    (SELECT COUNT(*) FROM follows WHERE follower_id = u.id) AS following_count,
                    (SELECT COUNT(*) FROM follows WHERE following_id = u.id) AS followers_count,
                    (SELECT COUNT(*) FROM matches 
                     WHERE user1_id = u.id OR user2_id = u.id) AS matches_count

                FROM users u
                WHERE u.id = %s
            """, (user_id,))
            
            user = cursor.fetchone()
            if not user:
                raise HTTPException(status_code=404, detail="User not found")
            
            return {
                "success": True,
                "user": user
            }

        

@app.post("/user/profile/upload-picture")
async def upload_profile_picture(
    file: UploadFile = File(...),
    user_id: int = Depends(verify_token)
):
    """Upload profile picture with compression"""
    if file.content_type not in ALLOWED_IMAGE_TYPES:
        raise HTTPException(status_code=400, detail="Invalid file type")
    
    file_info = await save_upload_file(file, "profiles", user_id)
    
    with get_db_connection() as conn:
        with get_db_cursor(conn) as cursor:
            cursor.execute(
                "UPDATE users SET profile_picture = %s WHERE id = %s",
                (file_info["filename"], user_id)
            )
            conn.commit()
            
            return {
                "success": True,
                "message": "Profile picture updated",
                "image_url": file_info["url"],
                "thumbnail_url": file_info.get("thumbnail")
            }

@app.post("/user/profile/update")
async def update_user_profile(
    profile_data: ProfileUpdate,
    user_id: int = Depends(verify_token)
):
    """Update user profile information"""
    update_fields = []
    update_values = []
    
    # Build dynamic update query
    if profile_data.full_name is not None:
        update_fields.append("full_name = %s")
        update_values.append(sanitize_input(profile_data.full_name))
    
    if profile_data.bio is not None:
        update_fields.append("bio = %s")
        update_values.append(sanitize_input(profile_data.bio))
    
    if profile_data.gender is not None:
        update_fields.append("gender = %s")
        update_values.append(profile_data.gender)
    
    if profile_data.looking_for is not None:
        update_fields.append("looking_for = %s")
        update_values.append(profile_data.looking_for)
    
    if profile_data.max_distance is not None:
        update_fields.append("max_distance = %s")
        update_values.append(profile_data.max_distance)
    
    if profile_data.age_range_min is not None:
        update_fields.append("age_range_min = %s")
        update_values.append(profile_data.age_range_min)
    
    if profile_data.age_range_max is not None:
        update_fields.append("age_range_max = %s")
        update_values.append(profile_data.age_range_max)
    
    if profile_data.interests is not None:
        update_fields.append("interests = %s")
        update_values.append(json.dumps(profile_data.interests))
    
    if profile_data.is_incognito is not None:
        update_fields.append("is_incognito = %s")
        update_values.append(profile_data.is_incognito)
    
    if profile_data.show_distance is not None:
        update_fields.append("show_distance = %s")
        update_values.append(profile_data.show_distance)
    
    if profile_data.show_age is not None:
        update_fields.append("show_age = %s")
        update_values.append(profile_data.show_age)
    
    if profile_data.show_online_status is not None:
        update_fields.append("show_online_status = %s")
        update_values.append(profile_data.show_online_status)
    
    if not update_fields:
        return {"success": True, "message": "No changes made"}
    
    update_values.append(user_id)
    query = f"UPDATE users SET {', '.join(update_fields)} WHERE id = %s"
    
    with get_db_connection() as conn:
        with get_db_cursor(conn) as cursor:
            cursor.execute(query, update_values)
            conn.commit()
            
            return {
                "success": True,
                "message": "Profile updated successfully"
            }

@app.post("/user/location/update")
async def update_user_location(
    location: LocationUpdate,
    user_id: int = Depends(verify_token)
):
    """Update user location for better matching"""
    with get_db_connection() as conn:
        with get_db_cursor(conn) as cursor:
            cursor.execute("""
                UPDATE users 
                SET location_lat = %s, location_lng = %s, location_updated_at = %s
                WHERE id = %s
            """, (location.lat, location.lng, datetime.now(), user_id))
            conn.commit()
            
            return {
                "success": True,
                "message": "Location updated",
                "lat": location.lat,
                "lng": location.lng
            }

@app.get("/user/profile/{user_id}")
async def get_user_profile(
    user_id: int,
    current_user_id: int = Depends(verify_token)
):
    """Get user profile by ID"""
    with get_db_connection() as conn:
        with get_db_cursor(conn) as cursor:
            cursor.execute("""
                SELECT 
                    u.id, u.username, u.full_name, u.bio, u.profile_picture, u.cover_photo,
                    u.is_verified, u.is_premium, u.created_at,
                    TIMESTAMPDIFF(YEAR, u.date_of_birth, CURDATE()) as age,
                    u.gender, u.looking_for, u.interests,
                    CASE 
                        WHEN u.show_age = FALSE THEN NULL
                        ELSE TIMESTAMPDIFF(YEAR, u.date_of_birth, CURDATE())
                    END as display_age,
                    CASE 
                        WHEN u.show_distance = FALSE THEN NULL
                        ELSE ROUND(
                            ST_Distance_Sphere(
                                point(u.location_lng, u.location_lat),
                                point(cu.location_lng, cu.location_lat)
                            ) / 1000, 1
                        )
                    END as distance_km,
                    (SELECT COUNT(*) FROM posts WHERE user_id = u.id) as posts_count,
                    (SELECT COUNT(*) FROM follows WHERE follower_id = u.id) as following_count,
                    (SELECT COUNT(*) FROM follows WHERE following_id = u.id) as followers_count,
                    EXISTS(SELECT 1 FROM follows WHERE follower_id = %s AND following_id = u.id) as is_following,
                    EXISTS(SELECT 1 FROM blocks WHERE blocker_id = %s AND blocked_id = u.id) as is_blocked,
                    EXISTS(SELECT 1 FROM blocks WHERE blocker_id = u.id AND blocked_id = %s) as has_blocked_you
                FROM users u
                CROSS JOIN (SELECT location_lat, location_lng FROM users WHERE id = %s) cu
                WHERE u.id = %s AND u.is_banned = FALSE
            """, (current_user_id, current_user_id, current_user_id, current_user_id, user_id))
            
            user = cursor.fetchone()
            if not user:
                raise HTTPException(status_code=404, detail="User not found")
            
            if user["has_blocked_you"]:
                raise HTTPException(status_code=403, detail="Cannot view this profile")
            
            return {"success": True, "user": user}
        

# ==================== FOLLOW SYSTEM ====================

@app.post("/user/follow/{target_user_id}")
async def follow_user(
    target_user_id: int,
    user_id: int = Depends(verify_token)
):
    """Follow/unfollow user"""
    if target_user_id == user_id:
        raise HTTPException(status_code=400, detail="Cannot follow yourself")
    
    with get_db_connection() as conn:
        with get_db_cursor(conn) as cursor:
            # Check if blocked
            cursor.execute("""
                SELECT id FROM blocks 
                WHERE (blocker_id = %s AND blocked_id = %s)
                OR (blocker_id = %s AND blocked_id = %s)
            """, (user_id, target_user_id, target_user_id, user_id))
            
            if cursor.fetchone():
                raise HTTPException(status_code=403, detail="Cannot follow this user")
            
            # Check if already following
            cursor.execute("""
                SELECT id FROM follows 
                WHERE follower_id = %s AND following_id = %s
            """, (user_id, target_user_id))
            
            existing = cursor.fetchone()
            
            if existing:
                # Unfollow
                cursor.execute("DELETE FROM follows WHERE id = %s", (existing["id"],))
                action = "unfollowed"
            else:
                # Follow
                cursor.execute("""
                    INSERT INTO follows (follower_id, following_id, created_at)
                    VALUES (%s, %s, %s)
                """, (user_id, target_user_id, datetime.now()))
                action = "followed"
                
                # Create notification
                cursor.execute("""
                    INSERT INTO notifications (user_id, from_user_id, type, data, created_at)
                    VALUES (%s, %s, %s, %s, %s)
                """, (target_user_id, user_id, "follow", json.dumps({}), datetime.now()))
            
            conn.commit()
            
            # Send real-time notification
            if action == "followed":
                await manager.send_personal_message({
                    "type": "follow",
                    "from_user_id": user_id,
                    "timestamp": datetime.now().isoformat()
                }, target_user_id)
            
            return {"success": True, "action": action}

@app.get("/user/followers")
async def get_followers(
    target_user_id: int,
    page: int = 1,
    limit: int = 50,
    current_user_id: int = Depends(verify_token)
):
    offset = (page - 1) * limit

    with get_db_connection() as conn:
        with get_db_cursor(conn) as cursor:
            cursor.execute("""
                SELECT 
                    u.id,
                    u.username,
                    u.full_name,
                    u.profile_picture,
                    u.is_verified,
                    u.is_premium,
                    f.created_at AS followed_at,

                    -- ✅ is_following relative to LOGGED-IN USER
                    EXISTS (
                        SELECT 1 FROM follows fx
                        WHERE fx.follower_id = %s
                        AND fx.following_id = u.id
                    ) AS is_following

                FROM follows f
                JOIN users u ON f.follower_id = u.id
                WHERE f.following_id = %s
                  AND u.is_banned = FALSE
                ORDER BY f.created_at DESC
                LIMIT %s OFFSET %s
            """, (
                current_user_id,     # for is_following
                target_user_id,      # whose followers we want
                limit,
                offset
            ))

            followers = cursor.fetchall()

            return {
                "success": True,
                "followers": followers,
                "page": page,
                "limit": limit
            }


@app.get("/user/following")
async def get_following(
    target_user_id: int,
    page: int = 1,
    limit: int = 50,
    current_user_id: int = Depends(verify_token)
):
    offset = (page - 1) * limit

    with get_db_connection() as conn:
        with get_db_cursor(conn) as cursor:
            cursor.execute("""
                SELECT 
                    u.id,
                    u.username,
                    u.full_name,
                    u.profile_picture,
                    u.is_verified,
                    u.is_premium,
                    f.created_at AS followed_at,

                    -- ✅ is_following relative to LOGGED-IN USER
                    EXISTS (
                        SELECT 1 FROM follows fx
                        WHERE fx.follower_id = %s
                        AND fx.following_id = u.id
                    ) AS is_following

                FROM follows f
                JOIN users u ON f.following_id = u.id
                WHERE f.follower_id = %s
                  AND u.is_banned = FALSE
                ORDER BY f.created_at DESC
                LIMIT %s OFFSET %s
            """, (
                current_user_id,     # for is_following
                target_user_id,      # whose following list
                limit,
                offset
            ))

            following = cursor.fetchall()

            return {
                "success": True,
                "following": following,
                "page": page,
                "limit": limit
            }

            
# follows
@app.post("/users/{target_id}/follow")
async def toggle_follow(
    target_id: int,
    user_id: int = Depends(verify_token)
):
    if target_id == user_id:
        raise HTTPException(status_code=400, detail="Cannot follow yourself")

    with get_db_connection() as conn:
        with get_db_cursor(conn) as cursor:

            # Check target user exists
            cursor.execute(
                "SELECT id FROM users WHERE id = %s AND is_banned = FALSE",
                (target_id,)
            )
            if not cursor.fetchone():
                raise HTTPException(status_code=404, detail="User not found")

            try:
                # Try follow
                cursor.execute("""
                    INSERT INTO follows (follower_id, following_id)
                    VALUES (%s, %s)
                """, (user_id, target_id))
                action = "followed"

            except Exception:
                # Already following → unfollow
                cursor.execute("""
                    DELETE FROM follows
                    WHERE follower_id = %s AND following_id = %s
                """, (user_id, target_id))
                action = "unfollowed"

            conn.commit()

            return {
                "success": True,
                "action": action
            }


@app.get("/users/{target_id}/follow-status")
async def follow_status(
    target_id: int,
    user_id: int = Depends(verify_token)
):
    with get_db_connection() as conn:
        with get_db_cursor(conn) as cursor:

            cursor.execute("""
                SELECT 1 FROM follows
                WHERE follower_id = %s AND following_id = %s
            """, (user_id, target_id))

            return {
                "success": True,
                "is_following": bool(cursor.fetchone())
            }


@app.get("/users/{user_id}/follow-counts")
async def follow_counts(user_id: int):
    with get_db_connection() as conn:
        with get_db_cursor(conn) as cursor:

            cursor.execute("""
                SELECT
                    (SELECT COUNT(*) FROM follows WHERE following_id = %s) AS followers,
                    (SELECT COUNT(*) FROM follows WHERE follower_id = %s) AS following
            """, (user_id, user_id))

            counts = cursor.fetchone()

            return {
                "success": True,
                **counts
            }


@app.get("/posts/user/{user_id}")
async def get_user_posts(
    user_id: int,
    page: int = 1,
    limit: int = 30,
    current_user_id: int = Depends(verify_token)
):
    offset = (page - 1) * limit

    with get_db_connection() as conn:
        with get_db_cursor(conn) as cursor:

            # 1️⃣ Check user exists
            cursor.execute("""
                SELECT id FROM users
                WHERE id = %s AND is_banned = FALSE
            """, (user_id,))
            if not cursor.fetchone():
                raise HTTPException(status_code=404, detail="User not found")

            # 2️⃣ Check block status (either side)
            cursor.execute("""
                SELECT 1 FROM blocks
                WHERE (blocker_id = %s AND blocked_id = %s)
                   OR (blocker_id = %s AND blocked_id = %s)
            """, (current_user_id, user_id, user_id, current_user_id))
            if cursor.fetchone():
                raise HTTPException(
                    status_code=403,
                    detail="You cannot view this user's posts"
                )

            # 3️⃣ Check follower status (for friends privacy)
            cursor.execute("""
                SELECT 1 FROM follows
                WHERE follower_id = %s AND following_id = %s
            """, (current_user_id, user_id))
            is_follower = cursor.fetchone() is not None

            # 4️⃣ Fetch posts with privacy logic
            cursor.execute("""
                SELECT
                    p.id,
                    p.user_id,
                    p.content,
                    p.media_url,
                    p.media_type,
                    p.post_type,
                    p.privacy,
                    p.tags,
                    p.location_lat,
                    p.location_lng,
                    p.created_at,

                    -- Likes
                    (SELECT COUNT(*) FROM likes WHERE post_id = p.id) AS likes_count,

                    -- Comments
                    (SELECT COUNT(*) FROM comments WHERE post_id = p.id) AS comments_count,

                    -- Saves
                    (SELECT COUNT(*) FROM saves WHERE post_id = p.id) AS saves_count,

                    -- Is liked
                    EXISTS (
                        SELECT 1 FROM likes
                        WHERE post_id = p.id AND user_id = %s
                    ) AS is_liked,

                    -- Is saved
                    EXISTS (
                        SELECT 1 FROM saves
                        WHERE post_id = p.id AND user_id = %s
                    ) AS is_saved

                FROM posts p
                WHERE p.user_id = %s
                  AND (
                        p.privacy = 'public'
                     OR (p.privacy = 'friends' AND %s = TRUE)
                     OR (p.privacy = 'private' AND p.user_id = %s)
                  )

                ORDER BY p.created_at DESC
                LIMIT %s OFFSET %s
            """, (
                current_user_id,
                current_user_id,
                user_id,
                is_follower,
                current_user_id,
                limit,
                offset
            ))

            posts = cursor.fetchall()

            return {
                "success": True,
                "posts": posts,
                "page": page,
                "limit": limit
            }
            
# ==================== POSTS & STORIES ====================

MAX_FILE_SIZE_MB = 50

@app.post("/posts")
async def create_post(
    content: Optional[str] = Form(None),
    post_type: str = Form("post"),   # post | story | reel
    privacy: str = Form("public"),   # public | friends | private
    tags: Optional[str] = Form(None),
    location_lat: Optional[float] = Form(None),
    location_lng: Optional[float] = Form(None),
    file: Optional[UploadFile] = File(None),
    user_id: int = Depends(verify_token)
):
    if post_type not in ["post", "story", "reel"]:
        raise HTTPException(400, "Invalid post type")

    if privacy not in ["public", "friends", "private"]:
        raise HTTPException(400, "Invalid privacy")

    if not content and not file:
        raise HTTPException(400, "Post must have content or media")

    tag_list = [t.strip() for t in tags.split(",")] if tags else []

    media_url = None
    media_type = None
    folder = "posts"

    if file:
        file_bytes = await file.read()
        await file.seek(0)

        size_mb = len(file_bytes) / (1024 * 1024)
        if size_mb > MAX_FILE_SIZE_MB:
            raise HTTPException(400, "File size exceeds 50MB")

        if file.content_type.startswith("image"):
            media_type = "image"
        elif file.content_type.startswith("video"):
            media_type = "video"
        elif file.content_type.startswith("audio"):
            media_type = "audio"
        else:
            raise HTTPException(400, "Unsupported media type")

        if post_type == "story":
            folder = "stories"
        elif post_type == "reel":
            folder = "reels"

        file_info = await save_upload_file(file, folder, user_id)
        media_url = f"/assets/{folder}/{file_info['filename']}"

    with get_db_connection() as conn:
        with get_db_cursor(conn) as cursor:
            cursor.execute("""
                INSERT INTO posts (
                    user_id, content, post_type, privacy,
                    media_url, media_type,
                    location_lat, location_lng,
                    tags, created_at
                )
                VALUES (%s,%s,%s,%s,%s,%s,%s,%s,%s,%s)
            """, (
                user_id,
                content,
                post_type,
                privacy,
                media_url,
                media_type,
                location_lat,
                location_lng,
                json.dumps(tag_list),
                datetime.utcnow()
            ))
            conn.commit()
            post_id = cursor.lastrowid

    return {
        "success": True,
        "post": {
            "id": post_id,
            "post_type": post_type,
            "privacy": privacy,
            "media_url": media_url,
            "media_type": media_type,
            "tags": tag_list,
            "created_at": datetime.utcnow().isoformat()
        }
    }
    
    
# frrd==================== FEED & ENGAGEMENT ====================

@app.get("/feed")
async def get_feed(
    page: int = 1,
    limit: int = 20,
    feed_type: str = "trending",
    user_id: int = Depends(verify_token)
):
    offset = (page - 1) * limit

    with get_db_connection() as conn:
        with get_db_cursor(conn) as cursor:

            profile_pic_sql = """
                CASE
                  WHEN u.profile_picture IS NULL OR u.profile_picture = ''
                    THEN NULL
                  WHEN u.profile_picture LIKE '/assets/%'
                    THEN u.profile_picture
                  ELSE CONCAT('/assets/profiles/', u.profile_picture)
                END AS profile_picture
            """

            if feed_type == "following":
                cursor.execute(f"""
                    SELECT 
                        p.*,
                        u.username,
                        u.full_name,
                        {profile_pic_sql},
                        u.is_verified,
                        u.is_premium,

                        (SELECT COUNT(*) FROM likes WHERE post_id = p.id) AS likes_count,
                        (SELECT COUNT(*) FROM comments WHERE post_id = p.id) AS comments_count,
                        (SELECT COUNT(*) FROM shares WHERE post_id = p.id) AS shares_count,

                        EXISTS(
                            SELECT 1 FROM likes 
                            WHERE post_id = p.id AND user_id = %s
                        ) AS is_liked,

                        EXISTS(
                            SELECT 1 FROM saves 
                            WHERE post_id = p.id AND user_id = %s
                        ) AS is_saved

                    FROM posts p
                    JOIN users u ON p.user_id = u.id
                    WHERE p.privacy IN ('public', 'friends')
                      AND (
                        p.user_id = %s OR p.user_id IN (
                            SELECT following_id 
                            FROM follows 
                            WHERE follower_id = %s
                        )
                      )
                      AND p.post_type = 'post'
                      AND u.is_banned = FALSE
                    ORDER BY p.created_at DESC
                    LIMIT %s OFFSET %s
                """, (user_id, user_id, user_id, user_id, limit, offset))

            elif feed_type == "discover":
                cursor.execute(f"""
                    SELECT 
                        p.*,
                        u.username,
                        u.full_name,
                        {profile_pic_sql},
                        u.is_verified,
                        u.is_premium,

                        (SELECT COUNT(*) FROM likes WHERE post_id = p.id) AS likes_count,
                        (SELECT COUNT(*) FROM comments WHERE post_id = p.id) AS comments_count,
                        (SELECT COUNT(*) FROM shares WHERE post_id = p.id) AS shares_count,

                        EXISTS(
                            SELECT 1 FROM likes 
                            WHERE post_id = p.id AND user_id = %s
                        ) AS is_liked,

                        EXISTS(
                            SELECT 1 FROM saves 
                            WHERE post_id = p.id AND user_id = %s
                        ) AS is_saved,

                        CASE 
                            WHEN JSON_CONTAINS(
                                u.interests,
                                (SELECT interests FROM users WHERE id = %s)
                            ) THEN 1
                            ELSE 0
                        END AS interest_match

                    FROM posts p
                    JOIN users u ON p.user_id = u.id
                    WHERE p.privacy = 'public'
                      AND p.user_id != %s
                      AND p.post_type = 'post'
                      AND u.is_banned = FALSE
                      AND NOT EXISTS (
                        SELECT 1 FROM blocks 
                        WHERE (blocker_id = %s AND blocked_id = p.user_id)
                           OR (blocker_id = p.user_id AND blocked_id = %s)
                      )
                    ORDER BY interest_match DESC, p.created_at DESC
                    LIMIT %s OFFSET %s
                """, (
                    user_id, user_id, user_id,
                    user_id, user_id, user_id,
                    limit, offset
                ))

            else:  # trending
                cursor.execute(f"""
                    SELECT 
                        p.*,
                        u.username,
                        u.full_name,
                        {profile_pic_sql},
                        u.is_verified,
                        u.is_premium,

                        (SELECT COUNT(*) FROM likes WHERE post_id = p.id) AS likes_count,
                        (SELECT COUNT(*) FROM comments WHERE post_id = p.id) AS comments_count,
                        (SELECT COUNT(*) FROM shares WHERE post_id = p.id) AS shares_count,

                        EXISTS(
                            SELECT 1 FROM likes 
                            WHERE post_id = p.id AND user_id = %s
                        ) AS is_liked,

                        EXISTS(
                            SELECT 1 FROM saves 
                            WHERE post_id = p.id AND user_id = %s
                        ) AS is_saved,

                        (
                          (SELECT COUNT(*) FROM likes WHERE post_id = p.id) * 2 +
                          (SELECT COUNT(*) FROM comments WHERE post_id = p.id) * 3 +
                          (SELECT COUNT(*) FROM shares WHERE post_id = p.id) * 5
                        ) AS engagement_score

                    FROM posts p
                    JOIN users u ON p.user_id = u.id
                    WHERE p.privacy = 'public'
                      AND p.created_at > DATE_SUB(NOW(), INTERVAL 7 DAY)
                      AND u.is_banned = FALSE
                    ORDER BY engagement_score DESC
                    LIMIT %s OFFSET %s
                """, (user_id, user_id, limit, offset))

            posts = cursor.fetchall()

            return {
                "success": True,
                "posts": posts,
                "page": page,
                "limit": limit,
                "feed_type": feed_type
            }


# ==================== REELS FEED ====================

@app.get("/feed/reels")
async def get_reels_feed(
    page: int = 1,
    limit: int = 10,
    user_id: int = Depends(verify_token)
):
    offset = (page - 1) * limit

    with get_db_connection() as conn:
        with get_db_cursor(conn) as cursor:

            cursor.execute("""
                SELECT
                    p.id,
                    p.user_id,
                    p.media_url,
                    p.media_type,
                    p.content,
                    p.created_at,

                    u.username,
                    u.profile_picture,
                    u.is_verified,

                    -- counts
                    (SELECT COUNT(*) FROM likes WHERE post_id = p.id) AS likes_count,
                    (SELECT COUNT(*) FROM comments WHERE post_id = p.id) AS comments_count,

                    -- flags
                    EXISTS(
                        SELECT 1 FROM likes
                        WHERE post_id = p.id AND user_id = %s
                    ) AS is_liked,

                    EXISTS(
                        SELECT 1 FROM saves
                        WHERE post_id = p.id AND user_id = %s
                    ) AS is_saved

                FROM posts p
                JOIN users u ON u.id = p.user_id

                WHERE p.post_type = 'reel'
                  AND p.media_type = 'video'
                  AND p.privacy = 'public'
                  AND u.is_banned = FALSE

                ORDER BY p.created_at DESC
                LIMIT %s OFFSET %s
            """, (
                user_id,
                user_id,
                limit,
                offset
            ))

            posts = cursor.fetchall()

            return {
                "success": True,
                "posts": posts,
                "page": page,
                "limit": limit
            }

            
@app.get("/posts/{post_id}/comments")
async def get_comments(
    post_id: int,
    page: int = 1,
    limit: int = 20,
    user_id: int = Depends(verify_token)
):
    offset = (page - 1) * limit

    with get_db_connection() as conn:
        with get_db_cursor(conn) as cursor:

            # 1️⃣ Fetch top-level comments
            cursor.execute("""
                SELECT
                    c.id, c.post_id, c.user_id, c.content, c.created_at,
                    u.username, u.profile_picture, u.is_verified,
                    (SELECT COUNT(*) FROM comment_likes WHERE comment_id = c.id) AS likes_count,
                    (SELECT COUNT(*) FROM comments WHERE parent_comment_id = c.id) AS replies_count,
                    EXISTS(
                        SELECT 1 FROM comment_likes
                        WHERE comment_id = c.id AND user_id = %s
                    ) AS is_liked
                FROM comments c
                JOIN users u ON u.id = c.user_id
                WHERE c.post_id = %s
                AND c.parent_comment_id IS NULL
                AND u.is_banned = FALSE
                ORDER BY c.created_at DESC
                LIMIT %s OFFSET %s
            """, (user_id, post_id, limit, offset))

            comments = cursor.fetchall()

            return {
                "success": True,
                "comments": comments,
                "page": page,
                "limit": limit
            }


@app.post("/posts/{post_id}/comment")
async def create_comment(
    post_id: int,
    data: CommentCreate,
    user_id: int = Depends(verify_token)
):
    with get_db_connection() as conn:
        with get_db_cursor(conn) as cursor:

            # 1️⃣ Validate post
            cursor.execute(
                "SELECT user_id FROM posts WHERE id = %s",
                (post_id,)
            )
            post = cursor.fetchone()
            if not post:
                raise HTTPException(status_code=404, detail="Post not found")

            # 2️⃣ Validate parent comment (if reply)
            if data.parent_comment_id:
                cursor.execute(
                    "SELECT id FROM comments WHERE id = %s AND post_id = %s",
                    (data.parent_comment_id, post_id)
                )
                if not cursor.fetchone():
                    raise HTTPException(status_code=400, detail="Invalid parent comment")

            # 3️⃣ Insert comment
            cursor.execute("""
                INSERT INTO comments
                (post_id, user_id, parent_comment_id, content, created_at)
                VALUES (%s, %s, %s, %s, NOW())
            """, (
                post_id,
                user_id,
                data.parent_comment_id,
                sanitize_input(data.content)
            ))

            comment_id = cursor.lastrowid

            # 4️⃣ Notification (avoid self)
            if post["user_id"] != user_id:
                cursor.execute("""
                    INSERT INTO notifications
                    (user_id, from_user_id, type, data, created_at)
                    VALUES (%s, %s, 'comment', %s, NOW())
                """, (
                    post["user_id"],
                    user_id,
                    json.dumps({
                        "post_id": post_id,
                        "comment_id": comment_id
                    })
                ))

            conn.commit()

            return {
                "success": True,
                "comment_id": comment_id
            }
            
@app.post("/comments/{comment_id}/like")
async def toggle_comment_like(
    comment_id: int,
    user_id: int = Depends(verify_token)
):
    with get_db_connection() as conn:
        with get_db_cursor(conn) as cursor:

            # 1️⃣ Check comment exists
            cursor.execute(
                "SELECT user_id FROM comments WHERE id = %s",
                (comment_id,)
            )
            comment = cursor.fetchone()
            if not comment:
                raise HTTPException(status_code=404, detail="Comment not found")

            try:
                # 2️⃣ Try to like
                cursor.execute("""
                    INSERT INTO comment_likes (comment_id, user_id)
                    VALUES (%s, %s)
                """, (comment_id, user_id))
                action = "liked"

            except Exception:
                # 3️⃣ Already liked → unlike
                cursor.execute("""
                    DELETE FROM comment_likes
                    WHERE comment_id = %s AND user_id = %s
                """, (comment_id, user_id))
                action = "unliked"

            # 4️⃣ Optional notification (skip self-like)
            if action == "liked" and comment["user_id"] != user_id:
                cursor.execute("""
                    INSERT INTO notifications
                    (user_id, from_user_id, type, data, created_at)
                    VALUES (%s, %s, 'comment_like', %s, NOW())
                """, (
                    comment["user_id"],
                    user_id,
                    json.dumps({"comment_id": comment_id})
                ))

            conn.commit()

            return {
                "success": True,
                "action": action
            }



@app.post("/posts/{post_id}/like")
async def like_post(
    post_id: int,
    user_id: int = Depends(verify_token)
):
    """Like / unlike a post and return updated state"""

    with get_db_connection() as conn:
        with get_db_cursor(conn) as cursor:

            # 1️⃣ Check if already liked
            cursor.execute("""
                SELECT id FROM likes
                WHERE post_id = %s AND user_id = %s
            """, (post_id, user_id))

            existing = cursor.fetchone()

            if existing:
                # 2️⃣ Unlike
                cursor.execute(
                    "DELETE FROM likes WHERE id = %s",
                    (existing["id"],)
                )
                action = "unliked"
                is_liked = False
            else:
                # 3️⃣ Like
                cursor.execute("""
                    INSERT INTO likes (post_id, user_id, created_at)
                    VALUES (%s, %s, %s)
                """, (post_id, user_id, datetime.now()))
                action = "liked"
                is_liked = True

                # 4️⃣ Notify post owner (skip self)
                cursor.execute(
                    "SELECT user_id FROM posts WHERE id = %s",
                    (post_id,)
                )
                post = cursor.fetchone()

                if post and post["user_id"] != user_id:
                    cursor.execute("""
                        INSERT INTO notifications
                        (user_id, from_user_id, type, data, created_at)
                        VALUES (%s, %s, 'like', %s, %s)
                    """, (
                        post["user_id"],
                        user_id,
                        json.dumps({"post_id": post_id}),
                        datetime.now()
                    ))

                    # Real-time notification
                    await manager.send_personal_message({
                        "type": "like",
                        "post_id": post_id,
                        "from_user_id": user_id,
                        "timestamp": datetime.now().isoformat()
                    }, post["user_id"])

            # 5️⃣ Get updated like count
            cursor.execute(
                "SELECT COUNT(*) AS total FROM likes WHERE post_id = %s",
                (post_id,)
            )
            likes_count = cursor.fetchone()["total"]

            conn.commit()

            return {
                "success": True,
                "action": action,      # "liked" | "unliked"
                "is_liked": is_liked,  # true | false
                "likes_count": likes_count
            }




# share

@app.post("/posts/{post_id}/share")
async def share_post(
    post_id: int,
    user_id: int = Depends(verify_token)
):
    with get_db_connection() as conn:
        with get_db_cursor(conn) as cursor:

            # 1️⃣ Check original post
            cursor.execute("""
                SELECT id, user_id, content, media_url, media_type, privacy
                FROM posts WHERE id = %s
            """, (post_id,))
            original = cursor.fetchone()

            if not original:
                raise HTTPException(status_code=404, detail="Post not found")

            # 2️⃣ Prevent duplicate share
            cursor.execute("""
                SELECT id FROM shares
                WHERE post_id = %s AND user_id = %s
            """, (post_id, user_id))

            if cursor.fetchone():
                return {
                    "success": True,
                    "shared": False,
                    "message": "Already shared"
                }

            # 3️⃣ Insert into shares table
            cursor.execute("""
                INSERT INTO shares (post_id, user_id)
                VALUES (%s, %s)
            """, (post_id, user_id))

            # 4️⃣ Create repost entry in posts table
            cursor.execute("""
                INSERT INTO posts
                (user_id, content, media_url, media_type, privacy, post_type, shared_post_id, created_at)
                VALUES (%s, %s, %s, %s, 'public', 'post', %s, NOW())
            """, (
                user_id,
                original["content"],
                original["media_url"],
                original["media_type"],
                post_id
            ))

            # 5️⃣ Notify original owner
            if original["user_id"] != user_id:
                cursor.execute("""
                    INSERT INTO notifications
                    (user_id, from_user_id, type, data, created_at)
                    VALUES (%s, %s, 'share', %s, NOW())
                """, (
                    original["user_id"],
                    user_id,
                    json.dumps({"post_id": post_id})
                ))

            conn.commit()

            return {
                "success": True,
                "shared": True
            }



# global search
# @app.get("/search")
# async def global_search(
#     q: str,
#     page: int = 1,
#     limit: int = 10,
#     user_id: int = Depends(verify_token)
# ):
#     offset = (page - 1) * limit

#     with get_db_connection() as conn:
#         with get_db_cursor(conn) as cursor:

#             # 🔍 Search users
#             cursor.execute("""
#                 SELECT
#                     id, username, full_name, profile_picture, is_verified,
#                     EXISTS(
#                         SELECT 1 FROM follows
#                         WHERE follower_id = %s AND following_id = users.id
#                     ) AS is_following
#                 FROM users
#                 WHERE (username LIKE %s OR full_name LIKE %s)
#                 AND is_banned = FALSE
#                 LIMIT %s OFFSET %s
#             """, (
#                 user_id,
#                 f"%{q}%", f"%{q}%",
#                 limit, offset
#             ))

#             users = cursor.fetchall()

#             # 🔍 Search posts
#             cursor.execute("""
#                 SELECT
#                     p.id, p.content, p.media_url, p.media_type, p.created_at,
#                     u.username, u.profile_picture, u.is_verified
#                 FROM posts p
#                 JOIN users u ON u.id = p.user_id
#                 WHERE p.privacy = 'public'
#                 AND (p.content LIKE %s OR p.tags LIKE %s)
#                 AND u.is_banned = FALSE
#                 ORDER BY p.created_at DESC
#                 LIMIT %s OFFSET %s
#             """, (
#                 f"%{q}%", f"%{q}%",
#                 limit, offset
#             ))

#             posts = cursor.fetchall()

#             return {
#                 "success": True,
#                 "query": q,
#                 "users": users,
#                 "posts": posts,
#                 "page": page
#             }
            
# ==================== SEARCH ====================

# ==================== SEARCH ====================

@app.get("/search")
async def search(
    query: str,                              # 🔑 REQUIRED (matches frontend)
    page: int = 1,
    limit: int = 20,
    user_id: int = Depends(verify_token)     # 🔐 JWT auth
):
    offset = (page - 1) * limit
    search_term = f"%{sanitize_input(query)}%"

    with get_db_connection() as conn:
        with get_db_cursor(conn) as cursor:

            # 🔍 SEARCH USERS
            cursor.execute("""
                SELECT 
                    u.id,
                    u.username,
                    u.full_name,
                    u.profile_picture,
                    u.is_verified,
                    EXISTS (
                        SELECT 1 FROM follows
                        WHERE follower_id = %s
                          AND following_id = u.id
                    ) AS is_following
                FROM users u
                WHERE (u.username LIKE %s OR u.full_name LIKE %s)
                  AND u.id != %s
                  AND u.is_banned = FALSE
                ORDER BY
                    u.is_verified DESC,
                    u.username ASC
                LIMIT %s OFFSET %s
            """, (
                user_id,
                search_term,
                search_term,
                user_id,
                limit,
                offset
            ))

            users = cursor.fetchall()

            return {
                "success": True,
                "results": users,
                "page": page,
                "limit": limit
            }

            
            
            
@app.get("/users/{target_id}/chat-permission")
async def chat_permission(
    target_id: int,
    user_id: int = Depends(verify_token)
):
    with get_db_connection() as conn:
        with get_db_cursor(conn) as cursor:

            cursor.execute("""
                SELECT
                    EXISTS(
                        SELECT 1 FROM follows
                        WHERE follower_id = %s AND following_id = %s
                    ) AS i_follow,
                    EXISTS(
                        SELECT 1 FROM follows
                        WHERE follower_id = %s AND following_id = %s
                    ) AS they_follow
            """, (
                user_id, target_id,
                target_id, user_id
            ))

            res = cursor.fetchone()

            can_chat = res["i_follow"] and res["they_follow"]

            return {
                "success": True,
                "can_chat": bool(can_chat)
            }
            
            
# ==================== DATING FEATURES ====================

@app.get("/dating/discover")
async def discover_profiles(
    page: int = 1,
    limit: int = 20,
    gender_filter: Optional[str] = None,
    age_min: Optional[int] = None,
    age_max: Optional[int] = None,
    distance_max: Optional[int] = None,
    interests: Optional[str] = None,
    user_id: int = Depends(verify_token)
):
    """Discover profiles for dating with intelligent filtering"""

    offset = (page - 1) * limit
    user_prefs = get_user_preferences(user_id) or {}

    filters = []
    where_params = []

    # ---------------- BASE FILTERS ----------------
    filters.append("u.id != %s")
    where_params.append(user_id)

    filters.append("u.is_banned = FALSE")
    filters.append("u.is_verified = TRUE")

    # ---------------- GENDER ----------------
    looking_for = user_prefs.get("looking_for", "any")
    if looking_for != "any":
        if looking_for == "both":
            filters.append("u.gender IN ('male','female')")
        else:
            filters.append("u.gender = %s")
            where_params.append(looking_for)

    # ---------------- AGE ----------------
    age_min = age_min if age_min is not None else user_prefs.get("age_range_min", 18)
    age_max = age_max if age_max is not None else user_prefs.get("age_range_max", 60)

    filters.append(
        "TIMESTAMPDIFF(YEAR, u.date_of_birth, CURDATE()) BETWEEN %s AND %s"
    )
    where_params.extend([age_min, age_max])

    # ---------------- DISTANCE (WHERE) ----------------
    distance_max = distance_max if distance_max is not None else user_prefs.get("max_distance", 100)
    user_lat = user_prefs.get("location_lat")
    user_lng = user_prefs.get("location_lng")

    if user_lat is not None and user_lng is not None and distance_max < 1000:
        filters.append("""
            ST_Distance_Sphere(
                POINT(u.location_lng, u.location_lat),
                POINT(%s, %s)
            ) <= %s * 1000
        """)
        where_params.extend([user_lng, user_lat, distance_max])

    # ---------------- INTERESTS ----------------
    if interests:
        interest_list = [sanitize_input(i.strip()) for i in interests.split(",") if i.strip()]
        if interest_list:
            filters.append("JSON_OVERLAPS(COALESCE(u.interests,'[]'), %s)")
            where_params.append(json.dumps(interest_list))

    # ---------------- SWIPES ----------------
    filters.append("""
        NOT EXISTS (
            SELECT 1 FROM swipes s
            WHERE s.user_id = %s AND s.target_user_id = u.id
        )
    """)
    where_params.append(user_id)

    # ---------------- BLOCKS ----------------
    filters.append("""
        NOT EXISTS (
            SELECT 1 FROM blocks b
            WHERE (b.blocker_id = %s AND b.blocked_id = u.id)
               OR (b.blocker_id = u.id AND b.blocked_id = %s)
        )
    """)
    where_params.extend([user_id, user_id])

    where_clause = " AND ".join(filters)

    # ---------------- FINAL QUERY ----------------
    query = f"""
        SELECT
            u.id, u.username, u.full_name, u.profile_picture, u.bio, u.gender,
            TIMESTAMPDIFF(YEAR, u.date_of_birth, CURDATE()) AS age,
            COALESCE(u.interests,'[]') AS interests,
            CASE
                WHEN %s IS NULL OR %s IS NULL
                     OR u.location_lat IS NULL OR u.location_lng IS NULL
                THEN NULL
                ELSE ROUND(
                    ST_Distance_Sphere(
                        POINT(u.location_lng, u.location_lat),
                        POINT(%s, %s)
                    ) / 1000, 1
                )
            END AS distance_km,
            u.is_premium, u.is_verified,
            (SELECT COUNT(*) FROM posts WHERE user_id = u.id) AS posts_count,
            (SELECT COUNT(*) FROM users u2 WHERE u2.gender = u.gender AND u2.id != u.id) AS same_gender_count
        FROM users u
        WHERE {where_clause}
        ORDER BY u.is_premium DESC, u.is_verified DESC, RAND()
        LIMIT %s OFFSET %s
    """

    # ---------------- PARAM ORDER (CRITICAL FIX) ----------------
    params = []

    # SELECT CASE params (appear FIRST in SQL)
    params.extend([user_lng, user_lat, user_lng, user_lat])

    # WHERE params
    params.extend(where_params)

    # LIMIT / OFFSET
    params.extend([limit, offset])

    # ---------------- EXECUTION ----------------
    with get_db_connection() as conn:
        with get_db_cursor(conn) as cursor:
            cursor.execute(query, params)
            profiles = cursor.fetchall()

            for profile in profiles:
                profile["interests"] = json.loads(profile["interests"])
                profile["match_score"] = calculate_match_score(user_prefs, profile)

            profiles.sort(key=lambda x: x["match_score"], reverse=True)

            # print("Discovery Params:", params)
            # print("Discovery Profiles:", profiles)

            return {
                "success": True,
                "profiles": profiles,
                "page": page,
                "limit": limit,
                "filters": {
                    "gender": looking_for,
                    "age_min": age_min,
                    "age_max": age_max,
                    "distance_max": distance_max
                }
            }

            
            
            
            
            
            
            

@app.post("/dating/swipe")
async def swipe_profile(
    swipe: SwipeAction,
    user_id: int = Depends(verify_token)
):
    """Swipe on a profile (like/pass/super_like)"""
    if swipe.target_user_id == user_id:
        raise HTTPException(status_code=400, detail="Cannot swipe on yourself")
    
    with get_db_connection() as conn:
        with get_db_cursor(conn) as cursor:
            # Check if already swiped
            cursor.execute("""
                SELECT id, action FROM swipes 
                WHERE user_id = %s AND target_user_id = %s
            """, (user_id, swipe.target_user_id))
            
            existing = cursor.fetchone()
            
            if existing:
                # Update existing swipe
                cursor.execute("""
                    UPDATE swipes SET action = %s, created_at = %s
                    WHERE id = %s
                """, (swipe.action, datetime.now(), existing["id"]))
                action = "updated"
            else:
                # Create new swipe
                cursor.execute("""
                    INSERT INTO swipes (user_id, target_user_id, action, created_at)
                    VALUES (%s, %s, %s, %s)
                """, (user_id, swipe.target_user_id, swipe.action, datetime.now()))
                action = "created"
            
            conn.commit()
            
            # Check for match
            is_match = False
            if swipe.action in ["like", "super_like"]:
                cursor.execute("""
                    SELECT id FROM swipes
                    WHERE user_id = %s AND target_user_id = %s AND action IN ('like', 'super_like')
                """, (swipe.target_user_id, user_id))
                
                if cursor.fetchone():
                    is_match = True
                    
                    # Create match
                    cursor.execute("""
                        INSERT INTO matches (user1_id, user2_id, created_at)
                        VALUES (%s, %s, %s)
                        ON DUPLICATE KEY UPDATE updated_at = %s
                    """, (
                        min(user_id, swipe.target_user_id),
                        max(user_id, swipe.target_user_id),
                        datetime.now(),
                        datetime.now()
                    ))
                    
                    conn.commit()
                    
                    # Send match notification to both users
                    match_data = {
                        "type": "match",
                        "match_with": swipe.target_user_id,
                        "timestamp": datetime.now().isoformat()
                    }
                    
                    await manager.send_personal_message(match_data, user_id)
                    await manager.send_personal_message(match_data, swipe.target_user_id)
            
            # Send swipe notification
            if swipe.action in ["like", "super_like"] and not is_match:
                await manager.send_personal_message({
                    "type": "like",
                    "from_user_id": user_id,
                    "is_super_like": swipe.action == "super_like",
                    "timestamp": datetime.now().isoformat()
                }, swipe.target_user_id)
            
            return {
                "success": True,
                "action": swipe.action,
                "is_match": is_match,
                "message": "Match!" if is_match else "Swipe recorded"
            }

@app.get("/dating/matches")
async def get_matches(
    page: int = 1,
    limit: int = 20,
    user_id: int = Depends(verify_token)
):
    """Get user's matches"""
    offset = (page - 1) * limit
    
    with get_db_connection() as conn:
        with get_db_cursor(conn) as cursor:
            cursor.execute("""
                SELECT 
                    m.*,
                    CASE 
                        WHEN m.user1_id = %s THEN m.user2_id
                        ELSE m.user1_id
                    END as match_user_id,
                    u.username, u.full_name, u.profile_picture, u.gender,
                    TIMESTAMPDIFF(YEAR, u.date_of_birth, CURDATE()) as age,
                    u.is_premium, u.is_verified,
                    CASE 
                        WHEN %s IS NULL OR u.location_lat IS NULL OR u.location_lng IS NULL THEN NULL
                        ELSE ROUND(
                            ST_Distance_Sphere(
                                point(u.location_lng, u.location_lat),
                                point(ul.location_lng, ul.location_lat)
                            ) / 1000, 1
                        )
                    END as distance_km,
                    (SELECT content FROM messages 
                     WHERE (sender_id = %s AND receiver_id = u.id)
                     OR (sender_id = u.id AND receiver_id = %s)
                     ORDER BY created_at DESC LIMIT 1) as last_message,
                    (SELECT COUNT(*) FROM messages 
                     WHERE sender_id = u.id AND receiver_id = %s AND is_read = FALSE) as unread_count
                FROM matches m
                JOIN users u ON u.id = CASE 
                    WHEN m.user1_id = %s THEN m.user2_id
                    ELSE m.user1_id
                END
                CROSS JOIN (SELECT location_lat, location_lng FROM users WHERE id = %s) ul
                WHERE (m.user1_id = %s OR m.user2_id = %s)
                AND u.is_banned = FALSE
                ORDER BY m.updated_at DESC
                LIMIT %s OFFSET %s
            """, (user_id, user_id, user_id, user_id, user_id, user_id, user_id, user_id, user_id, limit, offset))
            
            matches = cursor.fetchall()
            
            return {
                "success": True,
                "matches": matches,
                "page": page,
                "limit": limit
            }

@app.get("/dating/match/{match_id}")
async def get_match_details(
    match_id: int,
    user_id: int = Depends(verify_token)
):
    """Get detailed information about a specific match"""
    with get_db_connection() as conn:
        with get_db_cursor(conn) as cursor:
            cursor.execute("""
                SELECT 
                    m.*,
                    u1.username as user1_username, u1.full_name as user1_full_name,
                    u1.profile_picture as user1_profile, u1.gender as user1_gender,
                    u2.username as user2_username, u2.full_name as user2_full_name,
                    u2.profile_picture as user2_profile, u2.gender as user2_gender,
                    CASE 
                        WHEN m.user1_id = %s THEN u2.id
                        ELSE u1.id
                    END as other_user_id,
                    CASE 
                        WHEN m.user1_id = %s THEN u2.username
                        ELSE u1.username
                    END as other_username
                FROM matches m
                JOIN users u1 ON m.user1_id = u1.id
                JOIN users u2 ON m.user2_id = u2.id
                WHERE m.id = %s AND (m.user1_id = %s OR m.user2_id = %s)
            """, (user_id, user_id, match_id, user_id, user_id))
            
            match = cursor.fetchone()
            if not match:
                raise HTTPException(status_code=404, detail="Match not found")
            
            return {"success": True, "match": match}

# ==================== CHAT & MESSAGING ====================

@app.post("/chat/messages")
async def send_message(
    message: MessageCreate,
    user_id: int = Depends(verify_token)
):
    """Send message to another user"""
    if message.receiver_id == user_id:
        raise HTTPException(status_code=400, detail="Cannot message yourself")
    
    with get_db_connection() as conn:
        with get_db_cursor(conn) as cursor:
            # Check if blocked
            cursor.execute("""
                SELECT id FROM blocks 
                WHERE (blocker_id = %s AND blocked_id = %s)
                OR (blocker_id = %s AND blocked_id = %s)
            """, (user_id, message.receiver_id, message.receiver_id, user_id))
            
            if cursor.fetchone():
                raise HTTPException(status_code=403, detail="Cannot message this user")
            
            # Send message
            cursor.execute("""
                INSERT INTO messages (sender_id, receiver_id, content, message_type, created_at, is_read)
                VALUES (%s, %s, %s, %s, %s, %s)
            """, (
                user_id,
                message.receiver_id,
                sanitize_input(message.content, allow_html=True),
                message.message_type,
                datetime.now(),
                False
            ))
            
            conn.commit()
            message_id = cursor.lastrowid
            
            # Send via WebSocket
            await manager.send_personal_message({
                "type": "message",
                "message_id": message_id,
                "sender_id": user_id,
                "receiver_id": message.receiver_id,
                "content": message.content,
                "message_type": message.message_type,
                "timestamp": datetime.now().isoformat()
            }, message.receiver_id)
            
            return {
                "success": True,
                "message_id": message_id,
                "timestamp": datetime.now().isoformat()
            }

@app.get("/chat/conversations")
async def get_conversations(
    page: int = 1,
    limit: int = 50,
    user_id: int = Depends(verify_token)
):
    """Get all conversations"""
    offset = (page - 1) * limit
    
    with get_db_connection() as conn:
        with get_db_cursor(conn) as cursor:
            cursor.execute("""
                SELECT 
                    DISTINCT
                    CASE 
                        WHEN m.sender_id = %s THEN m.receiver_id
                        ELSE m.sender_id
                    END as other_user_id,
                    u.username, u.full_name, u.profile_picture, u.is_verified, u.is_premium,
                    MAX(m.created_at) as last_message_time,
                    (SELECT content FROM messages m2 
                     WHERE ((m2.sender_id = %s AND m2.receiver_id = u.id)
                     OR (m2.sender_id = u.id AND m2.receiver_id = %s))
                     ORDER BY m2.created_at DESC LIMIT 1) as last_message,
                    (SELECT COUNT(*) FROM messages m3 
                     WHERE m3.sender_id = u.id AND m3.receiver_id = %s 
                     AND m3.is_read = FALSE) as unread_count
                FROM messages m
                JOIN users u ON u.id = CASE 
                    WHEN m.sender_id = %s THEN m.receiver_id
                    ELSE m.sender_id
                END
                WHERE (m.sender_id = %s OR m.receiver_id = %s)
                AND u.is_banned = FALSE
                GROUP BY other_user_id, u.username, u.full_name, u.profile_picture, 
                         u.is_verified, u.is_premium
                ORDER BY last_message_time DESC
                LIMIT %s OFFSET %s
            """, (user_id, user_id, user_id, user_id, user_id, user_id, user_id, limit, offset))
            
            conversations = cursor.fetchall()
            
            return {
                "success": True,
                "conversations": conversations,
                "page": page,
                "limit": limit
            }

@app.get("/chat/messages/{other_user_id}")
async def get_messages(
    other_user_id: int,
    page: int = 1,
    limit: int = 50,
    user_id: int = Depends(verify_token)
):
    """Get message history with another user"""
    offset = (page - 1) * limit
    
    with get_db_connection() as conn:
        with get_db_cursor(conn) as cursor:
            # Get messages
            cursor.execute("""
                SELECT 
                    m.*,
                    u_sender.username as sender_username,
                    u_sender.profile_picture as sender_profile,
                    u_receiver.username as receiver_username,
                    u_receiver.profile_picture as receiver_profile
                FROM messages m
                LEFT JOIN users u_sender ON m.sender_id = u_sender.id
                LEFT JOIN users u_receiver ON m.receiver_id = u_receiver.id
                WHERE (m.sender_id = %s AND m.receiver_id = %s)
                OR (m.sender_id = %s AND m.receiver_id = %s)
                ORDER BY m.created_at DESC
                LIMIT %s OFFSET %s
            """, (user_id, other_user_id, other_user_id, user_id, limit, offset))
            
            messages = cursor.fetchall()
            
            # Mark as read
            cursor.execute("""
                UPDATE messages SET is_read = TRUE
                WHERE sender_id = %s AND receiver_id = %s AND is_read = FALSE
            """, (other_user_id, user_id))
            conn.commit()
            
            return {
                "success": True,
                "messages": messages,
                "page": page,
                "limit": limit
            }

# ==================== WEBSOCKET ====================

def decode_access_token(token: str) -> dict:
    try:
        payload = jwt.decode(token, SECRET_KEY, algorithms=[ALGORITHM])

        exp = payload.get("exp")
        if exp and datetime.utcfromtimestamp(exp) < datetime.utcnow():
            raise HTTPException(status_code=401, detail="Token expired")

        return payload

    except JWTError:
        raise HTTPException(status_code=403, detail="Invalid token")


@app.websocket("/ws/{user_id}")
async def websocket_endpoint(websocket: WebSocket, user_id: int):
    """
    Authenticated WebSocket endpoint
    - JWT auth
    - Cloudflare tunnel safe
    - Single accept()
    """

    # 🔐 AUTH (BEFORE ACCEPT)
    token = websocket.query_params.get("token")
    if not token:
        await websocket.close(code=1008)
        return

    try:
        payload = decode_access_token(token)
    except HTTPException:
        await websocket.close(code=1008)
        return

    current_user_id = payload.get("user_id")
    if current_user_id != user_id:
        await websocket.close(code=1008)
        return

    # ✅ ACCEPT ONCE
    await websocket.accept()
    await manager.connect(websocket, user_id)

    try:
        while True:
            data = await websocket.receive_json()

            msg_type = data.get("type")

            if msg_type == "typing":
                target_id = data.get("target_user_id")
                if target_id:
                    await manager.send_personal_message({
                        "type": "typing",
                        "user_id": user_id,
                        "timestamp": datetime.utcnow().isoformat()
                    }, target_id)

            elif msg_type == "call_request":
                target_id = data.get("target_user_id")
                if target_id:
                    await manager.send_personal_message({
                        "type": "call_request",
                        "user_id": user_id,
                        "call_type": data.get("call_type", "voice"),
                        "call_id": data.get("call_id", str(uuid.uuid4())),
                        "timestamp": datetime.utcnow().isoformat()
                    }, target_id)

            elif msg_type == "call_response":
                target_id = data.get("target_user_id")
                call_id = data.get("call_id")
                if target_id and call_id:
                    await manager.send_personal_message({
                        "type": "call_response",
                        "user_id": user_id,
                        "call_id": call_id,
                        "accepted": data.get("accepted", False),
                        "timestamp": datetime.utcnow().isoformat()
                    }, target_id)

            elif msg_type == "call_ice_candidate":
                target_id = data.get("target_user_id")
                candidate = data.get("candidate")
                if target_id and candidate:
                    await manager.send_personal_message({
                        "type": "call_ice_candidate",
                        "user_id": user_id,
                        "candidate": candidate,
                        "timestamp": datetime.utcnow().isoformat()
                    }, target_id)

            elif msg_type == "call_offer":
                target_id = data.get("target_user_id")
                offer = data.get("offer")
                if target_id and offer:
                    await manager.send_personal_message({
                        "type": "call_offer",
                        "user_id": user_id,
                        "offer": offer,
                        "timestamp": datetime.utcnow().isoformat()
                    }, target_id)

            elif msg_type == "call_answer":
                target_id = data.get("target_user_id")
                answer = data.get("answer")
                if target_id and answer:
                    await manager.send_personal_message({
                        "type": "call_answer",
                        "user_id": user_id,
                        "answer": answer,
                        "timestamp": datetime.utcnow().isoformat()
                    }, target_id)

            elif msg_type == "call_end":
                target_id = data.get("target_user_id")
                call_id = data.get("call_id")
                if target_id and call_id:
                    await manager.send_personal_message({
                        "type": "call_end",
                        "user_id": user_id,
                        "call_id": call_id,
                        "timestamp": datetime.utcnow().isoformat()
                    }, target_id)

            elif msg_type == "ping":
                await websocket.send_json({
                    "type": "pong",
                    "timestamp": datetime.utcnow().isoformat()
                })

    except WebSocketDisconnect:
        pass

    finally:
        manager.disconnect(user_id)

        with get_db_connection() as conn:
            with get_db_cursor(conn) as cursor:
                cursor.execute(
                    "UPDATE users SET last_seen = %s WHERE id = %s",
                    (datetime.utcnow(), user_id)
                )
                conn.commit()


# ==================== NOTIFICATIONS ====================

@app.get("/notifications")
async def get_notifications(
    page: int = 1,
    limit: int = 20,
    user_id: int = Depends(verify_token)
):
    """Get user notifications"""
    offset = (page - 1) * limit
    
    with get_db_connection() as conn:
        with get_db_cursor(conn) as cursor:
            cursor.execute("""
                SELECT 
                    n.*,
                    u.username, u.full_name, u.profile_picture, u.is_verified,
                    CASE n.type
                        WHEN 'like' THEN CONCAT(u.username, ' liked your post')
                        WHEN 'comment' THEN CONCAT(u.username, ' commented on your post')
                        WHEN 'follow' THEN CONCAT(u.username, ' started following you')
                        WHEN 'message' THEN CONCAT(u.username, ' sent you a message')
                        WHEN 'match' THEN CONCAT('You matched with ', u.username)
                        WHEN 'super_like' THEN CONCAT(u.username, ' super liked you!')
                        ELSE 'New notification'
                    END as message
                FROM notifications n
                JOIN users u ON n.from_user_id = u.id
                WHERE n.user_id = %s
                ORDER BY n.created_at DESC
                LIMIT %s OFFSET %s
            """, (user_id, limit, offset))
            
            notifications = cursor.fetchall()
            
            # Mark as read
            cursor.execute("""
                UPDATE notifications SET is_read = TRUE
                WHERE user_id = %s AND is_read = FALSE
            """, (user_id,))
            conn.commit()
            
            return {
                "success": True,
                "notifications": notifications,
                "page": page,
                "limit": limit
            }

@app.get("/notifications/unread-count")
async def get_unread_notification_count(user_id: int = Depends(verify_token)):
    """Get count of unread notifications"""
    with get_db_connection() as conn:
        with get_db_cursor(conn) as cursor:
            cursor.execute("""
                SELECT COUNT(*) as count FROM notifications
                WHERE user_id = %s AND is_read = FALSE
            """, (user_id,))
            
            result = cursor.fetchone()
            
            return {
                "success": True,
                "count": result["count"] if result else 0
            }
            


















# ==================== PREMIUM FEATURES ====================

# @app.post("/premium/purchase")
# async def purchase_premium(
#     purchase: PremiumPurchase,
#     user_id: int = Depends(verify_token)
# ):
#     """Purchase premium subscription"""
#     plan = PREMIUM_PLANS.get(purchase.plan)
#     if not plan:
#         raise HTTPException(status_code=400, detail="Invalid plan")
    
#     # Calculate price
#     price = plan["price_monthly"] * purchase.duration_months
    
#     with get_db_connection() as conn:
#         with get_db_cursor(conn) as cursor:
#             # Calculate expiry date
#             expires_at = datetime.now() + timedelta(days=30 * purchase.duration_months)
            
#             # Update user
#             cursor.execute("""
#                 UPDATE users 
#                 SET is_premium = TRUE, premium_plan = %s, premium_expires_at = %s,
#                     premium_features = %s, premium_purchased_at = %s
#                 WHERE id = %s
#             """, (
#                 purchase.plan,
#                 expires_at,
#                 json.dumps(plan["features"]),
#                 datetime.now(),
#                 user_id
#             ))
            
#             # Record transaction
#             cursor.execute("""
#                 INSERT INTO premium_transactions (
#                     user_id, plan, duration_months, amount, payment_method, status, created_at
#                 ) VALUES (%s, %s, %s, %s, %s, %s, %s)
#             """, (
#                 user_id,
#                 purchase.plan,
#                 purchase.duration_months,
#                 price,
#                 purchase.payment_method,
#                 "completed",
#                 datetime.now()
#             ))
            
#             conn.commit()
            
#             return {
#                 "success": True,
#                 "message": "Premium subscription activated",
#                 "plan": purchase.plan,
#                 "duration_months": purchase.duration_months,
#                 "price": price,
#                 "expires_at": expires_at.isoformat(),
#                 "features": plan["features"]
#             }

# @app.get("/premium/plans")
# async def get_premium_plans():
#     """Get available premium plans"""
#     return {
#         "success": True,
#         "plans": PREMIUM_PLANS
#     }

# @app.get("/premium/features")
# async def get_premium_features(user_id: int = Depends(verify_token)):
#     """Get user's premium features"""
#     with get_db_connection() as conn:
#         with get_db_cursor(conn) as cursor:
#             cursor.execute("""
#                 SELECT 
#                     is_premium, premium_plan, premium_expires_at, premium_features
#                 FROM users WHERE id = %s
#             """, (user_id,))
            
#             user = cursor.fetchone()
            
#             if not user or not user["is_premium"]:
#                 raise HTTPException(status_code=403, detail="Premium subscription required")
            
#             return {
#                 "success": True,
#                 "is_premium": user["is_premium"],
#                 "plan": user["premium_plan"],
#                 "expires_at": user["premium_expires_at"],
#                 "features": json.loads(user["premium_features"] or "[]")
#             }
            
            
            










# ==================== ADMIN ENDPOINTS ====================

@app.post("/admin/login")
async def admin_login(credentials: AdminLogin):
    """Admin login"""
    with get_db_connection() as conn:
        with get_db_cursor(conn) as cursor:
            cursor.execute("""
                SELECT id, email, username, password_hash, is_admin, is_super_admin
                FROM users WHERE email = %s AND is_admin = TRUE
            """, (credentials.email.lower(),))
            
            admin = cursor.fetchone()
            
            if not admin:
                raise HTTPException(status_code=401, detail="Invalid admin credentials")
            
            if not verify_password(credentials.password, admin["password_hash"]):
                raise HTTPException(status_code=401, detail="Invalid admin credentials")
            
            # Generate tokens
            access_token = create_access_token({
                "user_id": admin["id"],
                "is_admin": True,
                "is_super_admin": admin["is_super_admin"]
            })
            
            refresh_token = create_refresh_token({
                "user_id": admin["id"],
                "is_admin": True,
                "is_super_admin": admin["is_super_admin"]
            })
            
            return {
                "success": True,
                "access_token": access_token,
                "refresh_token": refresh_token,
                "token_type": "bearer",
                "admin": {
                    "id": admin["id"],
                    "email": admin["email"],
                    "username": admin["username"],
                    "is_super_admin": admin["is_super_admin"]
                }
            }

@app.get("/admin/dashboard/stats")
async def get_admin_stats(admin_info: dict = Depends(verify_admin)):
    """Get admin dashboard statistics"""
    with get_db_connection() as conn:
        with get_db_cursor(conn) as cursor:
            # Total users
            cursor.execute("SELECT COUNT(*) as total_users FROM users WHERE is_banned = FALSE")
            total_users = cursor.fetchone()["total_users"]
            
            # New users today
            cursor.execute("""
                SELECT COUNT(*) as new_users_today 
                FROM users 
                WHERE DATE(created_at) = CURDATE() AND is_banned = FALSE
            """)
            new_users_today = cursor.fetchone()["new_users_today"]
            
            # Active users (last 24h)
            cursor.execute("""
                SELECT COUNT(DISTINCT user_id) as active_users
                FROM (
                    SELECT user_id FROM posts WHERE created_at > DATE_SUB(NOW(), INTERVAL 24 HOUR)
                    UNION
                    SELECT user_id FROM likes WHERE created_at > DATE_SUB(NOW(), INTERVAL 24 HOUR)
                    UNION
                    SELECT user_id FROM comments WHERE created_at > DATE_SUB(NOW(), INTERVAL 24 HOUR)
                    UNION
                    SELECT sender_id as user_id FROM messages WHERE created_at > DATE_SUB(NOW(), INTERVAL 24 HOUR)
                ) activity
            """)
            active_users = cursor.fetchone()["active_users"]
            
            # Premium users
            cursor.execute("SELECT COUNT(*) as premium_users FROM users WHERE is_premium = TRUE AND is_banned = FALSE")
            premium_users = cursor.fetchone()["premium_users"]
            
            # Total posts
            cursor.execute("SELECT COUNT(*) as total_posts FROM posts")
            total_posts = cursor.fetchone()["total_posts"]
            
            # Posts today
            cursor.execute("""
                SELECT COUNT(*) as posts_today FROM posts
                WHERE DATE(created_at) = CURDATE()
            """)
            posts_today = cursor.fetchone()["posts_today"]
            
            # Total matches
            cursor.execute("SELECT COUNT(*) as total_matches FROM matches")
            total_matches = cursor.fetchone()["total_matches"]
            
            # Revenue (premium)
            cursor.execute("""
                SELECT COALESCE(SUM(amount), 0) as total_revenue
                FROM premium_transactions 
                WHERE status = 'completed'
            """)
            total_revenue = cursor.fetchone()["total_revenue"]
            
            return {
                "success": True,
                "stats": {
                    "total_users": total_users,
                    "new_users_today": new_users_today,
                    "active_users": active_users,
                    "premium_users": premium_users,
                    "total_posts": total_posts,
                    "posts_today": posts_today,
                    "total_matches": total_matches,
                    "total_revenue": float(total_revenue)
                }
            }

@app.get("/admin/users")
async def admin_get_users(
    page: int = 1,
    limit: int = 50,
    search: Optional[str] = None,
    role: Optional[str] = None,
    status: Optional[str] = None,
    admin_info: dict = Depends(verify_admin)
):
    """Get all users for admin management"""
    offset = (page - 1) * limit
    
    filters = ["1=1"]
    params = []
    
    if search:
        filters.append("(username LIKE %s OR email LIKE %s OR full_name LIKE %s)")
        search_term = f"%{search}%"
        params.extend([search_term, search_term, search_term])
    
    if role == "admin":
        filters.append("is_admin = TRUE")
    elif role == "premium":
        filters.append("is_premium = TRUE")
    elif role == "verified":
        filters.append("is_verified = TRUE")
    
    if status == "active":
        filters.append("last_login > DATE_SUB(NOW(), INTERVAL 7 DAY)")
    elif status == "inactive":
        filters.append("last_login < DATE_SUB(NOW(), INTERVAL 30 DAY)")
    elif status == "banned":
        filters.append("is_banned = TRUE")
    
    where_clause = " AND ".join(filters)
    
    with get_db_connection() as conn:
        with get_db_cursor(conn) as cursor:
            # Get users
            query = f"""
                SELECT 
                    u.*,
                    TIMESTAMPDIFF(YEAR, u.date_of_birth, CURDATE()) as age,
                    (SELECT COUNT(*) FROM posts WHERE user_id = u.id) as posts_count,
                    (SELECT COUNT(*) FROM follows WHERE follower_id = u.id) as following_count,
                    (SELECT COUNT(*) FROM follows WHERE following_id = u.id) as followers_count,
                    (SELECT COUNT(*) FROM matches WHERE user1_id = u.id OR user2_id = u.id) as matches_count,
                    (SELECT COUNT(*) FROM reports WHERE reported_user_id = u.id AND status = 'pending') as pending_reports
                FROM users u
                WHERE {where_clause}
                ORDER BY u.created_at DESC
                LIMIT %s OFFSET %s
            """
            params.extend([limit, offset])
            
            cursor.execute(query, params)
            users = cursor.fetchall()
            
            # Get total count
            count_query = f"SELECT COUNT(*) as total FROM users WHERE {where_clause}"
            cursor.execute(count_query, params[:-2])  # Remove limit and offset
            total = cursor.fetchone()["total"]
            
            return {
                "success": True,
                "users": users,
                "page": page,
                "limit": limit,
                "total": total,
                "pages": (total + limit - 1) // limit
            }

@app.get("/admin/user/{user_id}")
async def admin_get_user_details(
    user_id: int,
    admin_info: dict = Depends(verify_admin)
):
    """Get detailed user information for admin"""
    with get_db_connection() as conn:
        with get_db_cursor(conn) as cursor:
            cursor.execute("""
                SELECT 
                    u.*,
                    TIMESTAMPDIFF(YEAR, u.date_of_birth, CURDATE()) as age,
                    (SELECT COUNT(*) FROM posts WHERE user_id = u.id) as posts_count,
                    (SELECT COUNT(*) FROM posts WHERE user_id = u.id AND post_type = 'story') as stories_count,
                    (SELECT COUNT(*) FROM likes WHERE user_id = u.id) as likes_given,
                    (SELECT COUNT(*) FROM comments WHERE user_id = u.id) as comments_given,
                    (SELECT COUNT(*) FROM messages WHERE sender_id = u.id) as messages_sent,
                    (SELECT COUNT(*) FROM follows WHERE follower_id = u.id) as following_count,
                    (SELECT COUNT(*) FROM follows WHERE following_id = u.id) as followers_count,
                    (SELECT COUNT(*) FROM swipes WHERE user_id = u.id) as swipes_count,
                    (SELECT COUNT(*) FROM matches WHERE user1_id = u.id OR user2_id = u.id) as matches_count,
                    (SELECT COUNT(*) FROM reports WHERE reported_user_id = u.id) as reports_count,
                    (SELECT COUNT(*) FROM blocks WHERE blocker_id = u.id) as blocked_count,
                    (SELECT COUNT(*) FROM blocks WHERE blocked_id = u.id) as blocked_by_count,
                    (SELECT COUNT(*) FROM premium_transactions WHERE user_id = u.id) as premium_transactions_count
                FROM users u
                WHERE u.id = %s
            """, (user_id,))
            
            user = cursor.fetchone()
            if not user:
                raise HTTPException(status_code=404, detail="User not found")
            
            # Get recent activity
            cursor.execute("""
                SELECT 
                    'post' as type, content, created_at
                FROM posts 
                WHERE user_id = %s
                UNION ALL
                SELECT 
                    'like' as type, CONCAT('Liked post #', post_id) as content, created_at
                FROM likes 
                WHERE user_id = %s
                UNION ALL
                SELECT 
                    'comment' as type, content, created_at
                FROM comments 
                WHERE user_id = %s
                ORDER BY created_at DESC
                LIMIT 20
            """, (user_id, user_id, user_id))
            
            recent_activity = cursor.fetchall()
            
            # Get premium transactions
            cursor.execute("""
                SELECT * FROM premium_transactions
                WHERE user_id = %s
                ORDER BY created_at DESC
                LIMIT 10
            """, (user_id,))
            
            premium_transactions = cursor.fetchall()
            
            return {
                "success": True,
                "user": user,
                "recent_activity": recent_activity,
                "premium_transactions": premium_transactions
            }

@app.post("/admin/user/{user_id}/ban")
async def admin_ban_user(
    user_id: int,
    reason: str = "Violation of terms of service",
    admin_info: dict = Depends(verify_admin)
):
    """Ban a user"""
    if admin_info["user_id"] == user_id:
        raise HTTPException(status_code=400, detail="Cannot ban yourself")
    
    with get_db_connection() as conn:
        with get_db_cursor(conn) as cursor:
            # Check if user exists and is admin
            cursor.execute("SELECT id, is_admin FROM users WHERE id = %s", (user_id,))
            user = cursor.fetchone()
            
            if not user:
                raise HTTPException(status_code=404, detail="User not found")
            
            if user["is_admin"] and not admin_info["is_super_admin"]:
                raise HTTPException(status_code=403, detail="Cannot ban other admins")
            
            # Ban user
            cursor.execute("""
                UPDATE users 
                SET is_banned = TRUE, banned_at = %s, banned_reason = %s,
                    banned_by = %s
                WHERE id = %s
            """, (datetime.now(), reason, admin_info["user_id"], user_id))
            
            conn.commit()
            
            return {
                "success": True,
                "message": "User banned successfully",
                "user_id": user_id,
                "reason": reason
            }

@app.post("/admin/user/{user_id}/unban")
async def admin_unban_user(
    user_id: int,
    admin_info: dict = Depends(verify_admin)
):
    """Unban a user"""
    with get_db_connection() as conn:
        with get_db_cursor(conn) as cursor:
            cursor.execute("""
                UPDATE users 
                SET is_banned = FALSE, banned_at = NULL, banned_reason = NULL,
                    banned_by = NULL
                WHERE id = %s
            """, (user_id,))
            
            conn.commit()
            
            return {
                "success": True,
                "message": "User unbanned successfully",
                "user_id": user_id
            }

@app.post("/admin/user/{user_id}/make-admin")
async def admin_make_admin(
    user_id: int,
    is_super_admin: bool = False,
    admin_info: dict = Depends(verify_admin)
):
    """Make a user an admin"""
    if not admin_info["is_super_admin"]:
        raise HTTPException(status_code=403, detail="Super admin required")
    
    with get_db_connection() as conn:
        with get_db_cursor(conn) as cursor:
            cursor.execute("""
                UPDATE users 
                SET is_admin = TRUE, is_super_admin = %s
                WHERE id = %s
            """, (is_super_admin, user_id))
            
            conn.commit()
            
            return {
                "success": True,
                "message": "User promoted to admin",
                "user_id": user_id,
                "is_super_admin": is_super_admin
            }

@app.post("/admin/user/{user_id}/remove-admin")
async def admin_remove_admin(
    user_id: int,
    admin_info: dict = Depends(verify_admin)
):
    """Remove admin privileges from a user"""
    if admin_info["user_id"] == user_id:
        raise HTTPException(status_code=400, detail="Cannot remove your own admin privileges")
    
    if not admin_info["is_super_admin"]:
        raise HTTPException(status_code=403, detail="Super admin required")
    
    with get_db_connection() as conn:
        with get_db_cursor(conn) as cursor:
            cursor.execute("""
                UPDATE users 
                SET is_admin = FALSE, is_super_admin = FALSE
                WHERE id = %s
            """, (user_id,))
            
            conn.commit()
            
            return {
                "success": True,
                "message": "Admin privileges removed",
                "user_id": user_id
            }

@app.delete("/admin/user/{user_id}")
async def admin_delete_user(
    user_id: int,
    admin_info: dict = Depends(verify_admin)
):
    """Permanently delete a user"""
    if admin_info["user_id"] == user_id:
        raise HTTPException(status_code=400, detail="Cannot delete yourself")
    
    with get_db_connection() as conn:
        with get_db_cursor(conn) as cursor:
            # Check if user is admin
            cursor.execute("SELECT is_admin FROM users WHERE id = %s", (user_id,))
            user = cursor.fetchone()
            
            if user and user["is_admin"] and not admin_info["is_super_admin"]:
                raise HTTPException(status_code=403, detail="Cannot delete admin users")
            
            # Delete user (cascade will handle related records)
            cursor.execute("DELETE FROM users WHERE id = %s", (user_id,))
            conn.commit()
            
            return {
                "success": True,
                "message": "User deleted successfully",
                "user_id": user_id
            }

@app.get("/admin/posts")
async def admin_get_posts(
    page: int = 1,
    limit: int = 50,
    search: Optional[str] = None,
    post_type: Optional[str] = None,
    admin_info: dict = Depends(verify_admin)
):
    """Get all posts for admin management"""
    offset = (page - 1) * limit
    
    filters = ["1=1"]
    params = []
    
    if search:
        filters.append("p.content LIKE %s")
        params.append(f"%{search}%")
    
    if post_type:
        filters.append("p.post_type = %s")
        params.append(post_type)
    
    where_clause = " AND ".join(filters)
    
    with get_db_connection() as conn:
        with get_db_cursor(conn) as cursor:
            query = f"""
                SELECT 
                    p.*,
                    u.username, u.email, u.is_verified, u.is_premium,
                    (SELECT COUNT(*) FROM likes WHERE post_id = p.id) as likes_count,
                    (SELECT COUNT(*) FROM comments WHERE post_id = p.id) as comments_count,
                    (SELECT COUNT(*) FROM reports WHERE target_type = 'post' AND target_id = p.id) as reports_count
                FROM posts p
                JOIN users u ON p.user_id = u.id
                WHERE {where_clause}
                ORDER BY p.created_at DESC
                LIMIT %s OFFSET %s
            """
            params.extend([limit, offset])
            
            cursor.execute(query, params)
            posts = cursor.fetchall()
            
            return {
                "success": True,
                "posts": posts,
                "page": page,
                "limit": limit
            }

@app.delete("/admin/post/{post_id}")
async def admin_delete_post(
    post_id: int,
    admin_info: dict = Depends(verify_admin)
):
    """Delete a post"""
    with get_db_connection() as conn:
        with get_db_cursor(conn) as cursor:
            cursor.execute("DELETE FROM posts WHERE id = %s", (post_id,))
            conn.commit()
            
            return {
                "success": True,
                "message": "Post deleted successfully",
                "post_id": post_id
            }

@app.get("/admin/reports")
async def admin_get_reports(
    page: int = 1,
    limit: int = 50,
    status: Optional[str] = None,
    admin_info: dict = Depends(verify_admin)
):
    """Get all reports"""
    offset = (page - 1) * limit
    
    filters = ["1=1"]
    params = []
    
    if status:
        filters.append("r.status = %s")
        params.append(status)
    
    where_clause = " AND ".join(filters)
    
    with get_db_connection() as conn:
        with get_db_cursor(conn) as cursor:
            query = f"""
                SELECT 
                    r.*,
                    u_reporter.username as reporter_username,
                    u_reporter.email as reporter_email,
                    u_reported.username as reported_username,
                    u_reported.email as reported_email,
                    a.username as admin_username
                FROM reports r
                LEFT JOIN users u_reporter ON r.reporter_id = u_reporter.id
                LEFT JOIN users u_reported ON r.reported_user_id = u_reported.id
                LEFT JOIN users a ON r.handled_by = a.id
                WHERE {where_clause}
                ORDER BY r.created_at DESC
                LIMIT %s OFFSET %s
            """
            params.extend([limit, offset])
            
            cursor.execute(query, params)
            reports = cursor.fetchall()
            
            return {
                "success": True,
                "reports": reports,
                "page": page,
                "limit": limit
            }

@app.post("/admin/report/{report_id}/resolve")
async def admin_resolve_report(
    report_id: int,
    action: str,
    notes: Optional[str] = None,
    admin_info: dict = Depends(verify_admin)
):
    """Resolve a report"""
    with get_db_connection() as conn:
        with get_db_cursor(conn) as cursor:
            cursor.execute("""
                UPDATE reports 
                SET status = 'resolved', action_taken = %s, 
                    resolved_at = %s, handled_by = %s, admin_notes = %s
                WHERE id = %s
            """, (action, datetime.now(), admin_info["user_id"], notes, report_id))
            
            conn.commit()
            
            return {
                "success": True,
                "message": "Report resolved",
                "report_id": report_id,
                "action": action
            }

@app.get("/admin/analytics")
async def admin_get_analytics(
    period: str = "week",  # day, week, month, year
    admin_info: dict = Depends(verify_admin)
):
    """Get platform analytics"""
    if period == "day":
        interval = "1 DAY"
        date_format = "%Y-%m-%d %H:00"
    elif period == "week":
        interval = "7 DAY"
        date_format = "%Y-%m-%d"
    elif period == "month":
        interval = "30 DAY"
        date_format = "%Y-%m-%d"
    else:  # year
        interval = "365 DAY"
        date_format = "%Y-%m"
    
    with get_db_connection() as conn:
        with get_db_cursor(conn) as cursor:
            # User growth
            cursor.execute(f"""
                SELECT 
                    DATE_FORMAT(created_at, '{date_format}') as date,
                    COUNT(*) as new_users
                FROM users
                WHERE created_at > DATE_SUB(NOW(), INTERVAL {interval})
                GROUP BY DATE_FORMAT(created_at, '{date_format}')
                ORDER BY date
            """)
            user_growth = cursor.fetchall()
            
            # Post activity
            cursor.execute(f"""
                SELECT 
                    DATE_FORMAT(created_at, '{date_format}') as date,
                    COUNT(*) as new_posts
                FROM posts
                WHERE created_at > DATE_SUB(NOW(), INTERVAL {interval})
                GROUP BY DATE_FORMAT(created_at, '{date_format}')
                ORDER BY date
            """)
            post_activity = cursor.fetchall()
            
            # Engagement metrics
            cursor.execute(f"""
                SELECT 
                    'likes' as metric, COUNT(*) as count
                FROM likes
                WHERE created_at > DATE_SUB(NOW(), INTERVAL {interval})
                UNION ALL
                SELECT 
                    'comments' as metric, COUNT(*) as count
                FROM comments
                WHERE created_at > DATE_SUB(NOW(), INTERVAL {interval})
                UNION ALL
                SELECT 
                    'messages' as metric, COUNT(*) as count
                FROM messages
                WHERE created_at > DATE_SUB(NOW(), INTERVAL {interval})
                UNION ALL
                SELECT 
                    'matches' as metric, COUNT(*) as count
                FROM matches
                WHERE created_at > DATE_SUB(NOW(), INTERVAL {interval})
            """)
            engagement = cursor.fetchall()
            
            # Premium revenue
            cursor.execute(f"""
                SELECT 
                    DATE_FORMAT(created_at, '{date_format}') as date,
                    SUM(amount) as revenue
                FROM premium_transactions
                WHERE status = 'completed'
                AND created_at > DATE_SUB(NOW(), INTERVAL {interval})
                GROUP BY DATE_FORMAT(created_at, '{date_format}')
                ORDER BY date
            """)
            revenue = cursor.fetchall()
            
            return {
                "success": True,
                "analytics": {
                    "user_growth": user_growth,
                    "post_activity": post_activity,
                    "engagement": engagement,
                    "revenue": revenue,
                    "period": period
                }
            }

@app.get("/admin/system/info")
async def admin_system_info(admin_info: dict = Depends(verify_admin)):
    """Get system information"""
    import platform
    import psutil
    
    system_info = {
        "python_version": platform.python_version(),
        "platform": platform.platform(),
        "cpu_count": psutil.cpu_count(),
        "memory_total": psutil.virtual_memory().total,
        "memory_available": psutil.virtual_memory().available,
        "disk_usage": psutil.disk_usage('/')._asdict(),
        "boot_time": datetime.fromtimestamp(psutil.boot_time()).isoformat()
    }
    
    # Database info
    with get_db_connection() as conn:
        with get_db_cursor(conn) as cursor:
            cursor.execute("SELECT VERSION() as version")
            db_version = cursor.fetchone()["version"]
            
            cursor.execute("""
                SELECT 
                    table_name,
                    table_rows,
                    data_length,
                    index_length
                FROM information_schema.tables
                WHERE table_schema = DATABASE()
            """)
            tables = cursor.fetchall()
            
            system_info["database"] = {
                "version": db_version,
                "tables": tables
            }
    
    return {
        "success": True,
        "system_info": system_info
    }
    
    
    
    
    
    
    
    
    

# ==================== ERROR HANDLING ====================

@app.exception_handler(HTTPException)
async def http_exception_handler(request: Request, exc: HTTPException):
    return JSONResponse(
        status_code=exc.status_code,
        content={
            "success": False,
            "error": exc.detail,
            "status_code": exc.status_code
        }
    )

@app.exception_handler(Exception)
async def general_exception_handler(request: Request, exc: Exception):
    logger.error(f"Unhandled exception: {exc}", exc_info=True)
    return JSONResponse(
        status_code=500,
        content={
            "success": False,
            "error": "Internal server error",
            "status_code": 500
        }
    )

# ==================== FILE SERVING ====================

@app.get("/assets/{folder}/{filename}")
async def serve_file(folder: str, filename: str):
    """Serve uploaded files"""
    file_path = UPLOAD_DIR / folder / filename
    if not file_path.exists():
        raise HTTPException(status_code=404, detail="File not found")
    
    return FileResponse(file_path)



BASE_DIR = os.path.dirname(os.path.abspath(__file__))
MEDIA_ROOT = os.path.join(BASE_DIR, "assets")

@app.get("/media/{path:path}")
def serve_media(path: str, request: Request):
    """
    Serves images & videos from:
    assets/
      ├─ posts/
      └─ profiles/
    """

    # Prevent directory traversal
    safe_path = os.path.normpath(path).lstrip("/")

    full_path = os.path.join(MEDIA_ROOT, safe_path)

    if not os.path.isfile(full_path):
        raise HTTPException(status_code=404, detail="Media not found")

    return FileResponse(
        full_path,
        media_type=None,  # let FastAPI auto-detect
        headers={
            "Cache-Control": "public, max-age=3600",
            "Accept-Ranges": "bytes",
            "Access-Control-Allow-Origin": "*",
            "Access-Control-Allow-Headers": "*",
        },
    )

# ==================== DATABASE INITIALIZATION ====================

# Initialize database on startup
# @app.on_event("startup")
# async def startup_event():
#     logger.info("Starting meiXuP Premium Social Platform...")

# if __name__ == "__main__":
#     import uvicorn
#     uvicorn.run(
#         "main:app",
#         host="0.0.0.0",
#         port=8000,
#         reload=True,
#         log_level="info",
#         workers=4
#     )








