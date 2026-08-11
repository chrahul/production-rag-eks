# Platform Runbook — Kubernetes Node Replacement

**Document Class:** Platform runbook
**Classification:** Internal
**Department:** Platform Engineering
**Region:** India
**Customer:** None

This runbook describes the standard procedure for replacing an unhealthy Kubernetes worker node in the engineering platform. Before removing a node, confirm that the node is reporting sustained health failures and that the affected workloads have sufficient replicas elsewhere in the cluster.

First, cordon the node so that new workloads are not scheduled on it. Then drain the node using the approved disruption settings. Verify that critical platform services remain healthy and that replacement capacity is available before terminating the underlying instance.

After the replacement node joins the cluster, verify node readiness, workload scheduling, application health checks, and monitoring signals. The incident or change record should contain the node identifier, reason for replacement, and validation results.

This document is intended for Platform Engineering and SRE personnel. It should not be distributed outside the engineering organization.
