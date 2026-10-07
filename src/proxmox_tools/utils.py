import base64
import re
from datetime import datetime

_DISK_BUSES = ("scsi", "virtio", "sata", "ide", "efidisk", "tpmstate")
_DISK_KEY = re.compile(rf"^({'|'.join(_DISK_BUSES)})(\d+)$")


def format_bytes_as_mb(num_bytes: int) -> str:
    return f"{num_bytes // (1024 * 1024)} MB"


def _net_keys(vm_config: dict) -> list[str]:
    keys = [k for k in vm_config if k.startswith("net") and k[3:].isdigit()]
    return sorted(keys, key=lambda k: int(k[3:]))


def parse_net_interfaces(vm_config: dict) -> list[dict]:
    interfaces = []
    for key in _net_keys(vm_config):
        model = mac = "-"
        attrs = {}
        for index, part in enumerate(vm_config[key].split(",")):
            attr_key, sep, attr_value = part.partition("=")
            if not sep:
                continue
            if index == 0 and attr_key not in ("model", "macaddr"):
                model, mac = attr_key, attr_value
            else:
                attrs[attr_key] = attr_value
        model = attrs.pop("model", model)
        mac = attrs.pop("macaddr", mac)
        interfaces.append({**attrs, "key": key, "model": model, "mac": mac})
    return interfaces


def extract_bridges(vm_config: dict) -> str:
    bridges = [iface["bridge"] for iface in parse_net_interfaces(vm_config) if iface.get("bridge")]
    return ", ".join(bridges) if bridges else "-"


def parse_disks(vm_config: dict) -> list[dict]:
    disks = []
    for key, value in vm_config.items():
        match = _DISK_KEY.match(key)
        if not match or "media=cdrom" in value:
            continue
        parts = value.split(",")
        storage = parts[0].split(":", 1)[0] if ":" in parts[0] else "-"
        size = "-"
        for part in parts[1:]:
            if part.startswith("size="):
                size = part.split("=", 1)[1]
        disks.append({"key": key, "storage": storage, "size": size, "_order": (_DISK_BUSES.index(match[1]), int(match[2]))})
    disks.sort(key=lambda d: d.pop("_order"))
    return disks


def parse_smbios(vm_config: dict) -> dict:
    raw = vm_config.get("smbios1")
    if not raw:
        return {}

    parts = dict(part.split("=", 1) for part in raw.split(",") if "=" in part)
    is_base64 = parts.get("base64") == "1"

    result = {}
    if "uuid" in parts:
        result["uuid"] = parts["uuid"]

    for field in ("serial", "manufacturer", "product", "version", "sku", "family"):
        if field not in parts:
            continue
        value = parts[field]
        if is_base64:
            padding = "=" * (-len(value) % 4)
            try:
                value = base64.b64decode(value + padding).decode("utf-8")
            except (ValueError, UnicodeDecodeError):
                pass
        result[field] = value
    return result


def format_firewall_status(enabled) -> str:
    if enabled:
        return "[green]🟢 enabled[/green]"
    return "[red]🔴 disabled[/red]"


def format_size_auto(num_bytes) -> str:
    if not num_bytes:
        return "-"
    gb = num_bytes / (1024 ** 3)
    if gb >= 1:
        return f"{gb:.1f} GB"
    return f"{num_bytes / (1024 ** 2):.0f} MB"


def format_timestamp(unix_time) -> str:
    if not unix_time:
        return "-"
    return datetime.fromtimestamp(int(unix_time)).strftime("%Y-%m-%d %H:%M")


def format_uptime(seconds) -> str:
    if not seconds:
        return "-"
    seconds = int(seconds)
    days, rem = divmod(seconds, 86400)
    hours, rem = divmod(rem, 3600)
    minutes, _ = divmod(rem, 60)
    if days:
        return f"{days}d {hours}h"
    if hours:
        return f"{hours}h {minutes}m"
    if minutes:
        return f"{minutes}m"
    return f"{seconds}s"


def format_cpu_usage(cpu_fraction, maxcpu) -> str:
    if cpu_fraction is None or not maxcpu:
        return "-"
    pct = round(cpu_fraction * 100)
    return f"{pct}% ({maxcpu} {'core' if maxcpu == 1 else 'cores'})"


def format_ram_usage(mem_bytes, maxmem_bytes) -> str:
    if mem_bytes is None or not maxmem_bytes:
        return "-"
    used_gb = mem_bytes / (1024 ** 3)
    total_gb = maxmem_bytes / (1024 ** 3)
    pct = round(mem_bytes / maxmem_bytes * 100)
    return f"{used_gb:.1f} GB / {total_gb:.1f} GB ({pct}%)"
