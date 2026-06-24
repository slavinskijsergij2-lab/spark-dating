"""Jinja2 template filters and globals registered in main.py."""
import json
from datetime import datetime

from app.utils.time import utcnow as _utcnow


def tojson_filter(value, indent=None) -> str:
    """Serialize *value* to JSON, escaping ``</script>`` so it's safe inside
    a ``<script>`` block.  Use ``| tojson | safe`` in templates."""
    def _default(o):
        if isinstance(o, datetime):
            return o.isoformat()
        raise TypeError(f"Not JSON serializable: {type(o)}")

    result = json.dumps(value, default=_default, indent=indent, ensure_ascii=False)
    return result.replace("</", "<\\/").replace("<!--", "<\\!--")


_ONLINE_LABELS: dict[str, tuple[str, str, str]] = {
    "ru": ("Онлайн", "{n} мин назад", "{n} ч назад"),
    "uk": ("Онлайн", "{n} хв тому", "{n} год тому"),
    "en": ("Online", "{n}m ago", "{n}h ago"),
    "de": ("Online", "vor {n}m", "vor {n}h"),
    "tr": ("Çevrimiçi", "{n}d önce", "{n}s önce"),
    "ar": ("متصل", "منذ {n}د", "منذ {n}س"),
}


def online_status(last_seen, lang: str = "en") -> dict | None:
    """Return ``{"is_online": bool, "label": str}`` or ``None`` if unknown."""
    if not last_seen:
        return None
    diff = (_utcnow() - last_seen).total_seconds()
    online_lbl, mins_lbl, hrs_lbl = _ONLINE_LABELS.get(lang, _ONLINE_LABELS["en"])
    if diff < 300:
        return {"is_online": True, "label": online_lbl}
    if diff < 3600:
        return {"is_online": False, "label": mins_lbl.replace("{n}", str(int(diff / 60)))}
    if diff < 86400:
        return {"is_online": False, "label": hrs_lbl.replace("{n}", str(int(diff / 3600)))}
    return None
