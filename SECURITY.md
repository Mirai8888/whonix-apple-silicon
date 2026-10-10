# Security policy

## Scope and support

This repository is an experimental build-tooling project. There are no supported
stable releases or distributed VM images. Security fixes target `main`; old
development snapshots may not receive backports. A passing test suite does not
establish safe anonymity or validate all upstream dependencies.

The project owns its preparation, build, launch, and benchmark helpers and local
patches. Issues in Whonix, Tor Browser, UTM, QEMU, or Debian should also follow
the affected upstream project's security-reporting process. Do not disclose
unpublished upstream vulnerabilities in this repository's public issues.

## Reporting a vulnerability

Use GitHub's **Security → Report a vulnerability** when available:
[private advisory form](https://github.com/Mirai8888/whonix-apple-silicon/security/advisories/new).
Private reporting availability depends on the repository settings. If the form
is unavailable, use the fallback below.

If the private form is unavailable and you have no existing private maintainer
contact, open an issue titled **Private security contact requested** with no
vulnerability details, affected code, exploit, logs, or personal information.
Wait for a private channel before sending technical details. This fallback
request is public; it is not a place to report the vulnerability itself.

In a private report, include the affected commit, relevant versions, prerequisite
access, minimal reproduction, observed impact, and any proposed fix. Redact
credentials and browsing state. Do not test against another person's host,
Gateway, or Workstation. No response-time SLA or bounty is offered.

## Known limits

- The recorded build uses an ARM64 Tor Browser alpha and lacks Tirdad. Kloak's
  ARM64 startup/device-grabbing fix has headless evidence; full keystroke-timing
  efficacy and post-fix UTM interaction remain unverified.
- The internal loopback socket is unauthenticated. Other local processes can
  attempt to connect or interfere. Trust in the host and owning account is
  required.
- Storage guards enforce POSIX ownership and modes. Custom ACL grants require
  separate review. These guards do not protect against root or compromised code
  running under the owning user.
- VM bundles registered in UTM must be the exact bundles checked by the helpers.
  A matching UUID alone does not bind registration to a filesystem path.
- The build uses development package repositories. The recorded source/package
  hashes are evidence of one build, not a guarantee of current reproducibility
  or a complete dependency-vulnerability audit.

Detailed findings and fixed issues are recorded in the dated
[security review](security_best_practices_report.md). Runtime boundaries are in
[VALIDATION.md](VALIDATION.md).
