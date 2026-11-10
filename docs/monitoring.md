# Monitoring

This service is instrumented for **Datadog APM** using `ddtrace`. The Kubernetes
manifests wire the tracer to a cluster-level Datadog Agent and enable Datadog's
recommended [unified service tagging](https://docs.datadoghq.com/getting_started/tagging/unified_service_tagging/).

## What you get

- **APM traces** for every request, with spans for the FastAPI handlers and the
  Postgres/Redis calls they make.
- **Log–trace correlation** is **off by default** (`DD_LOGS_INJECTION=false`),
  because this deployment does not ship logs to Datadog. When enabled, ddtrace
  injects `dd.trace_id`/`dd.span_id` into log records so a log line links to its
  trace. The log formatter surfaces these fields whenever they are present, so
  re-enabling is just a ConfigMap flip (see below) with no code change.
- **Error tracking**: traces carry exception info, surfaced in the APM error view.
- **Runtime metrics** (`DD_RUNTIME_METRICS_ENABLED=true`): GC, memory, and
  thread metrics for the Python process, sent over DogStatsD (UDP 8125).
- **Profiling** is available but **off by default** (`DD_PROFILING_ENABLED=false`).

## Cluster-agent assumption

**This repo does not install the Datadog Agent.** It assumes a cluster-level
**Datadog Agent DaemonSet** already runs on every node (installed via the
Datadog Helm chart or the Datadog Operator, with a valid API key supplied out of
band). The application pods send traces and metrics to the agent **on their own
node**, reached through the node host IP:

```
DD_AGENT_HOST = status.hostIP   # the pod's node, via the downward API
DD_TRACE_AGENT_PORT = 8126      # APM trace intake
DogStatsD = <host IP>:8125/UDP  # runtime metrics
```

No Datadog API key or account ID lives in this repo. Those belong to the
cluster agent's own Secret, managed by the platform team.

## Env var contract

The application (`ddtrace`) reads these variables. The manifests supply them:

| Variable                     | Value                                  | Source in cluster                                     |
| ---------------------------- | -------------------------------------- | ----------------------------------------------------- |
| `DD_SERVICE`                 | `k8s-fastapi-starter`                  | pod label `tags.datadoghq.com/service` via `fieldRef` |
| `DD_ENV`                     | `dev` / `prod`                         | pod label `tags.datadoghq.com/env` via `fieldRef`     |
| `DD_VERSION`                 | image tag (placeholder `0.0.0`)        | pod label `tags.datadoghq.com/version` via `fieldRef` |
| `DD_AGENT_HOST`              | node host IP                           | `status.hostIP` via `fieldRef`                        |
| `DD_TRACE_AGENT_PORT`        | `8126`                                 | ConfigMap `app-config`                                |
| `DD_TRACE_ENABLED`           | `true` (API) / `false` (migration Job) | ConfigMap, overridden on the Job                      |
| `DD_LOGS_INJECTION`          | `false` (logs not shipped to Datadog)  | ConfigMap `app-config`                                |
| `DD_RUNTIME_METRICS_ENABLED` | `true`                                 | ConfigMap `app-config`                                |
| `DD_PROFILING_ENABLED`       | `false`                                | ConfigMap `app-config`                                |

Non-secret flags and the port live in the ConfigMap (`deploy/k8s/base/configmap.yaml`).
The service/env/version values come from the pod's own labels, not the ConfigMap,
so one set of labels drives both Datadog's tagging and the env vars. `DATABASE_URL`
and `REDIS_URL` stay in the SOPS-encrypted Secret and are unchanged.

## Unified service tagging

The Deployment's **pod template** carries the three unified-tag labels:

```yaml
tags.datadoghq.com/env: "local"     # overlays patch this to dev / prod
tags.datadoghq.com/service: k8s-fastapi-starter
tags.datadoghq.com/version: "0.0.0" # CD patches this to the image tag
```

These are the single source of truth: the container reads them back into
`DD_ENV`/`DD_SERVICE`/`DD_VERSION` with `fieldRef`, and the Datadog Agent uses
the same labels to tag the pod's logs and metrics. Env and service, version, and
APM data therefore all agree.

The dev overlay patches the `env` label to `dev`; the prod overlay patches it to
`prod`.

## How version is wired at deploy

`tags.datadoghq.com/version` (and `app.kubernetes.io/version`) are a placeholder
`0.0.0` in git. When selecting an image for an overlay, update both labels to
match its tag so `DD_VERSION` identifies the deployed artifact. The template
does not automate image selection or version-label updates; until configured,
`DD_VERSION` reports `0.0.0`. See [Deployment](deployment.md#open-items).

## Admission Controller (optional)

The pod template sets the annotation:

```yaml
admission.datadoghq.com/enabled: "true"
```

If the [Datadog Admission Controller](https://docs.datadoghq.com/containers/cluster_agent/admission_controller/)
is enabled on the cluster, it mutates the pod to inject the agent host and
standard config automatically. The annotation is **harmless when the controller
is absent**, so it is safe to leave on. It is optional and cluster-dependent.

## NetworkPolicy

The base NetworkPolicy allows egress to the Datadog Agent on the node:

- `8126/TCP` — APM trace intake
- `8125/UDP` — DogStatsD (runtime metrics)

These are selected by port only (like the Postgres/Redis rules), because the
destination is the pod's own node host IP, which NetworkPolicy cannot target
directly. Tighten per environment with `ipBlock`/`podSelector` if the platform
allows.

## Enabling / disabling tracing

- **Disable for the whole service:** set `DD_TRACE_ENABLED: "false"` in
  `deploy/k8s/base/configmap.yaml` (or patch it in an overlay). The app still
  runs; it just stops sending traces.
- **The migration Job is already opted out.** `deploy/k8s/base/migration-job.yaml`
  sets `DD_TRACE_ENABLED: "false"` with an explicit container env, which wins
  over the ConfigMap value, so short-lived migration pods produce no APM noise.
- **Runtime metrics / profiling:** toggle `DD_RUNTIME_METRICS_ENABLED` and
  `DD_PROFILING_ENABLED` in the ConfigMap.
- **Log–trace correlation:** off by default (`DD_LOGS_INJECTION: "false"`).
  Set it to `"true"` in the ConfigMap if you start shipping container logs to
  Datadog and want log lines to link to their traces. No code change is needed —
  the log formatter already surfaces the `dd.*` fields whenever ddtrace injects
  them.

## Verify the manifests

```bash
kubectl kustomize deploy/k8s/overlays/dev
kubectl kustomize deploy/k8s/overlays/prod
uv run poe k8s-check   # both overlays, kubeconform, kube-linter
```
