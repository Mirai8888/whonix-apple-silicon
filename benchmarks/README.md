# Local Tor Browser CPU comparison

This experiment compares four and two Workstation vCPUs at 4096 MiB RAM using
fresh disposable disk clones. It measures the normal Tor Browser launcher's
headless startup, deterministic local JavaScript, layout, rasterization, and
PNG encoding. It does not measure interactive UTM frame rate or Tor throughput.

The [recorded comparison](browser-cpu-results.json) retains the existing
four-vCPU profile. See [performance results](../PERFORMANCE.md) for the
per-boot timings, host-load limitations, and content-validation record.

For native ARM64/HVF versus emulated x86-64/TCG at the same CPU and RAM allocation,
see the separate [architecture comparison protocol](ARCHITECTURE.md).

The workload is entirely local and uses no external assets, requests, workers,
or timers. Its synchronous script creates 4,000 cards, forces layout, and renders
`PASS checksum=e5f37d05 cards=4000` only on successful completion. Browser
preferences and sandbox settings remain unchanged. Only the fixed benchmark
child skips the launcher's link-confirmation dialog.

## Prepare a disposable template

Keep the imported Workstation stopped and leave it untouched. Create a private
`artifacts/benchmarks/browser-cpu/` directory (owned by the current macOS account,
mode `0700`), APFS-clone the stopped Workstation's raw disk to `template.raw`,
and explicitly set the clone to `0600`. Do not attach an imported VM disk to
maintenance while it is running.

Using the maintenance builder, mount only that disposable template and install:

| Source | Path inside disposable guest | Owner / mode |
| --- | --- | --- |
| `scripts/guest-browser-benchmark.py` | `/var/lib/whonix-arm64-browser-benchmark/guest-browser-benchmark.py` | root:root / `0700` |
| Empty marker | `/var/lib/whonix-arm64-browser-benchmark/enabled` | root:root / `0600` |
| `benchmarks/browser-local.html` | `/home/user/.tb/tor-browser/Browser/Downloads/bench/local.html` | user:user / `0600` |
| `benchmarks/whonix-arm64-browser-benchmark.service` | `/etc/systemd/system/whonix-arm64-browser-benchmark.service` | root:root / `0644` |

The state directory must be root-owned `0700`; the `bench` directory must be
user-owned `0700`. Enable the service in the template's `graphical.target.wants`.
The marker is consumed once, reports remain private, and the guest requests
shutdown after success or failure. The service also has a runtime limit and
powers off its disposable guest on failure. Never install it in the production
Workstation. Unmount the template and shut down the maintenance builder before
running trials.

## Run and validate

With Gateway already running, Workstation stopped, and builder stopped:

```sh
/usr/bin/python3 scripts/run-browser-benchmark.py --smoke
```

The smoke run creates a four-vCPU copy. It must finish and shut itself down.
Extract its PNGs from the stopped copy using offline maintenance; verify the
visible PASS checksum and card count. A syntactically valid PNG or exit code
zero alone does not establish that the workload completed. Reports deliberately
retain `captured-unvalidated` until this separate content check is recorded.

Then run the default four-boot comparison:

```sh
/usr/bin/python3 scripts/run-browser-benchmark.py
```

The order is 4, 2, 2, 4 vCPUs. Each guest settles for 40 seconds, performs one
first launch and three repeated launches, and powers off. A launch has a
60-second timeout. Keep the first launch separate from repeated launches;
host caching means a fresh clone is not a guaranteed cold physical-disk cache.
The host runner refuses to overwrite trial disks or logs. Preserve failed
smoke evidence separately before retrying, only after its VM is stopped.

Guest durations use monotonic time and Linux pidfd exit notification;
unsupported pidfds fail explicitly. Guest counters include minimum sampled
available RAM, memory/CPU/I/O pressure, swap, faults, and reclaim. Host CPU
measurements cover boot, settling, and workload; they are not isolated browser
CPU measurements. Host RSS peaks are sampled, and host swap counters include
all applications. Busy-host variation and the small sample count limit claims.

Validate the final trial PNGs as well. Keep only resource changes supported by
repeated measurements and acceptable regressions; the runner itself never
changes the imported UTM profiles. Original security and privacy settings,
including the single internal Workstation NIC and static guest display policy,
are retained.
