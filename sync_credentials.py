#!/usr/bin/env python3
# -*- coding: utf-8 -*-
from __future__ import annotations
import json, datetime as dt, subprocess, os, shutil, glob as glob_mod
from pathlib import Path

ROOT = Path(__file__).resolve().parent
RESULTS = ROOT / "runtime_outlook" / "results.jsonl"
RESULTS2 = ROOT / "自动化定时注册Outlook邮箱" / "runtime_outlook" / "results.jsonl"
CLOUD = ROOT / "云端注册邮箱"
CLOUD_REMOTE = os.environ.get("CLOUD_REGISTER_EMAIL_REMOTE", "git@github.com:xingluoyuankong/cloud-register-email.git")
THREE = CLOUD / "三凭证"
FOUR = CLOUD / "四凭证"
ALL = CLOUD / "all_success.jsonl"
CLIENT_ID_DEFAULT = "14d82eec-204b-4c2f-b7e8-296a70dab67e"

# Additional credential sources to import
EXTRA_CRED_DIRS = [
    ROOT.parent / "6",  # /home/workspace/6/
]

def safe_name(email: str) -> str:
    return email.replace("/", "_").replace("\\", "_") + ".txt"

def load_existing_emails() -> set[str]:
    emails = set()
    if ALL.exists():
        for line in ALL.read_text(encoding="utf-8").splitlines():
            try:
                d = json.loads(line)
                if d.get("email"):
                    emails.add(d["email"])
            except Exception:
                pass
    return emails

def load_existing_rt_status() -> dict[str, bool]:
    status = {}
    if ALL.exists():
        for line in ALL.read_text(encoding="utf-8").splitlines():
            try:
                d = json.loads(line)
                if d.get("email"):
                    status[d["email"]] = bool(d.get("has_refresh_token"))
            except Exception:
                pass
    return status

def load_four_cred_emails() -> set[str]:
    """Scan all 四凭证 directories to find which emails already have 四凭证."""
    emails = set()
    if FOUR.exists():
        for txt in FOUR.rglob("*.txt"):
            try:
                email = txt.stem  # filename without .txt
                if "@" in email:
                    emails.add(email)
            except Exception:
                pass
    return emails

RT_DIR = ROOT / "runtime_outlook" / "rt_tokens"

def load_rt_tokens() -> dict[str, tuple[str, str, str]]:
    result = {}
    if not RT_DIR.exists():
        return result
    for f in RT_DIR.glob("*.txt"):
        try:
            content = f.read_text(encoding="utf-8").strip()
            parts = content.split("----")
            if len(parts) >= 4 and parts[3].strip() and len(parts[3].strip()) > 20:
                result[parts[0].strip()] = (parts[1].strip(), parts[2].strip(), parts[3].strip())
        except Exception:
            pass
    return result

def update_jsonl_record(email: str, has_rt: bool) -> None:
    if not ALL.exists():
        return
    lines = ALL.read_text(encoding="utf-8").splitlines()
    changed = False
    for i, line in enumerate(lines):
        if not line.strip():
            continue
        try:
            d = json.loads(line)
        except Exception:
            continue
        if d.get("email") == email and not d.get("has_refresh_token") and has_rt:
            d["has_refresh_token"] = True
            lines[i] = json.dumps(d, ensure_ascii=False)
            changed = True
    if changed:
        ALL.write_text("\n".join(lines) + "\n", encoding="utf-8")


def _import_extra_cred_dirs(existing: set[str], rt_status: dict[str, bool],
                          four_emails: set[str]) -> tuple[list[str], list[str]]:
    """Scan extra credential directories (e.g. /home/workspace/6/) and import 四凭证."""
    added = []
    upgraded = []
    today = dt.datetime.now().strftime("%Y-%m-%d")
    for src_dir in EXTRA_CRED_DIRS:
        if not src_dir.exists():
            continue
        for txt_file in src_dir.glob("*.txt"):
            try:
                content = txt_file.read_text(encoding="utf-8").strip()
                parts = content.split("----")
                if len(parts) < 3:
                    continue
                email = parts[0].strip()
                password = parts[1].strip()
                client_id = parts[2].strip() if len(parts) > 2 else CLIENT_ID_DEFAULT
                rt = parts[3].strip() if len(parts) >= 4 and len(parts[3].strip()) > 20 else ""

                if not email or not password:
                    continue

                if email in existing:
                    # Upgrade 三→四 if RT available and not yet upgraded
                    if rt and not rt_status.get(email, False):
                        four_dir = FOUR / today
                        four_dir.mkdir(parents=True, exist_ok=True)
                        (four_dir / safe_name(email)).write_text(
                            f"{email}----{password}----{client_id}----{rt}\n", encoding="utf-8")
                        for td in THREE.iterdir():
                            tf = td / safe_name(email)
                            if tf.exists():
                                tf.unlink()
                        update_jsonl_record(email, True)
                        rt_status[email] = True
                        four_emails.add(email)
                        upgraded.append(email)
                    continue

                # New email
                if rt:
                    four_dir = FOUR / today
                    four_dir.mkdir(parents=True, exist_ok=True)
                    (four_dir / safe_name(email)).write_text(
                        f"{email}----{password}----{client_id}----{rt}\n", encoding="utf-8")
                    four_emails.add(email)
                else:
                    # Don't write 三凭证 if 四凭证 already exists
                    if email not in four_emails:
                        three_dir = THREE / today
                        three_dir.mkdir(parents=True, exist_ok=True)
                        (three_dir / safe_name(email)).write_text(
                            f"{email}----{password}----{client_id}\n", encoding="utf-8")

                record = {
                    "ts": dt.datetime.now().isoformat(),
                    "email": email, "password": password,
                    "client_id": client_id, "has_refresh_token": bool(rt),
                    "source": f"extra_dir/{src_dir.name}"
                }
                with ALL.open("a", encoding="utf-8") as f:
                    f.write(json.dumps(record, ensure_ascii=False) + "\n")
                existing.add(email)
                added.append(email)
            except Exception as e:
                print(f"  warn: skip {txt_file}: {e}")
    return added, upgraded


def _ensure_clean_git_state() -> None:
    """Abort any in-progress rebase or merge to ensure clean state."""
    git_dir = CLOUD / ".git"
    if not git_dir.exists():
        return

    # Check for rebase in progress
    rebase_dir = git_dir / "rebase-merge"
    rebase_apply = git_dir / "rebase-apply"
    if rebase_dir.exists() or rebase_apply.exists():
        print("  cleaning: abort stale rebase")
        subprocess.run(["git", "-C", str(CLOUD), "rebase", "--abort"],
                       check=False, capture_output=True)

    # Check for merge in progress
    merge_head = git_dir / "MERGE_HEAD"
    if merge_head.exists():
        print("  cleaning: abort stale merge")
        subprocess.run(["git", "-C", str(CLOUD), "merge", "--abort"],
                       check=False, capture_output=True)

    # Reset any unstaged changes (keep our credential files safe - they'll be re-added)
    r = subprocess.run(["git", "-C", str(CLOUD), "status", "--porcelain"],
                       capture_output=True, text=True)
    if r.stdout.strip():
        # Check for unmerged files
        unmerged = [l for l in r.stdout.splitlines() if l.startswith("UU") or l.startswith("AA") or l.startswith("DU") or l.startswith("UD")]
        if unmerged:
            print(f"  cleaning: {len(unmerged)} unmerged files, resetting")
            subprocess.run(["git", "-C", str(CLOUD), "reset", "--hard", "HEAD"],
                           check=False, capture_output=True)


def _normalize_at_filenames() -> int:
    """Rename any *_at_outlook.com.txt to *@outlook.com.txt in credential dirs."""
    renamed = 0
    for subdir in [THREE, FOUR]:
        if not subdir.exists():
            continue
        for txt in subdir.rglob("*_at_*.txt"):
            new_name = txt.with_name(txt.name.replace("_at_", "@"))
            if not new_name.exists():
                txt.rename(new_name)
                renamed += 1
            else:
                # Target exists, keep the @ version, remove the _at_ version
                txt.unlink()
                renamed += 1
    return renamed


def _push_credentials() -> bool:
    """Robust git fetch → rebase → commit → push with conflict handling."""
    _ensure_clean_git_state()

    # Step 1: Stage only credential-related files
    for subdir in ["三凭证", "四凭证"]:
        cred_dir = CLOUD / subdir
        if cred_dir.exists():
            for txt in cred_dir.rglob("*.txt"):
                subprocess.run(["git", "-C", str(CLOUD), "add", str(txt)],
                               check=False, capture_output=True)
    for fname in ["all_success.jsonl", "README.md"]:
        fpath = CLOUD / fname
        if fpath.exists():
            subprocess.run(["git", "-C", str(CLOUD), "add", str(fpath)],
                           check=False, capture_output=True)

    # Check if there's anything to commit
    diff_out = subprocess.check_output(
        ["git", "-C", str(CLOUD), "diff", "--cached", "--name-only"],
        text=True, stderr=subprocess.DEVNULL
    ).strip()
    if not diff_out:
        print("  nothing to commit locally")
        return True  # Nothing to push is OK

    msg = "sync credentials " + dt.datetime.now().strftime("%Y-%m-%d %H:%M:%S")
    subprocess.run(["git", "-C", str(CLOUD), "commit", "-m", msg],
                   check=False, capture_output=True)

    # Step 2: Fetch and sync with remote (3 retries)
    for attempt in range(3):
        r_fetch = subprocess.run(["git", "-C", str(CLOUD), "fetch", "origin"],
                                 check=False, capture_output=True)
        if r_fetch.returncode != 0:
            print(f"  fetch failed (attempt {attempt+1})")
            if attempt < 2:
                continue
            # Try push without fetch
            r_push = subprocess.run(["git", "-C", str(CLOUD), "push"],
                                    check=False, capture_output=True)
            if r_push.returncode == 0:
                return True
            continue

        # Check divergence
        behind = subprocess.check_output(
            ["git", "-C", str(CLOUD), "rev-list", "--count", "HEAD..origin/main"],
            text=True, stderr=subprocess.DEVNULL
        ).strip()

        if int(behind or 0) > 0:
            # Try rebase first
            r_rebase = subprocess.run(
                ["git", "-C", str(CLOUD), "rebase", "origin/main"],
                check=False, capture_output=True, text=True
            )
            if r_rebase.returncode != 0:
                # Rebase failed - abort and try merge
                subprocess.run(["git", "-C", str(CLOUD), "rebase", "--abort"],
                               check=False, capture_output=True)
                r_merge = subprocess.run(
                    ["git", "-C", str(CLOUD), "merge", "origin/main",
                     "--no-edit", "-X", "theirs"],
                    check=False, capture_output=True
                )
                if r_merge.returncode != 0:
                    subprocess.run(["git", "-C", str(CLOUD), "merge", "--abort"],
                                   check=False, capture_output=True)
                    print(f"  merge also failed (attempt {attempt+1}), skipping remote sync")
                    # Push our state anyway
                    pass

        # Try push
        r_push = subprocess.run(["git", "-C", str(CLOUD), "push"],
                                check=False, capture_output=True, text=True)
        if r_push.returncode == 0:
            n_files = len(diff_out.splitlines())
            print(f"  push_ok ({n_files} files, attempt {attempt+1})")
            return True

        print(f"  push rejected (attempt {attempt+1})")

    print("  push_failed after 3 attempts")
    return False


def _archive_old_local_credentials() -> None:
    """Move old local credential files to 已推送凭证/ after successful push."""
    archive_root = CLOUD / "已推送凭证"
    moved = 0

    # Archive from 自动化定时注册Outlook邮箱/三凭证 and 四凭证
    local_root = ROOT / "自动化定时注册Outlook邮箱"
    for subdir in ["三凭证", "四凭证"]:
        src_dir = local_root / subdir
        if not src_dir.exists():
            continue
        for day_dir in src_dir.iterdir():
            if not day_dir.is_dir():
                continue
            for txt_file in day_dir.glob("*.txt"):
                dest_day = archive_root / subdir / day_dir.name
                dest_day.mkdir(parents=True, exist_ok=True)
                dest = dest_day / txt_file.name
                if not dest.exists():
                    shutil.move(str(txt_file), str(dest))
                    moved += 1
                else:
                    # Already archived, remove source
                    txt_file.unlink()
                    moved += 1
        for day_dir in list(src_dir.iterdir()):
            if day_dir.is_dir() and not any(day_dir.iterdir()):
                day_dir.rmdir()

    # Archive from extra dirs (e.g. /home/workspace/6/)
    for src_dir in EXTRA_CRED_DIRS:
        if not src_dir.exists():
            continue
        dest_dir = archive_root / src_dir.name
        dest_dir.mkdir(parents=True, exist_ok=True)
        for txt_file in src_dir.glob("*.txt"):
            dest = dest_dir / txt_file.name
            if not dest.exists():
                shutil.move(str(txt_file), str(dest))
                moved += 1
            else:
                txt_file.unlink()
                moved += 1

    if moved:
        print(f"  archived {moved} old local credential files")


def main(push: bool = False):
    CLOUD.mkdir(parents=True, exist_ok=True)
    THREE.mkdir(parents=True, exist_ok=True)
    FOUR.mkdir(parents=True, exist_ok=True)

    existing = load_existing_emails()
    rt_status = load_existing_rt_status()
    four_emails = load_four_cred_emails()
    added = []
    upgraded = []

    # --- Source 1: results.jsonl files ---
    all_results = []
    for rf in [RESULTS, RESULTS2]:
        if rf.exists():
            all_results.extend(rf.read_text(encoding="utf-8").splitlines())
    if not all_results:
        print("no results file")

    for line in all_results:
        if not line.strip():
            continue
        try:
            d = json.loads(line)
        except Exception:
            continue
        if not d.get("success") or not d.get("email") or not d.get("password"):
            continue
        email = d["email"].strip()
        ts = d.get("ts") or dt.datetime.now().isoformat()
        day = ts[:10]
        client_id = d.get("client_id") or CLIENT_ID_DEFAULT
        rt = d.get("refresh_token") or ""

        if email in existing:
            # Existing email - only upgrade 三→四
            if rt and not rt_status.get(email, False):
                four_dir = FOUR / day
                four_dir.mkdir(parents=True, exist_ok=True)
                (four_dir / safe_name(email)).write_text(
                    f"{email}----{d['password']}----{client_id}----{rt}\n", encoding="utf-8")
                for td in THREE.iterdir():
                    tf = td / safe_name(email)
                    if tf.exists():
                        tf.unlink()
                update_jsonl_record(email, True)
                rt_status[email] = True
                four_emails.add(email)
                upgraded.append(email)
            continue

        # New email
        if rt:
            four_dir = FOUR / day
            four_dir.mkdir(parents=True, exist_ok=True)
            (four_dir / safe_name(email)).write_text(
                f"{email}----{d['password']}----{client_id}----{rt}\n", encoding="utf-8")
            four_emails.add(email)
        else:
            # Don't write 三凭证 if 四凭证 already exists for this email
            if email not in four_emails:
                three_dir = THREE / day
                three_dir.mkdir(parents=True, exist_ok=True)
                (three_dir / safe_name(email)).write_text(
                    f"{email}----{d['password']}----{client_id}\n", encoding="utf-8")

        record = {
            "ts": ts, "email": email, "password": d["password"],
            "client_id": client_id, "has_refresh_token": bool(rt),
            "source": "runtime_outlook/results.jsonl"
        }
        with ALL.open("a", encoding="utf-8") as f:
            f.write(json.dumps(record, ensure_ascii=False) + "\n")
        existing.add(email)
        added.append(email)

    # --- Source 2: rt_tokens/ ---
    rt_tokens = load_rt_tokens()
    for email, (pw, cid, token) in rt_tokens.items():
        if not rt_status.get(email, False):
            day = dt.datetime.now().strftime("%Y-%m-%d")
            four_dir = FOUR / day
            four_dir.mkdir(parents=True, exist_ok=True)
            (four_dir / safe_name(email)).write_text(
                f"{email}----{pw}----{cid}----{token}\n", encoding="utf-8")
            for td in THREE.iterdir():
                tf = td / safe_name(email)
                if tf.exists():
                    tf.unlink()
            update_jsonl_record(email, True)
            rt_status[email] = True
            four_emails.add(email)
            upgraded.append(email)

    # --- Source 3: Extra credential directories (e.g. /home/workspace/6/) ---
    extra_added, extra_upgraded = _import_extra_cred_dirs(existing, rt_status, four_emails)
    added.extend(extra_added)
    upgraded.extend(extra_upgraded)

    # --- Normalize _at_ filenames ---
    renamed = _normalize_at_filenames()
    if renamed:
        print(f"  normalized {renamed} _at_ → @ filenames")

    # --- README ---
    readme = CLOUD / "README.md"
    if not readme.exists():
        readme.write_text(
            "# 云端注册邮箱\n\n私有仓库：保存 Outlook 注册成功后的三凭证/四凭证。\n\n"
            "- 三凭证：`email----password----client_id`\n"
            "- 四凭证：`email----password----client_id----refresh_token`\n",
            encoding="utf-8")

    # --- Summary ---
    print(f"added={len(added)}")
    for e in added:
        print(f"  {e}")
    if upgraded:
        print(f"upgraded={len(upgraded)} (三凭证→四凭证)")
        for e in upgraded:
            print(f"  {e}")

    # --- Push ---
    if push:
        git_dir = CLOUD / ".git"
        if not git_dir.exists():
            subprocess.run(["git", "-C", str(CLOUD), "init"], check=False, capture_output=True)
            subprocess.run(["git", "-C", str(CLOUD), "remote", "add", "origin", CLOUD_REMOTE],
                           check=False, capture_output=True)

        ok = _push_credentials()
        if ok:
            _archive_old_local_credentials()

    return 0


if __name__ == "__main__":
    import argparse
    p = argparse.ArgumentParser()
    p.add_argument("--push", action="store_true")
    raise SystemExit(main(push=p.parse_args().push))