# Security Incident Postmortem — Temporary Credential Exposure

**Document Class:** Security incident report
**Classification:** Restricted
**Department:** Security
**Region:** India
**Customer:** Internal

On 14 June, the security monitoring team detected an exposed temporary credential in an application troubleshooting artifact. The credential had a short lifetime, but it was still considered a security event because the artifact was accessible to a broader engineering audience than intended.

The security team immediately revoked the affected credential and reviewed CloudTrail activity associated with it. No confirmed unauthorized production access was identified during the investigation. The affected artifact was removed from the shared troubleshooting location.

The root cause was an operational debugging process that allowed temporary credentials to appear in diagnostic output. The remediation includes updated logging controls, stronger credential redaction, and an additional review of troubleshooting procedures.

This report contains internal security investigation details. Access is restricted to authorized Security personnel.
