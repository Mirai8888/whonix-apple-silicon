# Security review — 2026-09-17

This historical review records findings in the development workspace. It is
not the vulnerability-reporting policy; see [SECURITY.md](SECURITY.md). File
and VM states describe the reviewed setup at the time of the pass.

## Summary

The review found and fixed three actionable weaknesses: an unvalidated QEMU
device field, guest state readable by other local accounts, and incomplete
bundle file trust checks. Builder SSH and launch error handling were also
hardened. The existing VM configurations use the approved devices; no evidence
of exploitation was observed, and no exploit was run against a VM.

This pass reviews the local Python CLI, shell helpers, C benchmark, build
patches, and macOS file/network boundaries. It is not a certification of the
guest operating systems or hypervisor. Findings below derive from the local
code, installed permissions, bounded fixtures, and the relevant UTM source.

## High priority — fixed

### 001. Sound configuration bypassed the approved QEMU device policy

**Impact:** a crafted bundle could request an additional QEMU device, including
a host-file loader, despite passing the helper's network/storage validation;
actual host-file access would depend on UTM's sandbox and QEMU device behavior.

The old validator checked `AdditionalArguments`, disk, and display settings but
ignored `Sound.Hardware`. UTM 4.7.5 decodes this as an arbitrary QEMU constant and
uses it as a `-device` value. A scratch configuration with a loader device in
this field passed both preparation and `--check` before the fix. This was a
configuration-only proof, not a demonstrated host compromise. The prerequisite
is preparing or accepting an attacker-supplied bundle configuration.

The validator now permits only an empty sound list or the existing `intel-hda`
device. It also rejects `TPMDevice=true`, which starts an additional helper with
an implicit writable `tpmdata` file outside the supported profile's checks.
See [device validation](scripts/prepare_utm.py#L138) and
[sound validation](scripts/prepare_utm.py#L165). The installed Gateway and
Workstation already have `intel-hda` and TPM disabled.

Primary evidence: UTM's
[sound decoder](https://github.com/utmapp/UTM/blob/v4.7.5/Configuration/UTMQemuConfigurationSound.swift#L33-L35),
[device argument generation](https://github.com/utmapp/UTM/blob/v4.7.5/Configuration/UTMQemuConfiguration%2BArguments.swift#L648-L667),
and [TPM state-file handling](https://github.com/utmapp/UTM/blob/v4.7.5/Services/UTMSWTPM.swift#L75-L88).

## Medium priority — fixed

### 002. Local accounts could read guest disks and retained copies

The imported raw disks, builder QCOW2 image, and retained benchmark clones were
mode `0644`, with traversable `0755` enclosing directories. The user's home was
`0750:staff`; its ACL only denied deletion. Another local account in `staff`
could therefore read these guest filesystems, potentially including browser
state, documents, build inputs, and guest SSH host keys. This is a separate
local UID boundary, not an assumption that an attacker already controls the
owner's account. Permission metadata established exposure; no other account was
used to extract guest data.

The existing workspace, `artifacts/`, `builder/`, `logs/`, research directory,
benchmark storage boundaries, and both imported `.utm` directories are now
owner-only (`0700`). They had no access-granting ACL entries. Changing these
directory permissions does not rewrite disk contents or interrupt the running
Gateway. Protection covers existing checkpoints and preliminary trial copies
through their private ancestors.

Future bundle preparation sets `0700`, and `--check` requires that private
boundary. The [builder launcher](scripts/start-builder.sh#L18) requires private
owned storage and uses `umask 077`. The
[benchmark clone helper](scripts/run-headless-benchmark.py#L56) requires a real,
caller-owned `0700` directory before copying, then explicitly sets each fresh
clone to `0600`. This is necessary because macOS `cp -c` can preserve the
template's `0644` permissions despite the process umask.

### 003. Writable or linked bundle files passed launch validation

The old helper checked the bundle directory and parts of the configuration,
but accepted group/other-writable `Data` directories and raw images, including
hard-linked images. A bounded fixture demonstrated acceptance of `Data=0777`
and a `0666` raw image with two hard links. A lower-privilege local account with
that granted write access could replace or modify the guest disk independently
of the checked plist; writable aliases could also affect unintended files.
The actual imported images did not have these writable modes or hard links.

[Metadata validation](scripts/prepare_utm.py#L70) now requires caller ownership,
regular files/directories, no group/other write access, and single-link
configuration/disk/firmware files. [Ancestor validation](scripts/prepare_utm.py#L88)
checks the resolved bundle path against writable or foreign-owned ancestors,
with an exception for root-owned sticky temporary directories. Legitimate
macOS `/var` and `/tmp` aliases are resolved before subsequent bundle access.
The read-only `--check` mode does not repair unsafe input silently.

## Additional hardening

- [Builder SSH](scripts/builder-ssh#L4) uses a dedicated, existing identity and
  pinned host key, ignoring personal SSH configuration and disabling agent use,
  forwarding, and connection sharing. The previously resolved settings already
  had forwarding disabled; this prevents future personal settings from changing
  that boundary, rather than establishing a past credential leak.
- [Builder launch checks](scripts/start-builder.sh#L44) fail closed on ambiguous
  or failed disk/port inspection and reject unsafe PID, disk, seed, and console
  paths. An empty `lsof` result with exit status 1 is the only unused-resource
  result accepted.

## Verified controls and remaining scope

- Only Gateway has the configured external network backend. Workstation retains
  its sole internal connection, with clipboard, directory sharing, and USB
  sharing disabled. The running Gateway listener was observed at
  `127.0.0.1:8010`; the builder SSH port had no listener. No network topology was
  changed or network acceptance test rerun during this pass.
- The inherited loopback socket protocol does not authenticate local clients.
  Another host process can attempt to connect or interfere. Owner-only storage
  does not change that transport property; host trust remains a requirement.
- Local package staging verifies the copied bytes against fixed hashes and
  package metadata. Upstream signature/freshness verification remains enabled,
  and Tor Browser installation fails closed. Kloak's ARM64 drop-in extends the
  existing syscall allowlist without removing the rest of its sandbox.
- The benchmark host caps serial reads at 2 MiB, parses reports as data, and
  cleans up only its own child. The guest benchmark checks root-owned protected
  paths and writes private reports atomically. The fixed-size Pixman workload
  showed no input-dependent memory-safety issue in this review.
- The archived `guest-validation-once.sh` is not installed in the imported
  Workstation. It assumes an offline-installed, root-owned state directory
  inaccessible to untrusted writers. It lacks the newer benchmark's automatic
  directory checks and must not be reused with a writable state directory;
  otherwise its report redirection could follow a malicious symlink.
- Permission checks enforce POSIX modes and ownership. This pass separately
  inspected the actual directory ACLs; future custom ACL grants require review.
  These controls do not defend against root or code already running as the
  owning macOS user. Registered UTM bundles must still be the checked bundles,
  not separate copies carrying the same UUID.
- There was no hypervisor/guest exploit testing, full dependency advisory scan,
  guest image rebuild, or complete anonymity audit. Tor Browser remains the
  ARM64 alpha; Tirdad is unavailable on ARM64. Earlier fail-closed network
  evidence and post-Kloak interactive-testing limits remain in
  [VALIDATION.md](VALIDATION.md).

## Validation

Regression fixtures cover unsafe device fields, ownership, writable paths,
hard links, private clone permissions, builder launch refusal, and SSH options.
They use temporary files and mocked tools; they do not boot a VM, connect to the
builder, or power off the host. The full suite passed **94 tests**; both actual
imported bundles passed read-only validation, shell syntax checks passed, and
`git diff --check` passed. An independent trailing review found no actionable
security regressions in the final diff and confirmed the documented limits.
See [VALIDATION.md](VALIDATION.md) for the runtime evidence boundary.

Gateway remains running, Workstation and builder remain stopped, and UTM was not
brought to the foreground during that pass. The fixes are committed in this
repository. Benchmark evidence and its
historical source hashes are preserved at baseline commit `2029e6d`.
