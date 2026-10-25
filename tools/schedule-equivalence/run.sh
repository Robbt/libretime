#!/usr/bin/env bash
#
# CI entry point — see tools/EQUIVALENCE.md.
#
# The scenario lives in ab.sh, which is what developers run by hand against the
# development stack. This only selects the other addressing mode: same seed, same two
# builders, same dumps, but php and python run on this host against the database the
# driver built, instead of inside containers.
#
# One scenario in two modes rather than a second copy of the logic, because a harness
# that drifts from the one developers actually run stops being the same comparison and
# starts being a test nobody looks at.
set -euo pipefail

here="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"

export AB_MODE=local

exec "$here/ab.sh"
