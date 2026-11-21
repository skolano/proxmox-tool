from rich.table import Table

from proxmox_tool.api import ProxmoxAPI, ProxmoxAPIError
from proxmox_tool.commands.common import console, fail, get_api, print_kv, run_parallel, section
from proxmox_tool.commands.node import CPU_OVERCOMMIT, GB, RAM_OVERCOMMIT, running_guests
from proxmox_tool.utils import (
    format_firewall_status,
    format_overcommit,
    format_usage,
    guest_type_label,
    is_lxc,
)

FIREWALL_POLICY_DEFAULTS = {"policy_in": "DROP", "policy_out": "ACCEPT"}


def _status_label(online: bool) -> str:
    return "[green]online[/green]" if online else "[red]offline[/red]"


def _print_cluster(cluster_status: list[dict], nodes: list[dict]) -> None:
    section("Cluster")
    info = next((item for item in cluster_status if item.get("type") == "cluster"), None)
    members = [item for item in cluster_status if item.get("type") == "node"]
    online = sum(1 for n in nodes if n.get("status") == "online")

    if info:
        quorate = "[green]yes[/green]" if info.get("quorate") else "[red]no[/red]"
        print_kv(
            [
                ("Name", info.get("name", "-")),
                ("Quorum", quorate),
                ("Nodes online", f"{online} / {info.get('nodes', len(nodes))}"),
                ("Config version", str(info.get("version", "-"))),
            ]
        )
    else:
        print_kv([("Name", "standalone node (no cluster)"), ("Nodes online", f"{online} / {len(nodes)}")])

    if members:
        table = Table()
        table.add_column("Node")
        table.add_column("ID")
        table.add_column("IP")
        table.add_column("Status")
        for member in sorted(members, key=lambda m: m.get("name", "")):
            table.add_row(
                member.get("name", "-"),
                str(member.get("nodeid", "-")),
                member.get("ip", "-"),
                _status_label(bool(member.get("online"))),
            )
        console.print(table)
    console.print()


def _print_versions(api: ProxmoxAPI, nodes: list[dict]) -> None:
    section("Versions")
    online = [n for n in nodes if n.get("status") == "online"]
    results = {node["node"]: (version, error) for node, version, error in run_parallel(
        lambda n: api.get_node_version(n["node"]), online
    )}

    table = Table()
    table.add_column("Node")
    table.add_column("PVE version")
    versions = set()
    for node in sorted(nodes, key=lambda n: n["node"]):
        name = node["node"]
        if name not in results:
            table.add_row(name, "[red]offline[/red]")
            continue
        version, error = results[name]
        if error:
            table.add_row(name, "[yellow]error[/yellow]")
            continue
        versions.add(version.get("version", "-"))
        table.add_row(name, version.get("version", "-"))
    console.print(table)
    if len(versions) > 1:
        console.print("[yellow]Nodes run different Proxmox VE versions.[/yellow]")
    console.print()


def _print_capacity(nodes: list[dict], guests: list[dict]) -> None:
    section("Capacity")
    online = [n for n in nodes if n.get("status") == "online"]
    threads = sum(n.get("maxcpu") or 0 for n in online)
    cpu_used = sum((n.get("cpu") or 0) * (n.get("maxcpu") or 0) for n in online)
    mem_total = sum(n.get("maxmem") or 0 for n in online)
    mem_used = sum(n.get("mem") or 0 for n in online)

    running = running_guests(guests)
    vcpus = sum(g.get("maxcpu") or 0 for g in running)
    ram = sum(g.get("maxmem") or 0 for g in running)

    print_kv(
        [
            ("CPU threads", str(threads)),
            ("CPU usage", f"{round(cpu_used / threads * 100)}%" if threads else "-"),
            ("RAM usage", format_usage(mem_used, mem_total)),
            ("vCPU allocated", format_overcommit(vcpus, threads, "threads", *CPU_OVERCOMMIT)),
            (
                "RAM allocated",
                format_overcommit(round(ram / GB, 1), round(mem_total / GB, 1), "GB", *RAM_OVERCOMMIT),
            ),
        ]
    )
    console.print("Online nodes only; allocation counts running guests.")
    console.print()


def _print_guests(guests: list[dict]) -> None:
    section("Guests")
    table = Table()
    table.add_column("Type")
    table.add_column("Running")
    table.add_column("Stopped")
    table.add_column("Templates")
    table.add_column("Total")

    totals = [0, 0, 0, 0]
    for label, lxc in (("VM", False), ("LXC", True)):
        group = [g for g in guests if is_lxc(g) == lxc]
        templates = sum(1 for g in group if g.get("template"))
        running = len(running_guests(group))
        stopped = len(group) - templates - running
        counts = [running, stopped, templates, len(group)]
        totals = [a + b for a, b in zip(totals, counts)]
        table.add_row(label, *map(str, counts))
    table.add_row("[bold]Total[/bold]", *map(str, totals))
    console.print(table)
    console.print()


def _print_storage(api: ProxmoxAPI) -> None:
    section("Storage")
    try:
        items = api.get_cluster_storage()
    except ProxmoxAPIError:
        console.print("Failed to fetch storage list.")
        console.print()
        return

    seen = {}
    for item in items:
        key = item["storage"] if item.get("shared") else (item["storage"], item.get("node"))
        seen.setdefault(key, item)

    table = Table()
    table.add_column("Storage")
    table.add_column("Node")
    table.add_column("Type")
    table.add_column("Usage")
    table.add_column("Status")
    for item in sorted(seen.values(), key=lambda i: (i["storage"], i.get("node", ""))):
        available = item.get("status") == "available"
        table.add_row(
            item["storage"],
            "shared" if item.get("shared") else item.get("node", "-"),
            item.get("plugintype", "-"),
            format_usage(item.get("disk"), item.get("maxdisk")) if available else "-",
            item.get("status", "-") if available else f"[red]{item.get('status', 'unknown')}[/red]",
        )
    console.print(table)
    console.print()


def _job_target(job: dict) -> str:
    if job.get("all"):
        target = "all"
        if job.get("exclude"):
            target += f" except {job['exclude']}"
    elif job.get("pool"):
        target = f"pool {job['pool']}"
    else:
        target = job.get("vmid") or "-"
    if job.get("node"):
        target += f" (node {job['node']})"
    return target


def _job_schedule(job: dict) -> str:
    if job.get("schedule"):
        return job["schedule"]
    return " ".join(filter(None, (job.get("dow"), job.get("starttime")))) or "-"


def _print_backup(api: ProxmoxAPI) -> None:
    section("Backup")
    try:
        jobs = api.get_backup_jobs()
    except ProxmoxAPIError:
        console.print("Failed to fetch backup jobs.")
        jobs = None

    if jobs is not None:
        if not jobs:
            console.print("[yellow]No backup jobs configured.[/yellow]")
        else:
            table = Table()
            table.add_column("Job")
            table.add_column("Schedule")
            table.add_column("Storage")
            table.add_column("Target")
            table.add_column("Mode")
            table.add_column("Enabled")
            for job in sorted(jobs, key=lambda j: j.get("id", "")):
                enabled = str(job.get("enabled", 1)) != "0"
                table.add_row(
                    job.get("comment") or job.get("id", "-"),
                    _job_schedule(job),
                    job.get("storage", "-"),
                    _job_target(job),
                    job.get("mode", "-"),
                    "yes" if enabled else "[yellow]no[/yellow]",
                )
            console.print(table)

    try:
        uncovered = api.get_not_backed_up()
    except ProxmoxAPIError:
        console.print("Failed to fetch guests not covered by backup jobs.")
        console.print()
        return

    if not uncovered:
        console.print("[green]All guests are covered by a backup job.[/green]")
    else:
        console.print(f"[yellow]Guests not covered by any backup job: {len(uncovered)}[/yellow]")
        table = Table()
        table.add_column("VMID")
        table.add_column("Type")
        table.add_column("Name")
        for guest in sorted(uncovered, key=lambda g: int(g.get("vmid", 0))):
            table.add_row(str(guest.get("vmid", "-")), guest_type_label(guest), guest.get("name") or "-")
        console.print(table)
    console.print()


def _print_security(api: ProxmoxAPI) -> None:
    section("Security")
    try:
        options = api.get_cluster_firewall_options()
    except ProxmoxAPIError:
        console.print("Failed to fetch datacenter firewall options.")
        console.print()
        return

    rows = [("Datacenter firewall", format_firewall_status(options.get("enable")))]
    for key, label in (("policy_in", "Input policy"), ("policy_out", "Output policy")):
        value = options.get(key)
        rows.append((label, value or f"{FIREWALL_POLICY_DEFAULTS[key]} (default)"))
    print_kv(rows)
    if not options.get("enable"):
        console.print("[yellow]Guest and node firewall rules are not applied while the datacenter firewall is disabled.[/yellow]")
    console.print()


def describe_cluster():
    """Show datacenter details: quorum, versions, capacity, guests, storage, backup, security."""
    api = get_api()
    try:
        nodes = api.get_nodes()
        guests = api.get_guests()
        cluster_status = api.get_cluster_status()
    except ProxmoxAPIError as exc:
        fail(str(exc))

    _print_cluster(cluster_status, nodes)
    _print_versions(api, nodes)
    _print_capacity(nodes, guests)
    _print_guests(guests)
    _print_storage(api)
    _print_backup(api)
    _print_security(api)
