#!/usr/bin/env python3
"""
K-FORGE Replication Server — P2P Object Receiver
Listens for incoming object replications from peers.

Usage: python3 replication_server.py --port 9403
"""
import socket
import threading
import hashlib
import argparse
import logging
from pathlib import Path

__version__ = "2.0.0"
FORGE_DIR = ".kforge"
OBJECTS_DIR = "objects"

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s [K-FORGE:REPL] %(message)s"
)
log = logging.getLogger("replication")


def store_object(forge_path: Path, sha: str, data: bytes) -> bool:
    actual = hashlib.sha256(data).hexdigest()
    if actual != sha:
        log.warning(f"Integrity mismatch: expected {sha[:12]}, got {actual[:12]}")
        return False

    prefix, rest = sha[:2], sha[2:]
    obj_dir = forge_path / OBJECTS_DIR / prefix
    obj_dir.mkdir(parents=True, exist_ok=True)
    obj_path = obj_dir / rest
    if not obj_path.exists():
        obj_path.write_bytes(data)
        return True
    return False


def handle_client(conn: socket.socket, addr: tuple, forge_path: Path):
    log.info(f"Peer connected: {addr[0]}:{addr[1]}")
    received = 0
    buf = b""

    try:
        while True:
            chunk = conn.recv(65536)
            if not chunk:
                break
            buf += chunk

            while b"\n" in buf:
                line_end = buf.index(b"\n")
                header = buf[:line_end].decode().strip()

                if header == "KFORGE-DONE":
                    log.info(f"Replication complete from {addr[0]}: {received} objects")
                    conn.close()
                    return

                if header.startswith("KFORGE-OBJ "):
                    parts = header.split(" ")
                    if len(parts) == 3:
                        sha = parts[1]
                        size = int(parts[2])
                        buf = buf[line_end + 1:]

                        while len(buf) < size:
                            buf += conn.recv(65536)

                        obj_data = buf[:size]
                        buf = buf[size:]

                        if store_object(forge_path, sha, obj_data):
                            received += 1
                else:
                    buf = buf[line_end + 1:]

    except Exception as e:
        log.error(f"Error handling peer {addr}: {e}")
    finally:
        conn.close()
        log.info(f"Peer {addr[0]} disconnected. Received {received} objects.")


def serve(port: int = 9403, repo_path: Path = Path(".")):
    forge_path = repo_path / FORGE_DIR
    if not forge_path.exists():
        log.error(f"No K-Forge repository at {repo_path}")
        return

    server = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
    server.setsockopt(socket.SOL_SOCKET, socket.SO_REUSEADDR, 1)
    server.bind(("0.0.0.0", port))
    server.listen(10)

    log.info(f"K-FORGE Replication Server v{__version__} listening on :{port}")
    log.info(f"Repository: {forge_path}")

    try:
        while True:
            conn, addr = server.accept()
            t = threading.Thread(target=handle_client, args=(conn, addr, forge_path))
            t.daemon = True
            t.start()
    except KeyboardInterrupt:
        log.info("Server stopped.")
    finally:
        server.close()


if __name__ == "__main__":
    p = argparse.ArgumentParser(description="K-FORGE Replication Server")
    p.add_argument("--port", type=int, default=9403)
    p.add_argument("--repo", type=str, default=".")
    args = p.parse_args()
    serve(port=args.port, repo_path=Path(args.repo))
