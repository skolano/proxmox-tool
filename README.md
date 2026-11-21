# proxmox-tool

`proxmox-tool` is a command-line tool for querying Proxmox VE clusters through the REST API, without the web UI. VMs and LXC containers are both covered; commands named `vm` work with either and show the guest type.

Full usage, columns and limitations: [user-guide.md](user-guide.md).

## Commands

| Command | Description |
|---|---|
| `context add/list/use/current` | Manage configured clusters and the active context |
| `get nodes` | Nodes with live CPU/RAM usage, uptime, running VMs and LXC containers |
| `get vms` | VMs and LXC containers with filters (`--type`, `--pool`, `--without-pool`, `--node`, `--status`, `--tag`), optional `--serial` column and summary |
| `get vm --ip/--serial/--name` | Find guests by IP, SMBIOS serial (VMs) or name |
| `get firewall` | Guest-level and per-interface firewall switches |
| `describe vm <vmid>` | VM or LXC details: general, resources, network, disks, snapshots, last backup, guest OS |
| `describe node [name]` | Node details: versions, CPU, memory, overcommit, storage, guests |
| `describe cluster` | Quorum, PVE versions, capacity, guests, storage, backup jobs and uncovered guests, datacenter firewall |

## Architecture

- Read-only: no command changes cluster state.
- Proxmox VE REST API (`https://<host>:8006/api2/json`), API token authentication. No SSH, no agent on the PVE hosts.
- Multiple clusters with one active context, similar to `kubectl config use-context`.
- Live data only, no cache. Per-guest, per-node and per-storage requests run in parallel (up to 20).
- VMs and LXC containers share one model (guest); the type is resolved from the VMID.
- Configuration and secrets in `~/.config/proxmox-tool/config.yaml` (mode `600`).
- Python, Typer, requests, rich, questionary, PyYAML.

## Requirements

- Python 3.10+ and `pipx` on the workstation.
- HTTPS access to the cluster API (port 8006).
- API token with the `PVEAuditor` role and its own ACL entry (see [user-guide.md](user-guide.md#requirements)).
- Proxmox VE 8.1+ (tested target: 9.1).
- `qemu-guest-agent` in VMs for IP lookup and live data in `describe vm`. LXC containers need no agent.

## Installation

```bash
pipx install .
proxmox-tool --version
```

Upgrade: `pipx install --force .`

## Quick start

```bash
proxmox-tool context add
proxmox-tool describe cluster
proxmox-tool get vms
proxmox-tool describe vm 100
```

## License

MIT, see [LICENSE](LICENSE).
