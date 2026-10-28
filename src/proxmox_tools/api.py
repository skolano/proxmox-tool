import requests
import urllib3
from requests.adapters import HTTPAdapter

MAX_WORKERS = 20
GUEST_TYPES = ("qemu", "lxc")


class ProxmoxAPIError(Exception):
    pass


class ProxmoxAPI:
    def __init__(self, api_url: str, token_id: str, token_secret: str, verify_ssl: bool = True):
        self.base_url = f"{api_url.rstrip('/')}/api2/json"
        self.verify_ssl = verify_ssl
        if not verify_ssl:
            urllib3.disable_warnings(urllib3.exceptions.InsecureRequestWarning)
        self.session = requests.Session()
        self.session.mount("https://", HTTPAdapter(pool_maxsize=MAX_WORKERS))
        self.session.mount("http://", HTTPAdapter(pool_maxsize=MAX_WORKERS))
        self.session.headers["Authorization"] = f"PVEAPIToken={token_id}={token_secret}"

    def get(self, path: str) -> dict | list:
        url = f"{self.base_url}{path}"
        try:
            resp = self.session.get(url, verify=self.verify_ssl, timeout=10)
        except requests.RequestException as exc:
            raise ProxmoxAPIError(f"Connection to {url} failed: {exc}") from exc
        if not resp.ok:
            raise ProxmoxAPIError(f"Proxmox API returned {resp.status_code} for {url}: {resp.text}")
        try:
            return resp.json()["data"]
        except (ValueError, KeyError) as exc:
            raise ProxmoxAPIError(f"Invalid Proxmox API response for {url}") from exc

    def get_nodes(self) -> list[dict]:
        return self.get("/nodes")

    def get_guests(self) -> list[dict]:
        resources = self.get("/cluster/resources?type=vm")
        return [r for r in resources if r.get("type") in GUEST_TYPES]

    def get_guest_config(self, guest_type: str, node: str, vmid: int) -> dict:
        return self.get(f"/nodes/{node}/{guest_type}/{vmid}/config")

    def get_guest_status(self, guest_type: str, node: str, vmid: int) -> dict:
        return self.get(f"/nodes/{node}/{guest_type}/{vmid}/status/current")

    def get_guest_snapshots(self, guest_type: str, node: str, vmid: int) -> list[dict]:
        return self.get(f"/nodes/{node}/{guest_type}/{vmid}/snapshot")

    def get_guest_firewall_options(self, guest_type: str, node: str, vmid: int) -> dict:
        return self.get(f"/nodes/{node}/{guest_type}/{vmid}/firewall/options")

    def get_lxc_interfaces(self, node: str, vmid: int) -> list[dict]:
        return self.get(f"/nodes/{node}/lxc/{vmid}/interfaces") or []

    def get_node_status(self, node: str) -> dict:
        return self.get(f"/nodes/{node}/status")

    def get_node_version(self, node: str) -> dict:
        return self.get(f"/nodes/{node}/version")

    def get_node_subscription(self, node: str) -> dict:
        return self.get(f"/nodes/{node}/subscription")

    def get_node_storage(self, node: str) -> list[dict]:
        return self.get(f"/nodes/{node}/storage")

    def get_cluster_status(self) -> list[dict]:
        return self.get("/cluster/status")

    def get_cluster_storage(self) -> list[dict]:
        return self.get("/cluster/resources?type=storage")

    def get_backup_jobs(self) -> list[dict]:
        return self.get("/cluster/backup")

    def get_not_backed_up(self) -> list[dict]:
        return self.get("/cluster/backup-info/not-backed-up")

    def get_cluster_firewall_options(self) -> dict:
        return self.get("/cluster/firewall/options")

    def get_storages(self) -> list[dict]:
        return self.get("/storage")

    def get_storage_backup_content(self, node: str, storage: str) -> list[dict]:
        return self.get(f"/nodes/{node}/storage/{storage}/content?content=backup")

    def get_vm_agent_network_interfaces(self, node: str, vmid: int) -> list[dict]:
        result = self.get(f"/nodes/{node}/qemu/{vmid}/agent/network-get-interfaces")
        return result.get("result", [])

    def get_vm_agent_hostname(self, node: str, vmid: int) -> dict:
        result = self.get(f"/nodes/{node}/qemu/{vmid}/agent/get-host-name")
        return result.get("result", {})

    def get_vm_agent_osinfo(self, node: str, vmid: int) -> dict:
        result = self.get(f"/nodes/{node}/qemu/{vmid}/agent/get-osinfo")
        return result.get("result", {})
