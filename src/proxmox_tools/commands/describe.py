import typer
from rich.table import Table

from proxmox_tools.api import ProxmoxAPI, ProxmoxAPIError
from proxmox_tools.commands.common import console, fail, fetch_vms, get_api, run_parallel
from proxmox_tools.utils import (
    format_bytes_as_mb,
    format_size_auto,
    format_timestamp,
    format_uptime,
    parse_disks,
    parse_net_interfaces,
    parse_smbios,
)

app = typer.Typer(help="Show detailed information about a single resource.")
STOPPED_NOTICE = "VM stopped, no live data"
AGENT_UNAVAILABLE_NOTICE = "Guest Agent unavailable"
SHARED_STORAGE_TYPES = {"pbs", "nfs", "cifs", "cephfs", "glusterfs"}


def _find_vm(api: ProxmoxAPI, vmid: int) -> dict:
    for vm in fetch_vms(api):
        if vm["vmid"] == vmid:
            return vm
    fail(f"VM {vmid} does not exist in the active cluster.")


def _print_kv(rows: list[tuple[str, str]]) -> None:
    table = Table(show_header=False, box=None, pad_edge=False)
    table.add_column(style="bold")
    table.add_column()
    for key, value in rows:
        table.add_row(key, value)
    console.print(table)


def _print_general(vm: dict, vm_config: dict, vm_status: dict) -> None:
    console.print("[bold]General[/bold]")
    smbios = parse_smbios(vm_config)
    running = vm_status.get("status") == "running"

    rows = [
        ("VMID", str(vm["vmid"])),
        ("Name", vm.get("name", "-")),
        ("Node", vm["node"]),
        ("Status", vm_status.get("status", "-")),
        ("Uptime", format_uptime(vm_status.get("uptime")) if running else "-"),
        ("Tags", vm_config.get("tags") or "-"),
        ("Pool", vm.get("pool") or "-"),
        ("Notes", vm_config.get("description") or "-"),
    ]

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

    _print_kv(rows)
    console.print()


def _print_resources(vm: dict, vm_status: dict, running: bool) -> None:
    console.print("[bold]Resources[/bold]")
    rows = [("RAM allocated", format_bytes_as_mb(vm.get("maxmem", 0)))]
    if running:
        rows.append(("RAM usage", format_bytes_as_mb(vm_status.get("mem", 0))))
    else:
        rows.append(("RAM usage", STOPPED_NOTICE))

    rows.append(("CPU allocated", f"{vm.get('maxcpu', '-')} vCPU"))
    if running:
        cpu_fraction = vm_status.get("cpu")
        cpu_pct = f"{round(cpu_fraction * 100)}%" if cpu_fraction is not None else "-"
        rows.append(("CPU usage", cpu_pct))
    else:
        rows.append(("CPU usage", STOPPED_NOTICE))

    _print_kv(rows)
    console.print()


def _print_network(api: ProxmoxAPI, node: str, vmid: int, vm_config: dict, running: bool) -> None:
    console.print("[bold]Network[/bold]")
    interfaces = parse_net_interfaces(vm_config)
    if not interfaces:
        console.print("No network interfaces configured.")
        console.print()
        return

    ip_by_mac: dict[str, str] = {}
    agent_error = None
    if running:
        try:
            for iface in api.get_vm_agent_network_interfaces(node, vmid):
                mac = iface.get("hardware-address", "").lower()
                ips = [
                    addr.get("ip-address")
                    for addr in iface.get("ip-addresses", [])
                    if addr.get("ip-address-type") == "ipv4"
                ]
                if mac and ips:
                    ip_by_mac[mac] = ", ".join(ips)
        except ProxmoxAPIError:
            agent_error = AGENT_UNAVAILABLE_NOTICE

    table = Table()
    table.add_column("Interface")
    table.add_column("Model")
    table.add_column("MAC")
    table.add_column("Bridge")
    table.add_column("VLAN")
    table.add_column("IP (live)")

    for iface in interfaces:
        if not running:
            ip_display = STOPPED_NOTICE
        elif agent_error:
            ip_display = agent_error
        else:
            ip_display = ip_by_mac.get(iface["mac"].lower(), "-")
        table.add_row(
            iface["key"],
            iface["model"],
            iface["mac"],
            iface.get("bridge", "-"),
            iface.get("tag", "-"),
            ip_display,
        )
    console.print(table)
    console.print()


def _print_disks(vm_config: dict) -> None:
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
    for disk in disks:
        table.add_row(disk["key"], disk["storage"], disk["size"])
    console.print(table)
    console.print()


def _print_snapshots(api: ProxmoxAPI, node: str, vmid: int) -> None:
    console.print("[bold]Snapshots[/bold]")
    try:
        snapshots = [s for s in api.get_vm_snapshots(node, vmid) if s.get("name") != "current"]
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
        _print_kv(
            [
                ("Last backup", format_timestamp(latest.get("ctime"))),
                ("Storage", latest["storage"]),
                ("Size", format_size_auto(latest.get("size"))),
            ]
        )
    else:
        console.print("No backups found for this VM.")
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
    _print_kv([("Hostname", hostname), ("OS", os_name)])
    console.print()


@app.command("vm")
def describe_vm(vmid: int = typer.Argument(..., help="VM ID.")):
    """Show VM details: general, resources, network, disks, snapshots, backup, guest OS."""
    api = get_api()
    vm = _find_vm(api, vmid)
    node = vm["node"]

    try:
        vm_config = api.get_vm_config(node, vmid)
        vm_status = api.get_vm_status(node, vmid)
    except ProxmoxAPIError as exc:
        fail(str(exc))

    running = vm_status.get("status") == "running"

    _print_general(vm, vm_config, vm_status)
    _print_resources(vm, vm_status, running)
    _print_network(api, node, vmid, vm_config, running)
    _print_disks(vm_config)
    _print_snapshots(api, node, vmid)
    _print_backup(api, node, vmid)
    _print_guest_os(api, node, vmid, running)
