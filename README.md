# pg-dbaas-ha-pitr

**Your own PostgreSQL-as-a-service:** one command creates a database, a machine dying doesn't take it
down, every change is backed up to object storage within seconds, and you can get it back to any minute of
the past two weeks.

This file is the plain-language version. The full design — every decision, why it was made, and what it
costs — lives in [`architecture.html`](./architecture.html). Read this one first; when you want the
reasoning, that one has it.

---

## The whole system in one picture

```
   your app (MedusaJS)
        │   postgres://…@db-gateway:5432/ecom
        ▼
 ┌──────────────────────────────────────┐
 │  gateway container  (in YOUR compose) │   PgBouncer: pools connections
 │  db-gateway                          │   HAProxy:   finds the writer
 └───────────────────┬──────────────────┘
                     │  private network (Cloudflare Mesh)
     ┌───────────────┼───────────────┐
     ▼               ▼               ▼
 ┌─────────┐     ┌─────────┐     ┌─────────┐
 │ Postgres │     │ Postgres │     │ Postgres │   three Pods, one per machine
 │ WRITER   │────▶│ sync     │     │ spare    │   exactly one accepts writes
 └────┬────┘     └────┬────┘     └────┬────┘
      │               │               │
      └───────────────┴───────┬───────┘
                              ▼
                    R2 object storage
                    every change as it happens,
                    a full copy nightly,
                    restored automatically to prove it works
```

Three ideas:

1. **Exactly one Pod accepts writes**, and the machines agree on which one through a tiny shared registry
   (etcd) rather than by talking to each other. Nobody votes except this.
2. **Your app never knows where the database is.** It talks to `db-gateway` in its own compose file; the
   gateway works out the rest every 30 seconds.
3. **Redundancy and backups are different tools.** Replication saves you from a dead machine. Backups save
   you from yourself — a bad migration, a dropped table, a ransomware Tuesday.

---

## How many machines do I need?

| Machines | You survive | Use this when |
|---|---|---|
| **3** | losing **one** machine | Starting out. This works fine and is a real HA setup. |
| **5** *(recommended for money)* | losing **two** machines at once | Production. Two of yours are in one region, so a provider/account problem can take two at once. |

The 5th machine is a cheap VPS whose only job is holding a vote — it runs no databases and no backups.
Growing from 3 to 5 later is a live, no-downtime operation.

> Why not 4? Because quorum maths: 4 machines still require 3 alive, which survives the same **one** loss
> that 3 does. 5 requires 3, which survives **two**. Adding a fourth buys you nothing — the details are in
> `architecture.html` §15.

---

## Getting started

Everything is driven from one CLI on any laptop (`pgdbaas`). The laptop is only needed while you're
running a command — close it afterwards and everything keeps running.

```bash
pgdbaas setup            # shows the 9 steps, resumes where you stopped
pgdbaas setup status     # what's configured, what's missing, and the command to fix each
pgdbaas setup step 3     # run one step
```

The nine steps, briefly:

| # | Step | What it does |
|---|---|---|
| 1 | Cloudflare network | checks 4 dashboard settings that are off by default |
| 2 | primary backup storage | R2 bucket + API token, then a real write/read/delete test |
| 3 | off-account dumps | a bucket in a **second** Cloudflare account (so one account loss isn't total) |
| 4 | third copy | optional, you can decline and it stops asking |
| 5 | encryption keys | public key goes to the databases, **private key is printed once** |
| 6 | hosts | `node add` on each machine: enrols, installs Docker, checks ports, issues certificates |
| 7 | fleet health | `doctor` — MTU, clocks, free ports, quorum, storage |
| 8 | first cluster | `cluster create` — backups are switched on *by* this step |
| 9 | app + first drill | hand it to your app, then prove you can restore |

```bash
pgdbaas cluster create ecom-db --nodes n1,n2,n3 --pg 16   # your database
pgdbaas status                                            # the one screen: everything
pgdbaas gateway emit ecom-db                              # writes ./ecom-db.env for your app
pgdbaas verify ecom-db --full                             # restores it for real → no longer PROVISIONAL
```

---

## Wiring it into your MedusaJS compose file

Yes — this is the whole integration. Add one service, point Medusa at it:

```yaml
services:
  db-gateway:
    image: ghcr.io/you/pgdbaas-gateway:1
    env_file: ./ecom-db.env          # written by: pgdbaas gateway emit ecom-db
    expose:
      - "5432"                       # one port: /ecom writes, /ecom_ro reads
    restart: always

  medusa:
    environment:
      DATABASE_URL: postgres://ecom:***@db-gateway:5432/ecom
    depends_on: [db-gateway]
```

Three conditions — all three matter:

1. **The machine running this compose file must be enrolled** (`pgdbaas node add …`). The gateway reaches
   the databases over the private network; a machine that isn't enrolled can't see them.
2. **Use the service name `db-gateway`, not `127.0.0.1`.** A container can't reach a sibling container
   through localhost.
3. **`restart: always`.** It's your app's only path to data.

Optional, and worth it — send catalogue/listing reads to your replicas by changing only the database name:

```yaml
DATABASE_URL: postgres://…@db-gateway:5432/ecom_ro   # reads on replicas
# checkout, orders and anything read-your-writes stays on /ecom
```

---

## Day-to-day

```bash
# look
pgdbaas status                     # nodes, clusters, roles, lag, backup health — one screen
pgdbaas topology ecom-db           # who is writer, who is the sync standby, ports, lag
pgdbaas psql ecom-db               # a shell on whatever the writer is
pgdbaas doctor                     # mesh, MTU, clocks, ports, quorum, storage, certificates

# backups
pgdbaas backup list ecom-db        # what exists (reads the bucket, works even if the fleet is down)
pgdbaas backup history ecom-db     # every restore point you can land on
pgdbaas backup now ecom-db         # take one now
pgdbaas backup prune ecom-db       # dry run by default; deletes nothing

# point-in-time recovery — the default DESTROYS NOTHING
pgdbaas restore ecom-db --to "1 hour ago" --as ecom-recovered
pgdbaas gateway point ecom-recovered    # move the app onto the recovered copy
pgdbaas gateway point ecom-db           # ...or back again
pgdbaas cluster delete ecom-recovered   # when you're sure

# planned changes
pgdbaas switchover ecom-db --to ecom-2  # hand over the writer on purpose
pgdbaas cluster move ecom-2 --to n1     # rebuild a database onto another machine, from the backup
```

`pgdbaas setup status` is always the answer to "what have I forgotten?" — it derives every line by
looking, so it can't go stale, and each unfinished line prints the command that finishes it.

---

## When something goes wrong

| What happened | What you'll see | What actually happens |
|---|---|---|
| A machine dies | app connections drop | ~40 seconds later a replica is promoted automatically; new connections work. Your pool must retry. |
| Two machines die | *(3-machine setup)* writes stop | Data is safe, clusters go read-only. On 5 machines you keep writing. |
| A bad migration / dropped table | you, wincing | `restore --to "…" --as recovered` → new cluster, old one untouched. |
| Backups going stale | `status` goes **red** | "last WAL archived: 6h ago — STUCK", plus `pgdbaas doctor` naming the cause. |
| `status` says `PROVISIONAL` | no surprise yet | It's never been restored successfully. Run `verify --full`; only then trust it. |

**A backup you've never restored is a hypothesis.** That's why verification runs after every full backup
and the restore drill runs monthly — and it's free, because R2 charges nothing for egress.

---

## The numbers that matter

| Property | Value | Where it comes from |
|---|---|---|
| Data loss, acknowledged writes | **zero** | writes wait for a second machine to flush (`synchronous_mode_strict`) |
| Data loss, total-loss scenario | **≤ 60 seconds** | continuous WAL archiving |
| Failover time | **~40 seconds** | leader lock expiry (30s) + health checks (~6s) |
| Restore time | **measured by the drill** | the only number here that isn't a setting |
| Backup verification | **after every full, automatically** | a real restore into a scratch Pod |
| PITR window | **14 days** (configurable) | 7 fulls + 14 days of WAL |

---

## Two things the database can't do for you

This project owns the database layer only — your application owns itself. But its guarantees depend on
two lines of config on your side:

1. **Retry connections.** Every failover drops every open connection. A pool without connect-retry turns a
   40-second database event into a down storefront.
2. **One idempotency key per checkout**, reused on every retry. Strict synchronous mode is durable but not
   atomic: your app can be told "failed" just as the commit lands — and a retry is how a store ends up
   charging twice.

---

## Vocabulary

| Term | Plain meaning |
|---|---|
| **Pod** | One database: PostgreSQL + its supervisor + its own disk. Three of them make a cluster. |
| **writer / primary** | The one Pod accepting writes. Exactly one exists at a time. |
| **replica / sync standby** | An exact copy. Your writes wait for the sync standby to store them. |
| **DCS / etcd** | A tiny shared registry saying who the writer is. Holds no data. |
| **WAL** | PostgreSQL's change log, in numbered segments. Replicas stream it; backups archive it. |
| **PITR** | Point-in-time recovery — back to *any moment*, not just the last backup. |
| **failover** | Automatic promotion of a replica when the writer's machine dies. ~40 seconds, no human. |

---

## Where to read more

Everything is in [`architecture.html`](./architecture.html) (open it in any browser, no build needed):

| You want to understand… | Read |
|---|---|
| the whole workflow, start to finish | §00 Start here |
| every command, grouped by job | Command sets (right after §00) |
| how the gateway finds the writer | §04, §06 |
| networking, ports, MTU | §05, §07 |
| split-brain and why it can't happen | §08 |
| synchronous replication, `nosync`, the 3→2→1 curve | §09 |
| bootstrapping and etcd ordering | §10 |
| what breaks and what should happen | §11 Failure drills |
| PgBouncer + HAProxy config | §12 |
| backups, restore, verification, R2 layout | §13 |
| what's decided vs. what's left to build | §14, §15 |
