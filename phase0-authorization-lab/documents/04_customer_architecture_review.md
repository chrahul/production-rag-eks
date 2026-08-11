# Customer Architecture Review — Project Apollo

**Document Class:** Customer architecture review
**Classification:** Confidential
**Department:** Cloud Architecture
**Region:** India
**Customer:** Apollo Financial Services

Project Apollo is evaluating a migration of its customer-facing application from an on-premises environment to AWS. The proposed target architecture uses private application subnets, managed Kubernetes for selected workloads, centralized logging, and controlled outbound connectivity.

The architecture review identified three major concerns. First, the application requires private connectivity to existing financial systems during the migration period. Second, disaster recovery objectives require a tested secondary environment rather than relying only on backups. Third, access to production workloads must be separated from development and support operations.

The current recommendation is to establish a dedicated landing-zone structure, enforce least-privilege access, and validate the disaster recovery design through a controlled exercise before production migration.

This document contains customer-specific architecture information and must only be accessible to personnel assigned to the Apollo account.
