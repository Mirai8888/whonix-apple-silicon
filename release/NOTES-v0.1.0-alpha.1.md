# Whonix ARM64 for Apple Silicon — v0.1.0-alpha.1

**Experimental source-only developer preview.** This independent project
provides tooling and patches for building Whonix Gateway and Workstation as
native ARM64 virtual machines on Apple Silicon using UTM/QEMU/HVF. It is not an
official Whonix release or a supported anonymity platform.

## Included

- ARM64 build recipes and compatibility patches for the recorded upstream
  sources, with provenance and third-party license notices.
- UTM preparation and launch helpers that validate the expected network,
  device, storage, and resource configuration.
- An additive ARM64 Kloak syscall compatibility fix.
- Fixture tests and bounded, historical performance and runtime observations.

The source archive contains no VM images, packages, builder, browser profiles,
SSH credentials, local guest state, or Git history. Setup requires the external
inputs and manual steps in [BUILDING.md](../docs/BUILDING.md). There is no
one-command image build or ready-to-run VM download in this preview.

## Compatibility and limitations

Runtime observations date to 2026-09-17 on **Apple M5 / UTM 4.7.5**. M1–M4 are
intended targets but have not been runtime-tested. The retained profile is
2 vCPUs / 2 GiB for Gateway and 4 vCPUs / 4 GiB for Workstation.

The recorded build uses Linux ARM64 Tor Browser **16.0a11 alpha**, lacks Tirdad,
and has incomplete interactive UTM and keystroke-timing validation after the
Kloak repair. The build uses development repositories; historical package
hashes do not establish reproducibility against today's repositories. Existing
local VM archives predate the Kloak fix and UTM preparation and are not release
assets. Do not use this preview for sensitive anonymity needs.

CPU virtualization uses HVF. Native GPU acceleration, native macOS performance
parity, and complete anonymity have not been established. See
[VALIDATION.md](../VALIDATION.md) and [SECURITY.md](../SECURITY.md).

## Verify and try the source

Download the source archive, JSON manifest, and `SHA256SUMS` together. On macOS:

```sh
shasum -a 256 -c SHA256SUMS
tar -xzf whonix-apple-silicon-v0.1.0-alpha.1-source.tar.gz
cd whonix-apple-silicon-v0.1.0-alpha.1
/usr/bin/python3 -m unittest discover -s tests -v
```

Install GNU coreutils for the tests on macOS; see
[CONTRIBUTING.md](../CONTRIBUTING.md#tests). Linux uses Python 3.9+, Bash, and
GNU coreutils. Tests use temporary fixtures and do not start production VMs.
Read [the build guide](../docs/BUILDING.md) before attempting a guest build.

SHA256 checksums detect changed files; they do not authenticate the publisher.
This candidate is unsigned. The JSON manifest records the source commit and
each distributed file's SHA256. Do not infer a passing hosted CI run or runtime
certification from the presence of these files. The maintainer's publication
gates are recorded in [PUBLISHING.md](../docs/PUBLISHING.md).

Original project work is GPL-3.0-or-later. Specifically identified upstream
patches and fixtures retain their GPL/AGPL terms; see
[THIRD_PARTY_NOTICES.md](../THIRD_PARTY_NOTICES.md).
