# Native ARM64 versus emulated x86-64

This experiment compares native ARM64/HVF and full-system x86-64/TCG on the
same Apple Silicon host. Both use four vCPUs, 4096 MiB RAM, Tor Browser 16.0a11
alpha, and the same [local workload](browser-local.html). It measures headless
browser launch, JavaScript, layout, rasterization, PNG encoding, and exit.
See the [recorded results](architecture-results.json) and
[performance report](../PERFORMANCE.md) for the measured comparison.

The ARM guest comes from this project's `18.2.2.7-developers-only` build. The
x86 guest comes from the signed official `18.2.1.9` KVM archive. Their guest
versions, kernels, profiles, device models, and virtualization backends differ.
This is a comparison of these configurations, not an isolated measurement of
instruction-set efficiency, UTM graphics, Tor throughput, or native macOS parity.
Whonix's [QEMU documentation](https://www.whonix.org/wiki/QEMU) does not support
the TCG configuration as a normal runtime target.

## Inputs and preparation

Keep Gateway running and the imported Workstation stopped. Create a current-user
owned `0700` directory at `artifacts/benchmarks/architecture/`. Prepare two
private `0600` raw-disk templates:

- `arm64-template.raw`: a fresh APFS copy of the stopped, repaired Workstation.
- `x86_64-template.raw`: convert only the Workstation QCOW2 from the verified
  [official KVM archive](https://download.whonix.org/libvirt/18.2.1.9/Whonix-LXQt-18.2.1.9.Intel_AMD64.qcow2.libvirt.xz)
  to sparse raw, after verifying its signature and checksum. Inspect its XML
  and disk metadata; this archive uses Q35 with legacy hard-disk boot. The archive
  also contains Gateway, which is not needed for this comparison.

Use GNU tar's sparse extraction support for the verified Whonix archive. Verify
the matching [x86 Tor Browser archive](https://dist.torproject.org/torbrowser/16.0a11/tor-browser-linux-x86_64-16.0a11.tar.xz)
against its detached signature and signed checksum before installing it into
the disposable x86 guest. Keep all upstream browser sandbox and privacy defaults.
Do not reuse private browsing state from another machine.

Mount only stopped disposable templates for offline preparation. Install:

| File | Guest destination | Owner / mode |
| --- | --- | --- |
| `scripts/guest-architecture-benchmark.py` | `/var/lib/whonix-architecture-benchmark/guest-architecture-benchmark.py` | root:root / `0600` |
| Unchanged `scripts/guest-browser-benchmark.py` | `/var/lib/whonix-architecture-benchmark/guest-browser-benchmark.py` | root:root / `0600` |
| Empty one-use marker | `/var/lib/whonix-architecture-benchmark/enabled` | root:root / `0600` |
| `benchmarks/browser-local.html` | `/home/user/.tb/tor-browser/Browser/Downloads/bench/local.html` | user:user / `0600` |
| `benchmarks/whonix-architecture-benchmark.service` | `/etc/systemd/system/whonix-architecture-benchmark.service` | root:root / `0644` |

The root state directory must be exactly `0700`; the user's `bench` directory
must also be `0700`. Every browser-directory ancestor from `/home/user` must
belong to the browser user. If the archive lacks `Downloads`, create it with
user ownership before creating `bench`; creating only the nested directory as
root can leave its parent root-owned, which the benchmark correctly rejects.
Enable only the architecture service in
`graphical.target.wants`. Disable any older one-boot benchmark service/marker in
the disposable template so it cannot race with this experiment. Never install
these services in an imported production guest. Unmount the templates, check
their filesystems, and cleanly stop the maintenance builder before testing.

The wrapper verifies the historical backend's hash before executing it. It
retains its launch timing, private files, user isolation, and output validation,
while selecting `/dev/ttyAMA0` or `/dev/ttyS0` according to the actual kernel
architecture. It checks Tor Browser package metadata and the ELF architecture
of `firefox.real`; it does not spoof Python's architecture report.

## Run

```sh
/usr/bin/python3 scripts/run-architecture-benchmark.py --smoke-arm
/usr/bin/python3 scripts/run-architecture-benchmark.py --smoke
```

After each smoke run shuts down, inspect the captured PNG through a read-only
offline mount. Require the visible `PASS checksum=e5f37d05 cards=4000`. A valid
PNG alone is not proof the workload completed. Architecture-specific rendering
may differ, so do not blindly require the x86 PNG to equal the ARM reference.
The unchanged page contains static ARM64 labels on both architectures; those
labels are not architecture detection. Kernel and browser ELF checks establish
the actual architecture independently.

Use the cleanly stopped smoke disks as the basis of fresh final templates, so
both browser profiles have completed four unmeasured launches before timing.
In those copies only, remove the four named `launch-*.png` captures from `bench`
and the corresponding stdout/stderr logs and `report.json` from the root state
directory; recreate its empty `0600` marker. Preserve the browser profile and
preferences. Keep the smoke disks and initial templates as separate evidence.
This avoids including first-ever profile creation only on the pristine x86
image; it does not make the two existing profiles or guest builds identical.

After validating both smoke runs:

```sh
/usr/bin/python3 scripts/run-architecture-benchmark.py
```

The final order is ARM, x86, x86, ARM, with a fresh disk copy for every boot.
The runner refuses existing names and never overwrites prior evidence. Each
guest settles for 40 seconds, runs one first launch and three repeat launches,
and requests shutdown. Both architectures have the same 180-second per-launch
deadline, an 840-second service limit, and a 1200-second host VM deadline.
Timeouts are failures, not timing samples or fabricated speed ratios.

Guest durations use monotonic time and Linux pidfd exit notification. Host
receipt times of serial launch markers provide a coarse cross-check; transport
and polling delay make them unsuitable for sub-second timing. No QEMU `-icount`
option is used. Host CPU spans boot, settling, and workload; host swap counters
include all applications, and memory peaks are sampled.

QEMU 11.1.1's non-icount TCG clock follows elapsed host time while the VM runs;
it does not scale elapsed time to compensate for slower emulation. The relevant
source paths are [TCG clock selection](https://github.com/qemu/qemu/blob/v11.1.1/accel/tcg/tcg-accel-ops.c),
[CPU timers](https://github.com/qemu/qemu/blob/v11.1.1/system/cpu-timers.c), and
[x86 TSC handling](https://github.com/qemu/qemu/blob/v11.1.1/hw/i386/x86-cpu.c).
Cross-check the complete launch block and individual launches against host
serial observations, using the actual maximum polling gap. Agreement is a
coarse timing sanity check, not a rigorous error bound.

Record image/signature provenance, QEMU arguments, actual browser build metadata,
source hashes, all launch durations, and separate content validation. Keep first
launches separate from repeats: fresh clones do not imply cold host caches.
Use per-boot summaries when comparing the two independent boots per setting.
Preserve busy-host variation and configuration differences in any published claim.
