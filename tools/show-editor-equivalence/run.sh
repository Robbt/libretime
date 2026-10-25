#!/usr/bin/env bash
#
# CI entry point — see tools/EQUIVALENCE.md.
#
# ab.sh holds the scenarios; this selects the mode in which php and python run on this
# host, against the database tools/equivalence-run.sh built, instead of inside the
# development containers and a cloned station schema.
#
# Note in the README what changes of substance between the modes: the schema source. A
# station's schema accumulated its constraints through upgrades, the installer SQL is
# what a new install gets, and the show editor's deletes are where the two differ.
set -euo pipefail

here="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"

export AB_MODE=local

exec "$here/ab.sh"
