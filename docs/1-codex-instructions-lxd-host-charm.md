# Instructions on minimal lxd host charm

We will create a lxd-host charm which only supports setting logging and metrics via juju relatations for already installed lxd:s. It should however be "cluster aware" such as to deliver proper monitoring and metrics for the cluster as whole. It should use the modern style metrics and be possible to relate to loki-vm, alloy-vm (for both metrics and logs) and loki-loadbalancer-vm and providing juju topology.

  - The charm should not install lxd, it should only configure and monitor already installed lxd.
  - Present the current snap version for each unit as part of an ActiveStatus. Present the leader lxd version for workload version.

# Use the skill for creating new charms
There is a skill that sets the implementation constraints
  
  - charm-ai-development

follow them.

# Initial lxc environment for fast reuse.
There are provided three fresh installed ubuntu24 nodes with passwordless sudo with my private key (/home/erik/.ssh/id_ed25519) and snapshots which we can use for development. When things break, reuse snapshots for fast turnaround.

  - lxd-node1/reusable-20260324-170013
  - lxd-node2/reusable-20260324-170013
  - lxd-node3/reusable-20260324-170013

# Existing monitoring stack

There is a juju model in the localhost-localhost controller: charmhub-stack-r2-20260317-193315 which can be used for offering loki-loadbalancer-vm and mimir-vm relations. Set up offers in that model as needed and consume them in future models.

Add the juju machines as manual machines to test the charm.

# Create an implementation plan in docs

Name the document 2-implementation-and-testing-plan.md