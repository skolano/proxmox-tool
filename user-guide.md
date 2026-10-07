# proxmox-tool — User Guide

## Requirements

- Python 3.10+ and `pipx`.
- HTTPS access to the Proxmox VE API (port 8006).
- An API token (Datacenter → Permissions → API Tokens) with read access. The `PVEAuditor` role is enough.
  - **Important:** tokens are created with Privilege Separation enabled. Until the token gets its own ACL entry, every query returns empty results without an error. Add it in Datacenter → Permissions → **Add → API Token Permission** (path `/`, role `PVEAuditor`, Propagate enabled) or on a PVE host:
    ```bash
    pveum acl modify / -token 'user@realm!token-name' -role PVEAuditor
    ```
- `qemu-guest-agent` installed and running in VMs that should be found by IP or show live data.

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

## Contexts (clusters)

Several clusters can be configured, commands always run against the **active context**. Configuration is stored in `~/.config/proxmox-tools/config.yaml` (mode `600`, directory `700`) and contains the API URL, token and SSL verification setting of each context.

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

Unlike `get vms`, which shows allocated resources, this command shows current host load. Missing data for offline nodes is shown as `-`.

## get vms

```bash
proxmox-tool get vms [--serial] [filters]
```

| Column | Description |
|---|---|
| VMID | VM ID |
| Name | VM name |
| Node | Node the VM runs on |
| Status | `running` / `stopped` / ... |
| RAM | Allocated memory |
| CPU | Allocated vCPUs |
| Bridge | Bridges of all network interfaces, comma-separated |
| Pool | Pool membership, `-` if none |
| Serial | SMBIOS serial, only with `--serial` |

The config of every VM is fetched in parallel (needed for Bridge and Serial). The Guest Agent is not queried. If a config cannot be fetched, the cell shows `error` and the affected VM IDs are listed below the table.

A summary is printed below the table and counts only the rows shown, templates excluded:

```
Total: 42 VMs (30 running, 12 stopped), excluding 5 templates | Without serial: 7
```

`Without serial` appears only with `--serial`.

Filters (exact match, combinable, no extra API requests):

| Option | Description |
|---|---|
| `--pool <name>` | Only VMs in the pool |
| `--without-pool` | Only VMs outside any pool; cannot be combined with `--pool` |
| `--node <name>` | Only VMs on the node |
| `--status <status>` | Only VMs in the state, e.g. `running` |
| `--tag <tag>` | Only VMs with the tag |

`--without-pool` is useful when pool membership drives backup jobs: a VM outside any pool may have no backup.

## get vm

```bash
proxmox-tool get vm --ip 192.168.140.55
proxmox-tool get vm --serial UMO/2026/048-CE47AC
proxmox-tool get vm --name web01
```

Exactly one option is required. Every matching VM is printed as `VMID` and `Name`, sorted by VMID (duplicate names or IP addresses show all matches). Exit code is `1` when nothing is found.

| Option | Source | Stopped VMs |
|---|---|---|
| `--ip` | Guest Agent, any interface | No |
| `--serial` | SMBIOS serial from VM config | Yes |
| `--name` | VM name | Yes |

`--ip` queries all running VMs in parallel. VMs without a responding Guest Agent are skipped.

## get firewall

```bash
proxmox-tool get firewall
```

Proxmox has two independent firewall switches:

| Column | Description |
|---|---|
| Firewall (VM) | VM switch (**Options → Firewall**): 🟢 enabled / 🔴 disabled |
| Firewalled interfaces | Interfaces with the firewall checkbox (**Hardware → Network Device → Firewall**), `-` if none |

Traffic on an interface is filtered only when **both** switches are enabled. `error` means the data could not be fetched.

## describe vm

```bash
proxmox-tool describe vm 100
```

Sections:

- **General** — VMID, name, node, status, uptime, tags, pool, notes, and SMBIOS fields when set (UUID, serial, manufacturer, product, version, SKU, family). Base64-encoded SMBIOS values are decoded.
- **Resources** — allocated RAM/CPU and live usage for running VMs.
- **Network** — interfaces from the config (model, MAC, bridge, VLAN) and IPv4 addresses from the Guest Agent matched by MAC.
- **Disks** — SCSI, VirtIO, SATA, IDE, EFI and TPM state disks with storage and size. CD-ROM drives are skipped.
- **Snapshots** — name, description and date.
- **Backup** — date, storage and size of the **most recent existing backup** (not the schedule). All enabled storages with `backup` content are checked: shared ones (PBS, NFS, CIFS, CephFS, GlusterFS or flagged `shared`) once, local ones on every online node. A backup on another node's local storage is shown as `storage (node)`. Storages that could not be read are listed below the section.
- **Guest OS** — hostname and OS name from the Guest Agent.

Edge cases:

- Unknown VMID: error message, exit code `1`.
- Stopped VM: live fields show `VM stopped, no live data`.
- Running VM without a responding agent: network shows config without IPs, Guest OS shows `Guest Agent unavailable`.
