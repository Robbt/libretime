"""Run the Python criteria compiler over the fixture blocks.

Prints the same lines as run-php.php, so the two can be compared directly.
"""

import django

django.setup()

from libretime_api.schedule.models import SmartBlock  # noqa: E402
from libretime_api.schedule.services.criteria import (  # noqa: E402
    UnsupportedCriteria,
    file_ids,
)

blocks = list(SmartBlock.objects.filter(id__range=(9500, 9599)).order_by("id"))

if not blocks:
    raise SystemExit("python: no fixture blocks, run seed.sql first")

for block in blocks:
    try:
        ids = file_ids(block)
    except UnsupportedCriteria as error:
        print(f"{block.pk}|UNSUPPORTED {error}")
        continue

    print(f"{block.pk}|{','.join(str(i) for i in ids)}")
