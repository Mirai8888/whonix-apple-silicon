# Apple Silicon validation — 2026-09-17

This is a dated record of the development workspace. Referenced local-only
logs, screenshots, and VM state are not distributed; compact benchmark results
and authored test sources are included. The VM states below describe the end of
the recorded work, not a fresh checkout or a reader's machine.

The imported Workstation disk now includes the ARM64 Kloak syscall fix.
Gateway remains running in UTM; Workstation is cleanly stopped so restarting
its console does not interrupt the Mac desktop. The maintenance builder and
headless test VMs are shut down. Start Workstation from UTM when ready, or use
`./scripts/start-whonix.sh` (this opens the guest console).

## Native CPU and resource profile

- Apple M5 host, 24 GiB RAM; both guests use native AArch64 with HVF and host CPU.
- Gateway: 2 vCPUs / 2 GiB. Workstation: 4 vCPUs / 4 GiB.
- VirtIO disk/network; Retina rendering and verbose QEMU logging disabled.
- No new Workstation network interface, clipboard sharing, shared directory,
  or guest agent was added.

This is hardware CPU virtualization, not a native GPU or native-speed claim.
The non-GL/Pixman display path and Whonix's display/privacy defaults are retained.

## Kloak repair and verification

The old service repeatedly crashed on ARM64 syscall 48 (`faccessat`), reaching
242 restarts in the observed UTM boot. The additive drop-in permits `faccessat`,
`ppoll`, `epoll_pwait`, and `unlinkat`, which correspond to calls already allowed
by the upstream service. The original unit, binary, and remaining sandbox are
unchanged. The fix is also installed in the builder's post-script directory for
future builds; the original compressed archives predate it.

The corrected headless report (local-only: `logs/headless-validation-kloak.txt`) records:

- The same Kloak PID, `active/running`, and zero restarts across two samples
  separated by the probe's 30-second `vmstat` interval.
- A running Labwc desktop and the actual Kloak process's Wayland environment,
  including `KLOAK_EXIT_ON_WL_CONNECT_FAILURE=1`.
- Open keyboard, mouse, and tablet event-device descriptors. Source inspection
  confirms successful Wayland initialization and device grabbing precede these
  open callbacks returning successfully.
- Six idle guest samples with zero swap I/O and CPU idle rounded to 100%.
- No failed system units; Kloak subsequently
  stopped cleanly (local-only: `logs/kloak-fixed-service-journal.txt`).

This verifies ARM64 Kloak startup and input-device grabbing. It does not measure
keystroke timing randomization or complete input behavior in UTM. To keep the
user's desktop undisturbed, this test used Homebrew QEMU 11.1.1/HVF with
`-display none`, `ramfb` plus `virtio-gpu-pci`, and the same internal socket
network. UTM uses its own QEMU build and combined `virtio-ramfb` device.
The launch arguments (local-only: `logs/headless-validation-launch.json`) are retained.

The first diagnostic pass held `graphical.target` pending and did not start
Kloak; it is excluded from Kloak acceptance. The corrected pass used an
asynchronous temporary service. That service, marker, scripts, and guest-side
report were removed afterward. Guest clock adjustment means journal wall-clock
timestamps are not a reliable elapsed-time measure.

## Network and storage

The corrected headless pass reports `IsTor: true`, HTTP exit 0, successful raw
IPv4/IPv6 connection attempts, and Gateway DNS exit 0. Those raw socket results
establish connectivity, not independent proof of each socket's Tor routing.
Earlier UTM Gateway-off checks (local-only: `logs/workstation-gateway-off.png`) established
failure of the listed HTTP, raw IPv4/IPv6 TCP, and DNS probes when Gateway was
stopped. The Tor Browser GUI check (local-only: `logs/tor-browser-connected.png`) was also
completed in UTM before this repair.

A paused-disk APFS checkpoint is retained in
`artifacts/checkpoints/Whonix-Workstation-before-kloak.utm`. It is a disk backup,
not a saved RAM state or a clean guest shutdown. When the guest ignored ACPI
shutdown, only its identified QEMU process received SIGTERM after checkpointing.
Offline ext4 journal recovery and FAT dirty-bit cleanup succeeded; subsequent
validation boots shut themselves down normally. Final read-only ext4 and FAT
checks passed (local-only: `logs/offline-repair-final-check.txt`).

## Measurement and remaining limits

`scripts/measure-host.py` records per-process CPU deltas, RSS snapshots, and
host-wide memory/swap counters without opening UTM. Its tests cover delayed
sampling, process changes, and duplicate UUIDs. The earlier UTM baseline is in
`logs/performance-before-kloak.json`; it is not comparable to the later headless
run, so those two reports are not used to calculate a percentage improvement.

A subsequent four-boot comparison uses disposable APFS clones and the same
headless configuration in control/repaired/repaired/control order. Median idle
host CPU fell from 3.79% to 1.06% of one core (72.1% lower in these short trials).
Control Kloak services restarted 11 times each during the measured minute;
repaired services stayed active with stable PIDs and zero restarts. All trials
passed the timing guards and shut down cleanly. The imported Workstation disk
was untouched by this comparison. [Performance results](PERFORMANCE.md) include
raw evidence, busy-host/swap limitations, and repeated component tests confirming
the already-enabled ARM64 crypto and Pixman paths. This does not establish UTM
GUI responsiveness or native performance parity.

Both prepared UTM configurations pass validation. The automated suite contains
156 passing tests on macOS, including 33 new architecture-comparison guard
tests and the existing security, browser-output, and process-cleanup regressions.
The earlier 123-test suite also passed from a clean Git index export without
upstream checkouts or VM artifacts, and in an isolated, unprivileged Linux
checkout. The expanded 156-test result is a macOS result; it does not extend
that earlier Linux validation.
Independent reviews of the security changes and browser benchmark found no
blocking security regressions.

A subsequent four-boot local Tor Browser comparison completed in 4/2/2/4-vCPU
order with 4 GiB RAM throughout. All 16 reported PNG hashes match the visually
validated PASS reference; a final-trial PNG was extracted offline and verified
as well. The existing four-CPU allocation is retained: grouped launch times
were lower, but the two-CPU results varied considerably. Sampled guest available
RAM stayed above 3.05 GiB with no OOM, reclaim, or memory-pressure counter
increase during launches. This small headless workload does not establish
multi-tab memory requirements, UTM GUI responsiveness, or native speed parity.
See the [protocol](benchmarks/README.md) and [results](PERFORMANCE.md).
The imported Workstation disk remained untouched; all disposable trial VMs and
the maintenance builder shut down cleanly.

## Architecture comparison validation

The separate [ARM64/HVF versus x86-64/TCG protocol](benchmarks/ARCHITECTURE.md)
uses four vCPUs, 4 GiB RAM, Tor Browser 16.0a11 alpha, and the same local workload
on both architectures. Its guards verify the actual kernel and browser ELF
architectures, package metadata, pinned measurement backend, private paths,
one-use markers, bounded execution, and serial record framing. Final screenshot
content requires a separate visual check; valid PNG output alone is insufficient.

These fixture tests validate the harness and failure handling. They do not
establish a performance result. The guest builds, kernels, profiles, device
models, and virtualization backends differ, so this comparison cannot isolate
instruction-set efficiency or establish native macOS performance parity.

The subsequent four-boot runtime comparison completed successfully. All 16
final PNGs were independently extracted through read-only offline mounts and
matched the reference hash; visual checks of one image per architecture
confirmed the workload's PASS result. An independent audit matched raw serial
reports, saved reports, source hashes, and timing summaries. The
[measured results](PERFORMANCE.md#native-arm64-versus-emulated-x86-64) and
[compact evidence](benchmarks/architecture-results.json) retain per-boot
variation and host-load limitations. Trial VMs and the maintenance builder
shut down cleanly; the imported Workstation remained stopped and unchanged.

This remains an experimental Whonix development build. Linux ARM64 Tor Browser
is the official 16.0a11 alpha; Tirdad remains unavailable on ARM64. Full UTM
interaction testing after the Kloak repair remains outside the runtime evidence
recorded here.

A subsequent [local security pass](security_best_practices_report.md) reviews
the project scripts, storage permissions, and configured VM boundaries. It
hardens private storage and launch checks without booting Workstation or
modifying guest disk contents. A full review of Whonix, Tor Browser, QEMU, UTM,
and their dependencies remains outside that scope.
