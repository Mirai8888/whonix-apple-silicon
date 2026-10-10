# Build and setup guide

This is a developer workflow for the source revisions recorded in
[build provenance](../artifacts/BUILD-PROVENANCE.md). There is no image download
or automated builder provisioning in this repository. The original build used
development APT repositories with `--freshness current`; current repositories
may differ. The recorded hashes establish provenance, not reproducible builds.

## Requirements

| Environment | Required inputs |
| --- | --- |
| Host | Apple Silicon macOS; the recorded host was an M5 with 24 GiB RAM and macOS 26.2 |
| UTM | The recorded target was UTM 4.7.5 build 118, installed at `/Applications/UTM.app` |
| Headless builder | Homebrew QEMU and its ARM64 UEFI firmware under `/opt/homebrew/`; the recorded version was 11.1.1 |
| Host tooling | Git, `/usr/bin/python3`, OpenSSH, `lsof`; Homebrew coreutils for macOS shell-fixture tests |
| Build guest | Dedicated Debian 13 ARM64 VM, account `user` with the privileges required by upstream derivative-maker |
| Capacity | Recorded builder: 4 CPUs, 6 GiB RAM, 100 GiB sparse disk; output raw disks: 25 GiB Gateway and 40 GiB Workstation |

Sparse images and retained clones consume additional physical space as written.
The resource numbers describe the tested environment, not guaranteed minimums.

Use the [upstream image-build documentation](https://www.whonix.org/wiki/Dev/Build_Documentation/images)
for base builder provisioning and source-signature verification. These local
helpers assume that provisioning is complete; they do not download, install,
or authenticate a base operating system for you.

## Private host workspace

Before storing disks, logs, or keys in a fresh checkout:

```sh
chmod 700 .
umask 077
mkdir -p builder artifacts/utm logs
chmod 700 builder artifacts artifacts/utm logs
```

Avoid shared or externally writable ancestors and custom ACL grants. The launch
checks enforce ownership, POSIX modes, file types, and link restrictions; they
do not sandbox other programs running under your own macOS account.

For `scripts/start-builder.sh`, provision these local files yourself:

```text
builder/Whonix ARM64 Builder.utm/Data/builder.qcow2
builder/Whonix ARM64 Builder.utm/Data/seed.iso
builder/keys/id_ed25519
builder/keys/known_hosts
```

The seed must configure the builder's `user` account for a dedicated SSH public
key. Generate a new key for your builder; do not reuse personal credentials.
Pin the builder host key after checking its fingerprint through a trusted
console. The helper deliberately refuses unknown or missing keys. It forwards
SSH only on `127.0.0.1:22022`, with no agent, personal SSH config, or forwarding.
No seed, cloud-init recipe, credentials, or trust-on-first-use setup is shipped.

## Prepare sources and packages in the builder

1. Obtain and verify the upstream sources listed in
   [provenance](../artifacts/BUILD-PROVENANCE.md#source-and-build-inputs). Put
   derivative-maker at `/home/user/derivative-maker`. The recorded tag is
   `18.2.2.7-developers-only`, commit
   `945b966e5fbc3b4ca58cc0eece7b91211190c6a4`. Preserve its OpenPGP verification
   policy and inspect local changes before allowing them in a build.
2. Apply `patches/grml-no-reimage.patch` to the pinned `grml-debootstrap`
   checkout inside derivative-maker. Apply the following to derivative-maker,
   in order: `bootstrap-helper-scripts.patch`, `adduser-uid-defaults.patch`,
   `dependency-error-nounset.patch`. Use `git apply --check` before each patch;
   do not force patches onto a different revision.
3. Build the real ARM64 `dist-vm-gui-all` and `kloak` packages from the pinned
   sources in provenance. The recorded package build used
   `dpkg-buildpackage --build=any --no-sign` and lintian. Install upstream build
   dependencies normally; this repository does not include an automated package
   build recipe or the resulting `.deb` files.
4. Place the packages in `/home/user/arm64-local-build/` with the exact filenames
   in `scripts/80-arm64-local-packages`. That hook checks SHA256, name, version,
   and architecture. A new package build may have different bytes. Investigate
   the difference and record verified provenance before updating expected
   hashes; do not bypass the check or substitute dummy packages.
5. Copy `build-config/95_local_arm64.conf` to
   `/home/user/buildconfig.d/95_local_arm64.conf`. Install the three hooks below
   as root-owned executable files in `/etc/debootstrap/post-scripts/`:

   - `scripts/80-arm64-local-packages`
   - `scripts/90-arm64-torbrowser-alpha`
   - `scripts/95-arm64-kloak-syscalls`

   These hooks are for the dedicated builder's mounted guests. Do not run them
   manually against your host root or an unrelated mounted filesystem.

See [recorded build notes](BUILD-NOTES.md#build-compatibility-fixes) for the
reason behind each patch and hook. Their separate licenses are listed in
[third-party notices](../THIRD_PARTY_NOTICES.md).

## Build the guest pair

Copy `scripts/build-guests.sh` into the builder and run it as `user`, not root.
It builds Gateway first, then Workstation, and saves logs under
`/home/user/build-logs/`. It assumes the input paths above.

The recipe retains upstream source verification and explicitly allows the
reviewed local patches. It uses the development repositories and the ARM64
Tor Browser alpha channel. Workstation passes `--tb closed`, so browser download
or verification failures fail the build. Do not treat the observed alpha version
as a permanently pinned or supported download.

The `WHONIX_START_AT`, `WHONIX_RESUME_GATEWAY`, and `WHONIX_RESUME_WORKSTATION`
switches in the script are recovery aids for an already prepared builder. Leave
them unset for a new build. A Workstation-only run skips preparation stages and
cannot bootstrap a fresh builder.

## Prepare and import new UTM bundles

Copy the resulting `.utm` bundles from the builder to these host paths:

```text
artifacts/utm/Whonix-Gateway-LXQt.utm
artifacts/utm/Whonix-Workstation-LXQt.utm
```

For **new, stopped, unregistered** bundles only:

```sh
/usr/bin/python3 scripts/prepare_utm.py artifacts/utm/Whonix-Gateway-LXQt.utm
/usr/bin/python3 scripts/prepare_utm.py artifacts/utm/Whonix-Workstation-LXQt.utm
/usr/bin/python3 scripts/prepare_utm.py --check artifacts/utm/Whonix-Gateway-LXQt.utm
/usr/bin/python3 scripts/prepare_utm.py --check artifacts/utm/Whonix-Workstation-LXQt.utm
```

Preparation changes configuration, assigns fresh UUIDs, restricts bundle
permissions, and applies the supported network/device/resource profile. Import
those exact prepared bundles into UTM. Do not run preparation again after
registration; `--check` is the read-only operation for existing bundles. The
scripts assume the registered bundles are the files they validate, not other
copies with the same UUID.

Then use `scripts/start-whonix.sh`. It opens the VM consoles. Workstation must
have only its internal connection to Gateway; do not add shared folders,
clipboard integration, or another NIC as a workaround for launch failures.
Review [validation limits](../VALIDATION.md) before testing connectivity.

## Benchmarks

The [browser protocol](../benchmarks/README.md) uses private disposable copies
and never boots the imported Workstation disk. It requires an already running
Gateway and a stopped Workstation and builder. One-boot guest services must be
installed only in disposable templates: they request shutdown automatically.

Host QEMU measurements use a headless graphics device arrangement different
from UTM's GUI. They cannot establish interactive graphics performance or
native macOS parity. Retain raw evidence privately and publish only reviewed
summaries with source hashes and measurement limits.
