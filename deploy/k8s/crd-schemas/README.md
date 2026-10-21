# Vendored CRD JSON schemas

These JSON schemas let `kubeconform` validate the Custom Resources this repo
ships — Argo CD `Application` and External Secrets Operator `ExternalSecret` /
`ClusterSecretStore` — **offline and deterministically**, with no dependency on
a remote schema catalog at validation time. They are fed to `kubeconform` via
`-schema-location` from the `k8s-check` poe task (see `pyproject.toml`).

## Files and apiVersions

| File                         | Kind                 | apiVersion               |
| ---------------------------- | -------------------- | ------------------------ |
| `ExternalSecret_v1.json`     | `ExternalSecret`     | `external-secrets.io/v1` |
| `ClusterSecretStore_v1.json` | `ClusterSecretStore` | `external-secrets.io/v1` |
| `Application_v1alpha1.json`  | `Application`        | `argoproj.io/v1alpha1`   |

> ESO's `external-secrets.io/v1` API group was promoted in ESO **v0.10.0**.
> These `_v1.json` filenames, the manifest `apiVersion` values, and the
> documented ESO version floor in `docs/deployment.md` must stay in agreement;
> an older ESO serves only `v1beta1` and rejects these manifests.

## Provenance

Sourced once from the [datreeio CRDs-catalog](https://github.com/datreeio/CRDs-catalog)
(the published `openAPIV3Schema` for each CRD) and committed here:

- `external-secrets.io/externalsecret_v1.json`
- `external-secrets.io/clustersecretstore_v1.json`
- `argoproj.io/application_v1alpha1.json`

To refresh, re-download the matching file from that catalog for the CRD version
in use and commit the result.

## Filename / schema-location caveat (API group intentionally omitted)

The `k8s-check` task points `kubeconform` at these files with the template:

```
deploy/k8s/crd-schemas/{{ .ResourceKind }}_{{ .ResourceAPIVersion }}.json
```

This template **deliberately omits the API group** — a schema is resolved by
`kind` + version only (e.g. `ExternalSecret_v1.json`). That is safe here because
each kind (`Application`, `ExternalSecret`, `ClusterSecretStore`) is unique
across groups, so kind+version names the file unambiguously. `.ResourceAPIVersion`
resolves to the **version segment only** (`v1`, `v1alpha1`), which is why the
files are named `_v1` / `_v1alpha1` rather than carrying the full group.

> **Filename casing.** The template uses `{{ .ResourceKind }}` **verbatim**, so
> the vendored files carry the canonical kind casing (`ExternalSecret_v1.json`,
> not `externalsecret_v1.json`). The pinned `kubeconform` **0.8.0** provides no
> `toLower`/`lower` template function (`function "toLower" not defined`), so the
> lowercase-template form the datreeio catalog documents cannot be used here;
> matching the canonical casing in the filenames keeps resolution deterministic
> on case-sensitive CI runners. If the catalog's lowercased copies are
> re-downloaded, rename them to the canonical kind casing.

If a future CRD introduces a kind that collides with one of these across a
different API group, the group must be re-added to both the filenames and the
`-schema-location` template.
