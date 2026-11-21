import typer
from rich.table import Table

from proxmox_tool.api import ProxmoxAPI, ProxmoxAPIError
from proxmox_tool.commands import cluster, node
from proxmox_tool.commands.common import console, fail, fetch_guests, get_api, print_kv, run_parallel
from proxmox_tool.utils import (
    format_bytes_as_mb,
    format_size_auto,
    format_timestamp,
    format_uptime,
    ipv4_by_mac,
    is_lxc,
    parse_disks,
    parse_net_interfaces,
    parse_smbios,
    plural,
    static_ip,
)

app = typer.Typer(help="Show detailed information about a guest, node or the cluster.")
STOPPED_NOTICE = "Stopped, no live data"
AGENT_UNAVAILABLE_NOTICE = "Guest Agent unavailable"
SHARED_STORAGE_TYPES = {"pbs", "nfs", "cifs", "cephfs", "glusterfs"}


def _find_guest(api: ProxmoxAPI, vmid: int) -> dict:
    for guest in fetch_guests(api):
        if guest["vmid"] == vmid:
            return guest
    fail(f"VM or LXC container {vmid} does not exist in the active cluster.")


def _type_display(guest: dict, guest_config: dict) -> str:
    if not is_lxc(guest):
        return "VM"
    privilege = "unprivileged" if str(guest_config.get("unprivileged", "0")) == "1" else "privileged"
    return f"LXC ({privilege})"


def _print_general(vm: dict, vm_config: dict, vm_status: dict) -> None:
    console.print("[bold]General[/bold]")
    smbios = parse_smbios(vm_config)
    running = vm_status.get("status") == "running"

    rows = [
        ("VMID", str(vm["vmid"])),
        ("Type", _type_display(vm, vm_config)),
        ("Name", vm.get("name", "-")),
        ("Node", vm["node"]),
        ("Status", vm_status.get("status", "-")),
        ("Uptime", format_uptime(vm_status.get("uptime")) if running else "-"),
        ("Tags", vm_config.get("tags") or "-"),
        ("Pool", vm.get("pool") or "-"),
        ("Notes", vm_config.get("description") or "-"),
    ]
    if is_lxc(vm):
        rows.append(("Hostname", vm_config.get("hostname") or "-"))
        rows.append(("OS", vm_config.get("ostype") or "-"))

    field_labels = (
        ("uuid", "UUID"),
        ("serial", "Serial"),
        ("manufacturer", "Manufacturer"),
        ("product", "Product"),
        ("version", "Version"),
        ("sku", "SKU"),
        ("family", "Family"),
    )
    for field, label in field_labels:
        if smbios.get(field):
            rows.append((label, smbios[field]))

    print_kv(rows)
    console.print()


def _print_resources(vm: dict, vm_status: dict, running: bool) -> None:
    lxc = is_lxc(vm)
    console.print("[bold]Resources[/bold]")
    rows = [("RAM allocated", format_bytes_as_mb(vm.get("maxmem", 0)))]
    if running:
        rows.append(("RAM usage", format_bytes_as_mb(vm_status.get("mem", 0))))
    else:
        rows.append(("RAM usage", STOPPED_NOTICE))

    if lxc:
        rows.append(("Swap allocated", format_bytes_as_mb(vm_status.get("maxswap", 0))))
        rows.append(("Swap usage", format_bytes_as_mb(vm_status.get("swap", 0)) if running else STOPPED_NOTICE))

    maxcpu = vm.get("maxcpu")
    rows.append(("CPU allocated", plural(maxcpu, "core") if lxc and maxcpu else f"{maxcpu or '-'} vCPU"))
    if running:
        cpu_fraction = vm_status.get("cpu")
        cpu_pct = f"{round(cpu_fraction * 100)}%" if cpu_fraction is not None else "-"
        rows.append(("CPU usage", cpu_pct))
    else:
        rows.append(("CPU usage", STOPPED_NOTICE))

    print_kv(rows)
    console.print()


def _live_ips(api: ProxmoxAPI, guest: dict) -> dict[str, str]:
    if is_lxc(guest):
        return ipv4_by_mac(api.get_lxc_interfaces(guest["node"], guest["vmid"]))
    return ipv4_by_mac(api.get_vm_agent_network_interfaces(guest["node"], guest["vmid"]))


def _print_network(api: ProxmoxAPI, guest: dict, guest_config: dict, running: bool) -> None:
    console.print("[bold]Network[/bold]")
    interfaces = parse_net_interfaces(guest_config)
    if not interfaces:
        console.print("No network interfaces configured.")
        console.print()
        return

    lxc = is_lxc(guest)
    ip_by_mac: dict[str, str] = {}
    live_error = None
    if running:
        try:
            ip_by_mac = _live_ips(api, guest)
        except ProxmoxAPIError:
            live_error = "Live data unavailable" if lxc else AGENT_UNAVAILABLE_NOTICE

    table = Table()
    table.add_column("Interface")
    table.add_column("Model")
    table.add_column("MAC")
    table.add_column("Bridge")
    table.add_column("VLAN")
    table.add_column("IP")

    for iface in interfaces:
        live_ip = ip_by_mac.get(iface["mac"].lower()) if running and not live_error else None
        config_ip = static_ip(iface) if lxc else None
        if live_ip:
            ip_display = live_ip
        elif config_ip:
            ip_display = f"{config_ip} (config)"
        elif not running:
            ip_display = STOPPED_NOTICE
        else:
            ip_display = live_error or "-"
        label = f"{iface['key']} ({iface['name']})" if lxc and iface.get("name") else iface["key"]
        table.add_row(
            label,
            iface["model"],
            iface["mac"],
            iface.get("bridge", "-"),
            iface.get("tag", "-"),
            ip_display,
        )
    console.print(table)
    console.print()


def _print_disks(guest: dict, vm_config: dict) -> None:
    console.print("[bold]Disks[/bold]")
    disks = parse_disks(vm_config)
    if not disks:
        console.print("No disks configured.")
        console.print()
        return

    table = Table()
    table.add_column("Disk")
    table.add_column("Storage")
    table.add_column("Size")
    if is_lxc(guest):
        table.add_column("Mount point")
    for disk in disks:
        row = [disk["key"], disk["storage"], disk["size"]]
        if is_lxc(guest):
            row.append(disk["mount"])
        table.add_row(*row)
    console.print(table)
    console.print()


def _print_snapshots(api: ProxmoxAPI, guest: dict) -> None:
    console.print("[bold]Snapshots[/bold]")
    try:
        snapshots = [
            s
            for s in api.get_guest_snapshots(guest["type"], guest["node"], guest["vmid"])
            if s.get("name") != "current"
        ]
    except ProxmoxAPIError:
        console.print("Failed to fetch snapshots.")
        console.print()
        return

    if not snapshots:
        console.print("No snapshots.")
        console.print()
        return

    table = Table()
    table.add_column("Name")
    table.add_column("Description")
    table.add_column("Date")
    for snap in sorted(snapshots, key=lambda s: s.get("snaptime", 0)):
        table.add_row(
            snap.get("name", "-"),
            snap.get("description") or "-",
            format_timestamp(snap.get("snaptime")),
        )
    console.print(table)
    console.print()


def _backup_targets(api: ProxmoxAPI, vm_node: str, storages: list[dict]) -> list[tuple[str, str, bool]]:
    try:
        online = [n["node"] for n in api.get_nodes() if n.get("status") == "online"]
    except ProxmoxAPIError:
        online = [vm_node]

    targets = []
    for storage in storages:
        if storage.get("disable") or "backup" not in (storage.get("content") or "").split(","):
            continue
        allowed = storage["nodes"].split(",") if storage.get("nodes") else online
        nodes = [n for n in online if n in allowed]
        shared = bool(storage.get("shared")) or storage.get("type") in SHARED_STORAGE_TYPES
        if shared:
            nodes = [vm_node] if vm_node in nodes else nodes[:1]
        targets.extend((storage["storage"], node, shared) for node in nodes)
    return targets


def _print_backup(api: ProxmoxAPI, node: str, vmid: int) -> None:
    console.print("[bold]Backup[/bold]")
    try:
        storages = api.get_storages()
    except ProxmoxAPIError:
        console.print("Failed to fetch storage list.")
        console.print()
        return

    results = run_parallel(
        lambda t: api.get_storage_backup_content(t[1], t[0]),
        _backup_targets(api, node, storages),
    )

    latest = None
    failed = []
    for (storage, storage_node, shared), entries, error in results:
        label = storage if shared else f"{storage} ({storage_node})"
        if error:
            failed.append(label)
            continue
        for entry in entries:
            entry_vmid = entry.get("vmid")
            if entry_vmid is None or int(entry_vmid) != vmid:
                continue
            if latest is None or entry.get("ctime", 0) > latest.get("ctime", 0):
                latest = {**entry, "storage": label}

    if latest:
        print_kv(
            [
                ("Last backup", format_timestamp(latest.get("ctime"))),
                ("Storage", latest["storage"]),
                ("Size", format_size_auto(latest.get("size"))),
            ]
        )
    else:
        console.print("No backups found.")
    if failed:
        console.print(f"[yellow]Failed to read storages: {', '.join(sorted(failed))}[/yellow]")
    console.print()


def _print_guest_os(api: ProxmoxAPI, node: str, vmid: int, running: bool) -> None:
    console.print("[bold]Guest OS[/bold]")
    if not running:
        console.print(STOPPED_NOTICE)
        console.print()
        return

    try:
        hostname = api.get_vm_agent_hostname(node, vmid).get("host-name", "-")
        osinfo = api.get_vm_agent_osinfo(node, vmid)
    except ProxmoxAPIError:
        console.print(AGENT_UNAVAILABLE_NOTICE)
        console.print()
        return

    os_name = osinfo.get("pretty-name") or osinfo.get("name", "-")
    print_kv([("Hostname", hostname), ("OS", os_name)])
    console.print()


@app.command("vm")
def describe_vm(vmid: int = typer.Argument(..., help="VM or LXC container ID.")):
    """Show VM or LXC details: general, resources, network, disks, snapshots, backup, guest OS (VM)."""
    api = get_api()
    guest = _find_guest(api, vmid)
    guest_type, node = guest["type"], guest["node"]

    try:
        guest_config = api.get_guest_config(guest_type, node, vmid)
        guest_status = api.get_guest_status(guest_type, node, vmid)
    except ProxmoxAPIError as exc:
        fail(str(exc))

    running = guest_status.get("status") == "running"

    _print_general(guest, guest_config, guest_status)
    _print_resources(guest, guest_status, running)
    _print_network(api, guest, guest_config, running)
    _print_disks(guest, guest_config)
    _print_snapshots(api, guest)
    _print_backup(api, node, vmid)
    if not is_lxc(guest):
        _print_guest_os(api, node, vmid, running)


app.command("node")(node.describe_node)
app.command("cluster")(cluster.describe_cluster)
