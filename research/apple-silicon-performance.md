# Apple Silicon performance assessment

Snapshot: 2026-09-17, approximately 12:50–12:53 UTC. This assessment changes no VM settings. Proposed resource changes below are starting points for measurement, not benchmarked speedups.

Later on the same day, the 2+4 vCPU profile was applied and the ARM64 Kloak
compatibility fix was installed. A subsequent four-boot headless comparison
measured idle CPU at 3.79% versus 1.06% of one host core, and repeated crypto
and Pixman tests verified their existing ARM64 fast paths. See the
[completed performance report](../PERFORMANCE.md) for measurements and limits;
the initial assessment below preserves its original observation context.

## What is already native

The host reports **Apple M5, 10 physical/logical cores, 4 performance cores and 6 efficiency cores, 24 GiB RAM**. These values came from `sysctl`, not a model-name assumption.

Both running UTM guests use:

```text
qemu-aarch64-softmmu
-machine virt -accel hvf -cpu host
-device virtio-blk-pci,drive=drive0
-device virtio-net-pci,...
```

The running process arguments and Gateway debug log (local-only: `artifacts/utm/Whonix-Gateway-LXQt.utm/Data/debug.log`) confirm explicit HVF selection with no TCG fallback configured. Both guests reach their desktop, and Workstation `uname -m` was observed as `aarch64`. The installed UTM QEMU framework also links Apple's Hypervisor.framework and imports `hv_vm_create` and `hv_vcpu_run`.

This is hardware-accelerated ARM64 CPU virtualization. Virtual devices, graphics, and Tor transport still have their own costs; this evidence does not establish native application speed or GPU acceleration. The plist's `System.CPU=default` already generates `-cpu host`; changing that string is unnecessary.

## Resource profile

| Guest | Observed configuration | Proposed initial CPU profile | Memory guidance |
|---|---|---|---|
| Gateway | 3 vCPUs, 2048 MiB | 2 vCPUs | Keep 2048 MiB initially. |
| Workstation | 3 vCPUs, 4096 MiB | 4 vCPUs | Keep 4096 MiB initially; consider 6144 only if browser workload causes guest memory pressure and the host has headroom. |

The recommended initial 2+4 CPU split retains the existing total of six vCPUs while allocating more to the browser desktop. It is a reasonable role allocation to compare against 3+3, not a proven improvement. vCPUs are scheduled by macOS; they are not reservations of particular performance cores. Do not assign every host core to each guest or introduce CPU-affinity assumptions.

**The completed builder was subsequently shut down cleanly.** It had been another configured 4-vCPU/6144-MiB workload. Before shutdown, the entire host had about **27.7 GiB of swap in use** and **11.1 GiB of physical memory occupied by the compressor**. Those numbers include other applications and past workload; they do not attribute pressure to these guests or show a current swap rate. The root agent then observed `memory_pressure`'s system-wide memory-free percentage increase from this assessment's 36% to 49%. Keep guest RAM unchanged initially and recheck under the real browser workload.

After diagnostic evidence is saved, turn off Gateway `QEMU.DebugLog` for routine use. Workstation already has it disabled. Avoid verbose SPICE logging during performance comparisons.

## Graphics: preserve the working non-GL path

Current settings are `virtio-ramfb`, SPICE `gl=off`, `NativeResolution=false`, and `DynamicResolution=true`. Keep this proven booting path.

The pinned desktop profile ([pinned source](https://github.com/Kicksecure/desktop-config-dist/blob/a32ad57256a1bca5e0771a67ddf03dfb56e282e5/etc/profile.d/15_desktop-config-dist.sh)) explicitly exports `WLR_RENDERER=pixman` in virtual machines as its workaround for virtualized graphics. It also selects Wayland for GTK and Qt. Software composition is an upstream configuration choice, not evidence that HVF failed.

Keep Retina/native-resolution rendering off. A subsequent source check found
that Workstation's static 1920×1080 mode is an intentional privacy default:
40_workstation.conf ([pinned source](https://github.com/Whonix/anon-ws-base-files/blob/f5d27b67938d062c156c3156e0ac3870e3c31314/etc/wlr-resize-watcher.d/40_workstation.conf))
sets `enable_dynamic_resolution=false` to reduce resolution fingerprinting.
The optimization pass preserves this choice and Tor Browser's letterboxing.
Changing the Workstation mode is not a neutral performance toggle.

The headless Workstation report confirms `systemd-detect-virt=qemu` and a
1920×1080 active mode. The pinned
resize watcher ([pinned source](https://github.com/Kicksecure/vm-config-dist/blob/068e23a88ca147707b677c935305fdbba769c910/usr/lib/python3/dist-packages/wlr_resize_watcher/wlr_resize_watcher.py))
also omits `qemu` from initial dynamic-resize synchronization, but adding it
would not override Workstation's explicit privacy policy. Host-side UTM
`DynamicResolution=true` does not establish that the guest resizes dynamically.

Debian's installed Pixman 0.44.0-3 already builds and automatically selects its
dedicated AArch64 NEON implementation. There is no missing NEON enablement switch.
The [fixed-input benchmark](pixman-benchmark.md) compares that existing path with
its process-local disabled-backend case, without changing guest display settings.

Do not enable GL, replace Pixman, force software cursors, or change Tor Browser rendering preferences on an untested assumption. The LXQt launcher ([pinned source](https://github.com/Kicksecure/lxqt-wayland-session/blob/85cdf13c661453913e1d501b4815567f3214aec9/startlxqtwayland.in)) deliberately removed the forced-software-cursor workaround from its normal labwc path.

## Keep the tested disk and network behavior

Both guests use VirtIO disks and NICs. Preserve the Gateway-only external backend and Workstation's single internal socket backend. No additional NIC, shared network, or clipboard integration is needed for performance tuning.

Keep the tested raw-disk overrides:

```text
-set drive.drive0.format=raw
-set drive.drive0.discard=ignore
-set drive.drive0.detect-zeroes=off
```

Explicit RAW alone removed the format-probe warning but did not stop `BLOCK_IO_ERROR` write failures. Disabling discard/zero-to-unmap allowed EFI, GRUB, Linux, and the desktops to boot. This isolates the failure to that path, though the exact failing host syscall was not traced. APFS uses 4096-byte allocation blocks, and Darwin hole punching has alignment requirements.

These overrides preserve ordinary disk writes and flush behavior. They do not make the disk temporary or disable persistence. They can increase allocated host storage because guest discard/zero writes no longer reclaim holes through that path. Do not use `cache=unsafe`, disable flushes, or add `cache=none`/direct-I/O as a speculative speed fix. No cache override is currently necessary.

## Knobs that do not address this workload

- `ForceMulticore` concerns TCG emulation threading; it is not the HVF vCPU count. Keep it false.
- `JITCacheSize` is a TCG translation-block cache control. Increasing it does not accelerate this HVF or Pixman path.
- UTM describes TSO as aiding Intel emulation inside a guest at a general performance cost. Keep it off for native ARM64 Whonix.
- UTM's renderer-backend selection applies to GPU-accelerated QEMU graphics; it is not a demonstrated optimization for the current non-GL display.

These meanings are supported by installed UTM tooltips/QEMU help. Exact Swift generation guards were not reverified during this assessment.

## Background operation and measurement tooling

`scripts/measure-host.py` now samples only the two configured UTM guest UUIDs.
It computes CPU use from cumulative process CPU time and per-process monotonic
sampling intervals, checks that process identities remain unchanged, and records
RSS snapshots plus host-wide swap/page-out counters. Run it with the same guest
workload before and after a change:

```sh
/usr/bin/python3 scripts/measure-host.py --seconds 30 > logs/idle-sample.json
```

`logs/performance-before-kloak.json` records the earlier UTM baseline while Kloak
was restarting: approximately 4.3% of one CPU core for Gateway and 7.7% for
Workstation. That initial sample preceded the per-process timing refinement;
it is a rough baseline, not evidence of a speedup. Subsequent headless validation
uses a different QEMU/display configuration and is not a comparable benchmark.

UTM 4.7.5's `utmctl start --hide` hides only its main library window. Its source
still opens and promotes guest console windows. The launch helper's optional
`--hide` flag documents this limitation; it is not a headless mode. To respect
the request to keep UTM out of the foreground, maintenance uses a temporary
headless validation boot and leaves Workstation stopped afterward.

## Verification before claiming improvement

Record the same local workload before and after one change: terminal responsiveness, opening the browser to a local page, desktop scrolling, and idle VM CPU use. Tor page-load time varies with circuits and remote servers and is not a clean CPU benchmark. Keep network acceptance separate and rerun it after the final configuration settles.

Useful guest observations, without changing settings:

```sh
uname -m
systemd-detect-virt
printenv WLR_RENDERER
wlr-randr
free -h
vmstat 1 10
```

The existing `detect-software-rendering` probe reports `software`, `accelerated`, or `unknown`; its corresponding exit statuses are 0, 1, and 2. A nonzero result is therefore not necessarily probe failure. The pinned QML software-renderer profile has an early-return guard when `QMLSCENE_DEVICE` is unset, so do not claim that separate setting is active merely from its comment.

On the host, record `memory_pressure`, `sysctl vm.swapusage`, and VM process CPU usage after comparable settling time. A single RSS or cumulative swap counter is insufficient to measure the guest's memory demand. Retain a change only when measured behavior improves without renewed memory pressure, disk errors, or network-test regressions.
