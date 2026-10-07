import typer
from rich.table import Table

from proxmox_tools.api import ProxmoxAPI, ProxmoxAPIError
from proxmox_tools.commands.common import console, fail, fetch_vms, get_api, run_parallel
from proxmox_tools.utils import (
    extract_bridges,
    format_bytes_as_mb,
    format_cpu_usage,
    format_firewall_status,
    format_ram_usage,
    format_uptime,
    parse_net_interfaces,
    parse_smbios,
)

app = typer.Typer(help="Read the state of the active Proxmox VE cluster.")

FETCH_ERROR = "[yellow]error[/yellow]"


def _print_failed(label: str, vmids) -> None:
    if vmids:
        ids = ", ".join(str(vmid) for vmid in sorted(vmids))
        console.print(f"[yellow]Failed to fetch {label} for VMs: {ids}[/yellow]")


@app.command("nodes")
def get_nodes():
    """List cluster nodes with live CPU/RAM usage."""
    api = get_api()

    try:
        nodes = api.get_nodes()
        vms = api.get_vms()
    except ProxmoxAPIError as exc:
        fail(str(exc))

    running_per_node: dict[str, int] = {}
    for vm in vms:
        if vm.get("status") == "running":
            running_per_node[vm["node"]] = running_per_node.get(vm["node"], 0) + 1

    table = Table()
    table.add_column("Node")
    table.add_column("Status")
    table.add_column("CPU")
    table.add_column("RAM")
    table.add_column("Uptime")
    table.add_column("Running VMs")

    for node in sorted(nodes, key=lambda n: n["node"]):
        table.add_row(
            node["node"],
            node.get("status", "-"),
            format_cpu_usage(node.get("cpu"), node.get("maxcpu")),
            format_ram_usage(node.get("mem"), node.get("maxmem")),
            format_uptime(node.get("uptime")),
            str(running_per_node.get(node["node"], 0)),
        )

    console.print(table)


def _fetch_vm_configs(api: ProxmoxAPI, vms: list[dict]) -> tuple[dict[int, dict], set[int]]:
    configs: dict[int, dict] = {}
    failed: set[int] = set()
    for vm, vm_config, error in run_parallel(lambda v: api.get_vm_config(v["node"], v["vmid"]), vms):
        if error:
            failed.add(vm["vmid"])
        else:
            configs[vm["vmid"]] = vm_config
    return configs, failed


@app.command("vms")
def get_vms(
    without_pool: bool = typer.Option(
        False, "--without-pool", help="Only VMs not assigned to any pool."
    ),
    pool: str = typer.Option(None, "--pool", help="Only VMs in the given pool."),
    node_filter: str = typer.Option(None, "--node", help="Only VMs on the given node."),
    status: str = typer.Option(None, "--status", help="Only VMs in the given state (e.g. running, stopped)."),
    tag: str = typer.Option(None, "--tag", help="Only VMs with the given tag."),
    serial: bool = typer.Option(
        False,
        "--serial",
        help="Add the SMBIOS serial column.",
    ),
):
    """List all VMs in the active cluster."""
    if without_pool and pool:
        fail("--without-pool cannot be combined with --pool.")

    api = get_api()
    vms = fetch_vms(api)

    if without_pool:
        vms = [vm for vm in vms if not vm.get("pool")]
    if pool:
        vms = [vm for vm in vms if vm.get("pool") == pool]
    if node_filter:
        vms = [vm for vm in vms if vm.get("node") == node_filter]
    if status:
        vms = [vm for vm in vms if vm.get("status") == status]
    if tag:
        vms = [vm for vm in vms if tag in (vm.get("tags") or "").split(";")]

    configs, failed = _fetch_vm_configs(api, vms)

    table = Table()
    table.add_column("VMID")
    table.add_column("Name")
    table.add_column("Node")
    table.add_column("Status")
    table.add_column("RAM")
    table.add_column("CPU")
    table.add_column("Bridge")
    table.add_column("Pool")
    if serial:
        table.add_column("Serial")

    for vm in sorted(vms, key=lambda v: v["vmid"]):
        vmid = vm["vmid"]
        vm_config = configs.get(vmid, {})
        row = [
            str(vmid),
            vm.get("name", "-"),
            vm["node"],
            vm.get("status", "-"),
            format_bytes_as_mb(vm.get("maxmem", 0)),
            str(vm.get("maxcpu", "-")),
            FETCH_ERROR if vmid in failed else extract_bridges(vm_config),
            vm.get("pool") or "-",
        ]
        if serial:
            row.append(FETCH_ERROR if vmid in failed else parse_smbios(vm_config).get("serial") or "-")
        table.add_row(*row)

    console.print(table)

    non_template_vms = [vm for vm in vms if not vm.get("template")]
    template_count = len(vms) - len(non_template_vms)
    running_count = sum(1 for vm in non_template_vms if vm.get("status") == "running")
    stopped_count = sum(1 for vm in non_template_vms if vm.get("status") == "stopped")

    summary = f"Total: {len(non_template_vms)} VMs ({running_count} running, {stopped_count} stopped)"
    if template_count:
        summary += f", excluding {template_count} templates"
    if serial:
        missing_serial = sum(
            1
            for vm in non_template_vms
            if vm["vmid"] not in failed and not parse_smbios(configs.get(vm["vmid"], {})).get("serial")
        )
        summary += f" | Without serial: {missing_serial}"
    console.print(summary)
    _print_failed("config", failed)


def _fetch_firewall(api: ProxmoxAPI, vm: dict) -> tuple[str, str]:
    node, vmid = vm["node"], vm["vmid"]
    try:
        vm_firewall = format_firewall_status(api.get_vm_firewall_options(node, vmid).get("enable"))
    except ProxmoxAPIError:
        vm_firewall = FETCH_ERROR

    try:
        interfaces = parse_net_interfaces(api.get_vm_config(node, vmid))
        fw_interfaces = [iface["key"] for iface in interfaces if iface.get("firewall") == "1"]
        fw_interfaces_display = ", ".join(fw_interfaces) if fw_interfaces else "-"
    except ProxmoxAPIError:
        fw_interfaces_display = FETCH_ERROR

    return vm_firewall, fw_interfaces_display


@app.command("firewall")
def get_firewall():
    """Show the VM-level and per-interface firewall switches for all VMs."""
    api = get_api()
    vms = fetch_vms(api)

    results = {vm["vmid"]: result for vm, result, _ in run_parallel(lambda v: _fetch_firewall(api, v), vms)}

    table = Table()
    table.add_column("VMID")
    table.add_column("Name")
    table.add_column("Node")
    table.add_column("Firewall (VM)")
    table.add_column("Firewalled interfaces")

    for vm in sorted(vms, key=lambda v: v["vmid"]):
        vm_firewall, fw_interfaces_display = results[vm["vmid"]]
        table.add_row(str(vm["vmid"]), vm.get("name", "-"), vm["node"], vm_firewall, fw_interfaces_display)

    console.print(table)


def _print_matches(matches: list[dict], not_found: str) -> None:
    if not matches:
        console.print(not_found)
        raise typer.Exit(1)
    for vm in sorted(matches, key=lambda v: v["vmid"]):
        console.print(f"VMID: {vm['vmid']}  Name: {vm.get('name', '-')}")


def _search_by_ip(api: ProxmoxAPI, vms: list[dict], ip: str) -> None:
    running_vms = [vm for vm in vms if vm.get("status") == "running"]
    results = run_parallel(lambda v: api.get_vm_agent_network_interfaces(v["node"], v["vmid"]), running_vms)

    matches = [
        vm
        for vm, interfaces, error in results
        if not error
        and any(
            addr.get("ip-address") == ip
            for interface in interfaces
            for addr in interface.get("ip-addresses", [])
        )
    ]
    _print_matches(matches, f"No running VM found with IP address {ip}.")


def _search_by_serial(api: ProxmoxAPI, vms: list[dict], serial: str) -> None:
    configs, failed = _fetch_vm_configs(api, vms)
    matches = [vm for vm in vms if parse_smbios(configs.get(vm["vmid"], {})).get("serial") == serial]
    if not matches:
        _print_failed("config", failed)
    _print_matches(matches, f"No VM found with serial {serial}.")


def _search_by_name(vms: list[dict], name: str) -> None:
    matches = [vm for vm in vms if vm.get("name") == name]
    _print_matches(matches, f"No VM found with name {name}.")


@app.command("vm")
def get_vm(
    ip: str = typer.Option(None, "--ip", help="Find by IP address (Guest Agent, running VMs only)."),
    serial: str = typer.Option(None, "--serial", help="Find by SMBIOS serial (includes stopped VMs)."),
    name: str = typer.Option(None, "--name", help="Find by exact VM name."),
):
    """Find VMs by IP address, serial or name; prints every match."""
    provided = [value for value in (ip, serial, name) if value]
    if len(provided) != 1:
        fail("Provide exactly one of --ip, --serial or --name.")

    api = get_api()
    vms = fetch_vms(api)

    if ip:
        _search_by_ip(api, vms, ip)
    elif serial:
        _search_by_serial(api, vms, serial)
    else:
        _search_by_name(vms, name)
