# Recorded build notes — 2026-09-17

These notes describe the original development workspace and observations on
2026-09-17. VM states and installed versions are historical, not a statement
about a reader's machine. Start with [the build guide](BUILDING.md) for setup.
This is not an official Whonix release.

The Git repository contains scripts, patches, tests, authored notes, build
provenance, and compact benchmark summaries. VM images, SSH keys, browser
profiles, upstream checkouts, packages, and raw logs remain local and are
excluded by `.gitignore`. References to local-only artifacts identify unpublished
evidence from this build. Host helpers locate the workspace
relative to themselves; builder recipes assume the guest account and checkout
under `/home/user/`. Building or launching requires preparing those local inputs.

Keep the workspace and directories containing guest state private to your macOS
account (`chmod 700` on the workspace, `builder/`, `artifacts/`, and `logs/`). VM
disks and APFS clones can contain guest browsing state and private files. Bundle
preparation now restricts the bundle directory to mode `0700`; launch validation
requires that boundary and rejects unsafe ownership, writable paths, and linked
disk/configuration files. The benchmark runner also requires a private `0700`
disk directory and creates `0600` trial disks. See the
[security review](../security_best_practices_report.md) for findings and scope.

## Recorded build

- Upstream: `Whonix/derivative-maker`, tag `18.2.2.7-developers-only`, commit
  `945b966e5fbc3b4ca58cc0eece7b91211190c6a4`.
- The release tag verified against the signing key published by Whonix;
  derivative-maker also verifies upstream commits against its shipped policy.
- Debian 13 ARM64 builder, 4 CPUs, 6 GiB RAM, 100 GiB sparse disk; QEMU/HVF.
- Final guest platform: installed UTM 4.7.5 (build 118).
- Packages: upstream development APT repository, using
  `--remote-derivative-packages true --freshness current`.
- Guest disks: 25 GiB Gateway, 40 GiB Workstation (sparse).

Both ARM64 UTM image builds completed successfully on 2026-09-17. Both guests
reach their graphical desktops, and Workstation reports `aarch64`. A Workstation
HTTP probe reports `IsTor: true`. With Gateway stopped, the same HTTP, raw IPv4
and IPv6 TCP, and DNS probes fail closed. Tor access recovers after restarting
Gateway and then Workstation. Tor Browser also opens its GUI and confirms a
Tor connection on the Tor Project's check page.

The subsequent ARM64 Kloak crash fix is installed in the imported Workstation
disk. Headless validation confirms stable startup, input-device grabbing, and
Tor connectivity. [Validation details](../VALIDATION.md) distinguish this check
from the earlier UTM GUI tests. Workstation is now cleanly stopped to avoid
opening its console while the Mac is in use; Gateway remains running.

Repeated headless trials measured idle CPU falling from 3.79% to 1.06% of one
host core with the Kloak fix, with zero service restarts. Synthetic crypto and
graphics tests also confirm that existing ARM64 acceleration is active.
See [performance results and measurement limits](../PERFORMANCE.md).

A subsequent local Tor Browser comparison retains the 4-vCPU / 4-GiB
Workstation profile. All 16 workload captures match a visually checked PASS
image; two-CPU runs were slower overall but varied substantially. The
[benchmark protocol](../benchmarks/README.md) and
[compact results](../benchmarks/browser-cpu-results.json) are included for review.

The original archives are in `artifacts/`:

- Gateway (local-only: `artifacts/Whonix-Gateway-LXQt.utm.tar.gz`): 1,037,264,658 bytes.
- Workstation (local-only: `artifacts/Whonix-Workstation-LXQt.utm.tar.gz`): 1,376,482,415 bytes.

See [build provenance](../artifacts/BUILD-PROVENANCE.md) for archive SHA256 hashes,
source commits, local packages, and the current validation boundary.
The archives preserve the original build output. The imported bundles with the
applied UTM fixes and resource profile are in `artifacts/utm/`; their disks change
as the guests run.

## Local commands

```sh
cd /path/to/whonix-apple-silicon
./scripts/whonix-status.sh
./scripts/start-whonix.sh
```

The start command validates both imported bundles, starts Gateway, checks its
loopback listener, then starts Workstation. It uses the bundles in
`artifacts/utm/` registered directly with UTM. Shut down Workstation before
Gateway. After restarting Gateway, restart Workstation to reconnect its socket.
The optional `--hide` flag hides UTM's library window only; guest consoles still
open and may take focus. It is not a background mode.

Measure the running UTM guests without opening windows:

```sh
/usr/bin/python3 scripts/measure-host.py --seconds 30 > logs/idle-sample.json
```

The build VM has been cleanly shut down. Build/package logs were copied to
build-evidence.tar.gz (local-only: `logs/build-evidence.tar.gz`). `scripts/build-status.sh`
reports builder status; `scripts/builder-ssh` connects while it is running.
`scripts/start-builder.sh` starts the dedicated headless builder after a clean
shutdown. Its SSH forward listens only on `127.0.0.1:22022`, using the private
key in `builder/keys/`. The Whonix UTM guests run independently of this builder.
`scripts/build-guests.sh` is the build recipe copied into the Debian guest.
The builder launcher requires an owned, private `builder/` directory. Its SSH
helper uses the dedicated key and pinned host key without inheriting personal
SSH configuration, agents, forwarding, or connection sharing.

## Build compatibility fixes

The pinned image creator does not implement `--no-reimage`, although
derivative-maker passes it. `patches/grml-no-reimage.patch` backports that option
from the development fork's commits `b62f8e5` and `f10ef15`. The original pinned
commit remains unchanged; the build explicitly permits the reviewed local edit.
The recipe also sets `USE_DEFAULT_INTERFACES=true` for reproducibility validation;
derivative-maker's `NOINTERFACES=true` still suppresses generic guest networking.

`patches/bootstrap-helper-scripts.patch` fixes stage 3500 to install the pinned
APT wrapper's shell and Python dependencies before using it in the new image.
The original stage copied only the wrapper, which failed before installing packages.

`patches/adduser-uid-defaults.patch` applies after that patch. It makes Debian's
standard `FIRST_UID=1000` and `LAST_UID=59999` defaults explicit when absent.
The current helper validates these values before applying its own defaults,
which otherwise breaks maintenance-account setup. Explicit settings are retained.
The adjustment runs after the pre-dependency packages install `adduser` and
after `dist-upgrade`, immediately before the kernel/maintenance-account package
transaction. A fresh base image does not yet contain `/etc/adduser.conf`.

`patches/dependency-error-nounset.patch` fixes an unset associative-array lookup
in dependency-error reporting, which otherwise interrupts cleanup after an APT
failure.

At build time, the development repository lacked `dist-vm-gui-all:arm64`. It was built
natively from the verified `kicksecure-meta-packages` source at
`a65f75485d72011deb86316edf6afcdad5abd980` using `dpkg-buildpackage --build=any
--no-sign`; lintian passed. `scripts/80-arm64-local-packages` stages the exact
hash-checked `.deb` via the existing post-script mechanism, and the recipe's
`build-config/95_local_arm64.conf` includes it in APT's normal dependency
transaction. That config is copied to `/home/user/buildconfig.d/` in the builder.

The Workstation also includes a native build of real `kloak` 0.9.1-1 from pinned
commit `50e974bd8c48d3014940b613ab17917a01a9979b`; its package passed lintian and an
AArch64 executable smoke test. The staging hook verifies its hash and metadata.
The later runtime repair and its validation are described below.
Upstream Tirdad remains amd64-only and its protection is unavailable here.

Runtime testing found kloak being killed by seccomp on ARM64 syscall 48
(`faccessat`). [95-arm64-kloak-syscalls](../scripts/95-arm64-kloak-syscalls) provides
an ARM64 Workstation-only build hook for the systemd drop-in
`/etc/systemd/system/kloak.service.d/50-arm64-syscalls.conf`:

```ini
[Service]
# ARM64 equivalents of upstream access, poll, epoll_wait, and unlink.
SystemCallFilter=faccessat ppoll epoll_pwait unlinkat
```

This extends the existing allowlist without resetting it or changing the native
kloak package. It is now installed in this Debian builder as an executable host
post-script. For another builder, install it before building:

```sh
sudo install -m 0755 /path/to/95-arm64-kloak-syscalls /etc/debootstrap/post-scripts/95-arm64-kloak-syscalls
```

The original Workstation archive does not contain this later compatibility
drop-in. The imported disk does. Headless validation (local-only: `logs/headless-validation-kloak.txt`)
shows a stable Kloak PID with zero restarts, the correct Wayland environment,
and open keyboard/mouse event devices. Actual keystroke timing and interactive
UTM input after this fix remain untested. The temporary diagnostic service was
removed, and both guest filesystems passed offline checks.

`scripts/90-arm64-torbrowser-alpha` is installed in the builder's
`/etc/debootstrap/post-scripts/`. It selects the official Linux ARM64 Tor Browser
alpha only for the Workstation. The Workstation recipe also passes `--tb closed`,
so a failed download or signature check fails the build. Upstream signature and
freshness checks remain enabled.
The completed Workstation build contains Tor Browser **16.0a11** for Linux ARM64.

## UTM preparation and validation

For a new, unpacked, stopped bundle that has not been imported into UTM, run:

```sh
/usr/bin/python3 scripts/prepare_utm.py /path/Whonix-Gateway-LXQt.utm
/usr/bin/python3 scripts/prepare_utm.py --check /path/Whonix-Gateway-LXQt.utm
/usr/bin/python3 -m unittest discover -s tests -v
```

Preparation assigns a fresh UUID. Do not rerun preparation on an already
imported VM; use `--check` for read-only validation of existing bundles.

The helper checks the expected ARM64 topology, binds the Gateway's internal
listener to localhost, disables clipboard sharing, uses non-GL graphics, and
assigns a fresh VM UUID. The Workstation has only its connection to the Gateway.
It requires disk identifier `0` and adds these exact UTM QEMU overrides:

```text
-set drive.drive0.format=raw -set drive.drive0.discard=ignore -set drive.drive0.detect-zeroes=off
```

On the tested UTM build, raw format alone still produced early disk-write
`EINVAL` errors. Both guests reach their desktops with all three settings.
The `--check` mode requires all three settings and the approved network arguments.

The current resource profile is applied to both imported bundles, preserving
their existing UUIDs:

| Guest | CPUs | Memory |
| --- | ---: | ---: |
| Gateway | 2 | 2 GiB |
| Workstation | 4 | 4 GiB |

Both use `CPU=default`, which UTM launches as `-cpu host` with `-accel hvf`, plus
native resolution disabled and debug logging off. UTM's dynamic-resolution
option is enabled, but Workstation's guest policy deliberately keeps a static
1920×1080 mode to reduce resolution fingerprinting; that policy is retained.
The running arguments are recorded in runtime-hvf-profile.json (local-only: `logs/runtime-hvf-profile.json`).
The initial CPU/RAM allocation is retained after a small headless browser
comparison of two versus four Workstation CPUs. It does not establish UTM GUI
performance or native speed parity. This setup uses non-GL graphics; native GPU
acceleration has not been established.

`--check` requires the exact role profile and rejects CPU feature overrides.
If `Data/efi_vars.fd` exists, it must be a regular file without symlinks, hard
links, or group/other write permissions. Fresh bundles may omit this file.
The inherited localhost socket protocol does not authenticate other host
processes.

## Runtime validation and limits

[runtime-probe.sh](../scripts/runtime-probe.sh) runs inside Workstation with
application proxies and `LD_PRELOAD` wrappers bypassed. The
baseline (local-only: `logs/workstation-network-baseline.png`) records `IsTor: true`, successful
IPv4/IPv6 TCP connection attempts, and DNS through Gateway. With
Gateway stopped (local-only: `logs/workstation-gateway-off.png`), HTTP fails with curl exit 6,
both TCP attempts return 113 (no route), and DNS times out with exit 9.

After restarting Gateway and then Workstation, the
recovery check (local-only: `logs/workstation-recovery-profile.png`) again reports `IsTor: true`.
Workstation sees four CPUs and 3.8 GiB total RAM, with 3.1 GiB available at that
snapshot.

The Tor Browser GUI check (local-only: `logs/tor-browser-connected.png`) confirms a Tor
connection. Kloak was still in its activating state during the recovery check;
the subsequent seccomp failure (local-only: `logs/kloak-seccomp-failure.png`) led to the
additive ARM64 syscall fix above. Startup and input-device grabbing then passed
headless validation on the same disk; see [the exact scope](../VALIDATION.md).
The browser is
the official **16.0a11 alpha**; upstream Tirdad protection is unavailable on
ARM64. These observations cover the listed probes on this local setup and do
not establish complete anonymity or release readiness.

## Upstream references

- [Official UTM instructions](https://www.whonix.org/wiki/MacOS#UTM)
- [Build documentation](https://www.whonix.org/wiki/Dev/Build_Documentation/images)
- [Image builder](https://github.com/Whonix/derivative-maker)

Research used local primary sources and OpenCLI. No browser automation profile
or personal browser state is included in this repository.
