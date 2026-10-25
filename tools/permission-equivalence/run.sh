#!/usr/bin/env bash
#
# CI entry point — see tools/EQUIVALENCE.md.
#
# ab.sh holds the 336 questions and both legs that answer them; this selects the mode
# where php and the api run on this host against the database the driver built. One
# scenario, two addressing modes, so the comparison CI runs is the one developers run.
set -euo pipefail

here="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"

export AB_MODE=local

exec "$here/ab.sh"
