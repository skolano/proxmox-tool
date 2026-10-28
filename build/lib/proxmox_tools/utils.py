import base64
import re
from datetime import datetime

_DISK_BUSES = ("rootfs", "mp", "scsi", "virtio", "sata", "ide", "efidisk", "tpmstate")
_DISK_KEY = re.compile(rf"^({'|'.join(_DISK_BUSES)})(\d*)$")
_MAC = re.compile(r"^[0-9A-Fa-f]{2}(:[0-9A-Fa-f]{2}){5}$")


def is_lxc(guest: dict) -> bool:
    return guest.get("type") == "lxc"


def guest_type_label(guest: dict) -> str:
    return "LXC" if is_lxc(guest) else "VM"


def format_bytes_as_mb(num_bytes: int) -> str:
    return f"{num_bytes // (1024 * 1024)} MB"


def _net_keys(vm_config: dict) -> list[str]:
    keys = [k for k in vm_config if k.startswith("net") and k[3:].isdigit()]
    return sorted(keys, key=lambda k: int(k[3:]))


def parse_net_interfaces(guest_config: dict) -> list[dict]:
    interfaces = []
    for key in _net_keys(guest_config):
        model = mac = "-"
        attrs = {}
        for index, part in enumerate(guest_config[key].split(",")):
            attr_key, sep, attr_value = part.partition("=")
            if not sep:
                continue
            if index == 0 and _MAC.match(attr_value):
                model, mac = attr_key, attr_value
            else:
                attrs[attr_key] = attr_value
        model = attrs.pop("model", attrs.pop("type", model))
        mac = attrs.pop("macaddr", attrs.pop("hwaddr", mac))
        interfaces.append({**attrs, "key": key, "model": model, "mac": mac})
    return interfaces


def static_ip(iface: dict) -> str | None:
    ip = iface.get("ip")
    if not ip or ip in ("dhcp", "manual"):
        return None
    return ip.split("/")[0]


def interface_ips(iface: dict, ipv4_only: bool = False) -> list[str]:
    ips = [
        addr.get("ip-address")
        for addr in iface.get("ip-addresses", [])
        if addr.get("ip-address") and (not ipv4_only or addr.get("ip-address-type") == "ipv4")
    ]
    for field in ("inet",) if ipv4_only else ("inet", "inet6"):
        for value in str(iface.get(field) or "").split(","):
            ip = value.strip().split("/")[0]
            if ip and ip not in ips:
                ips.append(ip)
    return ips


def ipv4_by_mac(interfaces: list[dict]) -> dict[str, str]:
    result = {}
    for iface in interfaces:
        mac = (iface.get("hardware-address") or iface.get("hwaddr") or "").lower()
        ips = interface_ips(iface, ipv4_only=True)
        if mac and ips:
            result[mac] = ", ".join(ips)
    return result


def extract_bridges(vm_config: dict) -> str:
    bridges = [iface["bridge"] for iface in parse_net_interfaces(vm_config) if iface.get("bridge")]
    return ", ".join(bridges) if bridges else "-"


def parse_disks(guest_config: dict) -> list[dict]:
    disks = []
    for key, value in guest_config.items():
        match = _DISK_KEY.match(key)
        if not match or (match[1] != "rootfs" and not match[2]) or "media=cdrom" in value:
            continue
        parts = value.split(",")
        if parts[0].startswith("/"):
            storage = "bind"
        else:
            storage = parts[0].split(":", 1)[0] if ":" in parts[0] else "-"
        attrs = dict(part.split("=", 1) for part in parts[1:] if "=" in part)
        disks.append(
            {
                "key": key,
                "storage": storage,
                "size": attrs.get("size", "-"),
                "mount": "/" if key == "rootfs" else attrs.get("mp", "-"),
                "_order": (_DISK_BUSES.index(match[1]), int(match[2] or 0)),
            }
        )
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
    if gb >= 1024:
        return f"{gb / 1024:.1f} TB"
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


def plural(count: int, word: str) -> str:
    return f"{count} {word}" if count == 1 else f"{count} {word}s"


def format_cpu_usage(cpu_fraction, maxcpu) -> str:
    if cpu_fraction is None or not maxcpu:
        return "-"
    pct = round(cpu_fraction * 100)
    return f"{pct}% ({plural(maxcpu, 'core')})"


def usage_color(pct: float, warn: int = 80, crit: int = 90) -> str:
    if pct >= crit:
        return "red"
    if pct >= warn:
        return "yellow"
    return "green"


def format_usage(used, total) -> str:
    if used is None or not total:
        return "-"
    pct = used / total * 100
    color = usage_color(pct)
    return f"{format_size_auto(used)} / {format_size_auto(total)} ([{color}]{pct:.0f}%[/{color}])"


def format_overcommit(allocated: float, physical: float, unit: str, warn: float, crit: float) -> str:
    if not physical:
        return "-"
    ratio = allocated / physical
    color = usage_color(ratio, warn=warn, crit=crit)
    return f"{allocated:g} / {physical:g} {unit} ([{color}]{ratio:.2f}x[/{color}])"


def format_ram_usage(mem_bytes, maxmem_bytes) -> str:
    if mem_bytes is None or not maxmem_bytes:
        return "-"
    used_gb = mem_bytes / (1024 ** 3)
    total_gb = maxmem_bytes / (1024 ** 3)
    pct = round(mem_bytes / maxmem_bytes * 100)
    return f"{used_gb:.1f} GB / {total_gb:.1f} GB ({pct}%)"
