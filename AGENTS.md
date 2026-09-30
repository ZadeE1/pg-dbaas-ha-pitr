# pg-dbaas-ha-pitr

## Purpose

Self-hosted, high-availability PostgreSQL as a service, orchestrated as Docker containers across a
Cloudflare Mesh overlay of personal machines.

The defining constraint: **the control plane is stateless.** A control machine runs one CLI, bootstraps the
hosts over SSH, and is then never needed at runtime. No machine holds state that cannot be rebuilt from
the DCS and the Pod volumes.

## Ownership

- Architecture decisions and their rationale: `architecture.html` (revision 6)
- Plain-language summary for humans starting out: `README.md`. It restates the same decisions in shorter
  form — if a decision changes, update both, and `architecture.html` is the one that wins on any conflict.
- Implementation: not started

## Local Contracts

- **`setup status` reports only derived facts.** Each of the nine steps is a live probe or a value read
  back from the spec/DCS; there is no local checklist file that can drift. A control machine without storage
  credentials reports `unknown`, never a wrong answer. Do not introduce setup state.
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
- **The gateway is one container: PgBouncer in front of HAProxy.** One app-facing port (5432); the database
  name does the split (`/ecom` writes, `/ecom_ro` reads); HAProxy binds loopback only and owns role
  selection with per-Pod REST-port checks. Do not reverse the order — HAProxy in front would require a
  PgBouncer reload on every failover.
- **The overlay is Cloudflare Mesh (Layer 3, `100.96.0.0/12`).** Cloudflare Tunnel is L7 and must never
  appear in the replication path.
- **Pods run with `--network host`.** This makes the container's MTU equal the overlay MTU and removes
  address translation from the replication path. It costs per-Pod port allocation, which is mandatory.
- **Ports come from a reserved range** (`21000-21999` by default), verified at bootstrap and recorded in
  the DCS. Do not introduce a port-multiplexing proxy: it adds two hops per replication link and
  destroys the source address that `pg_hba.conf` depends on.
- **Every Pod needs both its data port and its REST port mesh-reachable.** The REST port is not optional;
  Patroni's leader race calls it on every other member. Consumers must read the port from the member list
  per Pod (`base + 2k + 1`); a constant such as `21001` is wrong for every Pod but the first, and it is what
  broke the rev-4 gateway config.
- **The mesh enrols, it does not authorise.** Until mTLS on the DCS and manager API, Patroni's mutating
  endpoints behind basic auth, and `scram-sha-256` on every `pg_hba` entry are implemented, every enrolled
  device is root. Decide that before the first `cluster create`, or treat the enrolment list as credentials.
- **Only the primary runs `archive_command`.** Archive continuity across failover comes from replication
  slots, `archive_timeout`, archived timeline history, and the promoted Pod pushing its own `pg_wal` — not
  from every Pod pushing. Never encode "every Pod archives".
- **Replication is strictly synchronous.** `synchronous_mode: on`, `synchronous_mode_strict: true`,
  `synchronous_node_count: 1`. Chosen so an acknowledged order always exists on two machines.
- **The far Pod carries `nosync: true`.** Your two Oracle VPS are ~1-2ms apart; home is ~25-30ms. The tag
  keeps writes waiting on the near link. It is a physical fact encoded in config and must be revisited if
  the hosts move.
- **Backups are WAL-G to R2, with no backup server.** One static binary per Pod, env-var config, so
  backups add no node to the DCS quorum. Do not introduce barman (needs a server) or move the design onto
  pgBackRest without weighing its single-maintainer history.
- **A restore creates a new cluster; it never overwrites the live one in place.** The default is
  `restore --as <name>`. In-place restore requires `--i-mean-it` plus typing the cluster name. Do not add a
  flag that skips that prompt.
- **The app reaches the gateway by compose service name, or by loopback only when the app shares the host's
  network namespace.** A sibling container cannot reach `127.0.0.1:<published port>`. Never publish
  `0.0.0.0:5432`, and never publish the gateway onto the mesh.
- **A rebuilt or relocated Pod is rebuilt out of R2, not off its peers.** `create_replica_methods` with
  `no_master: 1` is what lets a Pod be created while the primary is down, and what makes `cluster move`
  possible at all.
- **etcd is staged, then started, then flipped.** Bootstrap writes an identical `initial-cluster` to every
  member, starts them, then empties it and sets `initial-cluster-state: existing`; growth after that is
  `etcdctl member add`, never `initial-cluster`. A populated block left behind makes a rejoining member try
  to bootstrap a fresh cluster and fail.
- **Every full backup is verified automatically, and a broken archive turns `status` red.** R2 has no
  egress fees, so this is free. Do not make verification opt-in.
- **One storage path per major PostgreSQL version.** Timeline numbers reset on upgrade, and retention
  ordered by name will then delete the newest backups. This is a documented footgun, not a hypothetical.
- **Five etcd voters, one per enrolled host, quorum three.** The three data hosts, the app host, and one
  etcd-only spare. Majority quorum means four voters tolerate the same *single* host loss as three, so never
  add a fourth expecting improvement; five tolerates two losses, and three losses is a restore from R2, not a
  repair. The spare runs no Pods, holds no data and is never a backup server.

## Work Guidance

- **The document must teach from the ground up.** A reader arriving at `architecture.html` has no prior
  context. §00 "Start here" opens with the nine-step quickstart (`pgdbaas setup` → `setup status`), explains
  the whole system in plain words, lists prerequisites, defines every invented term, and is followed by the
  four command sets. Preserve that when editing.
- Validate architectural claims against vendor and upstream documentation before encoding them. The
  rationale in `architecture.html` cites sources; keep that property as it grows.
- `architecture.html` is self-contained: inline CSS, inline SVG, no external assets, no build step, no
  network dependencies. It must render from a bare `file://` open.
- Diagrams are hand-authored inline SVG with explicit `viewBox` coordinates. There is no label
  auto-placement, so check text against its enclosing panel, not just the canvas — a canvas-bounds check
  will not catch a label overflowing its box. Decode HTML entities before measuring string width.
- Prefer Patroni + etcd over bespoke replication or leader election. Do not build custom failover.
- Lifecycle surfaces (deletion, secrets, upgrades) and control-plane authorisation are **decided but
  unbuilt**: §15 records every decision and its reasoning, §14 says what is left to implement. Read both
  before implementing any of them.

## Verification

- `python3 tools/check-architecture.py` — the record command. Exit 0 means the document parses with balanced
  tags, has no duplicate or unresolved SVG marker ids, has no `<text>` overflowing its enclosing panel, and
  references no external asset. Run it after every edit; do not call an edit finished while it fails.
- Read the file from a bare `file://` open after any change to the CSS, and eyeball the widest diagram
  (`§13`) at a narrow window — the checker measures text width, it does not see wrapped or clipped layout.
- No build or test tooling exists for the implementation yet; record those check commands here when it
  starts.
- When editing, validate after every structural change: unclosed tags, SVG marker references, and text
  that overflows its panel. Large in-place edits to this file have broken tag nesting before.

## Child DOX Index

This project has no child `AGENTS.md` files. `architecture.html` is a single document at the repository
root and is governed by this file.

Create a child `AGENTS.md` when a durable boundary appears — the CLI package, the manager, the gateway, a
shared library, or a docs subtree — and add it to the index above.
