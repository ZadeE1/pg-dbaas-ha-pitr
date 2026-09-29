# pg-dbaas-ha-pitr

## Purpose

Self-hosted, high-availability PostgreSQL as a service, orchestrated as Docker containers across a
Cloudflare Mesh overlay of personal machines.

The defining constraint: **the control plane is stateless.** A control machine runs one CLI, bootstraps the
hosts over SSH, and is then never needed at runtime. No machine holds state that cannot be rebuilt from
the DCS and the Pod volumes.

## Ownership

- Architecture decisions and their rationale: `architecture.html` (revision 3)
- Implementation: not started

## Local Contracts

- **Managers are leaderless.** No manager is primary, replica, or coordinator. The only election in the
  system is per-Pod, run by Patroni, namespaced in the DCS. Never introduce a manager-level leader,
  quorum, or "active manager" concept.
- **The DCS holds desired state; managers are independent reconcilers.** Each manager runs the same
  reconcile loop and acts only on entries naming its own `node_id`. No manager-to-manager messaging.
- **Every manager must take a leased node lock before acting.** `/pgdbaas/v1/nodes/<id>/lease`, TTL 15s,
  renewed. Two managers on one `node_id` would run two Pods over one volume. This is not optional.
- **A Pod's peers are defined by logical cluster, not by host.** Co-location is a placement detail.
- **Gateways are stateless and disposable.** They resync their member list from the managers on a loop
  and health-check Patroni's REST API. They hold no state, hold no Pod, and are not in the replication
  path. Nothing about a gateway may be baked in at deploy time.
- **The overlay is Cloudflare Mesh (Layer 3, `100.96.0.0/12`).** Cloudflare Tunnel is L7 and must never
  appear in the replication path.
- **Pods run with `--network host`.** This makes the container's MTU equal the overlay MTU and removes
  address translation from the replication path. It costs per-Pod port allocation, which is mandatory.
- **Ports come from a reserved range** (`21000-21999` by default), verified at bootstrap and recorded in
  the DCS. Do not introduce a port-multiplexing proxy: it adds two hops per replication link and
  destroys the source address that `pg_hba.conf` depends on.
- **Every Pod needs both its data port and its REST port mesh-reachable.** The REST port is not optional;
  Patroni's leader race calls it on every other member.
- **Replication is strictly synchronous.** `synchronous_mode: on`, `synchronous_mode_strict: true`,
  `synchronous_node_count: 1`. Chosen so an acknowledged order always exists on two machines.
- **The far Pod carries `nosync: true`.** Your two Oracle VPS are ~1-2ms apart; home is ~25-30ms. The tag
  keeps writes waiting on the near link. It is a physical fact encoded in config and must be revisited if
  the hosts move.
- **The DCS is a sidecar, not a manager.** It is the only leader-ful component, pinned 1:1 to managers.
  That coupling is the system's known availability cliff.

## Work Guidance

- Validate architectural claims against vendor and upstream documentation before encoding them. The
  rationale in `architecture.html` cites sources; keep that property as it grows.
- `architecture.html` is self-contained: inline CSS, inline SVG, no external assets, no build step, no
  network dependencies. It must render from a bare `file://` open.
- Diagrams are hand-authored inline SVG with explicit `viewBox` coordinates. There is no label
  auto-placement, so check text against its enclosing panel, not just the canvas — a canvas-bounds check
  will not catch a label overflowing its box. Decode HTML entities before measuring string width.
- Expand abbreviations on first use. The document keeps a glossary; add to it rather than assuming.
- Prefer Patroni + etcd over bespoke replication or leader election. Do not build custom failover.
- Lifecycle surfaces (backup, PITR, deletion, secrets, upgrades) are deliberately undesigned. See §13
  before implementing any of them.

## Verification

None yet. No build, test, or lint tooling — `architecture.html` is a static document. When implementation
begins, record the check commands here.

## Child DOX Index

This project has no child `AGENTS.md` files. `architecture.html` is a single document at the repository
root and is governed by this file.

Create a child `AGENTS.md` when a durable boundary appears — the CLI package, the manager, the gateway, a
shared library, or a docs subtree — and add it to the index above.
