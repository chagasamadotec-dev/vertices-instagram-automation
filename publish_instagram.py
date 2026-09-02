#!/usr/bin/env python3
"""
Publica automaticamente 1 post (carrossel) + 1 story por dia no Instagram,
lendo as pastas em queue/AAAA-MM-DD_slug/.

Roda dentro do GitHub Actions (publish.yml), 1x por dia.
Depois de publicar com sucesso, move a pasta processada para posted/.
"""
import os
import sys
import time
import glob
import shutil
import requests
from datetime import datetime
from zoneinfo import ZoneInfo

API_BASE = "https://graph.instagram.com/v23.0"
IG_USER_ID = os.environ["IG_USER_ID"]
IG_ACCESS_TOKEN = os.environ["IG_ACCESS_TOKEN"]

REPO = os.environ.get("GITHUB_REPOSITORY", "")
BRANCH = os.environ.get("GITHUB_REF_NAME", "main")
RAW_BASE = f"https://raw.githubusercontent.com/{REPO}/{BRANCH}" if REPO else None
FORCE_POST = os.environ.get("FORCE_POST", "false").lower() == "true"

TZ = ZoneInfo("America/Sao_Paulo")

def raw_url(path):
    if not RAW_BASE:
        raise RuntimeError("GITHUB_REPOSITORY nao definido.")
    return RAW_BASE + "/" + requests.utils.quote(path)


def api_post(path, **params):
    params["access_token"] = IG_ACCESS_TOKEN
    r = requests.post(f"{API_BASE}/{path}", params=params, timeout=60)
    data = r.json()
    if r.status_code >= 400 or "error" in data:
        raise RuntimeError(f"Erro na chamada {path}: {data}")
    return data


def api_get(path, **params):
    params["access_token"] = IG_ACCESS_TOKEN
    r = requests.get(f"{API_BASE}/{path}", params=params, timeout=60)
    data = r.json()
    if r.status_code >= 400 or "error" in data:
        raise RuntimeError(f"Erro na chamada {path}: {data}")
    return data

def wait_container_ready(container_id, timeout=300):
    start = time.time()
    while time.time() - start < timeout:
        data = api_get(container_id, fields="status_code")
        status = data.get("status_code")
        if status == "FINISHED":
            return
        if status == "ERROR":
            raise RuntimeError(f"Container {container_id} falhou: {data}")
        time.sleep(5)
    raise RuntimeError(f"Timeout esperando container {container_id}.")

def find_next_post(queue_dir="queue"):
    folders = sorted(d for d in glob.glob(os.path.join(queue_dir, "*")) if os.path.isdir(d))
    if not folders:
        return None
    if FORCE_POST:
        print(f"FORCE_POST ativo: publicando {folders[0]} independente da data.")
        return folders[0]
    today = datetime.now(TZ).date()
    for folder in folders:
        name = os.path.basename(folder)
        date_str = name.split("_", 1)[0]
        try:
            folder_date = datetime.strptime(date_str, "%Y-%m-%d").date()
        except ValueError:
            continue
        if folder_date <= today:
            return folder
    return None

def publish_feed_post(folder):
    caption = open(os.path.join(folder, "caption.txt"), encoding="utf-8").read().strip()
    photos = sorted(glob.glob(os.path.join(folder, "0*.jpg")) + glob.glob(os.path.join(folder, "0*.png")))
    video_path = os.path.join(folder, "video.mp4")
    has_video = os.path.exists(video_path)
    children_ids = []
    for photo in photos:
        rel = os.path.relpath(photo)
        data = api_post(f"{IG_USER_ID}/media", image_url=raw_url(rel), is_carousel_item="true")
        children_ids.append(data["id"])
        print(f"  container criado (foto): {data['id']}")
    if has_video:
        rel = os.path.relpath(video_path)
        data = api_post(f"{IG_USER_ID}/media", video_url=raw_url(rel), media_type="VIDEO", is_carousel_item="true")
        vid_container = data["id"]
        print(f"  container criado (video): {vid_container}")
        wait_container_ready(vid_container)
        children_ids.append(vid_container)

    if not children_ids:
        raise RuntimeError(f"Nenhuma midia encontrada em {folder}")
    if len(children_ids) == 1:
        carousel = {"id": children_ids[0]}
    else:
        carousel = api_post(
            f"{IG_USER_ID}/media",
            media_type="CAROUSEL",
            children=",".join(children_ids),
            caption=caption,
        )
        print(f"  container do carrossel: {carousel['id']}")
        wait_container_ready(carousel["id"])
    result = api_post(f"{IG_USER_ID}/media_publish", creation_id=carousel["id"])
    print(f"  POST PUBLICADO: {result}")
    return result

def publish_story(folder):
    story_path = os.path.join(folder, "story.jpg")
    if not os.path.exists(story_path):
        print("  sem story.jpg, pulando story.")
        return None
    rel = os.path.relpath(story_path)
    data = api_post(f"{IG_USER_ID}/media", image_url=raw_url(rel), media_type="STORIES")
    container_id = data["id"]
    wait_container_ready(container_id)
    result = api_post(f"{IG_USER_ID}/media_publish", creation_id=container_id)
    print(f"  STORY PUBLICADO: {result}")
    return result

def main():
    folder = find_next_post()
    if not folder:
        print("Nenhum post pendente para hoje. Nada a fazer.")
        return
    print(f"Publicando: {folder}")
    publish_feed_post(folder)
    publish_story(folder)
    os.makedirs("posted", exist_ok=True)
    dest = os.path.join("posted", os.path.basename(folder))
    shutil.move(folder, dest)
    print(f"Pasta movida para {dest}")


if __name__ == "__main__":
    try:
        main()
    except Exception as e:
        print(f"ERRO: {e}", file=sys.stderr)
        sys.exit(1)
