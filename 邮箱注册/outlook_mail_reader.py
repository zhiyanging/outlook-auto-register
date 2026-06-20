#!/usr/bin/env python3
from __future__ import annotations
import json, re, time
from pathlib import Path
from urllib.parse import urlencode
from datetime import datetime, timezone
import requests

CLIENT_ID_DEFAULT = "14d82eec-204b-4c2f-b7e8-296a70dab67e"
TOKEN_URL = "https://login.microsoftonline.com/consumers/oauth2/v2.0/token"
GRAPH_MESSAGES = "https://graph.microsoft.com/v1.0/me/messages"
GRAPH_FOLDERS = "https://graph.microsoft.com/v1.0/me/mailFolders"


def refresh_access_token(refresh_token: str, client_id: str = CLIENT_ID_DEFAULT) -> str:
    data = {
        "client_id": client_id,
        "grant_type": "refresh_token",
        "refresh_token": refresh_token,
        "scope": "offline_access Mail.Read User.Read",
    }
    r = requests.post(TOKEN_URL, data=data, timeout=30)
    r.raise_for_status()
    return r.json()["access_token"]


def read_recent_messages(access_token: str, top: int = 20) -> list[dict]:
    params = {
        "$top": str(top),
        "$select": "subject,bodyPreview,receivedDateTime,from",
        "$orderby": "receivedDateTime desc",
    }
    r = requests.get(
        GRAPH_MESSAGES + "?" + urlencode(params),
        headers={"Authorization": f"Bearer {access_token}"},
        timeout=30,
    )
    r.raise_for_status()
    return r.json().get("value", [])


def read_recent_messages_all_folders(access_token: str, top_per_folder: int = 10) -> list[dict]:
    """Read recent messages from inbox, junk, and other folders."""
    headers = {"Authorization": f"Bearer {access_token}"}
    folders_resp = requests.get(
        GRAPH_FOLDERS + "?" + urlencode({"$top": "50", "$select": "id,displayName"}),
        headers=headers,
        timeout=30,
    )
    folders_resp.raise_for_status()
    folders = folders_resp.json().get("value", [])
    messages: list[dict] = []
    for folder in folders:
        fid = folder.get("id")
        if not fid:
            continue
        params = {
            "$top": str(top_per_folder),
            "$select": "subject,bodyPreview,receivedDateTime,from,parentFolderId",
            "$orderby": "receivedDateTime desc",
        }
        try:
            r = requests.get(
                f"https://graph.microsoft.com/v1.0/me/mailFolders/{fid}/messages?" + urlencode(params),
                headers=headers,
                timeout=30,
            )
            if r.status_code == 200:
                messages.extend(r.json().get("value", []))
        except Exception:
            pass
    messages.sort(key=lambda m: m.get("receivedDateTime", ""), reverse=True)
    return messages


def _parse_graph_time(value: str) -> float:
    if not value:
        return 0.0
    try:
        if value.endswith("Z"):
            value = value[:-1] + "+00:00"
        return datetime.fromisoformat(value).timestamp()
    except Exception:
        return 0.0


def extract_code(messages: list[dict], since_ts: float = 0.0) -> str | None:
    texts = []
    for m in messages:
        if since_ts and _parse_graph_time(m.get("receivedDateTime", "")) + 5 < since_ts:
            continue
        texts.append(m.get("subject", "") + "\n" + m.get("bodyPreview", ""))
    patterns = [
        r"验证码[^0-9A-Za-z]{0,40}([0-9]{4,8})",
        r"verification code[^0-9A-Za-z]{0,40}([0-9]{4,8})",
        r"security code[^0-9A-Za-z]{0,40}([0-9]{4,8})",
        r"one[- ]?time code[^0-9A-Za-z]{0,40}([0-9]{4,8})",
        r"\b([0-9]{4,8})\b",
    ]
    for text in texts:
        for pat in patterns:
            m = re.search(pat, text, re.I)
            if m:
                return m.group(1)
    return None


def wait_for_code(refresh_token: str, client_id: str = CLIENT_ID_DEFAULT, timeout: int = 180, interval: int = 10, since_ts: float = 0.0) -> str | None:
    deadline = time.time() + timeout
    access_token = refresh_access_token(refresh_token, client_id)
    while time.time() < deadline:
        msgs = read_recent_messages_all_folders(access_token, top_per_folder=15)
        code = extract_code(msgs, since_ts=since_ts)
        if code:
            return code
        time.sleep(interval)
    return None


def load_four_credentials(root: Path) -> list[tuple[str, str, str, str]]:
    rows = []
    cloud = root / "云端注册邮箱" / "四凭证"
    if not cloud.exists():
        return rows
    for f in sorted(cloud.glob("*/*.txt"), reverse=True):
        try:
            parts = f.read_text(encoding="utf-8").strip().split("----")
            if len(parts) >= 4 and parts[3].strip():
                rows.append((parts[0].strip(), parts[1].strip(), parts[2].strip(), parts[3].strip()))
        except Exception:
            pass
    return rows

if __name__ == "__main__":
    import argparse
    p = argparse.ArgumentParser()
    p.add_argument("--root", default=str(Path(__file__).resolve().parents[1]))
    args = p.parse_args()
    creds = load_four_credentials(Path(args.root))
    print(f"four_credentials={len(creds)}")
