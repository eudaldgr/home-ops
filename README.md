<div align="center">

# Eudald's Homelab

_GitOps-managed home infrastructure powered by Talos, Kubernetes, OpenTofu, Flux CD, and Cilium._

</div>

<div align="center">

[![Status-Page](https://kromgo.eudald.gr/badges/home_status_page)](https://status.eudald.gr)&nbsp;&nbsp;
[![Alertmanager](https://kromgo.eudald.gr/badges/home_heartbeat)](https://cadi-gatus.eudald.gr)

</div>

<div align="center">

[![Talos](https://kromgo.eudald.gr/badges/talos_version)](https://talos.dev)&nbsp;&nbsp;
[![Kubernetes](https://kromgo.eudald.gr/badges/kubernetes_version)](https://kubernetes.io)&nbsp;&nbsp;
[![Flux](https://kromgo.eudald.gr/badges/flux_version)](https://fluxcd.io)&nbsp;&nbsp;
![Renovate](https://img.shields.io/badge/enabled-blue?logo=renovate)

</div>

<div align="center">

[![Age](https://kromgo.eudald.gr/badges/cluster_birth_age)](https://github.com/home-operations/kromgo)&nbsp;&nbsp;
[![Uptime](https://kromgo.eudald.gr/badges/cluster_uptime_age)](https://github.com/home-operations/kromgo)&nbsp;&nbsp;
[![Nodes](https://kromgo.eudald.gr/badges/cluster_node_count)](https://github.com/home-operations/kromgo)&nbsp;&nbsp;
[![Pods](https://kromgo.eudald.gr/badges/cluster_pod_count)](https://github.com/home-operations/kromgo)&nbsp;&nbsp;
[![CPU](https://kromgo.eudald.gr/badges/cluster_cpu_usage)](https://github.com/home-operations/kromgo)&nbsp;&nbsp;
[![Memory](https://kromgo.eudald.gr/badges/cluster_memory_usage)](https://github.com/home-operations/kromgo)&nbsp;&nbsp;
[![UPS Load](https://kromgo.eudald.gr/badges/ups_load)](https://github.com/home-operations/kromgo)&nbsp;&nbsp;
[![Alerts](https://kromgo.eudald.gr/badges/cluster_alert_count)](https://github.com/home-operations/kromgo)

</div>

---

## Overview

This repository is the source of truth for my homelab: bare-metal Talos nodes,
bootstrap, Kubernetes platform controllers, applications, secrets, backups, DNS,
and selected services that still run outside the cluster.

The operating model is deliberately boring:

1. Talos runs Kubernetes directly on bare metal, configured from `talos/`.
1. A one-shot helmfile bootstrap (`bootstrap/`) installs the minimum Flux needs.
1. Flux CD reconciles the desired state from `kubernetes/`.
1. Controllers handle networking, storage, certificates, databases, policy, and backups.

---

## Kubernetes

The active cluster is a three-node, control-plane-only Talos Kubernetes
deployment on bare metal.

| Node    | Hardware                 | CPU       | RAM  | System / Miroir | Ceph OSD | NIC                 |
| ------- | ------------------------ | --------- | ---- | --------------- | -------- | ------------------- |
| `k8s-0` | Dell OptiPlex 3080 Micro | i5-10500T | 64GB | NVMe            | SATA SSD | RTL8125B 2.5GbE     |
| `k8s-1` | HP ProDesk 400 G5 Mini   | i5-9500T  | 32GB | NVMe            | SATA SSD | Intel I226-V 2.5GbE |
| `k8s-2` | Dell OptiPlex 3050 Micro | i5-7500T  | 24GB | NVMe            | SATA SSD | RTL8125B 2.5GbE     |

The Kubernetes API is exposed at `https://k8s.eudald.gr:6443`. `mise` exports
`KUBECONFIG` and `TALOSCONFIG` pointing at the `kubeconfig` and `talosconfig`
files in the repository root:

```bash
kubectl get nodes -o wide
flux get kustomizations -A
```

### Core Components

| Area             | Components                                                                    |
| ---------------- | ----------------------------------------------------------------------------- |
| GitOps           | Flux Operator, Flux CD, Kustomize, Helm Controller                            |
| Networking       | Cilium (kube-proxy replacement, LB IPAM, BGP), Multus, Spegel                 |
| Ingress and DNS  | Envoy Gateway (Gateway API), ExternalDNS for OPNsense and Cloudflare, towonel |
| Auth and secrets | Pocket ID (OIDC/SSO), External Secrets + 1Password Connect, cert-manager      |
| Storage          | Rook Ceph on SATA SSDs, Miroir (DRBD) on NVMe, NFS from the ds920plus         |
| Databases        | CloudNativePG operator, Dragonfly operator                                    |
| Backups          | Kopiur volume snapshots                                                       |
| Observability    | kube-prometheus-stack, Grafana operator, VictoriaLogs, Gatus, Kromgo          |
| Upgrades         | tuppr (Talos and Kubernetes upgrades), Renovate                               |

---

## GitOps

Flux bootstraps from `kubernetes/flux/cluster` and reconciles the platform and
applications under `kubernetes/apps`. Each deployable component owns a leaf
`Kustomization` and its manifests.

```bash
flux get sources git -A
flux get kustomizations -A
kustomize build kubernetes/apps/network/omada/app
```

```mermaid
flowchart TD
  repo["Git repository"] --> flux["Flux CD"]
  flux --> applications["kubernetes/apps"]
  applications --> platform["Platform controllers"]
  applications --> workloads["User-facing workloads"]
  platform --> cluster["Talos Kubernetes"]
  workloads --> cluster
```

---

## Infrastructure

The cluster is built in three steps, all driven by `just`:

| Step      | Path          | Responsibility                                                                                       |
| --------- | ------------- | ---------------------------------------------------------------------------------------------------- |
| Talos     | `talos/`      | Machine config rendered per node, secrets resolved from 1Password (`op inject`)                      |
| Bootstrap | `bootstrap/`  | Talos apply + etcd bootstrap, CRDs, Cilium, CoreDNS, Spegel, cert-manager, External Secrets and Flux |
| GitOps    | `kubernetes/` | Everything else, reconciled by Flux                                                                  |

```bash
just talos download-image v1.14.2   # metal ISO for the shared schematic
just talos apply-node k8s-0         # render and apply a node config
just bootstrap cluster              # bootstrap Talos, Kubernetes and Flux end to end
```

Additional infrastructure lives in:

- `tofu/backblaze/` for backup buckets.
- `tofu/hetzner/cadi/` for the `cadi` Hetzner VPS.
- `ansible/cadi/` for bootstrapping `cadi`: Docker, SSH hardening, UFW, country blocks and doco-cd.
- `docker/cadi/` for the edge stack on `cadi` (CrowdSec, towonel, Caddy, Gatus), deployed with doco-cd.
- `docker/ds920plus/` for NAS workloads (Garage, Zot, exporters, towonel agent), deployed with doco-cd.

---

## Repository

```text
.
├── ansible/      # Host preparation for the cadi VPS
├── bootstrap/    # Helmfile and Kustomize used once to bootstrap the cluster
├── docker/       # Compose stacks for cadi and ds920plus
├── coreboot/     # Coreboot firmware work
├── kubernetes/   # Kubernetes desired state reconciled by Flux CD
├── talos/        # Talos schematic, machine config templates and node patches
└── tofu/         # OpenTofu stacks for Backblaze and Hetzner
```

---

## Operations

Useful read-only checks:

```bash
flux get kustomizations -A
kubectl get gateway,httproute -A -o wide
kubectl get storageclass,pvc -A
kubectl get snapshotschedules.kopiur.home-operations.com -A
talosctl health
```

Talos configs are validated before applying them:

```bash
just talos render-config k8s-0 | talosctl validate --strict --mode metal --config /dev/stdin
```

---

## Safety

This repository controls real infrastructure.

- Prefer GitOps over manual cluster mutation.
- Do not commit plaintext secrets, kubeconfigs, private keys, tfstate, or local environment files.
- Keep secrets in 1Password and reference them (ExternalSecret, `op://`); never inline them in manifests or templates.

---

## Inspiration

The layout is inspired by the home-operations community, especially repositories
that keep infrastructure understandable by making the current state visible at a
glance. The implementation here is tailored to this cluster's actual Talos,
Cilium, Rook Ceph, Miroir, Flux CD, Kopiur, and Compose setup.
