import json
import os
import re
from datetime import datetime, timezone
from pathlib import Path
from typing import Any
import requests

STATE_FILE = Path("state.json")
MEDIA_DIR = Path("media_queue")

X_GRAPHQL_USER_BY_NAME_URL = (
    "https://x.com/i/api/graphql/G3KGOASz96M-Qu0nwmGIOg/UserByScreenName"
)
X_USER_TWEETS_QUERY_IDS = [
    "E3opETHurmVJflFsUBVuAG",
    "V1ze5qBHGlSc7SM5M9-Dqg",
    "9LYM8v9_3xI4f0A2r7H6Gg",
    "Q6a1d4M6t9L8k2J5h7G3fA",
]
X_BEARER_TOKEN = (
    "AAAAAAAAAAAAAAAAAAAAANRILgAAAAAAnNwIzUejRCOuH5E6I8xnZz4puTs"
    "%3D1Zv7ttfk8LF81IUq16cHjhLTvJu4FA33AGWWjCpTnA"
)
X_USER_ID_MAP: dict[str, str] = {"has7n_kw": "1904353351455686659"}


def load_state() -> dict[str, Any]:
    if not STATE_FILE.exists():
        return {
            "last_tweet_id": None,
            "processed_ids": [],
            "last_run": None,
            "last_status": "initialized",
            "pending_drafts": [],
        }
    data = json.loads(STATE_FILE.read_text(encoding="utf-8"))
    data.setdefault("last_tweet_id", None)
    data.setdefault("processed_ids", [])
    data.setdefault("last_run", None)
    data.setdefault("last_status", "idle")
    data.setdefault("pending_drafts", [])
    return data


def save_state(state: dict[str, Any]) -> None:
    state["processed_ids"] = state["processed_ids"][-200:]
    STATE_FILE.write_text(
        json.dumps(state, ensure_ascii=False, indent=2) + "\n",
        encoding="utf-8",
    )


def parse_raw_cookies(raw_str: str) -> dict[str, str]:
    cookies: dict[str, str] = {}
    for part in raw_str.split(";"):
        item = part.strip()
        if "=" in item:
            k, v = item.split("=", 1)
            cookies[k.strip()] = v.strip()
    return cookies


def validate_secrets() -> tuple[bool, str, dict[str, str], dict[str, Any]]:
    x_raw = os.getenv("X_COOKIES_RAW", "").strip()
    tt_raw = os.getenv("TIKTOK_COOKIES_JSON", "").strip()
    if not x_raw or not tt_raw:
        return False, "Missing X_COOKIES_RAW or TIKTOK_COOKIES_JSON.", {}, {}

    x_cookies = parse_raw_cookies(x_raw)
    if "auth_token" not in x_cookies or "ct0" not in x_cookies:
        return False, "X_COOKIES_RAW missing auth_token or ct0.", {}, {}

    try:
        tt_parsed = json.loads(tt_raw)
        if not isinstance(tt_parsed, dict) or not isinstance(tt_parsed.get("cookies"), list):
            return False, "TIKTOK_COOKIES_JSON must be an object with a 'cookies' list.", {}, {}
    except json.JSONDecodeError:
        return False, "Invalid JSON in TIKTOK_COOKIES_JSON.", {}, {}

    return True, "Secrets validated.", x_cookies, tt_parsed


def build_x_session(x_cookies: dict[str, str]) -> requests.Session:
    session = requests.Session()
    session.cookies.update(x_cookies)
    session.headers.update({
        "authorization": f"Bearer {requests.utils.unquote(X_BEARER_TOKEN)}",
        "x-csrf-token": x_cookies["ct0"],
        "x-twitter-active-user": "yes",
        "x-twitter-auth-type": "OAuth2Session",
        "user-agent": (
            "Mozilla/5.0 (Linux; Android 13; Mobile) AppleWebKit/537.36 "
            "(KHTML, like Gecko) Chrome/124.0.0.0 Mobile Safari/537.36"
        ),
        "referer": "https://x.com/",
    })
    return session


def _extract_id_from_html_fallback(session: requests.Session, username: str) -> str | None:
    try:
        page = session.get(f"https://x.com/{username}", timeout=25)
        if page.status_code != 200:
            return None
        html = page.text
        patterns = [
            r'"userId"\s*:\s*"(\d+)"',
            r'"rest_id"\s*:\s*"(\d+)"',
            r'user_id=(\d+)',
        ]
        for pat in patterns:
            m = re.search(pat, html)
            if m:
                return m.group(1)
    except Exception:
        return None
    return None


def resolve_user_id(session: requests.Session, username: str) -> str:
    clean_name = username.strip().lstrip("@")
    if not clean_name:
        raise RuntimeError("Target username is empty.")

    cached = X_USER_ID_MAP.get(clean_name.lower())
    if cached:
        return cached

    variables = {"screen_name": clean_name, "withSafetyModeUserFields": True}
    features = {
        "hidden_profile_subscriptions_enabled": True,
        "rweb_tipjar_consumption_enabled": True,
        "responsive_web_graphql_exclude_directive_enabled": True,
        "verified_phone_label_enabled": False,
        "subscriptions_verification_info_is_identity_verified_enabled": True,
        "subscriptions_verification_info_verified_since_enabled": True,
        "highlights_tweets_tab_ui_enabled": True,
        "responsive_web_twitter_article_notes_tab_enabled": True,
        "creator_subscriptions_tweet_preview_api_enabled": True,
        "responsive_web_graphql_skip_user_profile_image_extensions_enabled": False,
        "responsive_web_graphql_timeline_navigation_enabled": True,
    }

    try:
        resp = session.get(
            X_GRAPHQL_USER_BY_NAME_URL,
            params={
                "variables": json.dumps(variables, separators=(",", ":")),
                "features": json.dumps(features, separators=(",", ":")),
            },
            timeout=25,
        )
        resp.raise_for_status()
        data = resp.json()
        rest_id = (
            data.get("data", {})
            .get("user", {})
            .get("result", {})
            .get("rest_id")
        )
        if rest_id:
            X_USER_ID_MAP[clean_name.lower()] = str(rest_id)
            return str(rest_id)
    except requests.HTTPError as exc:
        if exc.response is None or exc.response.status_code != 404:
            raise

    fallback_id = _extract_id_from_html_fallback(session, clean_name)
    if fallback_id:
        X_USER_ID_MAP[clean_name.lower()] = str(fallback_id)
        return str(fallback_id)

    raise RuntimeError(
        f"Could not resolve user_id for '{clean_name}' (GraphQL 404). "
        "Add the numeric ID to X_USER_ID_MAP in runner.py."
    )


def extract_media_from_tweet(tweet_result: dict[str, Any]) -> tuple[str | None, str, list[dict[str, str]]]:
    legacy = tweet_result.get("legacy") or {}
    tweet_id = tweet_result.get("rest_id") or legacy.get("id_str")
    text = legacy.get("full_text", "")
    clean_text = re.sub(r"https?://t\.co/\S+", "", text).strip()

    media_items: list[dict[str, str]] = []
    extended_media = (
        (legacy.get("extended_entities") or {}).get("media")
        or (legacy.get("entities") or {}).get("media")
        or []
    )

    for m in extended_media:
        m_type = m.get("type")
        if m_type == "photo":
            url = m.get("media_url_https")
            if url:
                media_items.append({"kind": "image", "url": url})
        elif m_type in ("video", "animated_gif"):
            variants = (m.get("video_info") or {}).get("variants") or []
            mp4s = [
                v for v in variants
                if v.get("content_type") == "video/mp4" and v.get("url")
            ]
            if mp4s:
                best = max(mp4s, key=lambda item: int(item.get("bitrate") or 0))
                media_items.append({"kind": "video", "url": best["url"]})

    return tweet_id, clean_text, media_items


def _request_user_tweets_payload(session: requests.Session, user_id: str) -> dict[str, Any]:
    variables = {
        "userId": user_id,
        "count": 20,
        "includePromotedContent": False,
        "withQuickPromoteEligibilityTweetFields": False,
        "withVoice": False,
        "withV2Timeline": True,
    }
    features = {
        "rweb_lists_timeline_redesign_enabled": True,
        "responsive_web_graphql_exclude_directive_enabled": True,
        "verified_phone_label_enabled": False,
        "creator_subscriptions_tweet_preview_api_enabled": True,
        "responsive_web_graphql_timeline_navigation_enabled": True,
        "responsive_web_graphql_skip_user_profile_image_extensions_enabled": False,
        "tweetypie_unmention_optimization_enabled": True,
        "responsive_web_edit_tweet_api_enabled": True,
        "graphql_is_translatable_rweb_tweet_is_translatable_enabled": True,
        "view_counts_everywhere_api_enabled": True,
        "longform_notetweets_consumption_enabled": True,
        "responsive_web_twitter_article_tweet_consumption_enabled": False,
        "tweet_awards_web_tipping_enabled": False,
        "freedom_of_speech_not_reach_fetch_enabled": True,
        "standardized_nudges_misinfo": True,
        "tweet_with_visibility_results_prefer_gql_limited_actions_policy_enabled": True,
        "longform_notetweets_rich_text_selection_enabled": True,
        "longform_notetweets_inline_media_enabled": True,
        "responsive_web_home_pinned_timelines_enabled": True,
        "responsive_web_enhance_cards_enabled": False,
    }

    last_404_error: Exception | None = None
    for qid in X_USER_TWEETS_QUERY_IDS:
        url = f"https://x.com/i/api/graphql/{qid}/UserTweets"
        try:
            resp = session.get(
                url,
                params={
                    "variables": json.dumps(variables, separators=(",", ":")),
                    "features": json.dumps(features, separators=(",", ":")),
                },
                timeout=25,
            )
            resp.raise_for_status()
            return resp.json()
        except requests.HTTPError as exc:
            if exc.response is not None and exc.response.status_code == 404:
                last_404_error = exc
                continue
            raise

    if last_404_error is not None:
        raise last_404_error
    raise RuntimeError("No UserTweets query ID succeeded.")


def fetch_media_posts(session: requests.Session, username: str) -> list[dict[str, Any]]:
    user_id = resolve_user_id(session, username)
    payload = _request_user_tweets_payload(session, user_id)

    instructions = (
        payload.get("data", {})
        .get("user", {})
        .get("result", {})
        .get("timeline_v2", {})
        .get("timeline", {})
        .get("instructions", [])
    )

    posts: list[dict[str, Any]] = []
    for inst in instructions:
        for entry in inst.get("entries") or []:
            content = entry.get("content") or {}
            item = (content.get("itemContent") or {}).get("tweet_results", {}).get("result") or {}
            if item.get("__typename") == "TweetWithVisibilityResults":
                item = item.get("tweet") or {}
            if item.get("__typename") != "Tweet":
                continue

            tweet_id, caption, media_items = extract_media_from_tweet(item)
            if tweet_id and media_items:
                posts.append({
                    "tweet_id": str(tweet_id),
                    "caption": caption,
                    "media": media_items,
                })

    posts.sort(key=lambda p: int(p["tweet_id"]) if p["tweet_id"].isdigit() else 0)
    return posts


def download_media_files(tweet_id: str, media_items: list[dict[str, str]]) -> list[Path]:
    tweet_dir = MEDIA_DIR / tweet_id
    tweet_dir.mkdir(parents=True, exist_ok=True)
    saved_paths: list[Path] = []

    for idx, item in enumerate(media_items, start=1):
        ext = ".mp4" if item["kind"] == "video" else ".jpg"
        out_path = tweet_dir / f"media_{idx}{ext}"
        r = requests.get(item["url"], timeout=40)
        r.raise_for_status()
        out_path.write_bytes(r.content)
        saved_paths.append(out_path)

    return saved_paths


def publish_to_tiktok(
    tt_cookies_obj: dict[str, Any],
    files: list[Path],
    caption: str,
) -> tuple[bool, str]:
    cookie_map = {
        c.get("name"): c.get("value")
        for c in tt_cookies_obj.get("cookies", [])
        if isinstance(c, dict) and c.get("name")
    }
    if not cookie_map.get("sessionid") and not cookie_map.get("sid_tt"):
        return False, "TikTok sessionid/sid_tt missing or expired -> queued in pending_drafts."

    _ = (files, caption)
    return False, "Direct upload requires session verification/signature -> queued in pending_drafts."


def main() -> None:
    target_user = os.getenv("TARGET_X_USER", "Has7n_Kw").strip().lstrip("@") or "Has7n_Kw"
    state = load_state()
    state["last_run"] = datetime.now(timezone.utc).isoformat()

    ok, msg, x_cookies, tt_cookies_obj = validate_secrets()
    print(f"[sync] {msg}")
    if not ok:
        state["last_status"] = f"secrets_error: {msg}"
        save_state(state)
        return

    try:
        x_session = build_x_session(x_cookies)
        media_posts = fetch_media_posts(x_session, target_user)
    except Exception as exc:
        state["last_status"] = f"x_fetch_error: {exc}"
        print(f"[sync] Fetch failed: {exc}")
        save_state(state)
        return

    processed_set = set(str(x) for x in state["processed_ids"])
    last_id_num = int(state["last_tweet_id"]) if str(state.get("last_tweet_id") or "").isdigit() else -1

    new_posts = []
    for p in media_posts:
        tid = p["tweet_id"]
        tid_num = int(tid) if tid.isdigit() else -1
        if tid in processed_set or (tid_num != -1 and tid_num <= last_id_num):
            continue
        new_posts.append(p)

    print(f"[sync] Found {len(new_posts)} new media post(s).")

    for post in new_posts:
        tid = post["tweet_id"]
        try:
            files = download_media_files(tid, post["media"])
            published, detail = publish_to_tiktok(tt_cookies_obj, files, post["caption"])
            if not published:
                state["pending_drafts"].append({
                    "tweet_id": tid,
                    "caption": post["caption"],
                    "media_count": len(files),
                    "reason": detail,
                    "queued_at": datetime.now(timezone.utc).isoformat(),
                })
                state["pending_drafts"] = state["pending_drafts"][-50:]
                print(f"[{tid}] {detail}")
            else:
                print(f"[{tid}] Published to TikTok.")

            state["processed_ids"].append(tid)
            if tid.isdigit():
                state["last_tweet_id"] = str(max(last_id_num, int(tid)))
                last_id_num = int(state["last_tweet_id"])
            processed_set.add(tid)
        except Exception as exc:
            state["pending_drafts"].append({
                "tweet_id": tid,
                "caption": post["caption"],
                "reason": f"processing_error: {exc}",
                "queued_at": datetime.now(timezone.utc).isoformat(),
            })
            state["pending_drafts"] = state["pending_drafts"][-50:]
            print(f"[{tid}] Error -> queued in pending_drafts: {exc}")

    state["last_status"] = "completed"
    save_state(state)


if __name__ == "__main__":
    main()
