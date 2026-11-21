import typer
from rich.table import Table

from proxmox_tool.api import ProxmoxAPI, ProxmoxAPIError
from proxmox_tool.commands.common import console, fail, fetch_guests, get_api, run_parallel
from proxmox_tool.utils import (
    extract_bridges,
    format_bytes_as_mb,
    format_cpu_usage,
    format_firewall_status,
    format_ram_usage,
    format_uptime,
    guest_type_label,
    interface_ips,
    is_lxc,
    parse_net_interfaces,
    plural,
    parse_smbios,
    static_ip,
)

app = typer.Typer(help="Read the state of the active Proxmox VE cluster.")

FETCH_ERROR = "[yellow]error[/yellow]"
TYPE_FILTERS = {"vm": "qemu", "lxc": "lxc"}


def _print_failed(label: str, vmids) -> None:
    if vmids:
        ids = ", ".join(str(vmid) for vmid in sorted(vmids))
        console.print(f"[yellow]Failed to fetch {label} for VMs: {ids}[/yellow]")


@app.command("nodes")
def get_nodes():
    """List cluster nodes with live CPU/RAM usage and running guests."""
    api = get_api()

    try:
        nodes = api.get_nodes()
        guests = api.get_guests()
    except ProxmoxAPIError as exc:
        fail(str(exc))

    running: dict[tuple[str, str], int] = {}
    for guest in guests:
        if guest.get("status") == "running":
            key = (guest["node"], guest["type"])
            running[key] = running.get(key, 0) + 1

    table = Table()
    table.add_column("Node")
    table.add_column("Status")
    table.add_column("CPU")
    table.add_column("RAM")
    table.add_column("Uptime")
    table.add_column("Running VMs")
    table.add_column("Running LXC")

    for node in sorted(nodes, key=lambda n: n["node"]):
        name = node["node"]
        table.add_row(
            name,
            node.get("status", "-"),
            format_cpu_usage(node.get("cpu"), node.get("maxcpu")),
            format_ram_usage(node.get("mem"), node.get("maxmem")),
            format_uptime(node.get("uptime")),
            str(running.get((name, "qemu"), 0)),
            str(running.get((name, "lxc"), 0)),
        )

    console.print(table)


def _fetch_configs(api: ProxmoxAPI, guests: list[dict]) -> tuple[dict[int, dict], set[int]]:
    configs: dict[int, dict] = {}
    failed: set[int] = set()
    for guest, guest_config, error in run_parallel(
        lambda g: api.get_guest_config(g["type"], g["node"], g["vmid"]), guests
    ):
        if error:
            failed.add(guest["vmid"])
        else:
            configs[guest["vmid"]] = guest_config
    return configs, failed


@app.command("vms")
def get_vms(
    type_filter: str = typer.Option(None, "--type", help="Only guests of the given type: vm or lxc."),
    without_pool: bool = typer.Option(
        False, "--without-pool", help="Only guests not assigned to any pool."
    ),
    pool: str = typer.Option(None, "--pool", help="Only guests in the given pool."),
    node_filter: str = typer.Option(None, "--node", help="Only guests on the given node."),
    status: str = typer.Option(None, "--status", help="Only guests in the given state (e.g. running, stopped)."),
    tag: str = typer.Option(None, "--tag", help="Only guests with the given tag."),
    serial: bool = typer.Option(False, "--serial", help="Add the SMBIOS serial column (VMs only)."),
):
    """List all VMs and LXC containers in the active cluster."""
    if without_pool and pool:
        fail("--without-pool cannot be combined with --pool.")
    if type_filter and type_filter not in TYPE_FILTERS:
        fail("--type must be 'vm' or 'lxc'.")

    api = get_api()
    guests = fetch_guests(api)

    if type_filter:
        guests = [g for g in guests if g["type"] == TYPE_FILTERS[type_filter]]
    if without_pool:
        guests = [g for g in guests if not g.get("pool")]
    if pool:
        guests = [g for g in guests if g.get("pool") == pool]
    if node_filter:
        guests = [g for g in guests if g.get("node") == node_filter]
    if status:
        guests = [g for g in guests if g.get("status") == status]
    if tag:
        guests = [g for g in guests if tag in (g.get("tags") or "").split(";")]

    configs, failed = _fetch_configs(api, guests)

    table = Table()
    table.add_column("VMID")
    table.add_column("Type")
    table.add_column("Name")
    table.add_column("Node")
    table.add_column("Status")
    table.add_column("RAM")
    table.add_column("CPU")
    table.add_column("Bridge")
    table.add_column("Pool")
    if serial:
        table.add_column("Serial")

    for guest in sorted(guests, key=lambda g: g["vmid"]):
        vmid = guest["vmid"]
        guest_config = configs.get(vmid, {})
        row = [
            str(vmid),
            guest_type_label(guest),
            guest.get("name", "-"),
            guest["node"],
            guest.get("status", "-"),
            format_bytes_as_mb(guest.get("maxmem", 0)),
            str(guest.get("maxcpu", "-")),
            FETCH_ERROR if vmid in failed else extract_bridges(guest_config),
            guest.get("pool") or "-",
        ]
        if serial:
            if is_lxc(guest):
                row.append("-")
            elif vmid in failed:
                row.append(FETCH_ERROR)
            else:
                row.append(parse_smbios(guest_config).get("serial") or "-")
        table.add_row(*row)

    console.print(table)

    active = [g for g in guests if not g.get("template")]
    template_count = len(guests) - len(active)
    vms = [g for g in active if not is_lxc(g)]
    running_count = sum(1 for g in active if g.get("status") == "running")
    stopped_count = sum(1 for g in active if g.get("status") == "stopped")

    counts = []
    if type_filter != "lxc":
        counts.append(plural(len(vms), "VM"))
    if type_filter != "vm":
        counts.append(f"{len(active) - len(vms)} LXC")
    summary = f"Total: {', '.join(counts)} ({running_count} running, {stopped_count} stopped)"
    if template_count:
        summary += f", excluding {plural(template_count, 'template')}"
    if serial and type_filter != "lxc":
        missing_serial = sum(
            1
            for vm in vms
            if vm["vmid"] not in failed and not parse_smbios(configs.get(vm["vmid"], {})).get("serial")
        )
        summary += f" | VMs without serial: {missing_serial}"
    console.print(summary)
    _print_failed("config", failed)


def _fetch_firewall(api: ProxmoxAPI, guest: dict) -> tuple[str, str]:
    guest_type, node, vmid = guest["type"], guest["node"], guest["vmid"]
    try:
        options = api.get_guest_firewall_options(guest_type, node, vmid)
        guest_firewall = format_firewall_status(options.get("enable"))
    except ProxmoxAPIError:
        guest_firewall = FETCH_ERROR

    try:
        interfaces = parse_net_interfaces(api.get_guest_config(guest_type, node, vmid))
        fw_interfaces = [iface["key"] for iface in interfaces if iface.get("firewall") == "1"]
        fw_interfaces_display = ", ".join(fw_interfaces) if fw_interfaces else "-"
    except ProxmoxAPIError:
        fw_interfaces_display = FETCH_ERROR

    return guest_firewall, fw_interfaces_display


@app.command("firewall")
def get_firewall():
    """Show the guest-level and per-interface firewall switches for all VMs and LXC containers."""
    api = get_api()
    guests = fetch_guests(api)

    results = {
        guest["vmid"]: result for guest, result, _ in run_parallel(lambda g: _fetch_firewall(api, g), guests)
    }

    table = Table()
    table.add_column("VMID")
    table.add_column("Type")
    table.add_column("Name")
    table.add_column("Node")
    table.add_column("Firewall (VM)")
    table.add_column("Firewalled interfaces")

    for guest in sorted(guests, key=lambda g: g["vmid"]):
        guest_firewall, fw_interfaces_display = results[guest["vmid"]]
        table.add_row(
            str(guest["vmid"]),
            guest_type_label(guest),
            guest.get("name", "-"),
            guest["node"],
            guest_firewall,
            fw_interfaces_display,
        )

    console.print(table)


def _print_matches(matches: list[dict], not_found: str) -> None:
    if not matches:
        console.print(not_found)
        raise typer.Exit(1)
    for guest in sorted(matches, key=lambda g: g["vmid"]):
        console.print(f"VMID: {guest['vmid']}  Name: {guest.get('name', '-')}  Type: {guest_type_label(guest)}")


def _guest_ips(api: ProxmoxAPI, guest: dict) -> list[str]:
    node, vmid = guest["node"], guest["vmid"]
    running = guest.get("status") == "running"

    if not is_lxc(guest):
        interfaces = api.get_vm_agent_network_interfaces(node, vmid)
        return [ip for iface in interfaces for ip in interface_ips(iface)]

    ips = []
    if running:
        try:
            ips = [ip for iface in api.get_lxc_interfaces(node, vmid) for ip in interface_ips(iface)]
        except ProxmoxAPIError:
            pass
    if not ips:
        config = api.get_guest_config("lxc", node, vmid)
        ips = [ip for ip in map(static_ip, parse_net_interfaces(config)) if ip]
    return ips


def _search_by_ip(api: ProxmoxAPI, guests: list[dict], ip: str) -> None:
    candidates = [g for g in guests if is_lxc(g) or g.get("status") == "running"]
    results = run_parallel(lambda g: _guest_ips(api, g), candidates)
    matches = [guest for guest, ips, error in results if not error and ip in ips]
    _print_matches(matches, f"No VM or LXC container found with IP address {ip}.")


def _search_by_serial(api: ProxmoxAPI, guests: list[dict], serial: str) -> None:
    vms = [g for g in guests if not is_lxc(g)]
    configs, failed = _fetch_configs(api, vms)
    matches = [vm for vm in vms if parse_smbios(configs.get(vm["vmid"], {})).get("serial") == serial]
    if not matches:
        _print_failed("config", failed)
    _print_matches(matches, f"No VM found with serial {serial}.")


def _search_by_name(guests: list[dict], name: str) -> None:
    matches = [g for g in guests if g.get("name") == name]
    _print_matches(matches, f"No VM or LXC container found with name {name}.")


@app.command("vm")
def get_vm(
    ip: str = typer.Option(
        None, "--ip", help="Find by IP address (VM: Guest Agent, running only; LXC: live or static config)."
    ),
    serial: str = typer.Option(None, "--serial", help="Find by SMBIOS serial (VMs only, includes stopped)."),
    name: str = typer.Option(None, "--name", help="Find by exact name."),
):
    """Find VMs and LXC containers by IP address, serial or name; prints every match."""
    provided = [value for value in (ip, serial, name) if value]
    if len(provided) != 1:
        fail("Provide exactly one of --ip, --serial or --name.")

    api = get_api()
    guests = fetch_guests(api)

    if ip:
        _search_by_ip(api, guests, ip)
    elif serial:
        _search_by_serial(api, guests, serial)
    else:
        _search_by_name(guests, name)
