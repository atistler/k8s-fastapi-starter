# TODO

Open follow-ups for the starter. Record agreed architecture changes in
[SPEC.md](SPEC.md) and [architecture decisions](docs/adr/).

- [ ] **Evaluate Colima Kubernetes for local development.** Use
  `colima start --kubernetes` to run the local environment instead of Docker
  Compose. Determine the local manifests and task runner commands needed for
  the API, Postgres, Redis, migrations, image builds, and access from the host.

- [ ] **Revisit the secrets delivery path.** Decrypting SOPS secrets and pushing
  them to AWS Secrets Manager just so Kubernetes External Secrets can retrieve
  them seems heavy handed. Compare simpler approaches and clarify the value of
  each step, including access control, rotation, and the source of truth.

- [ ] **Validate Datadog metrics handling.** Confirm which application and
  runtime metrics are emitted, how they reach the Datadog Agent, and whether
  they appear in Datadog with the expected service, environment, and version
  tags. Verify the relevant configuration end to end in a deployed environment.

- [ ] **Clarify where logs get pushed — follow up with Doug.** Identify the
  collector or forwarding mechanism, destination, retention, and ownership.
  Confirm how application logs are searched and correlated with traces.
