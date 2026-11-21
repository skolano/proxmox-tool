from typing import Optional

import questionary
import typer
from rich.table import Table

from proxmox_tool.api import ProxmoxAPI, ProxmoxAPIError
from proxmox_tool.commands.common import console, fail, get_api, print_kv, section
from proxmox_tool.utils import (
    format_bytes_as_mb,
    format_overcommit,
    format_size_auto,
    format_uptime,
    format_usage,
    guest_type_label,
    plural,
    usage_color,
)

GB = 1024 ** 3
CPU_OVERCOMMIT = (2.0, 4.0)
RAM_OVERCOMMIT = (0.9, 1.0)
SUBSCRIPTION_LABELS = {"notfound": "none"}


def pve_version(status: dict) -> str:
    parts = (status.get("pveversion") or "").split("/")
    return parts[1] if len(parts) > 1 else "-"


def _kernel(status: dict) -> str:
    release = (status.get("current-kernel") or {}).get("release")
    if release:
        return release
    parts = (status.get("kversion") or "").split()
    return parts[1] if len(parts) > 1 else "-"


def _boot_mode(status: dict) -> str:
    info = status.get("boot-info") or {}
    mode = info.get("mode")
    if mode == "efi":
        return "UEFI (Secure Boot)" if info.get("secureboot") else "UEFI"
    if mode == "legacy-bios":
        return "Legacy BIOS"
    return mode or "-"


def _subscription(api: ProxmoxAPI, node: str) -> str:
    try:
        subscription = api.get_node_subscription(node)
    except ProxmoxAPIError:
        return "-"
    status = subscription.get("status") or "-"
    level = subscription.get("level")
    label = SUBSCRIPTION_LABELS.get(status, status)
    return f"{label} ({level})" if level and status == "active" else label


def _print_general(api: ProxmoxAPI, node: dict, status: dict) -> None:
    section("General")
    online = node.get("status") == "online"
    rows = [
        ("Node", node["node"]),
        ("Status", "[green]online[/green]" if online else f"[red]{node.get('status', 'unknown')}[/red]"),
        ("Uptime", format_uptime(node.get("uptime"))),
    ]
    if status:
        rows += [
            ("PVE version", pve_version(status)),
            ("Kernel", _kernel(status)),
            ("Boot mode", _boot_mode(status)),
            ("Subscription", _subscription(api, node["node"])),
        ]
    print_kv(rows)
    console.print()


def _print_cpu(status: dict) -> None:
    section("CPU")
    info = status.get("cpuinfo") or {}
    sockets = info.get("sockets") or 0
    cores = (info.get("cores") or 0) * sockets
    wait = (status.get("wait") or 0) * 100
    wait_color = usage_color(wait, warn=5, crit=10)
    print_kv(
        [
            ("Model", info.get("model") or "-"),
            ("Topology", f"{plural(sockets, 'socket')}, {plural(cores, 'core')}, {plural(info.get('cpus') or 0, 'thread')}"),
            ("Usage", f"{round((status.get('cpu') or 0) * 100)}%"),
            ("Load average", " / ".join(status.get("loadavg") or []) or "-"),
            ("IO delay", f"[{wait_color}]{wait:.1f}%[/{wait_color}]"),
        ]
    )
    console.print()


def _print_memory(status: dict) -> None:
    section("Memory")
    memory = status.get("memory") or {}
    swap = status.get("swap") or {}
    rootfs = status.get("rootfs") or {}
    print_kv(
        [
            ("RAM", format_usage(memory.get("used"), memory.get("total"))),
            ("Swap", format_usage(swap.get("used"), swap.get("total"))),
            ("KSM shared", format_size_auto((status.get("ksm") or {}).get("shared"))),
            ("Root FS", format_usage(rootfs.get("used"), rootfs.get("total"))),
        ]
    )
    console.print()


def running_guests(guests: list[dict]) -> list[dict]:
    return [g for g in guests if g.get("status") == "running" and not g.get("template")]


def _print_allocation(status: dict, guests: list[dict]) -> None:
    section("Allocation")
    running = running_guests(guests)
    vcpus = sum(g.get("maxcpu") or 0 for g in running)
    ram = sum(g.get("maxmem") or 0 for g in running)
    threads = (status.get("cpuinfo") or {}).get("cpus") or 0
    total_ram = (status.get("memory") or {}).get("total") or 0
    print_kv(
        [
            ("Running guests", str(len(running))),
            ("vCPU", format_overcommit(vcpus, threads, "threads", *CPU_OVERCOMMIT)),
            ("RAM", format_overcommit(round(ram / GB, 1), round(total_ram / GB, 1), "GB", *RAM_OVERCOMMIT)),
        ]
    )
    console.print()


def _print_storage(api: ProxmoxAPI, node: str) -> None:
    section("Storage")
    try:
        storages = api.get_node_storage(node)
    except ProxmoxAPIError:
        console.print("Failed to fetch storage list.")
        console.print()
        return

    table = Table()
    table.add_column("Storage")
    table.add_column("Type")
    table.add_column("Shared")
    table.add_column("Usage")
    table.add_column("Active")
    for storage in sorted(storages, key=lambda s: s["storage"]):
        active = bool(storage.get("active"))
        table.add_row(
            storage["storage"],
            storage.get("type", "-"),
            "yes" if storage.get("shared") else "no",
            format_usage(storage.get("used"), storage.get("total")) if active else "-",
            "yes" if active else "[red]no[/red]",
        )
    console.print(table)
    console.print()


def _print_guests(guests: list[dict]) -> None:
    section("Guests")
    active = [g for g in guests if not g.get("template")]
    if not active:
        console.print("No guests on this node.")
        console.print()
        return

    table = Table()
    table.add_column("VMID")
    table.add_column("Type")
    table.add_column("Name")
    table.add_column("Status")
    table.add_column("CPU")
    table.add_column("RAM")
    for guest in sorted(active, key=lambda g: (g.get("status") != "running", g["vmid"])):
        table.add_row(
            str(guest["vmid"]),
            guest_type_label(guest),
            guest.get("name", "-"),
            guest.get("status", "-"),
            str(guest.get("maxcpu", "-")),
            format_bytes_as_mb(guest.get("maxmem", 0)),
        )
    console.print(table)

    running = len(running_guests(active))
    templates = len(guests) - len(active)
    summary = f"Running: {running}, not running: {len(active) - running}"
    if templates:
        summary += f", templates: {templates}"
    console.print(summary)
    console.print()


def describe_node(name: Optional[str] = typer.Argument(None, help="Node name; without it, a selection menu opens.")):
    """Show node details: general, CPU, memory, allocation, storage, guests."""
    api = get_api()
    try:
        nodes = api.get_nodes()
        guests = api.get_guests()
    except ProxmoxAPIError as exc:
        fail(str(exc))

    names = sorted(n["node"] for n in nodes)
    if name is None:
        name = questionary.select("Select node:", choices=names).ask()
        if name is None:
            raise typer.Exit(1)

    node = next((n for n in nodes if n["node"] == name), None)
    if node is None:
        fail(f"Node {name} does not exist in the active cluster. Available: {', '.join(names)}")

    status = {}
    if node.get("status") == "online":
        try:
            status = api.get_node_status(name)
        except ProxmoxAPIError as exc:
            console.print(f"[yellow]Failed to fetch node status: {exc}[/yellow]")

    node_guests = [g for g in guests if g["node"] == name]

    _print_general(api, node, status)
    if status:
        _print_cpu(status)
        _print_memory(status)
        _print_allocation(status, node_guests)
        _print_storage(api, name)
    else:
        console.print("[yellow]Node offline or unreachable, live sections skipped.[/yellow]")
        console.print()
    _print_guests(node_guests)
