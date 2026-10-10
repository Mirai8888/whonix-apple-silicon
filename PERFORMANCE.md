# Apple Silicon performance results — 2026-09-17

This report describes the recorded development host. Local-only raw evidence
and VM state are not distributed. The included compact summaries preserve the
reported measurements; fresh replication requires the documented guest inputs.

The ARM64 Kloak compatibility fix reduced measured headless Workstation idle CPU
from **3.79% to 1.06% of one host core**, using the median of two boots per
condition. That is **72.1% lower CPU use in these short trials**. Both control
guests restarted Kloak 11 times during the measured minute; both repaired guests
had zero restarts and a stable Kloak PID.

The fix was already installed in the imported Workstation. This measurement
pass used disposable APFS disk clones and left that disk unchanged. All four
trial VMs and the maintenance builder shut down cleanly. Gateway remains running
in UTM, and the imported Workstation remains stopped to keep its console closed.

## Idle comparison

| Trial order | ARM64 syscall fix | Host CPU, % of one core | Guest CPU busy, % across four vCPUs | Kloak restarts in interval |
| --- | --- | ---: | ---: | ---: |
| 1 | Disabled | 4.41 | 1.05 | 11 |
| 2 | Enabled | 1.25 | 0.23 | 0 |
| 3 | Enabled | 0.87 | 0.20 | 0 |
| 4 | Disabled | 3.16 | 0.76 | 11 |
| Control median | Disabled | **3.79** | **0.90** | 11 |
| Repaired median | Enabled | **1.06** | **0.21** | 0 |

The control differs only by disabling the additive Kloak systemd drop-in. The
repair permits the ARM64 equivalents of syscalls already allowed by the
upstream service; it retains the original binary and remaining sandbox. See
[validation](VALIDATION.md) for the repair and input-device checks.

The host was in normal use throughout. Host-wide swap-in ranged from 48.9 to
130.6 MiB per interval, and the final control interval included 590.6 MiB of
swap-out. These are host-wide counters, not swap attributed to the guest.
Two samples per condition do not establish a confidence interval or an exact
repeatable reduction. Both repaired trials nevertheless used less idle CPU
than either control, alongside elimination of the observed restart loop.

## Existing ARM64 acceleration

Native CPU crypto and Pixman's AArch64 NEON path were **already enabled**.
These tests confirm their benefit for specific synthetic workloads; this pass
did not newly enable them. Each table entry is the median of six samples per
mode, pooled across the two repaired boots. Rates use decimal GB and Gpixels.

| Workload | Automatic ARM64 path | Backend disabled for comparison | Ratio of medians |
| --- | ---: | ---: | ---: |
| OpenSSL AES-256-GCM, 16 KiB buffers | 5.81 GB/s | 0.166 GB/s | 35.03× |
| OpenSSL SHA-256, 16 KiB buffers | 2.08 GB/s | 0.390 GB/s | 5.34× |
| Pixman 1920×1080 reset + OVER composition | 1.586 Gpixels/s | 0.217 Gpixels/s | 7.32× |

OpenSSL was Debian `3.5.7-1~deb13u2`; Pixman was `0.44.0-3`. Guest CPU features
include ASIMD, AES, PMULL, and SHA instructions through HVF's host CPU model.
OpenSSL comparison children use `OPENSSL_armcap=0`; Pixman comparison children
use `PIXMAN_DISABLE=arm-neon`. These process-local settings do not persist and
are not guaranteed pure-scalar baselines.

The [crypto harness](scripts/benchmark-arm64-crypto.py) alternates mode order
over three one-second samples per algorithm per boot. It uses OpenSSL's elapsed
timer, whose Linux implementation uses elapsed `times()` ticks rather than
adjustable wall-clock time. The [Pixman benchmark](research/pixman-benchmark.md)
alternates modes over three samples each per boot and times at least one second
with `CLOCK_MONOTONIC`. Its rate includes restoring the destination on every
iteration. All Pixman outputs match the independent RGB checksum
`457c95b0871b8af3`.

These ratios are not desktop frame rates, Tor throughput, overall application
speedups, or evidence of performance parity with native macOS applications.
Graphics remain software-rendered; native GPU acceleration has not been
established.

## Method and evidence

- Host: Apple M5, 10 cores (4 performance + 6 efficiency), 24 GiB RAM, macOS 26.2.
- Guest: native AArch64, four vCPUs, 4096 MiB RAM, QEMU 11.1.1 with HVF and
  `-cpu host`. Trials run headless with `ramfb` plus `virtio-gpu-pci`; UTM's own
  QEMU build uses the combined `virtio-ramfb` device. Results therefore describe
  this headless configuration rather than measured UTM GUI responsiveness.
- Every boot uses a fresh APFS clone. The sole guest NIC connects to Gateway's
  existing internal socket; no external Workstation NIC is added.
- Order: control, repaired, repaired, control. Each boot has a 40-second warmup,
  a 60-second guest idle interval, then a five-second idle guard before benchmark
  work or shutdown. Host CPU uses cumulative process CPU time and monotonic
  per-process timestamps around the guest's serial markers.
- Serial polling is nominally 250 ms. The maximum observed gap was 275.4 ms;
  maximum end-sample latency was 43.4 ms. Host intervals ranged from 59.987 to
  60.116 seconds, within the guard and the enforced timing bounds.
- Crypto and Pixman work starts after idle measurement and its guard. The
  benchmark service exists only in disposable copies and powers them off.

[Summary](logs/kloak-comparison/summary.json),
all trial results (local-only: `logs/kloak-comparison/results.json`), and
[method with source/binary hashes](logs/kloak-comparison/method.json) preserve
the measurements. The same directory contains each trial's JSON and serial
log. An independent audit matched all four serial reports to their JSON,
recomputed the summaries, and checked all five recorded hashes. The
[host runner](scripts/run-headless-benchmark.py) and
[guest sampler](scripts/guest-idle-benchmark.py) retain the measurement logic.
The recorded source hashes describe the measured code preserved in local Git
commit `2029e6d`. Subsequent security changes harden storage permissions and
launch checks; the historical measurements and their manifest remain unchanged.

An earlier exploratory run lacked the five-second guard and could include
post-idle work in host CPU samples. Its files remain separately under
`logs/kloak-comparison-preliminary/`; none contribute to these final results.
The older UTM baseline in `logs/performance-before-kloak.json` also remains
excluded because its configuration differs.

## Browser CPU allocation comparison

The existing **4-vCPU / 4-GiB Workstation profile is retained**. A four-boot
comparison against two vCPUs at the same RAM allocation found lower grouped
launch times with four CPUs, but substantial variation between boots. This is
evidence against reducing the current allocation for this workload, not a new
speedup applied to the imported Workstation.

| Boot order | vCPUs | First launch, seconds | Median of three repeat launches, seconds | Minimum sampled guest available RAM, GiB |
| --- | ---: | ---: | ---: | ---: |
| 1 | 4 | 0.712 | 0.340 | 3.092 |
| 2 | 2 | 0.897 | 0.708 | 3.109 |
| 3 | 2 | 0.719 | 0.390 | 3.111 |
| 4 | 4 | 0.685 | 0.399 | 3.057 |

The first-launch medians across two boots were 0.698 seconds with four CPUs and
0.808 seconds with two. The corresponding medians of each boot's repeat-launch
median were 0.369 and 0.549 seconds. One two-CPU boot was comparable to the
four-CPU boots; the other was appreciably slower. Two boots per setting on a
busy host cannot establish a reliable percentage speedup or confidence interval.

Each launch runs the normal Tor Browser wrapper and profile in headless mode
against an authored local page. The measured interval includes startup,
deterministic JavaScript, layout, rasterization, PNG encoding, and process exit.
It uses monotonic time and Linux pidfd exit notification. No browser preferences
or sandbox settings were changed. A fresh disk clone does not guarantee a cold
host disk cache, so the first launch is kept separate from repeat launches.

All 16 reported PNG hashes match the [reference image](benchmarks/browser-workload-reference.png).
The first PNG from the final boot was also extracted through a read-only offline
mount: its hash matches the reference, and visual inspection confirms
`PASS checksum=e5f37d05 cards=4000`. The raw capture reports remain marked
`captured-unvalidated`; this separate content validation is recorded in the
[compact results and source manifest](benchmarks/browser-cpu-results.json).

Guest available RAM never sampled below 3.057 GiB. No launch recorded an OOM,
memory-pressure, reclaim, or swap-counter increase; these guests have no swap
configured. Sampling every 250 ms can miss short memory peaks. This small local
page provides no reason to increase RAM, but does not represent many tabs or
large websites. Host-wide swap activity occurred in every trial and cannot be
attributed to the guest. Host CPU covers boot, settling, and workload together;
it is not isolated browser CPU time.

The [reproduction protocol](benchmarks/README.md), host and guest runners, local
page, reference PNG, and compact evidence are included in Git. Raw logs and disk
clones remain local under `logs/browser-cpu/` and
`artifacts/benchmarks/browser-cpu/`. Both smoke attempts are excluded from the
performance data: the first stopped at a filesystem permission guard, and the
second validated the rendered page before completion timing switched to pidfd.
All final trials and the maintenance builder shut down cleanly. The imported
Workstation disk and UTM resource profiles were not modified by this experiment.

## Native ARM64 versus emulated x86-64

Native ARM64/HVF completed this local browser workload substantially sooner
than full-system x86-64/TCG on the same Apple M5 host. Both configurations use
four vCPUs, 4096 MiB RAM, QEMU 11.1.1, and Tor Browser 16.0a11 alpha with Firefox
153.2.0 and matching application/platform build IDs `20260902080000`.

| Boot order | Configuration | First launch, seconds | Median of three repeat launches, seconds |
| --- | --- | ---: | ---: |
| 1 | ARM64 / HVF | 0.709 | 0.340 |
| 2 | x86-64 / TCG | 15.331 | 12.812 |
| 3 | x86-64 / TCG | 11.902 | 9.571 |
| 4 | ARM64 / HVF | 0.577 | 0.326 |

The first-launch medians across two boots were **0.643 seconds for ARM and
13.616 seconds for x86**, a **21.2× elapsed-time ratio**. The medians of each
boot's repeat-launch median were **0.333 and 11.192 seconds**, respectively,
a **33.6× ratio**. These describe two boots per configuration on an active host;
they do not establish a confidence interval or a general application speedup.
The primary repeat statistic gives each boot equal weight; the separate
pooled-six medians and all individual timings are retained in the evidence.

Each boot uses a fresh disposable disk clone and a 40-second settling period.
Both browser profiles first completed four unmeasured smoke launches, so neither
final sample includes first-ever profile creation. Existing profiles were
retained and host caches were not flushed. The workload measures headless
startup, synchronous local JavaScript, layout, rasterization, PNG encoding,
and exit. Browser sandbox and privacy settings were preserved.

All **16 final PNGs** were extracted through read-only offline mounts. Every
actual file matches its reported hash and the existing reference image;
inspection of one final image per architecture confirms
`PASS checksum=e5f37d05 cards=4000`. Raw reports retain their original
`captured-unvalidated` status; the independent acceptance is recorded separately
in the [compact results](benchmarks/architecture-results.json), together with
source hashes, signed input provenance, browser metadata, and QEMU arguments.
Host serial observations corroborated the x86 elapsed times: maximum per-launch
differences were 0.270 and 0.259 seconds, below the respective observed polling
gaps of 0.579 and 0.530 seconds. This is a coarse clock sanity check, not a formal
timing-error bound; ARM launches are too short for precise serial corroboration.

The first x86 boot's repeat median was 12.812 seconds versus 9.571 in the second.
Host-wide swap-out during that first x86 trial was 354.5 MiB; other applications
share these counters, so this cannot be attributed to the guest. No launch
recorded guest OOM, swap, reclaim, or memory-pressure counter increases; the
guests have no swap configured. Minimum sampled available guest RAM was
3.061 GiB for ARM and 2.915 GiB for x86. These samples do not establish multi-tab
memory requirements.

The ARM guest is Whonix `18.2.2.7-developers-only`, Debian 13.7, Linux
`6.12.107+deb13-arm64`; the signed x86 image is Whonix `18.2.1.9`, Debian 13.6,
Linux `6.12.95+deb13-amd64`. Kernel, library, profile, graphics-device, and
virtualization-backend differences remain. This comparison cannot isolate ISA
efficiency or measure UTM GUI responsiveness, Tor throughput, or parity with
native macOS applications. See the [reproduction protocol](benchmarks/ARCHITECTURE.md).
All trial VMs and the maintenance builder shut down cleanly; the imported
Workstation and its resource settings were unchanged.

## Preserved settings and remaining measurements

The Gateway remains at 2 vCPUs / 2 GiB; its allocation was not compared here.
Workstation remains at 4 vCPUs / 4 GiB following the browser comparison above.
Retina rendering remains off. Workstation's static 1920×1080 mode
is an explicit upstream privacy policy, so it and Tor Browser letterboxing are
retained. Pixman already selects its ARM64 implementation automatically; no
display-policy change is needed to obtain the measured component benefit.

Actual UTM interaction after the Kloak repair, keystroke timing behavior,
multi-tab browser memory demand, and end-to-end responsiveness still need measurement.
The existing build remains experimental, as detailed in [validation](VALIDATION.md).
