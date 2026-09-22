"""
Google Workspace 인증 모듈
기존 ~/.hermes/google_token.json 재사용
"""
import os
from pathlib import Path
from google.oauth2.credentials import Credentials
from google.auth.transport.requests import Request
from googleapiclient.discovery import build

TOKEN_PATH = Path.home() / ".hermes" / "google_token.json"

SCOPES = [
    'https://www.googleapis.com/auth/spreadsheets',
    'https://www.googleapis.com/auth/calendar'
]


def get_credentials():
    """기존 토큰에서 인증 정보 가져오기"""
    if not TOKEN_PATH.exists():
        raise FileNotFoundError(f"Google token not found: {TOKEN_PATH}")
    
    creds = Credentials.from_authorized_user_file(str(TOKEN_PATH), SCOPES)
    
    # 토큰 만료 시 갱신
    if creds and creds.expired and creds.refresh_token:
        creds.refresh(Request())
        # 갱신된 토큰 저장
        TOKEN_PATH.write_text(creds.to_json())
    
    return creds


def get_sheets_service():
    """Google Sheets API 서비스 객체 반환"""
    creds = get_credentials()
    return build('sheets', 'v4', credentials=creds)


def get_calendar_service():
    """Google Calendar API 서비스 객체 반환"""
    creds = get_credentials()
    return build('calendar', 'v3', credentials=creds)
