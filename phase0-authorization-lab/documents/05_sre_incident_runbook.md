# SRE Runbook — Production API Latency

**Document Class:** SRE runbook
**Classification:** Internal
**Department:** SRE
**Region:** Global
**Customer:** None

Use this runbook when the production API latency alert remains above the defined service objective for more than five minutes.

Start by checking the service dashboard for request rate, error rate, CPU, memory, and downstream dependency latency. Determine whether the increase is isolated to one service or affects multiple services. Check recent deployments and infrastructure changes before making corrective changes.

If the affected service is overloaded, verify that horizontal scaling is functioning and that the cluster has sufficient capacity. If a downstream dependency is responsible, follow the corresponding dependency runbook rather than repeatedly restarting the application.

All production changes must be recorded in the incident channel and change record. After recovery, capture the timeline, contributing factors, and follow-up actions.
