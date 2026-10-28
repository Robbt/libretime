# Smart block criteria equivalence harness

Checks that `libretime_api/schedule/services/criteria.py` returns the same files,
in the same order, as `Application_Model_Block::getListofFilesMeetCriteria()` in
the legacy service. A smart block decides what a station plays, so "it looks
right" is not enough: the answer is a list of files and its order.

## Run it

With the development stack up:

```bash
tools/criteria-equivalence/ab.sh            # both sides, then compare
tools/criteria-equivalence/ab.sh php        # legacy side only
tools/criteria-equivalence/ab.sh python     # python side only
```

### In CI

Same scenario, no containers. `run.sh` sets `AB_MODE=local`, and
`tools/equivalence-run.sh` (see `tools/EQUIVALENCE.md`) provides what the container mode
gets from the development stack: a database built by the api's own migrations, one config
file both sides read, and a RabbitMQ broker.

```bash
tools/equivalence-run.sh tools/criteria-equivalence
```

On a workstation that means php 8.2 with `bcmath`, `yaml` and `pdo_pgsql`,
`make -C legacy vendor` in this checkout, `make -C api install`, and a postgres and a
broker the run can reach:

```bash
docker run -d --name eq-pg -e POSTGRES_PASSWORD=postgres -p 5432:5432 postgres:15
docker exec eq-pg psql -U postgres -c "CREATE USER libretime WITH PASSWORD 'libretime' CREATEDB;"
EQ_DB_SUPER_PASSWORD=postgres tools/equivalence-run.sh tools/criteria-equivalence
```

Steps, each bounded by a timeout and printed:

1. `seed.sql` - a nine file library and 38 blocks, one per behaviour of the
   legacy compiler.
2. `run-php.php` - the legacy compiler over every fixture block.
3. `seed.sql` again, so both sides see identical rows.
4. `run_python_criteria.py` - the Python compiler over the same blocks.
5. `compare_criteria.py` - line by line, `<block id>|<file ids in order>`.

When a block differs, both answers are printed in full.

To hand the database back afterwards:

```bash
docker exec -i libretime-postgres-1 psql -U libretime -d libretime \
    < tools/criteria-equivalence/clean.sql
```

## Why the legacy side is run from the checkout

The first run of this harness reported criteria groups behaving as
`(a AND b) OR c` - the left to right chaining from before
[#3318](https://github.com/libretime/libretime/pull/3318), because the legacy
container runs the code baked into its image, not the code under review.

`ab.sh` therefore starts a throwaway container from the same image with
`legacy/application` from this checkout mounted over the image's application
directory - the image's `vendor/` stays, since composer dependencies are not in
git - and verifies the mounted `Block.php` has the same checksum as the one in
the worktree before running anything. If a future change to the legacy criteria
code is not in the checkout, the comparison is against the wrong code, and that
has to fail loudly rather than quietly agree.

## What the fixture blocks cover

Each row of `seed.sql`'s block list exists because the legacy code does
something specific there, and several exist because the Python side originally
got it wrong:

| blocks      | behaviour                                                                                                                              |
| ----------- | -------------------------------------------------------------------------------------------------------------------------------------- |
| 9501, 9502  | `contains` and `does not contain`; the negation keeps files whose genre is null, but only on the first negating row of that field      |
| 9503, 9504  | `is not` on a first row keeps nulls; a second negated group drops them again                                                           |
| 9504        | two negations of one field - the reason the null alternative is attached to the first row only                                         |
| 9505        | `is` is exact and case sensitive, so `mood is Calm` does not match `calm`                                                              |
| 9506, 9507  | `starts with` and `ends with`, case insensitive                                                                                        |
| 9508        | `%` in a value is escaped, not a wildcard                                                                                              |
| 9509 - 9511 | `bit_rate` and `sample_rate` are entered in kbit and stored in bit                                                                     |
| 9512 - 9514 | `is` on an interval becomes a prefix match on its text, because the user sees a rounded value                                          |
| 9515        | `year` is compared as stored text                                                                                                      |
| 9516, 9517  | `owner_id` compares the login of the owner, joined from cc_subjs                                                                       |
| 9518, 9519  | track type as an id                                                                                                                    |
| 9520 - 9522 | `filepath` matches the file name, so a folder name cannot match                                                                        |
| 9523, 9524  | `before` and `after` on absolute timestamps                                                                                            |
| 9525        | `after "7 days ago"`, the relative form storeCriteriaIntoDb() saves                                                                    |
| 9526 - 9528 | `before` keeps files that never aired; `after` and `between` do not, because `COALESCE(col, DATE '-infinity')` fails those comparisons |
| 9529, 9530  | `is in the range` on timestamps, and the `now{Y-m-d}` placeholder                                                                      |
| 9531, 9532  | `alpha AND (beta OR gamma)`, written in both orders: groups are AND-ed, rows within a group OR-ed                                      |
| 9533, 9534  | contradicting groups of one field match nothing; two fields are AND-ed                                                                 |
| 9535 - 9538 | the four deterministic sorts, including that files which never aired come last for "most recently played"                              |

`random` is deliberately absent: the legacy compiler orders by `random()`, so
its answer differs between runs and there is nothing to compare. The fill
algorithm that consumes these lists - repeats, the subset-sum solver, overflow -
is not part of this harness either, and inherits the same limitation.
