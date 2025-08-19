from pathlib import Path
from typing import Dict, Any, Optional
import json, os, time, logging
from googleapiclient.discovery import build
from googleapiclient.http import MediaFileUpload
from google_auth_oauthlib.flow import InstalledAppFlow
from google.oauth2.credentials import Credentials

LOG = logging.getLogger("pipeline.upload")
SCOPES = ["https://www.googleapis.com/auth/youtube.upload",
          "https://www.googleapis.com/auth/youtube"]

def _auth_for_alias(cfg: Dict[str, Any], alias: str):
    c = cfg["channels"]["aliases"][alias]
    secrets = c["client_secret"]
    creds_path = c["credentials"]
    creds = None
    if os.path.exists(creds_path):
        creds = Credentials.from_authorized_user_file(creds_path, SCOPES)
    if not creds or not creds.valid:
        if creds and creds.expired and creds.refresh_token:
            # Исправляем вызов refresh
            from google.auth.transport.requests import Request
            creds.refresh(Request())
        else:
            flow = InstalledAppFlow.from_client_secrets_file(secrets, SCOPES)
            creds = flow.run_local_server(port=0)
        with open(creds_path, "w", encoding="utf-8") as f:
            f.write(creds.to_json())
    return build("youtube", "v3", credentials=creds)

def upload_short(cfg: Dict[str, Any], alias: str, video_path: Path, title: str, description: str, tags: list, privacy: str, category_id: str, playlist_id: Optional[str]=""):
    youtube = _auth_for_alias(cfg, alias)
    body = {
        "snippet": {
            "title": title[:100],
            "description": description[:4900],
            "tags": tags,
            "categoryId": str(category_id),
            "defaultLanguage": "ru"
        },
        "status": {
            "privacyStatus": privacy
        }
    }
    media = MediaFileUpload(str(video_path), chunksize=-1, resumable=True, mimetype="video/*")
    LOG.info("Uploading %s to channel %s ...", video_path.name, alias)
    request = youtube.videos().insert(part="snippet,status", body=body, media_body=media)
    response = None
    while response is None:
        status, response = request.next_chunk()
    video_id = response["id"]
    LOG.info("Uploaded: https://youtube.com/watch?v=%s", video_id)

    if playlist_id:
        try:
            youtube.playlistItems().insert(
                part="snippet",
                body={"snippet":{"playlistId": playlist_id, "resourceId":{"kind":"youtube#video","videoId": video_id}}}
            ).execute()
        except Exception as e:
            LOG.warning("Add to playlist failed: %s", e)
    return video_id
