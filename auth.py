import json
import os
import uuid
from datetime import datetime, timedelta
from typing import Optional, List, Dict
import smtplib
from email.mime.text import MIMEText
from email.mime.multipart import MIMEMultipart

# FastAPI & Pydantic
from fastapi import FastAPI, APIRouter, Depends, HTTPException, status, Body, BackgroundTasks, Header
from fastapi.security import HTTPBearer, HTTPAuthorizationCredentials
from fastapi.middleware.cors import CORSMiddleware
from fastapi.middleware.trustedhost import TrustedHostMiddleware
from fastapi.middleware.gzip import GZipMiddleware
from fastapi.requests import Request
from pydantic import BaseModel, Field, EmailStr
from pydantic_settings import BaseSettings

# Database
from sqlalchemy import create_engine, Column, Integer, String, Boolean, DateTime, Text, BigInteger, Enum, ForeignKey, select, update, text
from sqlalchemy.ext.declarative import declarative_base
from sqlalchemy.orm import relationship
from sqlalchemy.ext.asyncio import AsyncSession, create_async_engine, async_sessionmaker

# Redis & Cache
import redis.asyncio as redis

# Authentication & Security
from jose import JWTError, jwt
from passlib.context import CryptContext
from dotenv import load_dotenv

# Utilities
import logging
import secrets
import string
import hashlib




# Load environment variables
# Load .env only in local environment
# if os.getenv("ENV") != "production":
#     load_dotenv()
# load_dotenv()

# =============================================================================
# SETTINGS & CONFIGURATION
# =============================================================================

class Settings(BaseSettings):
    # =========================
    # Database
    # =========================
    DATABASE_URL: str
    DATABASE_POOL_SIZE: int = 20
    DATABASE_MAX_OVERFLOW: int = 40
    DATABASE_POOL_RECYCLE: int = 3600

    # =========================
    # Redis
    # =========================
    REDIS_URL: str
    REDIS_POOL_SIZE: int = 50

    # =========================
    # JWT
    # =========================
    JWT_SECRET_KEY: str
    JWT_ALGORITHM: str = "HS256"
    JWT_ACCESS_TOKEN_EXPIRE_MINUTES: int = 30
    JWT_REFRESH_TOKEN_EXPIRE_DAYS: int = 30

    # =========================
    # SMTP
    # =========================
    SMTP_SERVER: str
    SMTP_PORT: int = 587
    SMTP_USERNAME: str
    SMTP_PASSWORD: str
    FROM_EMAIL: str

    # =========================
    # App
    # =========================
    APP_NAME: str = "meiXuP"
    APP_VERSION: str = "1.0.0"
    APP_ENVIRONMENT: str = "development"
    DEBUG: bool = False
    API_PREFIX: str = "/api"

    # IMPORTANT: keep as STRING, not List
    CORS_ORIGINS: str = "[]"

    @property
    def cors_origins(self) -> List[str]:
        """
        Safely parse CORS origins from env.
        Never crashes, even if env is missing or invalid.
        """
        try:
            return json.loads(self.CORS_ORIGINS)
        except Exception:
            return []

    class Config:
        env_file = ".env" if os.getenv("APP_ENVIRONMENT") != "production" else None
        extra = "ignore"


settings = Settings()

# =============================================================================
# DATABASE MODELS (SQLAlchemy) - Simplified
# =============================================================================

Base = declarative_base()

# Helper functions
def generate_uuid():
    return str(uuid.uuid4())

class User(Base):
    """Users table model"""
    __tablename__ = "users"
    
    id = Column(BigInteger, primary_key=True, autoincrement=True)
    uuid = Column(String(36), unique=True, nullable=False, default=generate_uuid)
    username = Column(String(50), unique=True, nullable=False)
    email = Column(String(255), unique=True, nullable=False)
    email_verified = Column(Boolean, default=False)
    
    # Profile Info
    full_name = Column(String(100), nullable=False)
    bio = Column(Text, nullable=True)
    avatar_url = Column(String(500), nullable=True)
    
    # Account Status
    status = Column(Enum('active', 'suspended', 'banned', 'deactivated', name='user_status_enum'), default='active')
    is_verified = Column(Boolean, default=False)
    
    # Security
    password_hash = Column(String(255), nullable=False)
    password_updated_at = Column(DateTime(6), nullable=False, default=datetime.utcnow)
    
    # Timestamps
    created_at = Column(DateTime(6), nullable=False, default=datetime.utcnow)
    updated_at = Column(DateTime(6), nullable=False, default=datetime.utcnow, onupdate=datetime.utcnow)
    
    def to_dict(self, include_sensitive=False):
        """Convert user to dictionary"""
        data = {
            "id": self.id,
            "uuid": self.uuid,
            "username": self.username,
            "email": self.email if include_sensitive else None,
            "email_verified": self.email_verified,
            "full_name": self.full_name,
            "bio": self.bio,
            "avatar_url": self.avatar_url,
            "status": self.status,
            "is_verified": self.is_verified,
            "created_at": self.created_at.isoformat(),
            "updated_at": self.updated_at.isoformat()
        }
        
        return data

class UserSession(Base):
    """User sessions table model"""
    __tablename__ = "user_sessions"
    
    id = Column(BigInteger, primary_key=True, autoincrement=True)
    user_id = Column(BigInteger, ForeignKey("users.id", ondelete="CASCADE"), nullable=False)
    session_token = Column(String(64), unique=True, nullable=False)
    refresh_token = Column(String(64), unique=True, nullable=False)
    device_id = Column(String(255), nullable=False)
    device_info = Column(Text, nullable=True)
    ip_address = Column(String(45), nullable=True)
    user_agent = Column(Text, nullable=True)
    
    # Token Info
    expires_at = Column(DateTime(6), nullable=False)
    refresh_expires_at = Column(DateTime(6), nullable=False)
    
    # Status
    is_active = Column(Boolean, default=True)
    
    # Timestamps
    created_at = Column(DateTime(6), nullable=False, default=datetime.utcnow)
    last_used_at = Column(DateTime(6), nullable=False, default=datetime.utcnow)
    
    # Relationship
    user = relationship("User")

# =============================================================================
# PYDANTIC SCHEMAS
# =============================================================================

# Base schemas
class BaseResponse(BaseModel):
    success: bool = True
    message: Optional[str] = None
    data: Optional[Dict] = None

class ErrorResponse(BaseResponse):
    success: bool = False
    error_code: str
    error_details: Optional[Dict] = None

# Auth schemas
class RegisterRequest(BaseModel):
    username: str = Field(..., min_length=3, max_length=50)
    email: EmailStr
    password: str = Field(..., min_length=8)
    full_name: str = Field(..., min_length=2, max_length=100)

class LoginRequest(BaseModel):
    username_or_email: str
    password: str
    device_id: str
    device_info: Optional[Dict] = None

class LoginResponse(BaseModel):
    access_token: str
    refresh_token: str
    token_type: str = "bearer"
    expires_in: int
    user: Dict

class RefreshTokenRequest(BaseModel):
    refresh_token: str

class VerifyOTPRequest(BaseModel):
    email: EmailStr
    otp: str

class ResetPasswordRequest(BaseModel):
    email: EmailStr

class ChangePasswordRequest(BaseModel):
    current_password: str
    new_password: str = Field(..., min_length=8)

# =============================================================================
# DATABASE CONNECTION & SESSION
# =============================================================================
import ssl
from pathlib import Path

BASE_DIR = Path(__file__).resolve().parent
CA_PATH = BASE_DIR / "assets" / "ca.pem"

# /etc/secrets/

ssl_context = ssl.create_default_context(cafile=str(CA_PATH))


engine = create_async_engine(
    settings.DATABASE_URL,
    pool_size=settings.DATABASE_POOL_SIZE,
    max_overflow=settings.DATABASE_MAX_OVERFLOW,
    pool_recycle=settings.DATABASE_POOL_RECYCLE,
    echo=settings.DEBUG,
    connect_args={
        "ssl": ssl_context   # ✅ THIS is the key fix
    }
)






AsyncSessionLocal = async_sessionmaker(
    engine,
    class_=AsyncSession,
    expire_on_commit=False
)

async def get_db():
    async with AsyncSessionLocal() as session:
        yield session


# =============================================================================
# REDIS CONNECTION
# =============================================================================


redis_client = redis.from_url(
    settings.REDIS_URL,
    max_connections=settings.REDIS_POOL_SIZE,
    decode_responses=True
)

async def get_redis():
    return redis_client

# =============================================================================
# AUTHENTICATION & SECURITY
# =============================================================================

pwd_context = CryptContext(schemes=["bcrypt"], deprecated="auto")
security = HTTPBearer()

def verify_password(plain_password, hashed_password):
    """Verify password"""
    return pwd_context.verify(plain_password, hashed_password)

def get_password_hash(password):
    """Hash password"""
    return pwd_context.hash(password)

def create_access_token(data: dict, expires_delta: Optional[timedelta] = None):
    """Create JWT access token"""
    to_encode = data.copy()
    
    if expires_delta:
        expire = datetime.utcnow() + expires_delta
    else:
        expire = datetime.utcnow() + timedelta(minutes=settings.JWT_ACCESS_TOKEN_EXPIRE_MINUTES)
    
    to_encode.update({"exp": expire, "type": "access"})
    encoded_jwt = jwt.encode(to_encode, settings.JWT_SECRET_KEY, algorithm=settings.JWT_ALGORITHM)
    return encoded_jwt

def create_refresh_token(data: dict):
    """Create JWT refresh token"""
    to_encode = data.copy()
    expire = datetime.utcnow() + timedelta(days=settings.JWT_REFRESH_TOKEN_EXPIRE_DAYS)
    to_encode.update({"exp": expire, "type": "refresh"})
    encoded_jwt = jwt.encode(to_encode, settings.JWT_SECRET_KEY, algorithm=settings.JWT_ALGORITHM)
    return encoded_jwt

async def get_current_user(
    credentials: HTTPAuthorizationCredentials = Depends(security),
    db: AsyncSession = Depends(get_db)
):
    """Get current authenticated user"""
    token = credentials.credentials
    
    try:
        payload = jwt.decode(token, settings.JWT_SECRET_KEY, algorithms=[settings.JWT_ALGORITHM])
        user_id: int = payload.get("sub")
        
        if user_id is None:
            raise HTTPException(
                status_code=status.HTTP_401_UNAUTHORIZED,
                detail="Invalid authentication credentials"
            )
        
        # Check token type
        token_type = payload.get("type")
        if token_type != "access":
            raise HTTPException(
                status_code=status.HTTP_401_UNAUTHORIZED,
                detail="Invalid token type"
            )
        
    except JWTError:
        raise HTTPException(
            status_code=status.HTTP_401_UNAUTHORIZED,
            detail="Invalid authentication credentials"
        )
    
    # Get user from database
    result = await db.execute(
        select(User).where(User.id == user_id, User.status == 'active')
    )
    user = result.scalar_one_or_none()
    
    if user is None:
        raise HTTPException(
            status_code=status.HTTP_401_UNAUTHORIZED,
            detail="User not found or inactive"
        )
    
    return user

async def get_current_user_optional(
    request: Request,
    db: AsyncSession = Depends(get_db)
):
    """Get current user if authenticated, otherwise return None"""
    auth_header = request.headers.get("Authorization")
    
    if not auth_header or not auth_header.startswith("Bearer "):
        return None
    
    token = auth_header.split(" ")[1]
    
    try:
        payload = jwt.decode(token, settings.JWT_SECRET_KEY, algorithms=[settings.JWT_ALGORITHM])
        user_id: int = payload.get("sub")
        
        if user_id is None:
            return None
        
        # Get user from database
        result = await db.execute(
            select(User).where(User.id == user_id, User.status == 'active')
        )
        user = result.scalar_one_or_none()
        
        return user
        
    except JWTError:
        return None

# =============================================================================
# EMAIL SERVICE (SMTP)
# =============================================================================

class EmailService:
    """Email service handler using SMTP"""
    
    def __init__(self):
        self.smtp_server = settings.SMTP_SERVER
        self.smtp_port = settings.SMTP_PORT
        self.smtp_username = settings.SMTP_USERNAME
        self.smtp_password = settings.SMTP_PASSWORD
        self.from_email = settings.FROM_EMAIL
    
    def get_otp_email_template(self, otp: str, username: str) -> str:
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
    
    def get_reset_password_email_template(self, otp: str, username: str) -> str:
        return f"""
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
            This OTP is valid for <strong>10 minutes</strong>.
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
    
    def get_welcome_email_template(self, username: str) -> str:
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
    
    async def send_email(self, to_email: str, subject: str, html_content: str, background_tasks: BackgroundTasks):
        """Send email using SMTP in background"""
        background_tasks.add_task(self._send_email_sync, to_email, subject, html_content)
    
    def _send_email_sync(self, to_email: str, subject: str, html_content: str):
        """Sync method to send email (called in background)"""
        try:
            # Create message
            msg = MIMEMultipart('alternative')
            msg['Subject'] = subject
            msg['From'] = self.from_email
            msg['To'] = to_email
            
            # Attach HTML content
            html_part = MIMEText(html_content, 'html')
            msg.attach(html_part)
            
            # Connect to SMTP server and send
            with smtplib.SMTP(self.smtp_server, self.smtp_port) as server:
                server.starttls()
                server.login(self.smtp_username, self.smtp_password)
                server.send_message(msg)
            
            logging.info(f"Email sent to {to_email}")
            
        except Exception as e:
            logging.error(f"Failed to send email to {to_email}: {str(e)}")
    
    async def send_verification_email(self, email: str, otp: str, username: str, background_tasks: BackgroundTasks):
        """Send verification email"""
        subject = "🔐 Verify Your meiXuP Account"
        html_content = self.get_otp_email_template(otp, username)
        await self.send_email(email, subject, html_content, background_tasks)
    
    async def send_reset_password_email(self, email: str, otp: str, username: str, background_tasks: BackgroundTasks):
        """Send password reset email"""
        subject = "🔐 Reset Your meiXuP Password"
        html_content = self.get_reset_password_email_template(otp, username)
        await self.send_email(email, subject, html_content, background_tasks)
    
    async def send_welcome_email(self, email: str, username: str, background_tasks: BackgroundTasks):
        """Send welcome email"""
        subject = "🎉 Welcome to meiXuP!"
        html_content = self.get_welcome_email_template(username)
        await self.send_email(email, subject, html_content, background_tasks)

email_service = EmailService()

# =============================================================================
# UTILITY FUNCTIONS
# =============================================================================

def generate_otp(length: int = 6) -> str:
    """Generate OTP"""
    digits = string.digits
    return ''.join(secrets.choice(digits) for _ in range(length))

async def rate_limit(key: str, limit: int, window: int, redis_client: redis.Redis):
    """Rate limiting using Redis"""
    current = await redis_client.get(key)
    if current and int(current) >= limit:
        return False
    
    if not current:
        await redis_client.setex(key, window, 1)
    else:
        await redis_client.incr(key)
    
    return True

# =============================================================================
# FASTAPI APPLICATION
# =============================================================================

app = FastAPI(
    title=settings.APP_NAME,
    version=settings.APP_VERSION,
    docs_url="/docs" if settings.DEBUG else None,
    redoc_url="/redoc" if settings.DEBUG else None,
    openapi_url="/openapi.json" if settings.DEBUG else None,
)

# =============================================================================
# MIDDLEWARE (ORDER MATTERS)
# =============================================================================

# 1️⃣ TRUSTED HOST (MUST BE FIRST)
app.add_middleware(
    TrustedHostMiddleware,
    allowed_hosts=["*"] if settings.DEBUG else [
        "app.meixup.com",
        "api.meixup.com",
    ],
)

# 2️⃣ CORS (Parsed list, NOT raw env string)
app.add_middleware(
    CORSMiddleware,
    allow_origins=settings.cors_origins,  # ✅ correct
    allow_credentials=True,
    allow_methods=["*"],
    allow_headers=["*"],
)

# 3️⃣ GZIP
app.add_middleware(GZipMiddleware, minimum_size=1000)

# =============================================================================
# API ROUTER
# =============================================================================

api_router = APIRouter(prefix=settings.API_PREFIX)
# =============================================================================
# SYSTEM ENDPOINTS
# =============================================================================

@api_router.get("/", tags=["System"])
async def root():
    """Root endpoint"""
    return {"message": "Welcome to the meiXuP AUTH API Service"}

@api_router.get("/health", tags=["System"])
async def health_check():
    """Health check endpoint"""
    return {"status": "healthy", "timestamp": datetime.utcnow().isoformat()}

@api_router.get("/status", tags=["System"])
async def system_status(
    redis_client: redis.Redis = Depends(get_redis),
    db: AsyncSession = Depends(get_db)
):
    redis_health = "unhealthy"
    db_health = "unhealthy"
    errors = {}

    # Redis check
    try:
        pong = await redis_client.ping()
        if pong in (True, "PONG"):
            redis_health = "healthy"
    except Exception as e:
        errors["redis"] = str(e)

    # Database check
    try:
        result = await db.execute(text("SELECT 1"))
        if result.scalar() == 1:
            db_health = "healthy"
    except Exception as e:
        errors["database"] = str(e)

    response = {
        "database": db_health,
        "redis": redis_health,
        "timestamp": datetime.utcnow().isoformat()
    }

    if errors:
        response["errors"] = errors

    return response

@api_router.get("/version", tags=["System"])
async def get_version():
    """Get API version"""
    return {
        "name": settings.APP_NAME,
        "version": settings.APP_VERSION,
        "environment": settings.APP_ENVIRONMENT
    }

# =============================================================================
# AUTHENTICATION ENDPOINTS
# =============================================================================

@api_router.post("/auth/register", response_model=BaseResponse, tags=["Authentication"])
async def register_user(
    request: RegisterRequest,
    background_tasks: BackgroundTasks,
    db: AsyncSession = Depends(get_db),
    redis_client: redis.Redis = Depends(get_redis)
):
    """Register new user"""
    # Check if user exists
    result = await db.execute(
        select(User).where(
            (User.email == request.email) | (User.username == request.username)
        )
    )
    existing_user = result.scalar_one_or_none()
    
    if existing_user:
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail="Email or username already registered"
        )
    
    # Create user
    hashed_password = get_password_hash(request.password)
    
    user = User(
        username=request.username,
        email=request.email,
        full_name=request.full_name,
        password_hash=hashed_password,
    )
    
    db.add(user)
    await db.commit()
    await db.refresh(user)
    
    # Generate OTP for email verification
    otp = generate_otp()
    otp_key = f"otp:email:{request.email}"
    await redis_client.setex(otp_key, 600, otp)  # 10 minutes expiry
    
    # Send verification email in background
    await email_service.send_verification_email(
        email=request.email,
        otp=otp,
        username=request.username,
        background_tasks=background_tasks
    )
    
    # Send welcome email in background
    await email_service.send_welcome_email(
        email=request.email,
        username=request.username,
        background_tasks=background_tasks
    )
    
    return BaseResponse(
        success=True,
        message="Registration successful. Please verify your email.",
        data={"user_id": user.id}
    )

@api_router.post("/auth/login", response_model=LoginResponse, tags=["Authentication"])
async def login_user(
    request: LoginRequest,
    db: AsyncSession = Depends(get_db),
    redis_client: redis.Redis = Depends(get_redis)
):
    """Login user"""
    # Check rate limiting
    rate_key = f"login_attempts:{request.username_or_email}"
    if not await rate_limit(rate_key, 5, 300, redis_client):  # 5 attempts per 5 minutes
        raise HTTPException(
            status_code=status.HTTP_429_TOO_MANY_REQUESTS,
            detail="Too many login attempts. Please try again later."
        )
    
    # Find user by email or username
    result = await db.execute(
        select(User).where(
            (User.email == request.username_or_email) | (User.username == request.username_or_email)
        )
    )
    user = result.scalar_one_or_none()
    
    if not user or not verify_password(request.password, user.password_hash):
        # Increment failed attempts
        await redis_client.incr(rate_key)
        raise HTTPException(
            status_code=status.HTTP_401_UNAUTHORIZED,
            detail="Invalid credentials"
        )
    
    # Check if user is active
    if user.status != 'active':
        raise HTTPException(
            status_code=status.HTTP_403_FORBIDDEN,
            detail="Account is suspended or deactivated"
        )
    
    # Generate tokens
    access_token = create_access_token({"sub": user.id})
    refresh_token = create_refresh_token({"sub": user.id})
    
    # Create session
    session = UserSession(
        user_id=user.id,
        session_token=hashlib.sha256(access_token.encode()).hexdigest(),
        refresh_token=hashlib.sha256(refresh_token.encode()).hexdigest(),
        device_id=request.device_id,
        device_info=str(request.device_info) if request.device_info else None,
        expires_at=datetime.utcnow() + timedelta(minutes=settings.JWT_ACCESS_TOKEN_EXPIRE_MINUTES),
        refresh_expires_at=datetime.utcnow() + timedelta(days=settings.JWT_REFRESH_TOKEN_EXPIRE_DAYS)
    )
    
    db.add(session)
    await db.commit()
    
    # Clear rate limit on successful login
    await redis_client.delete(rate_key)
    
    return LoginResponse(
        access_token=access_token,
        refresh_token=refresh_token,
        expires_in=settings.JWT_ACCESS_TOKEN_EXPIRE_MINUTES * 60,
        user=user.to_dict()
    )

@api_router.post("/auth/logout", response_model=BaseResponse, tags=["Authentication"])
async def logout_user(
    current_user: User = Depends(get_current_user),
    db: AsyncSession = Depends(get_db),
    authorization: Optional[str] = Header(None)
):
    """Logout user (invalidate current session)"""
    if authorization and authorization.startswith("Bearer "):
        token = authorization.split(" ")[1]
        session_token = hashlib.sha256(token.encode()).hexdigest()
        
        # Invalidate session
        await db.execute(
            update(UserSession)
            .where(UserSession.session_token == session_token)
            .values(is_active=False)
        )
        await db.commit()
    
    return BaseResponse(
        success=True,
        message="Logged out successfully"
    )

@api_router.post("/auth/refresh", response_model=LoginResponse, tags=["Authentication"])
async def refresh_token(
    request: RefreshTokenRequest,
    db: AsyncSession = Depends(get_db)
):
    """Refresh access token"""
    try:
        # Verify refresh token
        payload = jwt.decode(
            request.refresh_token,
            settings.JWT_SECRET_KEY,
            algorithms=[settings.JWT_ALGORITHM]
        )
        
        user_id = payload.get("sub")
        token_type = payload.get("type")
        
        if token_type != "refresh":
            raise HTTPException(
                status_code=status.HTTP_401_UNAUTHORIZED,
                detail="Invalid token type"
            )
        
        # Check if session exists
        refresh_token_hash = hashlib.sha256(request.refresh_token.encode()).hexdigest()
        result = await db.execute(
            select(UserSession).where(
                UserSession.refresh_token == refresh_token_hash,
                UserSession.is_active == True,
                UserSession.refresh_expires_at > datetime.utcnow()
            )
        )
        session = result.scalar_one_or_none()
        
        if not session:
            raise HTTPException(
                status_code=status.HTTP_401_UNAUTHORIZED,
                detail="Invalid or expired refresh token"
            )
        
        # Get user
        result = await db.execute(select(User).where(User.id == user_id))
        user = result.scalar_one_or_none()
        
        if not user or user.status != 'active':
            raise HTTPException(
                status_code=status.HTTP_401_UNAUTHORIZED,
                detail="User not found or inactive"
            )
        
        # Generate new tokens
        new_access_token = create_access_token({"sub": user.id})
        new_refresh_token = create_refresh_token({"sub": user.id})
        
        # Update session
        session.session_token = hashlib.sha256(new_access_token.encode()).hexdigest()
        session.refresh_token = hashlib.sha256(new_refresh_token.encode()).hexdigest()
        session.expires_at = datetime.utcnow() + timedelta(minutes=settings.JWT_ACCESS_TOKEN_EXPIRE_MINUTES)
        session.refresh_expires_at = datetime.utcnow() + timedelta(days=settings.JWT_REFRESH_TOKEN_EXPIRE_DAYS)
        session.last_used_at = datetime.utcnow()
        
        await db.commit()
        
        return LoginResponse(
            access_token=new_access_token,
            refresh_token=new_refresh_token,
            expires_in=settings.JWT_ACCESS_TOKEN_EXPIRE_MINUTES * 60,
            user=user.to_dict()
        )
        
    except JWTError:
        raise HTTPException(
            status_code=status.HTTP_401_UNAUTHORIZED,
            detail="Invalid refresh token"
        )

@api_router.post("/auth/send-otp", response_model=BaseResponse, tags=["Authentication"])
async def send_otp(
    email: str = Body(..., embed=True),
    background_tasks: BackgroundTasks = BackgroundTasks(),
    db: AsyncSession = Depends(get_db),
    redis_client: redis.Redis = Depends(get_redis)
):
    """Send OTP for verification"""
    # Check if user exists
    result = await db.execute(
        select(User).where(User.email == email)
    )
    user = result.scalar_one_or_none()
    
    if not user:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail="User not found"
        )
    
    otp = generate_otp()
    otp_key = f"otp:email:{email}"
    await redis_client.setex(otp_key, 600, otp)  # 10 minutes expiry
    
    # Send verification email in background
    await email_service.send_verification_email(
        email=email,
        otp=otp,
        username=user.username,
        background_tasks=background_tasks
    )
    
    return BaseResponse(
        success=True,
        message="OTP sent successfully"
    )

@api_router.post("/auth/verify-otp", response_model=BaseResponse, tags=["Authentication"])
async def verify_otp(
    request: VerifyOTPRequest,
    db: AsyncSession = Depends(get_db),
    redis_client: redis.Redis = Depends(get_redis)
):
    """Verify OTP"""
    otp_key = f"otp:email:{request.email}"
    
    # Get stored OTP
    stored_otp = await redis_client.get(otp_key)
    
    if not stored_otp or stored_otp != request.otp:
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail="Invalid or expired OTP"
        )
    
    # Update user verification status
    result = await db.execute(
        select(User).where(User.email == request.email)
    )
    user = result.scalar_one_or_none()
    
    if user:
        user.email_verified = True
        await db.commit()
    
    # Delete OTP
    await redis_client.delete(otp_key)
    
    return BaseResponse(
        success=True,
        message="Email verified successfully"
    )

@api_router.post("/auth/reset-password", response_model=BaseResponse, tags=["Authentication"])
async def reset_password(
    request: ResetPasswordRequest,
    background_tasks: BackgroundTasks = BackgroundTasks(),
    db: AsyncSession = Depends(get_db),
    redis_client: redis.Redis = Depends(get_redis)
):
    """Send password reset OTP"""
    # Check if user exists
    result = await db.execute(
        select(User).where(User.email == request.email)
    )
    user = result.scalar_one_or_none()
    
    if not user:
        # Don't reveal that user doesn't exist
        return BaseResponse(
            success=True,
            message="If your email is registered, you will receive a reset OTP"
        )
    
    # Generate reset OTP
    otp = generate_otp()
    reset_key = f"reset_token:{otp}"
    
    # Store token with user ID (10 minute expiry)
    await redis_client.setex(reset_key, 600, str(user.id))
    
    # Send reset email in background
    await email_service.send_reset_password_email(
        email=request.email,
        otp=otp,
        username=user.username,
        background_tasks=background_tasks
    )
    
    return BaseResponse(
        success=True,
        message="Password reset OTP sent to your email"
    )

@api_router.post("/auth/verify-reset-otp", response_model=BaseResponse, tags=["Authentication"])
async def verify_reset_otp(
    email: str = Body(..., embed=True),
    otp: str = Body(..., embed=True),
    new_password: str = Body(..., embed=True),
    db: AsyncSession = Depends(get_db),
    redis_client: redis.Redis = Depends(get_redis)
):
    """Verify reset OTP and set new password"""
    reset_key = f"reset_token:{otp}"
    
    # Get stored user ID
    user_id_str = await redis_client.get(reset_key)
    
    if not user_id_str:
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail="Invalid or expired OTP"
        )
    
    user_id = int(user_id_str)
    
    # Get user
    result = await db.execute(
        select(User).where(User.id == user_id, User.email == email)
    )
    user = result.scalar_one_or_none()
    
    if not user:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail="User not found"
        )
    
    # Update password
    user.password_hash = get_password_hash(new_password)
    user.password_updated_at = datetime.utcnow()
    
    await db.commit()
    
    # Delete reset token
    await redis_client.delete(reset_key)
    
    return BaseResponse(
        success=True,
        message="Password reset successfully"
    )

@api_router.post("/auth/change-password", response_model=BaseResponse, tags=["Authentication"])
async def change_password(
    request: ChangePasswordRequest,
    current_user: User = Depends(get_current_user),
    db: AsyncSession = Depends(get_db)
):
    """Change password for authenticated user"""
    # Verify current password
    if not verify_password(request.current_password, current_user.password_hash):
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail="Current password is incorrect"
        )
    
    # Update password
    current_user.password_hash = get_password_hash(request.new_password)
    current_user.password_updated_at = datetime.utcnow()
    
    await db.commit()
    
    return BaseResponse(
        success=True,
        message="Password changed successfully"
    )

@api_router.get("/auth/me", response_model=BaseResponse, tags=["Authentication"])
async def get_current_user_info(current_user: User = Depends(get_current_user)):
    """Get current user info"""
    return BaseResponse(
        success=True,
        data={"user": current_user.to_dict(include_sensitive=True)}
    )

@api_router.get("/auth/sessions", response_model=BaseResponse, tags=["Authentication"])
async def get_user_sessions(
    current_user: User = Depends(get_current_user),
    db: AsyncSession = Depends(get_db)
):
    """Get active sessions for current user"""
    result = await db.execute(
        select(UserSession).where(
            UserSession.user_id == current_user.id,
            UserSession.is_active == True,
            UserSession.expires_at > datetime.utcnow()
        ).order_by(UserSession.created_at.desc())
    )
    sessions = result.scalars().all()
    
    sessions_data = []
    for session in sessions:
        sessions_data.append({
            "device_id": session.device_id,
            "device_info": session.device_info,
            "ip_address": session.ip_address,
            "created_at": session.created_at.isoformat(),
            "last_used_at": session.last_used_at.isoformat(),
            "expires_at": session.expires_at.isoformat()
        })
    
    return BaseResponse(
        success=True,
        data={"sessions": sessions_data}
    )

@api_router.post("/auth/revoke-session", response_model=BaseResponse, tags=["Authentication"])
async def revoke_session(
    device_id: str = Body(..., embed=True),
    current_user: User = Depends(get_current_user),
    db: AsyncSession = Depends(get_db)
):
    """Revoke a specific session"""
    await db.execute(
        update(UserSession)
        .where(
            UserSession.user_id == current_user.id,
            UserSession.device_id == device_id
        )
        .values(is_active=False)
    )
    await db.commit()
    
    return BaseResponse(
        success=True,
        message="Session revoked successfully"
    )

# Mount API router
app.include_router(api_router)




if __name__ == "__main__":
    import uvicorn
    uvicorn.run("auth:app", host="0.0.0.0", port=8000, reload=True, log_level="info")


    