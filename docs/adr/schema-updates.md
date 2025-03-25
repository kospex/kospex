
# ADR - SQLite db schema upates

Over time, kospex will possibly require changes to columns in tables. \
The following is the proposed initial approach for adding columns to tables, given constraints on how SQLite works.
Our initial implementation is to:
- create a kospex upgrade-db CLI function
- Allow adding of columns which needs to allow nulls
- run an upgrade process (applying the alter table commands)
- update the schema version in the kospex DB TBL_KOSPEX_CONFIG table 'kospex_config'

From [SQLite Alter table](https://www.sqlite.org/lang_altertable.html) \
*The new column is always appended to the end of the list of existing columns.*

## Initial implementation

The schema update capability was introduced in verion 0.0.16

This simpler version covers the core functionality we need:
 - Extracts columns from CREATE TABLE statements
 - Handles SQL comments correctly
 - Handles square-bracketed column names
 - Identifies new columns
 - Generates proper ALTER TABLE commands

The main things it doesn't handle:
 - No error handling for malformed SQL
 - Doesn't handle column definitions without square brackets
 - Doesn't preserve table constraints
 - Doesn't validate the table names match
 - Can't change constraints

The table name matching is handled outside the KospexSchema generate_alter_table, and is passed in as a variable.

The current implementation only looks at the column definitions and ignores all the constraints at the end.
A few notes:
 - We can't add constraints with ALTER TABLE in SQLite
 - The PRIMARY KEY constraint remains unchanged in the old and new schemas
