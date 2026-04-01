from datetime import datetime, timedelta, timezone

try:
    from zoneinfo import ZoneInfo
except ImportError:  # pragma: no cover - Python fallback
    ZoneInfo = None


BUSINESS_TIMEZONE_NAME = "Asia/Bangkok"
TIME_TEXT_FORMAT = "%Y-%m-%d %H:%M:%S"
DATE_TEXT_FORMAT = "%Y-%m-%d"

if ZoneInfo is not None:
    try:
        BUSINESS_TZ = ZoneInfo(BUSINESS_TIMEZONE_NAME)
    except Exception:  # pragma: no cover - tz database fallback
        BUSINESS_TZ = timezone(timedelta(hours=7), name=BUSINESS_TIMEZONE_NAME)
else:  # pragma: no cover - Python fallback
    BUSINESS_TZ = timezone(timedelta(hours=7), name=BUSINESS_TIMEZONE_NAME)


def business_dt(value: datetime) -> datetime:
    if value.tzinfo is None:
        return value.replace(tzinfo=BUSINESS_TZ)
    return value.astimezone(BUSINESS_TZ)


def now_dt():
    return datetime.now(BUSINESS_TZ)


def format_time_text(value: datetime) -> str:
    return business_dt(value).strftime(TIME_TEXT_FORMAT)


def format_date_text(value: datetime) -> str:
    return business_dt(value).strftime(DATE_TEXT_FORMAT)


def now_text():
    return format_time_text(now_dt())


def today_key():
    return format_date_text(now_dt())


def coerce_time_text(value: datetime | str) -> str:
    if isinstance(value, datetime):
        return format_time_text(value)
    return format_time_text(parse_time_text(str(value)))


def parse_time_text(value: str) -> datetime:
    normalized = str(value).strip()
    if not normalized:
        raise ValueError("time text must be non-empty")

    try:
        parsed = datetime.fromisoformat(normalized)
    except ValueError:
        parsed = datetime.strptime(normalized, TIME_TEXT_FORMAT)

    return business_dt(parsed)


def from_timestamp(timestamp: int | float) -> datetime:
    return datetime.fromtimestamp(float(timestamp), BUSINESS_TZ)
