# K-FORGE

**P2P Version Control | Cryptographically Undeletable | KHAWRIZM Labs**

> When they delete your repos, your code survives on every peer.

K-Forge is a decentralized version control system built for sovereignty. It uses content-addressed storage (SHA-256), P2P replication, and Merkle tree verification to ensure your code can never be deleted by a central authority.

## Why K-Forge?

GitHub deleted our repositories. Microsoft erased months of work with a single action. K-Forge ensures this never happens again by distributing your code across peers where no single entity controls it.

## Architecture

```
┌─────────────────────────────────────────────┐
│                K-FORGE v2.0                  │
│                                              │
│  ┌──────────────┐    ┌───────────────────┐   │
│  │ Content-      │    │ Merkle Tree       │   │
│  │ Addressed     │    │ Verification      │   │
│  │ Storage       │    │ (SHA-256)         │   │
│  │ (SHA-256)     │    │                   │   │
│  └──────┬────────┘    └──────────┬────────┘   │
│         │                        │            │
│  ┌──────┴────────────────────────┴────────┐   │
│  │          P2P Replication Layer          │   │
│  │  ┌──────┐  ┌──────┐  ┌──────┐         │   │
│  │  │Peer 1│  │Peer 2│  │Peer N│         │   │
│  │  └──────┘  └──────┘  └──────┘         │   │
│  └────────────────────────────────────────┘   │
│                                              │
│  Objects: blob | tree | commit                │
│  Refs:    branches | HEAD                     │
└─────────────────────────────────────────────┘
```

## Usage

```bash
# Initialize a repository
kforge init

# Stage files
kforge add src/ README.md

# Commit
kforge commit -m "Sovereign kernel module v3.1"

# View history
kforge log

# Check integrity
kforge verify

# Add a peer
kforge peer add 192.168.1.100 --port 9403

# Push to all peers
kforge push

# Start replication server (on receiving peer)
python3 src/replication_server.py --port 9403
```

## Object Model

Like Git, K-Forge uses three object types:

| Object | Description |
|--------|-------------|
| **Blob** | Raw file content, SHA-256 addressed |
| **Tree** | Directory listing with file modes, names, and blob SHAs |
| **Commit** | Points to a tree, parent commit, author, message, and timestamp |

## P2P Replication

K-Forge peers communicate over TCP port 9403. When you `push`, all objects are sent to known peers. Each peer independently verifies SHA-256 integrity before storing.

No central server. No single point of failure. No corporate kill switch.

## Roadmap

- [ ] Ed25519 commit signatures
- [ ] NAT traversal for WAN peering
- [ ] Merkle DAG diff for efficient sync
- [ ] Web UI for repository browsing
- [ ] Integration with Phalanx Gate (trusted peers only)

## Author

**Sulaiman Alshammari** — Dragon403 — KHAWRIZM Labs, Riyadh

---

*This project was deleted from GitHub by Microsoft. It has been rebuilt with actual P2P capabilities.*
