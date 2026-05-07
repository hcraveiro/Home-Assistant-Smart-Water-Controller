import random
import uuid
from datetime import time, datetime
from homeassistant.util import dt as dt_util
from homeassistant.util import slugify

def mac_to_uuid(mac: str, last_part: int ) -> str:
    # Remover os dois pontos do MAC Address
    mac_numbers = mac.replace(':', '')
    
    # Pegar os 12 primeiros dígitos do MAC para formar a parte fixa do UUID
    x_part = f"{mac_numbers[:4]}-{mac_numbers[4:8]}-{mac_numbers[8:12]}"
    
    # Gerar um número aleatório para os últimos 3 dígitos (YYY)
    yyy_part = f"{last_part:03d}"
    
    return f"{x_part}-{yyy_part}"

def ensure_datetime(value):
    if isinstance(value, datetime):
        return value
    if isinstance(value, str):
        try:
            return datetime.strptime(value, "%Y-%m-%d %H:%M:%S")
        except ValueError:
            return datetime.min  # Se o formato for inválido, usa datetime.min
    return datetime.min  # Se for None ou outro tipo inesperado
    
def ensure_aware(dt_obj: datetime | None) -> datetime | None:
    """Ensure datetime is timezone-aware."""
    if dt_obj and dt_obj.tzinfo is None:
        return dt_util.as_local(dt_obj)
    return dt_obj

def parse_time_string(value: str) -> time:
    """Parse a time string like 'HH:MM' or 'HH:MM:SS' into a time object.
    Raises ValueError on invalid format."""
    if not isinstance(value, str):
        raise ValueError(f"Time must be string, got {type(value)}")

    value = value.strip()
    # Try HH:MM:SS
    for fmt in ("%H:%M:%S", "%H:%M"):
        try:
            return datetime.strptime(value, fmt).time()
        except ValueError:
            continue

    # Also accept 'H' (e.g., "6" → 06:00)
    if value.isdigit():
        h = int(value)
        if 0 <= h <= 23:
            return time(hour=h, minute=0, second=0)

    raise ValueError(f"Invalid time format: '{value}'. Expected 'HH:MM' or 'HH:MM:SS'.")

def normalize_mac_address(mac: str) -> str:
    """Normalize a MAC address to lowercase colon-separated format."""
    if not isinstance(mac, str):
        return ""

    value = mac.strip()
    if not value:
        return ""

    value = value.replace("-", ":").lower()
    parts = value.split(":")
    if len(parts) != 6:
        return value

    try:
        parts = [f"{int(p, 16):02x}" for p in parts]
    except ValueError:
        return value

    return ":".join(parts)


def get_controller_unique_id(*, controller_mac: str | None, controller_name: str | None) -> str:
    """Return a stable unique identifier for a controller."""
    mac = normalize_mac_address(controller_mac or "")
    if mac:
        return mac

    name = (controller_name or "").strip() or "controller"
    return slugify(name)


def get_controller_service_prefix(*, controller_mac: str | None, controller_name: str | None) -> str:
    """Return the service prefix used to build service names."""
    mac = normalize_mac_address(controller_mac or "")
    if mac:
        return mac.replace(":", "_")

    name = (controller_name or "").strip() or "controller"
    return slugify(name)