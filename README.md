# Oracle Schema Comparison Tool

This tool compares Oracle table and column metadata between two databases and can apply the safe structural changes to the second database.

DB1 is the source schema. DB2 is the schema that should receive those changes. A table-name prefix limits the comparison to one group of tables, such as `ROWIS_` or `CLAIM_`.

It was created for the common case where an older database still holds the schema you trust, and a newer database has fallen behind. Checking `ALL_TABLES` and `ALL_TAB_COLUMNS` by hand is slow, and a missed column length or nullability change is easy to overlook. The tool reads the dictionary views, prints every missing or different table and column, shows the SQL it would run, and waits for a `yes` before changing DB2.

## What a run does

1. Connect to DB1 and DB2.
2. Read table and column metadata for the prefix.
3. Print what is missing or different in DB2.
4. Print the SQL that would make DB2 match DB1 for those tables and columns.
5. Ask whether to run that SQL on DB2.

Answer `yes` or `y` to apply it. Any other answer leaves both databases unchanged. If the command is not running in an interactive terminal, the SQL is shown and nothing is applied.

DB1 is never changed. Objects that exist only in DB2 stay in place. Each successful Oracle DDL statement commits on its own, so a later failure leaves the earlier statements applied.

## Requirements

- Python 3.12 or newer.
- Network access to both Oracle databases.
- An account that can read `ALL_*` dictionary views for the schema being compared.
- python-oracledb thin mode by default. No Oracle Client install is required for databases that thin mode supports.
- Oracle Instant Client 19c when one database is Oracle 11g. Thick mode is process-wide, so both connections then use that client. Instant Client 19c can reach Oracle 11g and newer databases, including 26ai. A 23ai client cannot connect to 11g.

## Setup

From the project directory:

```bash
py -3.12 -m venv .venv
.venv\Scripts\activate
pip install -r requirements.txt
copy .env.example .env
```

In Git Bash:

```bash
py -3.12 -m venv .venv
source .venv/Scripts/activate
pip install -r requirements.txt
cp .env.example .env
```

Then edit `.env`. The file is listed in `.gitignore` and must stay on your machine. The repository includes `.env.example` with placeholder values only.

Run the tool with the virtual environment's Python so the installed packages are found:

```bash
.venv\Scripts\python.exe main.py test-connections
```

## Environment file

`.env` lives in the project directory. `python main.py` reads it automatically. Pass `--env-file` when the file has another path.

```text
ORACLE_DB1_USER=source_user
ORACLE_DB1_PASSWORD=source_password
ORACLE_DB1_HOST=192.168.1.100
ORACLE_DB1_PORT=1521
ORACLE_DB1_SERVICE_NAME=ORCL
ORACLE_DB1_MODE=thin

ORACLE_DB2_USER=target_user
ORACLE_DB2_PASSWORD=target_password
ORACLE_DB2_HOST=192.168.1.101
ORACLE_DB2_PORT=1521
ORACLE_DB2_SERVICE_NAME=ORCL
ORACLE_DB2_MODE=thin

TABLE_PREFIX=ROWIS_
```

| Variable | Purpose |
| --- | --- |
| `ORACLE_DB1_USER` | Login for the source database. |
| `ORACLE_DB1_PASSWORD` | Password for that login. It is never printed. |
| `ORACLE_DB1_HOST` | Host name or IP address. |
| `ORACLE_DB1_PORT` | Listener port. `1521` is used when this is omitted. |
| `ORACLE_DB1_SERVICE_NAME` | Oracle service name, not a SID. |
| `ORACLE_DB1_MODE` | `thin` or `thick`. Use `thick` for Oracle 11g. |
| `ORACLE_DB2_*` | The same settings for the target database. |
| `ORACLE_CLIENT_LIB_DIR` | Directory that contains Instant Client, required when either mode is `thick`. |
| `TABLE_PREFIX` | Default table-name prefix. `--prefix` overrides it. |

The schema owner defaults to the login user in uppercase. Pass `--db1-owner` and `--db2-owner` when the schema is different from the login, or when the two owners differ.

### Oracle 11g and a newer database

Thin mode cannot connect to Oracle 11g (`DPY-3010`). Install [Oracle Instant Client 19c](https://www.oracle.com/database/technologies/instant-client.html), then point DB1 at it. Thick mode applies to the whole process, so DB2 uses the same client.

```text
ORACLE_DB1_MODE=thick
ORACLE_DB2_MODE=thin
ORACLE_CLIENT_LIB_DIR=C:\oracle\instantclient_19_3
```

`ORACLE_DB2_MODE` can stay `thin` in the file. Once thick mode is enabled for either database, both connections use the Instant Client.

## How to use it

Activate the virtual environment first, or call `.venv\Scripts\python.exe` directly.

### 1. Test the connections

```bash
python main.py test-connections
```

This opens DB1 and DB2 separately and prints the Oracle version, current schema, and driver mode. A failure on DB1 still produces a DB2 result. Passwords are removed from error text.

A successful run looks like this:

```text
Testing DB1 (source_user@192.168.1.100:1521/ORCL)...
DB1 OK source_user@192.168.1.100:1521/ORCL schema SOURCE_USER Oracle 11.2.0.4.0 (thick mode)
Testing DB2 (target_user@192.168.1.101:1521/ORCL)...
DB2 OK target_user@192.168.1.101:1521/ORCL schema TARGET_USER Oracle 23.26.2.0.0 (thick mode)
Both database connections succeeded.
```

### 2. Compare one prefix

```bash
python main.py compare --prefix ROWIS_
```

This compares every table whose name starts with `ROWIS_`, using each login user as the schema owner. Matching is case-insensitive, so `rowis_` finds `ROWIS_CL_DOC_REQ_D`. The report keeps the names Oracle returned.

The console shows:

- Tables and columns present in DB1 and missing or different in DB2.
- Extra tables and columns that exist only in DB2. These are reported and left alone.
- Warnings when a dictionary property could not be compared, such as identity columns on a database that does not expose them.

### 3. Compare different schema owners

```bash
python main.py compare --prefix CLAIM_ --db1-owner ROWIS --db2-owner ROWIS_NEW
```

DB1 is read from schema `ROWIS`. Changes are planned for schema `ROWIS_NEW` on DB2. Use this when the target schema name is different from the source, or different from the login user.

### 4. Use a specific environment file

```bash
python main.py --env-file .env compare --prefix DOC_
```

`--env-file` belongs before the command name. `--prefix` overrides `TABLE_PREFIX` from that file. An empty prefix is rejected so the tool cannot read every table in the schema. `_` in a prefix is a literal character.

### 5. Apply the changes to DB2

When the comparison finds table or column changes, the tool prints the statements and asks:

```text
Apply these changes to DB2? Type yes to continue:
```

Type `yes` to run them on DB2. The statements can:

- Create a missing table.
- Add a missing column.
- Change a column's type, length, `BYTE` or `CHAR` semantics, precision, scale, default, or visibility.
- Set table and column comments.
- Make a column nullable, or make it `NOT NULL` when DB2 has no nulls in that column.

Example for a column that is `VARCHAR2(20)` in DB2 and `VARCHAR2(500)` in DB1:

```sql
ALTER TABLE ROWIS.ROWIS_CL_DOC_REQ_D MODIFY (TES VARCHAR2(500 BYTE))
```

`NULL` is included only when nullability itself differs. Repeating `NULL` on a column that is already nullable makes Oracle raise `ORA-01451`.

A length change and a `NOT NULL` change are separate statements. Before `NOT NULL`, the tool checks DB2 for existing nulls. If any row is null, that statement is skipped and the length change can still run. Before a shorter length, the tool checks whether a stored value would no longer fit. Fill the data, then run the compare again.

These differences are listed for manual review and are not executed:

- Column order.
- Index-organized, partitioned, nested, or clustered tables.
- Identity or virtual-column changes on a column that already exists.
- A table rename.

Constraints, primary keys, foreign keys, and indexes are outside this comparison, so the apply step does not create them.

## What is compared

Tables are matched by case-insensitive name. Columns on matching tables are matched by case-insensitive column name.

Tables: comments, temporary flag, duration, index-organized type, partitioning, nested flag, status, and cluster. Tablespace names are ignored because they usually differ between environments on purpose.

Columns: position, data type, character length, `BYTE` versus `CHAR`, numeric precision and scale, nullability, default, virtual flag, hidden flag, identity flag and options, `DEFAULT ON NULL`, and comments.

`VARCHAR2(100 BYTE)` and `VARCHAR2(100 CHAR)` are different. Character length is compared in characters when `CHAR_LENGTH` is available, so a difference caused only by the database character set is not reported.

Defaults are trimmed, unquoted text is uppercased, and whitespace outside quotes is collapsed. Text inside quotes keeps its case. `DEFAULT NULL` is treated as no default. For identity columns, the system-generated sequence name is not compared.

| Status | Meaning |
| --- | --- |
| `MISSING_IN_DB2` | Present in DB1 and absent from DB2. |
| `DIFFERENT` | Present in both, and a property does not match. |
| `ONLY_IN_DB2` | Extra object in DB2. It is left in place. |
| `WARNING` | The check could not be completed reliably. |
| `MATCH` | Equivalent. Matching objects are omitted from the report. |

A missing table is one difference. Its columns are not repeated as separate missing columns.

## Exit codes

| Code | Meaning |
| --- | --- |
| 0 | The command finished. A comparison can finish with differences and still return 0 when nothing failed. |
| 1 | A connection, dictionary query, or DB2 statement failed, or a statement was skipped because existing rows do not allow it. |
| 2 | Configuration or command-line usage is invalid. |

## Dictionary views

Metadata queries use `ALL_TABLES`, `ALL_TAB_COLUMNS`, `ALL_TAB_COMMENTS`, `ALL_COL_COMMENTS`, and `ALL_TAB_IDENTITY_COLS`.

`ALL_*` views show objects the connected user can access and include an owner column, so DB1 and DB2 can use different owners without a DBA account. This tool does not query `DBA_*` or `USER_*` views.

The tool asks the dictionary which columns exist before building each metadata query. Optional properties that are missing on either database are skipped and reported as warnings. User-generated columns are compared. Oracle-maintained columns, recycle-bin tables, index-organized overflow segments, and secondary objects are skipped when those dictionary columns exist.

Metadata reads are single `SELECT` statements with bind variables for the owner and prefix. DDL runs only on the DB2 connection, only after you type `yes`, and only when the statement is a `CREATE TABLE`, `ALTER TABLE ADD`, `ALTER TABLE MODIFY`, or `COMMENT` produced by this tool.

## Tests

Unit tests use fake metadata and do not need a database:

```bash
pytest
```

Live checks are skipped unless you opt in. Put real connections in `.env`, then:

```bash
set ORACLE_SCHEMA_DIFF_INTEGRATION=1
pytest -m integration
```

The live test calls `test-connections` and `compare` for `TABLE_PREFIX`. It reads dictionary views and does not apply DDL.

## Troubleshooting

`Missing required configuration`  
Copy `.env.example` to `.env` and set both connections. Process environment variables override the file.

`No module named 'pydantic'`  
The system Python does not have the project packages. Use the virtual environment: `.venv\Scripts\python.exe main.py test-connections`.

`DPY-3010`  
The database is too old for thin mode. Oracle 11g needs thick mode and Instant Client 19c. Set `ORACLE_DB1_MODE=thick` and `ORACLE_CLIENT_LIB_DIR` before the first connection.

`ORA-01017`  
The user or password was rejected. The password is not included in the error text.

`ORA-12541` or `ORA-12170`  
The host or port could not be reached. Check the firewall and listener.

`ORA-12514`  
The listener does not know that service name.

`ORA-01451: column to be modified to NULL cannot be modified to NULL`  
The column is already nullable. Current builds omit `NULL` unless nullability is the change being applied.

`ORA-02296: cannot enable ... null values found`  
DB2 has nulls in a column that DB1 defines as `NOT NULL`. The length change is applied separately. Fill the nulls, then run the compare again.

`cannot read ALL_TABLES` or zero tables for a prefix you know exists  
`ALL_*` returns only objects the account can see. Connect as the schema owner, or grant privileges on that schema's tables.

## Project layout

```text
main.py                         command-line entry point
cli.py                          connection tests, compare, and the DB2 apply prompt
config/settings.py              .env loading
database/oracle_connection.py   thin and thick connections
database/metadata_repository.py ALL_* metadata reads
database/ddl_guard.py           allows only the generated DB2 DDL
migration/sql_generator.py      CREATE, ALTER, and COMMENT statements
comparison/                     table and column comparison
reporting/console_report.py     console report
tests/                          unit tests
.env.example                    placeholder configuration
```

Excel, HTML, and JSON reports, plus constraint and index comparison, are not in this version.
