import json
import os
from datetime import datetime, timezone
from pathlib import Path

STATE_FILE = Path("state.json")


def load_state() -> dict:
    if not STATE_FILE.exists():
        return {"last_tweet_id": None, "last_run": None, "pending_drafts": []}
    return json.loads(STATE_FILE.read_text(encoding="utf-8"))


def save_state(state: dict) -> None:
    STATE_FILE.write_text(json.dumps(state, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")


def validate_secrets() -> tuple[bool, str]:
    x_raw = os.getenv("X_COOKIES_RAW", "").strip()
    tt_json = os.getenv("TIKTOK_COOKIES_JSON", "").strip()
    if not x_raw or not tt_json:
        return False, "Missing X_COOKIES_RAW or TIKTOK_COOKIES_JSON in GitHub Secrets."
    try:
        parsed = json.loads(tt_json)
        if not isinstance(parsed, dict) or "cookies" not in parsed:
            return False, "TIKTOK_COOKIES_JSON must be a JSON object with a 'cookies' list."
    except json.JSONDecodeError:
        return False, "Invalid JSON format in TIKTOK_COOKIES_JSON."
    return True, "Secrets loaded."


def main() -> None:
    target_user = os.getenv("TARGET_X_USER", "iwp70").strip().lstrip("@") or "iwp70"
    state = load_state()
    state["last_run"] = datetime.now(timezone.utc).isoformat()

    ok, msg = validate_secrets()
    print(f"[{target_user}] {msg}")
    if not ok:
        # مسار احتياطي: لا نكسر المهمة بصمت؛ نحفظ وقت الفحص وننبه لتحديث الكوكيز
        save_state(state)
        return

    # هنا يُربط منطق جلب أحدث منشورات target_user وتصفية الصور/الفيديو فقط
    # وتجاهل النص الصرف، ثم التحقق من tweet_id > last_tweet_id لمنع التكرار.
    # في حال فشل النشر المباشر لـ TikTok أو طلب تحقق، يُضاف العنصر إلى pending_drafts.
    save_state(state)


if __name__ == "__main__":
    main()
