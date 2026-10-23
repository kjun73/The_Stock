from pydantic_settings import BaseSettings, SettingsConfigDict
from typing import Optional


class Settings(BaseSettings):
    # Broker Selection: "TOSS", "KIS", "NAVER", "MOCK"
    BROKER_TYPE: str = "TOSS"

    # Toss Securities Settings
    TOSS_CLIENT_ID: Optional[str] = None
    TOSS_CLIENT_SECRET: Optional[str] = None

    # KIS Settings
    KIS_ENV: str = "PROD"  # PROD 또는 DEV
    KIS_APP_KEY: str = "mock_key"
    KIS_APP_SECRET: str = "mock_secret"
    KIS_CANO: str = "00000000"
    KIS_ACNT_PRDT_CD: str = "01"

    # Execution Mode
    EXECUTION_MODE: str = "SHADOW"  # SHADOW (자체 가상체결) 또는 LIVE

    # AI & JEV
    OPENROUTER_API_KEY: Optional[str] = None
    JEV_TIMEOUT_MS: int = 700
    GEMINI_API_KEY: Optional[str] = None

    # Firebase Realtime Database
    FIREBASE_CREDENTIALS_PATH: str = "./firebase_credentials.json"
    FIREBASE_DATABASE_URL: str = "https://default-rtdb.firebaseio.com/"
    FIREBASE_DB_URL: str = "https://default-rtdb.firebaseio.com/"

    # Server
    WEB_HOST: str = "127.0.0.1"
    WEB_PORT: int = 8000
    SERVER_AUTH_TOKEN: str = "dev-secret-token"
    DB_PATH: str = "the_stock.db"

    # KRX Data Portal (data.krx.co.kr) Login Credentials for pykrx
    KRX_ID: Optional[str] = None
    KRX_PW: Optional[str] = None

    model_config = SettingsConfigDict(
        env_file=".env",
        env_file_encoding="utf-8",
        extra="ignore"
    )

    @property
    def kis_base_url(self) -> str:
        if self.KIS_ENV == "PROD":
            return "https://openapi.koreainvestment.com:9443"
        return "https://openapivts.koreainvestment.com:29443"

    @property
    def kis_ws_url(self) -> str:
        if self.KIS_ENV == "PROD":
            return "ws://ops.koreainvestment.com:21000"
        return "ws://ops.koreainvestment.com:31000"


settings = Settings()


def init_krx_environment():
    """
    .env에 설정된 KRX_ID, KRX_PW를 os.environ에 주입하고
    pykrx가 이미 로드된 경우 KRX 인증 세션을 즉시 갱신합니다.
    """
    import os
    import sys
    if settings.KRX_ID and settings.KRX_PW and not settings.KRX_ID.startswith("your_"):
        os.environ["KRX_ID"] = settings.KRX_ID
        os.environ["KRX_PW"] = settings.KRX_PW
        if "pykrx.website.comm.auth" in sys.modules:
            try:
                from pykrx.website.comm.auth import build_krx_session, set_auth_session
                krx_sess = build_krx_session(settings.KRX_ID, settings.KRX_PW)
                if krx_sess:
                    set_auth_session(krx_sess)
            except Exception:
                pass


init_krx_environment()
