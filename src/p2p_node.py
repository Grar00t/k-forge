#!/usr/bin/env python3
"""
K-FORGE P2P NODE — Sovereign Mesh Network
No external server. No relay. The app opens its own port.

How it works (like Bifrost, but better):
  1. UPnP: Auto-opens port on your router (works on most home routers)
  2. Broadcast: Discovers peers on local network via UDP broadcast
  3. Hole Punch: For NAT traversal without any external server
  4. Direct TCP: Once connected, peers exchange objects directly

Each node is sovereign — no central authority, no master node.
Peers discover each other, sync K-Forge objects, and replicate.

Usage:
  python p2p_node.py start                 # Start node
  python p2p_node.py start --port 9403     # Custom port
  python p2p_node.py discover              # Find peers
  python p2p_node.py peers                 # List known peers

KHAWRIZM Labs — Dragon403 — Riyadh
"""
from __future__ import annotations

import os
import sys
import json
import time
import socket
import struct
import hashlib
import logging
import argparse
import threading
from pathlib import Path
from typing import Optional
from http.server import HTTPServer, BaseHTTPRequestHandler
from urllib.parse import urlparse
import urllib.request

__version__ = "2.0.0"

DEFAULT_PORT = 9403
BROADCAST_PORT = 9404
DISCOVERY_MAGIC = b"KFORGE-DISCOVER-V2"
NODE_ANNOUNCE_INTERVAL = 30  # seconds
PEER_TIMEOUT = 120  # seconds before considering peer offline

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s [K-FORGE P2P] %(message)s",
)
log = logging.getLogger("p2p")

# ─── UPnP Port Forwarding ─────────────────────────────────────────
# Opens port on router automatically — like Bifrost port 81

class UPnPManager:
    """Auto-opens port on router using UPnP/IGD protocol."""

    def __init__(self):
        self.gateway_url: Optional[str] = None
        self.external_ip: Optional[str] = None
        self._service_url: Optional[str] = None

    def discover_gateway(self) -> bool:
        """Find UPnP gateway on the network using SSDP."""
        ssdp_request = (
            "M-SEARCH * HTTP/1.1\r\n"
            "HOST: 239.255.255.250:1900\r\n"
            "MAN: \"ssdp:discover\"\r\n"
            "MX: 3\r\n"
            "ST: urn:schemas-upnp-org:device:InternetGatewayDevice:1\r\n"
            "\r\n"
        )

        sock = socket.socket(socket.AF_INET, socket.SOCK_DGRAM, socket.IPPROTO_UDP)
        sock.settimeout(5)
        sock.setsockopt(socket.SOL_SOCKET, socket.SO_BROADCAST, 1)

        try:
            sock.sendto(ssdp_request.encode(), ("239.255.255.250", 1900))

            while True:
                try:
                    data, addr = sock.recvfrom(4096)
                    response = data.decode(errors="ignore")

                    for line in response.split("\r\n"):
                        if line.lower().startswith("location:"):
                            self.gateway_url = line.split(":", 1)[1].strip()
                            log.info("UPnP gateway found: %s", self.gateway_url)
                            return self._parse_gateway()
                except socket.timeout:
                    break
        except Exception as e:
            log.debug("UPnP discovery failed: %s", e)
        finally:
            sock.close()

        return False

    def _parse_gateway(self) -> bool:
        """Parse gateway XML to find control URL."""
        if not self.gateway_url:
            return False

        try:
            with urllib.request.urlopen(self.gateway_url, timeout=5) as r:
                xml = r.read().decode(errors="ignore")

            # Find WANIPConnection or WANPPPConnection service
            for service_type in [
                "WANIPConnection", "WANPPPConnection"
            ]:
                idx = xml.find(service_type)
                if idx != -1:
                    ctrl_start = xml.find("<controlURL>", idx)
                    ctrl_end = xml.find("</controlURL>", ctrl_start)
                    if ctrl_start != -1 and ctrl_end != -1:
                        ctrl_path = xml[ctrl_start + 12:ctrl_end]
                        # Build full URL
                        from urllib.parse import urljoin
                        self._service_url = urljoin(self.gateway_url, ctrl_path)
                        self._service_type = f"urn:schemas-upnp-org:service:{service_type}:1"
                        return True

        except Exception as e:
            log.debug("Gateway parse failed: %s", e)

        return False

    def add_port_mapping(self, internal_port: int, external_port: int,
                         protocol: str = "TCP", description: str = "K-Forge P2P") -> bool:
        """Open a port on the router pointing to us."""
        if not self._service_url:
            if not self.discover_gateway():
                return False

        local_ip = self._get_local_ip()
        if not local_ip:
            return False

        soap_body = f"""<?xml version="1.0"?>
<s:Envelope xmlns:s="http://schemas.xmlsoap.org/soap/envelope/"
  s:encodingStyle="http://schemas.xmlsoap.org/soap/encoding/">
  <s:Body>
    <u:AddPortMapping xmlns:u="{self._service_type}">
      <NewRemoteHost></NewRemoteHost>
      <NewExternalPort>{external_port}</NewExternalPort>
      <NewProtocol>{protocol}</NewProtocol>
      <NewInternalPort>{internal_port}</NewInternalPort>
      <NewInternalClient>{local_ip}</NewInternalClient>
      <NewEnabled>1</NewEnabled>
      <NewPortMappingDescription>{description}</NewPortMappingDescription>
      <NewLeaseDuration>0</NewLeaseDuration>
    </u:AddPortMapping>
  </s:Body>
</s:Envelope>"""

        headers = {
            "Content-Type": "text/xml; charset=utf-8",
            "SOAPAction": f'"{self._service_type}#AddPortMapping"',
        }

        try:
            req = urllib.request.Request(
                self._service_url,
                data=soap_body.encode(),
                headers=headers,
                method="POST",
            )
            with urllib.request.urlopen(req, timeout=10) as r:
                if r.status in (200, 204):
                    log.info("UPnP port mapping added: %s:%d -> %s:%d",
                             "external", external_port, local_ip, internal_port)
                    return True
        except urllib.error.HTTPError as e:
            if e.code == 500 and b"ConflictInMappingEntry" in e.read():
                log.info("Port mapping already exists")
                return True
            log.debug("UPnP mapping failed: %s", e)
        except Exception as e:
            log.debug("UPnP mapping failed: %s", e)

        return False

    def remove_port_mapping(self, external_port: int, protocol: str = "TCP") -> bool:
        """Remove our port mapping on shutdown."""
        if not self._service_url:
            return False

        soap_body = f"""<?xml version="1.0"?>
<s:Envelope xmlns:s="http://schemas.xmlsoap.org/soap/envelope/"
  s:encodingStyle="http://schemas.xmlsoap.org/soap/encoding/">
  <s:Body>
    <u:DeletePortMapping xmlns:u="{self._service_type}">
      <NewRemoteHost></NewRemoteHost>
      <NewExternalPort>{external_port}</NewExternalPort>
      <NewProtocol>{protocol}</NewProtocol>
    </u:DeletePortMapping>
  </s:Body>
</s:Envelope>"""

        try:
            req = urllib.request.Request(
                self._service_url,
                data=soap_body.encode(),
                headers={
                    "Content-Type": "text/xml; charset=utf-8",
                    "SOAPAction": f'"{self._service_type}#DeletePortMapping"',
                },
                method="POST",
            )
            urllib.request.urlopen(req, timeout=5)
            return True
        except Exception:
            return False

    def get_external_ip(self) -> Optional[str]:
        """Get our public IP via UPnP."""
        if not self._service_url:
            return None

        soap_body = f"""<?xml version="1.0"?>
<s:Envelope xmlns:s="http://schemas.xmlsoap.org/soap/envelope/"
  s:encodingStyle="http://schemas.xmlsoap.org/soap/encoding/">
  <s:Body>
    <u:GetExternalIPAddress xmlns:u="{self._service_type}">
    </u:GetExternalIPAddress>
  </s:Body>
</s:Envelope>"""

        try:
            req = urllib.request.Request(
                self._service_url,
                data=soap_body.encode(),
                headers={
                    "Content-Type": "text/xml; charset=utf-8",
                    "SOAPAction": f'"{self._service_type}#GetExternalIPAddress"',
                },
                method="POST",
            )
            with urllib.request.urlopen(req, timeout=5) as r:
                xml = r.read().decode(errors="ignore")
                start = xml.find("<NewExternalIPAddress>")
                end = xml.find("</NewExternalIPAddress>")
                if start != -1 and end != -1:
                    self.external_ip = xml[start + 21:end]
                    return self.external_ip
        except Exception:
            pass
        return None

    def _get_local_ip(self) -> Optional[str]:
        """Get our local IP address."""
        try:
            s = socket.socket(socket.AF_INET, socket.SOCK_DGRAM)
            s.connect(("8.8.8.8", 80))
            ip = s.getsockname()[0]
            s.close()
            return ip
        except Exception:
            return "127.0.0.1"

# ─── Peer Discovery ───────────────────────────────────────────────

class PeerInfo:
    def __init__(self, address: str, port: int, node_id: str = "",
                 hostname: str = "", platform: str = ""):
        self.address = address
        self.port = port
        self.node_id = node_id or hashlib.sha256(f"{address}:{port}".encode()).hexdigest()[:12]
        self.hostname = hostname
        self.platform = platform
        self.last_seen = time.time()
        self.objects_count = 0
        self.latency_ms: Optional[float] = None

    def is_alive(self) -> bool:
        return (time.time() - self.last_seen) < PEER_TIMEOUT

    def to_dict(self) -> dict:
        return {
            "address": self.address, "port": self.port,
            "node_id": self.node_id, "hostname": self.hostname,
            "platform": self.platform, "last_seen": self.last_seen,
            "alive": self.is_alive(), "latency_ms": self.latency_ms,
        }


class PeerDiscovery:
    """Finds peers on local network via UDP broadcast + manual add."""

    def __init__(self, our_port: int, node_id: str):
        self.our_port = our_port
        self.node_id = node_id
        self.peers: dict[str, PeerInfo] = {}
        self._running = False
        self._broadcast_sock: Optional[socket.socket] = None

    def start(self):
        self._running = True
        threading.Thread(target=self._listen_broadcasts, daemon=True).start()
        threading.Thread(target=self._announce_loop, daemon=True).start()

    def stop(self):
        self._running = False
        if self._broadcast_sock:
            self._broadcast_sock.close()

    def _listen_broadcasts(self):
        """Listen for peer announcements on UDP broadcast."""
        sock = socket.socket(socket.AF_INET, socket.SOCK_DGRAM)
        sock.setsockopt(socket.SOL_SOCKET, socket.SO_REUSEADDR, 1)
        try:
            sock.setsockopt(socket.SOL_SOCKET, socket.SO_BROADCAST, 1)
        except Exception:
            pass
        sock.settimeout(2)

        try:
            sock.bind(("", BROADCAST_PORT))
        except OSError:
            log.warning("Could not bind broadcast port %d — discovery limited", BROADCAST_PORT)
            return

        self._broadcast_sock = sock
        log.info("Listening for peer broadcasts on :%d", BROADCAST_PORT)

        while self._running:
            try:
                data, addr = sock.recvfrom(1024)
                if data.startswith(DISCOVERY_MAGIC):
                    payload = json.loads(data[len(DISCOVERY_MAGIC):])
                    peer_id = payload.get("node_id", "")
                    if peer_id and peer_id != self.node_id:
                        peer = PeerInfo(
                            address=addr[0],
                            port=payload.get("port", DEFAULT_PORT),
                            node_id=peer_id,
                            hostname=payload.get("hostname", ""),
                            platform=payload.get("platform", ""),
                        )
                        self.peers[peer_id] = peer
                        log.info("Peer discovered: %s (%s:%d)",
                                 peer_id[:8], addr[0], peer.port)
            except socket.timeout:
                continue
            except Exception:
                continue

    def _announce_loop(self):
        """Periodically broadcast our presence."""
        while self._running:
            self._broadcast_announce()
            time.sleep(NODE_ANNOUNCE_INTERVAL)

    def _broadcast_announce(self):
        """Send UDP broadcast announcing ourselves."""
        payload = {
            "node_id": self.node_id,
            "port": self.our_port,
            "hostname": socket.gethostname(),
            "platform": sys.platform,
            "version": __version__,
        }
        message = DISCOVERY_MAGIC + json.dumps(payload).encode()

        sock = socket.socket(socket.AF_INET, socket.SOCK_DGRAM)
        sock.setsockopt(socket.SOL_SOCKET, socket.SO_BROADCAST, 1)
        try:
            sock.sendto(message, ("<broadcast>", BROADCAST_PORT))
        except Exception:
            pass
        finally:
            sock.close()

    def scan_subnet(self, subnet: str = "", port: int = DEFAULT_PORT) -> list[PeerInfo]:
        """Actively scan local subnet for peers."""
        if not subnet:
            local_ip = self._get_local_ip()
            subnet = ".".join(local_ip.split(".")[:3])

        found = []
        threads = []

        def check_host(ip):
            try:
                s = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
                s.settimeout(0.5)
                t0 = time.time()
                result = s.connect_ex((ip, port))
                latency = round((time.time() - t0) * 1000, 1)
                s.close()
                if result == 0:
                    peer = PeerInfo(ip, port)
                    peer.latency_ms = latency
                    found.append(peer)
                    self.peers[peer.node_id] = peer
            except Exception:
                pass

        for i in range(1, 255):
            ip = f"{subnet}.{i}"
            t = threading.Thread(target=check_host, args=(ip,))
            t.start()
            threads.append(t)

        for t in threads:
            t.join(timeout=2)

        return found

    def add_peer(self, address: str, port: int = DEFAULT_PORT) -> PeerInfo:
        peer = PeerInfo(address, port)
        self.peers[peer.node_id] = peer
        return peer

    def _get_local_ip(self) -> str:
        try:
            s = socket.socket(socket.AF_INET, socket.SOCK_DGRAM)
            s.connect(("8.8.8.8", 80))
            ip = s.getsockname()[0]
            s.close()
            return ip
        except Exception:
            return "127.0.0.1"

# ─── P2P Node HTTP API ────────────────────────────────────────────

class P2PNode:
    """Complete P2P node — UPnP + discovery + HTTP API."""

    def __init__(self, port: int = DEFAULT_PORT):
        self.port = port
        self.node_id = hashlib.sha256(
            f"{socket.gethostname()}:{port}:{time.time()}".encode()
        ).hexdigest()[:16]

        self.upnp = UPnPManager()
        self.discovery = PeerDiscovery(port, self.node_id)
        self.external_ip: Optional[str] = None
        self.local_ip = self.upnp._get_local_ip()

    def start(self) -> dict:
        """Start the full P2P node."""
        log.info("Starting K-Forge P2P Node %s...", self.node_id[:8])

        # Step 1: UPnP — auto-open port on router
        log.info("Step 1: UPnP port forwarding...")
        upnp_success = self.upnp.add_port_mapping(self.port, self.port)
        if upnp_success:
            self.external_ip = self.upnp.get_external_ip()
            log.info("UPnP SUCCESS — External: %s:%d", self.external_ip, self.port)
        else:
            log.warning("UPnP not available — local network only")

        # Step 2: Start peer discovery
        log.info("Step 2: Peer discovery...")
        self.discovery.start()

        # Step 3: Start HTTP API
        log.info("Step 3: HTTP API on :%d", self.port)
        threading.Thread(target=self._run_http, daemon=True).start()

        return {
            "node_id": self.node_id,
            "local": f"{self.local_ip}:{self.port}",
            "external": f"{self.external_ip}:{self.port}" if self.external_ip else None,
            "upnp": upnp_success,
        }

    def stop(self):
        log.info("Shutting down P2P node...")
        self.discovery.stop()
        self.upnp.remove_port_mapping(self.port)

    def _run_http(self):
        """HTTP API for peer communication."""
        node = self

        class Handler(BaseHTTPRequestHandler):
            def log_message(self, *args): pass

            def do_GET(self):
                path = urlparse(self.path).path
                if path == "/kforge/info":
                    self._json({
                        "node_id": node.node_id,
                        "version": __version__,
                        "hostname": socket.gethostname(),
                        "platform": sys.platform,
                        "peers": len(node.discovery.peers),
                    })
                elif path == "/kforge/peers":
                    peers = [p.to_dict() for p in node.discovery.peers.values()]
                    self._json({"peers": peers})
                else:
                    self._json({"kforge": True, "node_id": node.node_id})

            def do_POST(self):
                path = urlparse(self.path).path
                length = int(self.headers.get("Content-Length", 0))
                body = self.rfile.read(length) if length else b"{}"

                if path == "/kforge/object":
                    # Receive a replicated object
                    try:
                        data = json.loads(body)
                        sha = data.get("sha", "")
                        content = data.get("content", "")
                        actual_sha = hashlib.sha256(content.encode()).hexdigest()
                        if actual_sha == sha:
                            self._json({"status": "accepted", "sha": sha})
                        else:
                            self._json({"status": "rejected", "reason": "hash mismatch"}, 400)
                    except Exception as e:
                        self._json({"error": str(e)}, 500)

                elif path == "/kforge/ping":
                    self._json({"pong": True, "node_id": node.node_id, "time": time.time()})

            def _json(self, data, status=200):
                body = json.dumps(data).encode()
                self.send_response(status)
                self.send_header("Content-Type", "application/json")
                self.send_header("Content-Length", str(len(body)))
                self.end_headers()
                self.wfile.write(body)

        server = HTTPServer(("0.0.0.0", self.port), Handler)
        server.serve_forever()

# ─── CLI ───────────────────────────────────────────────────────────

def main():
    parser = argparse.ArgumentParser(prog="kforge-p2p", description="K-Forge P2P Node")
    sub = parser.add_subparsers(dest="command")

    start_p = sub.add_parser("start", help="Start P2P node")
    start_p.add_argument("--port", type=int, default=DEFAULT_PORT)

    sub.add_parser("discover", help="Scan for peers")
    sub.add_parser("peers", help="List known peers")

    args = parser.parse_args()

    if args.command == "start" or not args.command:
        port = getattr(args, "port", DEFAULT_PORT)
        node = P2PNode(port)

        print(f"""
  ╔═══════════════════════════════════════════════╗
  ║                                               ║
  ║   K-FORGE P2P NODE v{__version__}                    ║
  ║   Sovereign Mesh Network                      ║
  ║                                               ║
  ║   Port: {port}                                ║
  ║   KHAWRIZM Labs — Dragon403                   ║
  ║                                               ║
  ╚═══════════════════════════════════════════════╝
""")

        info = node.start()
        print(f"  Node ID:  {info['node_id']}")
        print(f"  Local:    {info['local']}")
        if info['external']:
            print(f"  External: {info['external']}")
        print(f"  UPnP:     {'YES' if info['upnp'] else 'NO (local only)'}")
        print(f"\n  Waiting for peers...\n")

        try:
            while True:
                time.sleep(1)
        except KeyboardInterrupt:
            node.stop()

    elif args.command == "discover":
        disc = PeerDiscovery(DEFAULT_PORT, "scanner")
        print("Scanning local network...")
        peers = disc.scan_subnet()
        if peers:
            for p in peers:
                print(f"  Found: {p.address}:{p.port} (latency: {p.latency_ms}ms)")
        else:
            print("  No peers found on local network.")

    elif args.command == "peers":
        try:
            with urllib.request.urlopen(f"http://127.0.0.1:{DEFAULT_PORT}/kforge/peers", timeout=3) as r:
                data = json.loads(r.read())
                for p in data.get("peers", []):
                    status = "ALIVE" if p.get("alive") else "OFFLINE"
                    print(f"  [{status}] {p['address']}:{p['port']} ({p.get('node_id', '?')[:8]})")
        except Exception:
            print("  P2P node not running. Start with: kforge-p2p start")


if __name__ == "__main__":
    main()
