# Internal Security Architecture Standard — Data Handling

**Document Class:** Security standard
**Classification:** Confidential
**Department:** Security
**Region:** Global
**Customer:** Internal

Applications handling confidential or restricted information must enforce authorization before sensitive data is passed to downstream processing components. Access decisions should be based on the enterprise identity and authorization model rather than on application-local copies of user entitlements.

Sensitive data should have an identifiable owner, classification, retention requirement, and approved processing purpose. Changes to classification or ownership must be propagated through the document lifecycle process so that downstream indexes do not continue using obsolete metadata.

Production services should use short-lived identity mechanisms and avoid embedding long-lived credentials in application configuration. Security-relevant access decisions and administrative changes should be auditable.

This standard is intended for internal engineering and security teams and must not be shared externally.
