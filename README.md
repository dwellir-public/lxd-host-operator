# LXD host operator

A Juju machine charm for observability of an existing LXD host or cluster.
It manages native metrics integrations, LXD log forwarding, cluster status and
read-only inventory access. It does not install LXD or bootstrap a cluster.

- [Architecture](docs/charm-architecture.md)
- [Development and validation](DEVELOPING.md)
- [NetBox inventory](docs/netbox-inventory.md)
- [Optional per-container network caps and proposed generic alerts](docs/operations/container-network-limits.md)

The network-cap collector is disabled by default. Its draft alert rules are
examples only; deploying the charm does not install those alerts.
