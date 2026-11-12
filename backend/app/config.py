from pathlib import Path
from pydantic_settings import BaseSettings, SettingsConfigDict

BASE_DIR = Path(__file__).resolve().parents[1]


class Settings(BaseSettings):
    model_config = SettingsConfigDict(env_file=BASE_DIR / ".env", extra="ignore")

    ollama_host: str = "http://localhost:11434"
    llm_model: str = "llama3.1"
    embed_model: str = "nomic-embed-text"
    chunk_size: int = 1000
    chunk_overlap: int = 150
    top_k: int = 5
    ocr_dpi: int = 200
    num_ctx: int = 4096          # smaller context = much faster on CPU
    keep_alive: str = "30m"      # keep the model loaded between calls
    extract_window: int = 6000   # chars per extraction call (bigger = fewer calls)
    extract_max_tokens: int = 1024
    data_dir: Path = BASE_DIR / "data"

    # handwriting / multilingual vision (on-prem)
    vision_enabled: bool = True
    vision_model: str = "gemma3:4b"
    vision_verify_passes: int = 2          # 2 = read twice and compare; 1 = every note needs human review
    vision_min_agreement: float = 0.85
    vision_max_tokens: int = 2048

    # security
    obligo_master_key: str = ""
    obligo_api_token: str = ""
    allowed_origins: str = "*"
    encrypt_at_rest: bool = True

    # reminders
    reminder_hour: int = 9
    default_notify_email: str = ""
    smtp_host: str = ""
    smtp_port: int = 587
    smtp_user: str = ""
    smtp_password: str = ""
    smtp_from: str = "obligo@localhost"
    slack_webhook_url: str = ""

    @property
    def upload_dir(self) -> Path:
        return self.data_dir / "uploads"

    @property
    def tmp_dir(self) -> Path:
        return self.data_dir / "tmp"

    @property
    def chroma_dir(self) -> Path:
        return self.data_dir / "chroma"

    @property
    def sqlite_path(self) -> Path:
        return self.data_dir / "obligo.db"


settings = Settings()
for p in (settings.data_dir, settings.upload_dir, settings.chroma_dir, settings.tmp_dir):
    p.mkdir(parents=True, exist_ok=True)