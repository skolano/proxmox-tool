# proxmox-tool — User Guide

- [Requirements](#requirements)
- [Guests: VMs and LXC containers](#guests-vms-and-lxc-containers)
- [Installation](#installation)
- [Output conventions](#output-conventions)
- [Contexts (clusters)](#contexts-clusters)
- [get nodes](#get-nodes) · [get vms](#get-vms) · [get vm](#get-vm) · [get firewall](#get-firewall)
- [describe vm](#describe-vm) · [describe node](#describe-node) · [describe cluster](#describe-cluster)

## Requirements

- Python 3.10+ and `pipx`.
- HTTPS access to the Proxmox VE API (port 8006).
- An API token (Datacenter → Permissions → API Tokens) with read access. The `PVEAuditor` role is enough.
  - **Important:** tokens are created with Privilege Separation enabled. Until the token gets its own ACL entry, every query returns empty results without an error. Add it in Datacenter → Permissions → **Add → API Token Permission** (path `/`, role `PVEAuditor`, Propagate enabled) or on a PVE host:
    ```bash
    pveum acl modify / -token 'user@realm!token-name' -role PVEAuditor
    ```
- Proxmox VE 8.1 or newer (live LXC addresses use the `/lxc/{vmid}/interfaces` endpoint).
- `qemu-guest-agent` installed and running in VMs that should be found by IP or show live data. LXC containers need no agent.

## Guests: VMs and LXC containers

Commands named `vm` (`get vms`, `get vm`, `describe vm`) work with both QEMU VMs and LXC containers. VMIDs are unique across both types, so a guest is always identified by its ID alone. The guest type is shown as `VM` or `LXC`.

## Installation

```bash
pipx install .
proxmox-tool --version
```

Run from the project directory. Upgrade with `pipx install --force .`.

Shell completion (zsh, bash, fish):

```bash
proxmox-tool --install-completion
```

## Output conventions

| Marker | Meaning |
|---|---|
| `-` | No value (not set, not applicable, or node offline) |
| `error` | The value could not be fetched; `get vms` also lists the affected IDs below the table |
| `Failed to fetch …` | A whole section could not be read, e.g. missing permission; other sections are still shown |
| `Stopped, no live data` | Live value of a stopped guest |

Thresholds (green / yellow / red):

| Value | Yellow from | Red from |
|---|---|---|
| Storage, RAM, root FS usage | 80% | 90% |
| IO delay | 5% | 10% |
| vCPU allocated / physical threads | 2.0x | 4.0x |
| RAM allocated / physical RAM | 0.9x | 1.0x |

Exit codes: `0` success, `1` error, invalid options, unknown VMID or node, or no match in `get vm`.

## Contexts (clusters)

Several clusters can be configured, commands always run against the **active context**. Configuration is stored in `~/.config/proxmox-tool/config.yaml` (mode `600`, directory `700`) and contains the API URL, token and SSL verification setting of each context.

| Command | Description |
|---|---|
| `context add` | Interactive prompt: name, API URL (`https://pve.example.com:8006`), token ID (`user@realm!token-name`), token secret, SSL verification. Asks before overwriting an existing context. The first context becomes active. |
| `context list` | All contexts, the active one marked with `*`. |
| `context use [name]` | Switch the active context. Without a name, opens an arrow-key menu. |
| `context current` | Show the active context. |

## get nodes

```bash
proxmox-tool get nodes
```

| Column | Description |
|---|---|
| Node | Host name |
| Status | `online` / `offline` |
| CPU | Live CPU usage, e.g. `15% (8 cores)` |
| RAM | Live RAM usage, e.g. `12.3 GB / 64.0 GB (19%)` |
| Uptime | e.g. `5d 5h` |
| Running VMs | VMs in `running` state on the node |
| Running LXC | LXC containers in `running` state on the node |

Unlike `get vms`, which shows allocated resources, this command shows current host load. Missing data for offline nodes is shown as `-`.

## get vms

```bash
proxmox-tool get vms [--serial] [filters]
```

Lists VMs and LXC containers in one table.

| Column | Description |
|---|---|
| VMID | Guest ID |
| Type | `VM` or `LXC` |
| Name | Guest name |
| Node | Node the guest runs on |
| Status | `running` / `stopped` / ... |
| RAM | Allocated memory |
| CPU | Allocated vCPUs (VM) or cores (LXC) |
| Bridge | Bridges of all network interfaces, comma-separated |
| Pool | Pool membership, `-` if none |
| Serial | SMBIOS serial, only with `--serial`; always `-` for LXC |

The config of every guest is fetched in parallel (needed for Bridge and Serial). The Guest Agent is not queried. If a config cannot be fetched, the cell shows `error` and the affected IDs are listed below the table.

A summary is printed below the table and counts only the rows shown, templates excluded:

```
Total: 42 VMs, 12 LXC (48 running, 6 stopped), excluding 5 templates | VMs without serial: 7
```

`VMs without serial` appears only with `--serial` and counts VMs only.

Filters (exact match, combinable, no extra API requests):

| Option | Description |
|---|---|
| `--type <type>` | Only `vm` or only `lxc` |
| `--pool <name>` | Only guests in the pool |
| `--without-pool` | Only guests outside any pool; cannot be combined with `--pool` |
| `--node <name>` | Only guests on the node |
| `--status <status>` | Only guests in the state, e.g. `running` |
| `--tag <tag>` | Only guests with the tag |

`--without-pool` is useful when pool membership drives backup jobs: a guest outside any pool may have no backup.

## get vm

```bash
proxmox-tool get vm --ip 192.0.2.10
proxmox-tool get vm --serial INV/2026/001-A1B2C3
proxmox-tool get vm --name web01
```

Exactly one option is required. Every matching guest is printed with `VMID`, `Name` and `Type`, sorted by VMID (duplicate names or IP addresses show all matches). Exit code is `1` when nothing is found.

```
VMID: 201  Name: web01  Type: LXC
```

| Option | VM | LXC |
|---|---|---|
| `--ip` | Guest Agent, running VMs only | Live addresses when running, static `ip=` from config otherwise |
| `--serial` | SMBIOS serial, includes stopped | Not searched (no SMBIOS) |
| `--name` | Exact name | Exact name |

`--ip` queries guests in parallel. VMs without a responding Guest Agent are skipped. Stopped LXC containers with DHCP cannot be found by IP.

## get firewall

```bash
proxmox-tool get firewall
```

Covers VMs and LXC containers (column **Type**). Proxmox has two independent firewall switches:

| Column | Description |
|---|---|
| Firewall (VM) | Guest switch (**Options → Firewall**): 🟢 enabled / 🔴 disabled |
| Firewalled interfaces | Interfaces with the firewall checkbox (**Hardware → Network Device → Firewall**), `-` if none |

Traffic on an interface is filtered only when **both** switches are enabled. `error` means the data could not be fetched.

## describe vm

```bash
proxmox-tool describe vm 100
```

Works for VMs and LXC containers; the type is detected from the ID.

Sections:

- **General** — VMID, type (`VM`, `LXC (unprivileged)` or `LXC (privileged)`), name, node, status, uptime, tags, pool, notes. VM: SMBIOS fields when set (UUID, serial, manufacturer, product, version, SKU, family; Base64 values are decoded). LXC: hostname and OS type from the config.
- **Resources** — allocated RAM/CPU and live usage for running guests. LXC: swap as well.
- **Network** — interfaces from the config (model, MAC, bridge, VLAN) and IPv4 addresses matched by MAC: from the Guest Agent (VM) or the container's live interfaces (LXC). A static LXC address is shown as `<ip> (config)` when no live data is available.
- **Disks** — VM: SCSI, VirtIO, SATA, IDE, EFI and TPM state disks with storage and size; CD-ROM drives are skipped. LXC: `rootfs` and mount points (`mpX`) with storage, size and mount point; bind mounts show storage `bind`.
- **Snapshots** — name, description and date.
- **Backup** — date, storage and size of the **most recent existing backup** (not the schedule). All enabled storages with `backup` content are checked: shared ones (PBS, NFS, CIFS, CephFS, GlusterFS or flagged `shared`) once, local ones on every online node. A backup on another node's local storage is shown as `storage (node)`. Storages that could not be read are listed below the section.
- **Guest OS** — VM only: hostname and OS name from the Guest Agent.

Edge cases:

- Unknown VMID: error message, exit code `1`.
- Stopped guest: live fields show `Stopped, no live data`.
- Running VM without a responding agent: network shows config without IPs, Guest OS shows `Guest Agent unavailable`.

## describe node

```bash
proxmox-tool describe node pve1
proxmox-tool describe node
```

Without a name, an arrow-key menu lists the cluster nodes.

- **General** — status, uptime, PVE version, kernel, boot mode (UEFI / Secure Boot / Legacy BIOS), subscription.
- **CPU** — model, sockets/cores/threads, usage, load average (1/5/15 min), IO delay (yellow from 5%, red from 10%).
- **Memory** — RAM, swap, memory shared by KSM, root filesystem.
- **Allocation** — vCPUs and RAM assigned to running guests against physical threads and RAM, as a ratio. vCPU: yellow from 2x, red from 4x. RAM: yellow from 0.9x, red from 1.0x.
- **Storage** — storages visible from the node: type, shared flag, usage (yellow from 80%, red from 90%), active state.
- **Guests** — guests on the node, running first; templates are counted, not listed.

For an offline node only General and Guests are shown. An unknown node name prints the available nodes and exits with `1`.

## describe cluster

```bash
proxmox-tool describe cluster
```

- **Cluster** — name, quorum, online nodes, config version, member nodes with IP and state. A standalone node is reported as such.
- **Versions** — PVE version per node, with a warning when nodes differ.
- **Capacity** — physical threads and RAM of online nodes, current usage, and resources allocated to running guests (same thresholds as `describe node`).
- **Guests** — running, stopped and template counts for VMs and LXC.
- **Storage** — shared storages once, local storages per node, with usage and state.
- **Backup** — scheduled backup jobs (schedule, storage, target, mode, enabled) and the guests not covered by any job, as reported by Proxmox.
- **Security** — datacenter firewall switch and default input/output policies. While the datacenter firewall is disabled, no guest or node rules are applied.

A section that cannot be read (e.g. the token lacks a permission) shows `Failed to fetch …`; the remaining sections are still printed.
