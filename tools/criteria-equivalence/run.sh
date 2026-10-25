#!/usr/bin/env bash
#
# CI entry point — see tools/EQUIVALENCE.md.
#
# ab.sh holds the scenario; this selects the mode in which php and python run on this
# host against the database tools/equivalence-run.sh built, instead of inside the
# development containers. One scenario, two addressing modes, so that the comparison CI
# runs cannot drift from the one developers run by hand.
set -euo pipefail

here="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"

export AB_MODE=local

exec "$here/ab.sh"
