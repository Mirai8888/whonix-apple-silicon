# Local ARM64 Whonix UTM build provenance

This record accompanies source tooling, not downloadable images. Referenced
local-only archives, packages, logs, and screenshots are excluded from Git.
Use [the setup guide](../docs/BUILDING.md) for the required external inputs.

Recorded 2026-09-17. These are experimental local development images, not an
official Whonix release. Both builds completed, and both guests reached their
graphical desktops in native ARM64 UTM/QEMU with HVF. Workstation reports
`aarch64`. Guest HTTP/Tor connectivity and the listed Gateway-off fail-closed
probes passed. Tor access recovered after restarting Gateway and then
Workstation. The Tor Browser GUI also confirmed a Tor connection.

## Original build artifacts

| Archive | Size in bytes | SHA256 |
| --- | ---: | --- |
| `Whonix-Gateway-LXQt.utm.tar.gz` | 1037264658 | `699b96c051e62e2aff9d89266d2b654f6c423007af0501752176c021b69e589d` |
| `Whonix-Workstation-LXQt.utm.tar.gz` | 1376482415 | `fd7b5a59176a2d560eab6dda76575e19ea4a1dde856a2165fadf991f3f634b1c` |

These hashes identify the original archives. Extracted disk contents change
when booted, and preparation changes the bundle's configuration and UUID.
The archives have not been repacked with the later UTM compatibility settings,
resource profile, or kloak syscall drop-in. The current imported bundles are in `artifacts/utm/` from
the workspace root; their existing UUIDs were preserved when applying the
resource profile.
The archives retain the build's nested
`home/user/derivative-binary/18.2.2.7/<name>.utm/` member paths. The sparse raw
disks have logical sizes of 25 GiB for Gateway and 40 GiB for Workstation.

## Source and build inputs

| Component | Pinned source commit |
| --- | --- |
| `Whonix/derivative-maker`, tag `18.2.2.7-developers-only` | `945b966e5fbc3b4ca58cc0eece7b91211190c6a4` |
| `Kicksecure/grml-debootstrap` | `5c73a609120ac2c77fee7c2e57ea09d774269de6` |
| `Kicksecure/helper-scripts`, package version `3:54.6-1` | `3e7fc26c2935f4124303831b4d58b13cbf2649c4` |
| `Kicksecure/kicksecure-meta-packages` | `a65f75485d72011deb86316edf6afcdad5abd980` |
| `Whonix/kloak` | `50e974bd8c48d3014940b613ab17917a01a9979b` |

The derivative-maker tag was verified using the published Whonix signing key.
The build also verifies pinned upstream commits against its shipped OpenPGP
policy. Local compatibility changes were retained as patches with
`--allow-uncommitted true`; source signature verification remained enabled.

The builder is Debian 13 ARM64 on QEMU/HVF, configured with four CPUs, 6 GiB RAM,
and a 100 GiB sparse disk. The guest target is installed UTM 4.7.5, build 118.
The recipe uses `--target utm --arch arm64 --freshness current`, the authenticated
upstream development APT repositories, and `--remote-derivative-packages true`.
Remote package versions therefore reflect the repositories at build time; this
record does not establish a reproducible rebuild from current repositories.

The build recipe is [build-guests.sh](../scripts/build-guests.sh), with local
package selection in [95_local_arm64.conf](../build-config/95_local_arm64.conf).
Build logs are retained inside the dedicated Debian builder at
`/home/user/build-logs/gateway.log` and `/home/user/build-logs/workstation.log`.
The builder was cleanly powered off after the builds. Copied build and package
logs are available in build-evidence.tar.gz (local-only: `logs/build-evidence.tar.gz`).

## Locally built packages

The remote repository lacked the required ARM64 binaries below. Real packages
were built natively from the pinned source, without removing dependencies or
substituting dummy packages. The copies used for staging are preserved in
`artifacts/local-packages/`.

| Package file | Debian package version | Used by | SHA256 |
| --- | --- | --- | --- |
| `dist-vm-gui-all_37.6-1_arm64.deb` | `3:37.6-1` | Both guests | `cfbc4df411248263d2549cb79ac77a46ff94d2d2c650cf7f436e0dd80b929c67` |
| `kloak_0.9.1-1_arm64.deb` | `0.9.1-1` | Workstation | `5450f13d713c64ce0df748c38b00858f1fdd7e9d3a9dbc74b94192c7e6c47901` |

The metapackage was built using `dpkg-buildpackage --build=any --no-sign` and
passed lintian. Kloak passed lintian and an AArch64 executable smoke test.
After the additive syscall repair below, its ARM64 startup and input-device
grabbing passed headless validation; this is not a keystroke-timing efficacy test.
[80-arm64-local-packages](../scripts/80-arm64-local-packages) verifies each
package's fixed SHA256, name, version, and ARM64 architecture before atomically
staging it in the guest. The existing `install_package_list` mechanism passes
those exact local files to APT alongside the normal flavor packages.

Workstation includes official Tor Browser **16.0a11** for Linux ARM64.
[90-arm64-torbrowser-alpha](../scripts/90-arm64-torbrowser-alpha) selects the
upstream alpha channel, and Workstation uses `--tb closed`. The build log records
the upstream signature/freshness checks and successful extraction. The
browser GUI check (local-only: `logs/tor-browser-connected.png`) confirms a Tor connection.
Upstream Tirdad is amd64-only;
its protection is unavailable in these ARM64 images.

Runtime testing exposed an ARM64 kloak sandbox mismatch: the kernel reported
`SECCOMP arch=c00000b7 syscall=48` (`faccessat`) and signal 31 for `/usr/bin/kloak`,
followed by repeated restarts. Evidence is in
kloak-seccomp-failure.png (local-only: `logs/kloak-seccomp-failure.png`) and
kloak-faccessat-48.png (local-only: `logs/kloak-faccessat-48.png`).

[95-arm64-kloak-syscalls](../scripts/95-arm64-kloak-syscalls) is a new host
post-script for future ARM64 Workstation builds. It writes
`/etc/systemd/system/kloak.service.d/50-arm64-syscalls.conf` with the additive
`SystemCallFilter=faccessat ppoll epoll_pwait unlinkat`. These are ARM64
counterparts of the upstream allowlist's `access`, `poll`, `epoll_wait`, and
`unlink`. There is no empty filter reset; the original service sandbox, native
binary, package file, and recorded package hash are unchanged. The hook is now
installed executable in this builder's `/etc/debootstrap/post-scripts/` and was
used to repair the stopped imported Workstation disk. Its syntax check and six
unprivileged fixture tests passed. The original archive predates this drop-in.

The headless validation report (local-only: `logs/headless-validation-kloak.txt`) records
the same active Kloak PID and zero restarts across a 30-second sampling interval,
the actual process's Wayland environment with
`KLOAK_EXIT_ON_WL_CONNECT_FAILURE=1`, and descriptors for keyboard/mouse/tablet
event devices. This supports successful startup and input-device grabbing.
The test used Homebrew QEMU 11.1.1/HVF with no display window and the existing
Gateway socket, retaining the same guest disk. Its graphics device differs from
UTM's, so it does not validate UTM interactive input or graphics performance.
The temporary diagnostic service was removed after testing, and the guest shut
down normally. Final offline ext4 and FAT checks passed. See
[VALIDATION.md](../VALIDATION.md) for the checkpoint, checksums, and scope.

## Local compatibility patches

| Patch | Purpose | SHA256 |
| --- | --- | --- |
| [grml-no-reimage.patch](../patches/grml-no-reimage.patch) | Backport `--no-reimage` and getopt registration | `c454a52f157d366c39a64a9103a4406fbd8baa8878e8102df36d29d2c3f8e64a` |
| [bootstrap-helper-scripts.patch](../patches/bootstrap-helper-scripts.patch) | Stage the APT wrapper's shell/Python dependencies | `a04ca7c944d2e35f24be8e75856c678a35248f726209a34926af3331e1081aba` |
| [adduser-uid-defaults.patch](../patches/adduser-uid-defaults.patch) | Make absent Debian UID defaults explicit after adduser installation and upgrade | `dff5a371a8fad84fffcbb943b6f156a3a802a9764df88aa6ca56db625528a41f` |
| [dependency-error-nounset.patch](../patches/dependency-error-nounset.patch) | Preserve dependency-error reporting under `nounset` | `8d9ebc147a515d460a1d77f70ba73a26ca658752fc9c8a11ddd937e381f64a91` |

The grml backport comes from development-fork commits
`b62f8e5c95972d4d77d4576d24753ca0f81ec594` and
`f10ef1536ecbaccff4072a8fe5dbbdd98a17cae0`. Apply the other three patches to
derivative-maker in the table's order. The recipe sets
`USE_DEFAULT_INTERFACES=true` for grml's reproducibility validation;
`NOINTERFACES=true` still prevents generic guest network configuration.

## UTM settings and validation boundary

[prepare_utm.py](../scripts/prepare_utm.py) prepares new, stopped, unpacked bundles
before their first import into UTM
with ARM64 `virt`, HVF, UEFI, a writable VirtIO raw disk, non-GL `virtio-ramfb`,
disabled clipboard/directory/USB sharing, and a fresh VM UUID. It requires disk
identifier `0` and exactly these additional disk options:

```text
-set drive.drive0.format=raw -set drive.drive0.discard=ignore -set drive.drive0.detect-zeroes=off
```

Raw format alone did not resolve early disk-write `EINVAL` failures in the
tested UTM setup. Both guests reach their graphical desktops with all three
overrides. Preparation generates a fresh UUID; do not rerun it on an imported
VM. Its `--check` mode is read-only and suitable for validating existing bundles.

The following profile is applied to the imported bundles and passes `--check`:

| Guest | `CPUCount` | `MemorySize` (MiB) |
| --- | ---: | ---: |
| Gateway | 2 | 2048 |
| Workstation | 4 | 4096 |

Both use `CPU=default`, `NativeResolution=False`, `DynamicResolution=True`, and
`DebugLog=False`. The running processes confirm `-accel hvf`, `-cpu host`, and
the CPU/memory allocations above in
runtime-hvf-profile.json (local-only: `logs/runtime-hvf-profile.json`). The helper's
strict `--check` requires the exact profile, the disk settings and approved
network arguments, and no CPU flag additions or removals. If the automatically
opened `Data/efi_vars.fd` exists, it must be a regular file with one hard link,
no symlink, and no group/other write permissions. Fresh bundles may omit it.
All 24 preparation unit tests passed using `/usr/bin/python3`.

The resource allocation began as a heuristic within a six-vCPU budget for
the pair. A later browser comparison supports retaining four Workstation CPUs
for its small local workload, with substantial host-load limitations. A
[headless comparison](../PERFORMANCE.md) measures the Kloak repair's reduction
in idle CPU use and verifies existing ARM64 component acceleration. Neither
performance parity nor native GPU acceleration has been demonstrated.

Gateway has the external user-mode network and an internal listener at
`127.0.0.1:8010`. Workstation has only the corresponding socket connection.
Start Gateway first, then Workstation. After restarting Gateway, restart
Workstation so its socket reconnects. This localhost socket protocol does not
authenticate other processes on the Mac.

[start-whonix.sh](../scripts/start-whonix.sh) validates the bundles and starts
Gateway before Workstation after confirming Gateway's loopback listener.
[whonix-status.sh](../scripts/whonix-status.sh) reports their UTM states. These
commands target the bundles registered directly from `artifacts/utm/`.

## Runtime observations

Both desktops booted, and Workstation's `uname -m` returned `aarch64`.
[runtime-probe.sh](../scripts/runtime-probe.sh) was run as the ordinary user in
Workstation. It unsets `LD_PRELOAD`, invokes `curl.anondist-orig` with application
proxies disabled, opens Python IPv4/IPv6 TCP sockets, and queries Gateway DNS
directly. The results are captured in the
baseline screenshot (local-only: `logs/workstation-network-baseline.png`) and
Gateway-off screenshot (local-only: `logs/workstation-gateway-off.png`).

| Probe | Gateway running | Gateway stopped |
| --- | --- | --- |
| HTTPS `check.torproject.org/api/ip` | `IsTor: true`, exit 0 | DNS lookup fails, curl exit 6 |
| IPv4 TCP `1.1.1.1:443` | `connect_ex=0` | `connect_ex=113` (no route) |
| IPv6 TCP `[2606:4700:4700::1111]:443` | `connect_ex=0` | `connect_ex=113` (no route) |
| `dig @10.152.152.10 example.com A` | Exit 0 | Timeout, exit 9 |

These are successful baseline and fail-closed results for the listed probes.
After restarting Gateway and then Workstation, the
recovery screenshot (local-only: `logs/workstation-recovery-profile.png`) records
`IsTor: true` again. Workstation also reports four CPUs and 3.8 GiB total RAM,
with 3.1 GiB available at that snapshot.

The Tor Browser GUI screenshot (local-only: `logs/tor-browser-connected.png`) confirms
a Tor connection. Kloak was still in its activating state during the recovery
check; the subsequent seccomp diagnosis and additive syscall fix are recorded
above, followed by successful headless startup/device-grabbing verification. The
installed browser is official Linux ARM64 Tor
Browser **16.0a11 alpha**, Tirdad protection is unavailable on ARM64, and Kloak's
keystroke-timing efficacy and interactive UTM behavior after repair remain
untested. The observed checks do not
establish complete anonymity or release readiness.
