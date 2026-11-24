# Security

This project is a development prototype. The current Phase 2 backend is maintained; there is no
production support commitment. Use synthetic records while developing. Refunds are simulated.

Report suspected vulnerabilities through the repository's **Security → Report a vulnerability** flow:
[private report](https://github.com/ellacmd/canopy-ai/security/advisories/new).
Do not put exploit details, tokens, passwords, database URLs, or customer data in a public issue.
If private reporting is unavailable, request a private contact route without publishing the details.

Include affected commit/version, reproduction steps using synthetic data, expected impact, and proposed
mitigation if known. No response-time guarantee is made.

For an exposed credential, revoke or rotate it with the issuing provider immediately, review access
and logs, and coordinate cleanup of Git history and artifacts. Secret scanners reduce accidental leaks;
they do not prove that every credential or vulnerability has been detected.
