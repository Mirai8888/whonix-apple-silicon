# Review the ARM64 preview changes

This page indexes the changes in the independent, source-only Whonix ARM64
preview at [`c2c261d`](https://github.com/Mirai8888/whonix-apple-silicon/tree/c2c261de03bf9dc1a959b7698c8f69918efbc1a9).
It makes the code easier to inspect; publishing a diff does not establish that
software is free of backdoors. Independent review is welcome.

## Changes to pinned upstream source

| Component and baseline | Review diff | Changes |
| --- | --- | --- |
| [Whonix/derivative-maker at `945b966e`](https://github.com/Whonix/derivative-maker/tree/945b966e5fbc3b4ca58cc0eece7b91211190c6a4) | [Combined derivative-maker diff](derivative-maker.patch) | Stage the APT wrapper's helper dependencies during bootstrap; supply missing adduser UID defaults; handle an unset dependency-tracking entry. Only `build-steps.d/3500_install-packages` changes. |
| [Kicksecure/grml-debootstrap at `5c73a609`](https://github.com/Kicksecure/grml-debootstrap/tree/5c73a609120ac2c77fee7c2e57ea09d774269de6) | [grml-debootstrap diff](https://github.com/Mirai8888/whonix-apple-silicon/blob/c2c261de03bf9dc1a959b7698c8f69918efbc1a9/patches/grml-no-reimage.patch) | Backport `--no-reimage` so the build can retain its guest disk image. |

The combined derivative-maker diff is an alternative representation of these
three patches, applied in this order:

1. [bootstrap-helper-scripts.patch](https://github.com/Mirai8888/whonix-apple-silicon/blob/c2c261de03bf9dc1a959b7698c8f69918efbc1a9/patches/bootstrap-helper-scripts.patch)
2. [adduser-uid-defaults.patch](https://github.com/Mirai8888/whonix-apple-silicon/blob/c2c261de03bf9dc1a959b7698c8f69918efbc1a9/patches/adduser-uid-defaults.patch)
3. [dependency-error-nounset.patch](https://github.com/Mirai8888/whonix-apple-silicon/blob/c2c261de03bf9dc1a959b7698c8f69918efbc1a9/patches/dependency-error-nounset.patch)

Apply either the combined diff or the original stack, once, to the exact pinned
baseline. The [build guide](https://github.com/Mirai8888/whonix-apple-silicon/blob/c2c261de03bf9dc1a959b7698c8f69918efbc1a9/docs/BUILDING.md)
uses the original stack. Licenses and upstream attribution are recorded in
[third-party notices](../THIRD_PARTY_NOTICES.md).

On 2026-10-10, both upstream patchsets passed `git apply --check` against their
pinned source files and shell syntax checks after application. Applying the
combined diff produced exactly the same bytes as the three original patches;
reversing it recovered the baseline. The grml patch also reversed cleanly.

SHA256 values for independent comparison:

```text
derivative-maker.patch:
81a67fd20b3f769e17ea0a8914abf7371ac7391c4c9748ca9881b116d2d65216
baseline build-steps.d/3500_install-packages:
7d2b4f9287fd034ce3fddcb838d2e0e63b55387a953a8dc9148b3d980794c145
patched build-steps.d/3500_install-packages:
59d57be34dc3fc61be3096c7488527ecd1676ff4bc72967ee6961c38134d867d
```

## Added guest configuration and host tooling

These files are original additions around the upstream build. They are part of
the preview's behavior and should be reviewed alongside the upstream patches.
The links below pin them to the published source snapshot.

| Source | Behavior to inspect |
| --- | --- |
| [80-arm64-local-packages](https://github.com/Mirai8888/whonix-apple-silicon/blob/c2c261de03bf9dc1a959b7698c8f69918efbc1a9/scripts/80-arm64-local-packages) and [95_local_arm64.conf](https://github.com/Mirai8888/whonix-apple-silicon/blob/c2c261de03bf9dc1a959b7698c8f69918efbc1a9/build-config/95_local_arm64.conf) | Stage locally built ARM64 packages, check SHA256 and package metadata, and select them for installation. |
| [90-arm64-torbrowser-alpha](https://github.com/Mirai8888/whonix-apple-silicon/blob/c2c261de03bf9dc1a959b7698c8f69918efbc1a9/scripts/90-arm64-torbrowser-alpha) | Select the official ARM64 Tor Browser alpha channel in Workstation; retain the upstream downloader's signature and freshness verification. |
| [95-arm64-kloak-syscalls](https://github.com/Mirai8888/whonix-apple-silicon/blob/c2c261de03bf9dc1a959b7698c8f69918efbc1a9/scripts/95-arm64-kloak-syscalls) | Add `faccessat`, `ppoll`, `epoll_pwait`, and `unlinkat` to Kloak's systemd syscall allowlist. The hook writes an additive drop-in; review it together with the [pinned upstream service](https://github.com/Whonix/kloak/blob/50e974bd8c48d3014940b613ab17917a01a9979b/usr/lib/systemd/system/kloak.service). |
| [build-guests.sh](https://github.com/Mirai8888/whonix-apple-silicon/blob/c2c261de03bf9dc1a959b7698c8f69918efbc1a9/scripts/build-guests.sh), [start-builder.sh](https://github.com/Mirai8888/whonix-apple-silicon/blob/c2c261de03bf9dc1a959b7698c8f69918efbc1a9/scripts/start-builder.sh), and [builder-ssh](https://github.com/Mirai8888/whonix-apple-silicon/blob/c2c261de03bf9dc1a959b7698c8f69918efbc1a9/scripts/builder-ssh) | Builder setup, access, repository policy, and guest build arguments. |
| [prepare_utm.py](https://github.com/Mirai8888/whonix-apple-silicon/blob/c2c261de03bf9dc1a959b7698c8f69918efbc1a9/scripts/prepare_utm.py) | Transform upstream UTM templates into ARM64/HVF bundles, configure Gateway and Workstation network adapters, and select storage and sharing settings. |
| [start-whonix.sh](https://github.com/Mirai8888/whonix-apple-silicon/blob/c2c261de03bf9dc1a959b7698c8f69918efbc1a9/scripts/start-whonix.sh) and [all scripts](https://github.com/Mirai8888/whonix-apple-silicon/tree/c2c261de03bf9dc1a959b7698c8f69918efbc1a9/scripts) | VM launch behavior, status checks, validation probes, benchmark runners, and source packaging. |

## Full source changeset and build inputs

[View the full published source changeset](https://github.com/Mirai8888/whonix-apple-silicon/commit/c2c261de03bf9dc1a959b7698c8f69918efbc1a9)
or [download its diff](https://github.com/Mirai8888/whonix-apple-silicon/commit/c2c261de03bf9dc1a959b7698c8f69918efbc1a9.diff).
This is the initial source snapshot compared with an empty tree, not a complete
Whonix-versus-ARM64 guest filesystem diff. It includes the original tooling,
tests, configuration, documentation, and recorded evidence.

[Build provenance](https://github.com/Mirai8888/whonix-apple-silicon/blob/c2c261de03bf9dc1a959b7698c8f69918efbc1a9/artifacts/BUILD-PROVENANCE.md)
lists the pinned upstream commits and recorded package hashes.
[Validation](https://github.com/Mirai8888/whonix-apple-silicon/blob/c2c261de03bf9dc1a959b7698c8f69918efbc1a9/VALIDATION.md)
records what was tested and what remains incomplete. There are no VM binaries
in this source preview; these diffs do not verify a separately built VM image.
