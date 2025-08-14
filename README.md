# YouTube Shorts Auto Pipeline (Windows)

Авто-нарезка серий → Shorts 9:16 (1080x1920) → загрузка на **каналы** (OAuth).

## Установка (Windows 10/11)

1. **FFmpeg**:
   ```powershell
   choco install ffmpeg -y
2. **Start**
    ```powershell
   cd shorts-pipeline
    python -m venv .venv
    .\.venv\Scripts\pip install -r requirements.txt
    Copy-Item config.example.yaml config.yaml
    # положи OAuth JSON в auth\owner1_client_secret.json и правь config.yaml
    .\.venv\Scripts\python -m pipeline.main   # разовая обработка
    # или
    .\.venv\Scripts\python -m pipeline.watch  # автопроцессинг при копировании в inbox
