# proxmox-tool — cheatsheet

## Install / upgrade

```bash
pipx install .
pipx install --force .
pipx uninstall proxmox-tool
proxmox-tool --version
proxmox-tool --install-completion
```

## Security audit

```bash
python3 --version
sudo dnf upgrade --refresh python3 python3-libs pipx
pipx run pip-audit --path ~/.local/share/pipx/venvs/proxmox-tool/lib/python3.14/site-packages
pipx runpip proxmox-tool install --upgrade <package>
```

Pinned direct dependencies are bumped in `pyproject.toml`, then `pipx install --force .`. Transitive ones (e.g. `certifi`) are upgraded in place with `runpip` or refreshed by reinstalling. The venv links to the system interpreter, so Python itself is patched by `dnf`.

## Build check

```bash
uv build --wheel -o /tmp/proxmox-tool-dist .
rm -rf build src/*.egg-info
```

## Run from source

```bash
PYTHONPATH=src ~/.local/share/pipx/venvs/proxmox-tool/bin/python -m proxmox_tool.cli --help
```

## Contexts

```bash
proxmox-tool context add
proxmox-tool context list
proxmox-tool context use
proxmox-tool context use <name>
proxmox-tool context current
```

## Queries

```bash
proxmox-tool get nodes
proxmox-tool get vms
proxmox-tool get vms --serial
proxmox-tool get vms --type vm
proxmox-tool get vms --type lxc
proxmox-tool get vms --without-pool
proxmox-tool get vms --node <node> --status running
proxmox-tool get vms --pool <pool> --tag <tag>
proxmox-tool get vm --ip <ip>
proxmox-tool get vm --serial <serial>
proxmox-tool get vm --name <name>
proxmox-tool get firewall
proxmox-tool describe vm <vmid>
proxmox-tool describe node
proxmox-tool describe node <node>
proxmox-tool describe cluster
```

## API token (on a PVE host)

```bash
pveum acl modify / -token 'user@realm!token-name' -role PVEAuditor
```

## Manual API test

```bash
curl -sk -H 'Authorization: PVEAPIToken=user@realm!token-name=SECRET' https://pve.example.com:8006/api2/json/nodes
```

Single quotes are required in zsh because of `!` in the token ID.
