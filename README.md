# Whonix ARM64 on Apple Silicon / UTM

Experimental build tooling, compatibility patches, and validation for running
Whonix Gateway and Workstation as native ARM64 virtual machines on Apple Silicon.
CPU virtualization uses QEMU/HVF through UTM.

This is an independent development project, not an official Whonix release.
It is intended for developers and testers. The recorded build uses Linux ARM64
Tor Browser **16.0a11 alpha**, lacks Tirdad, and has incomplete interactive
validation after its Kloak repair. It is not ready for sensitive anonymity use.
Native GPU acceleration and performance parity with macOS apps are unproven.

## Status

The 2026-09-17 development build booted both graphical desktops on an Apple M5
with UTM 4.7.5. Listed Gateway-off network probes failed closed; Tor Browser
confirmed a Tor connection. An additive ARM64 syscall fix stopped Kloak's
observed restart loop. These are bounded observations, not a complete anonymity
audit. See [validation](VALIDATION.md) and the [security review](security_best_practices_report.md).

The intended hardware range is **Apple Silicon M1–M5**. Runtime validation
currently covers **M5 only**; compatibility with M1–M4 is expected from the
shared ARM64 architecture and remains unverified. Other hardware configurations
and macOS/UTM versions need their own validation.

The retained resource profile is **2 CPUs / 2 GiB for Gateway** and
**4 CPUs / 4 GiB for Workstation**. Repeated headless measurements found lower
idle CPU use after the Kloak fix and support retaining four Workstation CPUs.
[Performance results](PERFORMANCE.md) include the measurements and limitations.

## Start here

The prepared release candidate is **v0.1.0-alpha.1**, a source-only developer
preview. [Release notes](release/NOTES-v0.1.0-alpha.1.md) describe its contents,
checksum verification, and known limitations. Preparation does not mean that
the candidate has been published or passed hosted CI.

The repository contains source, patches, tests, build provenance, and compact
benchmark evidence. **It does not contain VM images, ready-to-run downloads, a
builder image, SSH keys, browser profiles, or upstream source checkouts.**
Building still requires manual setup and locally built packages.

- [Build and setup guide](docs/BUILDING.md): prerequisites, inputs, patch order,
  and preparation of new UTM bundles.
- [Review the changes](review/README.md): diffs against pinned upstream source,
  guest hooks, and the full published source changeset.
- [Contributing](CONTRIBUTING.md): run the tests and propose focused changes.
- [Security policy](SECURITY.md): scope, reporting, and known limitations.
- [Build provenance](artifacts/BUILD-PROVENANCE.md): source commits and hashes.
- [Recorded build notes](docs/BUILD-NOTES.md): detailed compatibility decisions.
- [Browser benchmark](benchmarks/README.md): isolated reproduction protocol.
- [Architecture comparison](benchmarks/ARCHITECTURE.md): native ARM64/HVF versus
  emulated x86-64/TCG using the same local browser workload.

To work on the source without starting a VM:

```sh
git clone https://github.com/Mirai8888/whonix-apple-silicon.git
cd whonix-apple-silicon
/usr/bin/python3 -m unittest discover -s tests -v
```

Tests use temporary fixtures and mocked VM commands. On Apple Silicon macOS,
install Homebrew `coreutils` before running the suite; see
[test requirements](CONTRIBUTING.md#tests). A Linux
[CI workflow template](ci/fixture-tests.yml) is included; hosted Actions are not
configured for this preview.

## Using prepared VMs

After following the build guide, place the prepared, stopped bundles in
`artifacts/utm/` and register those exact bundles with UTM. Keep directories
holding guest state private to your account. Then:

```sh
./scripts/whonix-status.sh
./scripts/start-whonix.sh
```

Starting opens guest consoles. `--hide` only hides UTM's library window; it is
not a background mode. Start Gateway before Workstation, shut down Workstation
before Gateway, and restart Workstation after restarting Gateway so its internal
socket reconnects. The [build guide](docs/BUILDING.md) covers required paths and
checks; a fresh Git clone alone cannot launch guests.

## License and upstream projects

Original project work is licensed under **GPL-3.0-or-later**; see [LICENSE](LICENSE).
Included upstream patches and an adapted test fixture retain their GPL/AGPL
terms. [Third-party notices](THIRD_PARTY_NOTICES.md) specify their scope and
attribution. VM images and external dependencies have their own licenses; no
images are distributed here. Project names identify compatibility and do not
imply endorsement.

Maintainers: see [source publication notes](docs/PUBLISHING.md) before publishing
new source or distributing images. The
[reviewed file list](release/source-files.txt) and
[packaging command](scripts/prepare-source-release.py) produce a source archive
from a committed revision without including local state or Git history.

Upstream: [Whonix](https://www.whonix.org/),
[derivative-maker](https://github.com/Whonix/derivative-maker),
[UTM](https://github.com/utmapp/UTM), and
[Tor Browser](https://www.torproject.org/).
