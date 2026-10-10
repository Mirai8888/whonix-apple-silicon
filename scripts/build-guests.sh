#!/bin/bash
# Run inside the dedicated Debian trixie ARM64 builder as user (not root).
set -euo pipefail
cd /home/user/derivative-maker
[[ $(dpkg --print-architecture) == arm64 ]]
mkdir -p /home/user/build-logs
# grml's deterministic-build validation requires this; derivative-maker's
# NOINTERFACES=true still prevents generic guest networking from being written.
export USE_DEFAULT_INTERFACES=true
# build-config/95_local_arm64.conf must be copied to /home/user/buildconfig.d/.
common=(--target utm --arch arm64 --freshness current --connection clearnet
  --remote-derivative-packages true --repo true --interactive false
  --policy-file "$PWD/buildconfig.d/openpgp-policy.toml" --tb open
  --allow-uncommitted true)
start_at=${WHONIX_START_AT:-gateway}
case "$start_at" in
  gateway|workstation) ;;
  *) printf 'WHONIX_START_AT must be gateway or workstation\n' >&2; exit 2 ;;
esac
if [[ $start_at == gateway ]]; then
  gateway_args=()
  if [[ ${WHONIX_RESUME_GATEWAY:-false} == true ]]; then
    [[ -s /home/user/derivative-binary/18.2.2.7/Whonix-Gateway-LXQt-18.2.2.7.arm64.raw ]]
    gateway_args=(--skip-prepare-build-machine --skip-cowbuilder-setup --skip-local-dependencies)
    gateway_skip=3200_create-raw-image
  else
    gateway_skip=
  fi
  SKIP_SCRIPTS="$gateway_skip" ./derivative-maker --flavor whonix-gateway-lxqt --vmsize 25G "${common[@]}" "${gateway_args[@]}" 2>&1 | tee /home/user/build-logs/gateway.log
fi
workstation_skip=
if [[ ${WHONIX_RESUME_WORKSTATION:-false} == true ]]; then
  [[ -s /home/user/derivative-binary/18.2.2.7/Whonix-Workstation-LXQt-18.2.2.7.arm64.raw ]]
  workstation_skip=3200_create-raw-image
fi
SKIP_SCRIPTS="$workstation_skip" ./derivative-maker --flavor whonix-workstation-lxqt --vmsize 40G "${common[@]}" \
  --tb closed \
  --skip-prepare-build-machine --skip-cowbuilder-setup --skip-local-dependencies \
  2>&1 | tee /home/user/build-logs/workstation.log
