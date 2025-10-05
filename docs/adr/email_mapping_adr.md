# ADR - Email Address Normalization

## Overview

Git commit data often contains multiple email addresses for the same contributor due to:
- GitHub privacy email addresses (e.g., `109841928+username@users.noreply.github.com`)
- Multiple work/personal email addresses
- Email address changes over time
- Typos or variations

This document describes the email mapping system that normalizes email addresses to canonical identities, similar to Git's `.mailmap` functionality.

## Schema Version

Email mapping feature introduced in version: **[TBD]**

## Table Structure

### TBL_EMAIL_MAPPING

Stores mappings from alias email addresses to canonical (main) email addresses.

```sql
CREATE TABLE IF NOT EXISTS TBL_EMAIL_MAPPING (
    alias_email TEXT PRIMARY KEY,
    main_email TEXT NOT NULL,
    created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
    notes TEXT
);

-- Index for reverse lookups (finding all aliases for a main email)
CREATE INDEX IF NOT EXISTS idx_email_mapping_main
ON TBL_EMAIL_MAPPING(main_email);
```

#### Column Descriptions

| Column | Type | Constraints | Description |
|--------|------|-------------|-------------|
| `alias_email` | TEXT | PRIMARY KEY | The email address variant/alias to map from |
| `main_email` | TEXT | NOT NULL | The canonical/primary email address to map to |
| `created_at` | TIMESTAMP | DEFAULT CURRENT_TIMESTAMP | When the mapping was created |
| `notes` | TEXT | NULL | Optional notes about why this mapping exists |

#### Index Details

- **PRIMARY KEY on `alias_email`**: Automatically indexed for fast JOIN lookups
- **`idx_email_mapping_main`**: Enables efficient reverse lookups to find all aliases for a main email

## View Structure

### VIEW_COMMITS_NORMALIZED

Normalizes commit email addresses using the mapping table. Returns original email if no mapping exists.

```sql
CREATE VIEW IF NOT EXISTS VIEW_COMMITS_NORMALIZED AS
SELECT
    c.*,
    COALESCE(m.main_email, c.email_address) as canonical_email
FROM TBL_GIT_COMMITS c
LEFT JOIN TBL_EMAIL_MAPPING m ON c.email_address = m.alias_email;
```

#### How It Works

1. **LEFT JOIN**: Attempts to find a mapping for each commit's email address
2. **COALESCE**: Returns `main_email` if mapping exists, otherwise returns original `email_address`
3. **Transparent**: All original commit columns are preserved with `c.*`

#### Column Output

All columns from `TBL_GIT_COMMITS` plus:
- `canonical_email` (TEXT): The normalized email address

## Usage Examples

### 1. Adding Email Mappings

```sql
-- Map GitHub privacy emails to real addresses
INSERT INTO TBL_EMAIL_MAPPING (alias_email, main_email, notes) VALUES
    ('109841928+sabbaticas@users.noreply.github.com', 'sabbaticas@real.com', 'GitHub privacy email'),
    ('12345+jane@users.noreply.github.com', 'jane@company.com', 'GitHub privacy email');

-- Map old email addresses to new ones
INSERT INTO TBL_EMAIL_MAPPING (alias_email, main_email, notes) VALUES
    ('john.old@company.com', 'john@company.com', 'Email changed after company rebrand'),
    ('jane.smith@oldcompany.com', 'jane@company.com', 'Joined from acquisition');

-- Handle typos
INSERT INTO TBL_EMAIL_MAPPING (alias_email, main_email, notes) VALUES
    ('bob@exampl.com', 'bob@example.com', 'Common typo');
```

### 2. Querying Normalized Commits

```sql
-- Get commit count per canonical author
SELECT
    canonical_email,
    COUNT(*) as commit_count
FROM VIEW_COMMITS_NORMALIZED
GROUP BY canonical_email
ORDER BY commit_count DESC;

-- Find all commits by a specific author (any alias)
SELECT *
FROM VIEW_COMMITS_NORMALIZED
WHERE canonical_email = 'jane@company.com'
ORDER BY commit_date DESC;

-- Date range analysis with normalized emails
SELECT
    canonical_email,
    COUNT(*) as commits,
    MIN(commit_date) as first_commit,
    MAX(commit_date) as last_commit
FROM VIEW_COMMITS_NORMALIZED
WHERE commit_date >= '2024-01-01'
GROUP BY canonical_email;
```

### 3. Managing Mappings

```sql
-- Find all aliases for a main email
SELECT alias_email, notes
FROM TBL_EMAIL_MAPPING
WHERE main_email = 'jane@company.com';

-- Check if an email has a mapping
SELECT
    alias_email,
    main_email,
    CASE
        WHEN main_email IS NOT NULL THEN 'Mapped'
        ELSE 'No mapping'
    END as status
FROM (
    SELECT 'test@example.com' as email
) e
LEFT JOIN TBL_EMAIL_MAPPING m ON e.email = m.alias_email;

-- Update a mapping
UPDATE TBL_EMAIL_MAPPING
SET main_email = 'new.primary@company.com'
WHERE alias_email = 'old.alias@company.com';

-- Remove a mapping
DELETE FROM TBL_EMAIL_MAPPING
WHERE alias_email = 'no.longer.needed@example.com';
```

### 4. Bulk Import from .mailmap

```python
# Python example for importing Git .mailmap file
import sqlite3

def parse_mailmap(filepath):
    """Parse Git .mailmap file into email mappings"""
    mappings = []
    with open(filepath, 'r') as f:
        for line in f:
            line = line.strip()
            if not line or line.startswith('#'):
                continue

            # Parse: Proper Name <proper@email.com> <alias@email.com>
            if '<' in line:
                parts = line.split('<')
                if len(parts) >= 3:
                    main_email = parts[1].split('>')[0].strip()
                    alias_email = parts[2].split('>')[0].strip()
                    mappings.append((alias_email, main_email, 'Imported from .mailmap'))

    return mappings

# Import mappings
conn = sqlite3.connect('kospex.db')
mappings = parse_mailmap('.mailmap')

conn.executemany(
    'INSERT OR IGNORE INTO TBL_EMAIL_MAPPING (alias_email, main_email, notes) VALUES (?, ?, ?)',
    mappings
)
conn.commit()
```

## Performance Considerations

### Index Usage

- **View queries automatically use indexes** on underlying tables
- `TBL_EMAIL_MAPPING.alias_email` (PRIMARY KEY) is used for JOIN lookups
- `TBL_GIT_COMMITS` indexes on `commit_hash`, `commit_date`, etc. work through the view
- The computed `canonical_email` column cannot be directly indexed (it's computed)

### Query Patterns

**Fast queries** (use indexes):
```sql
-- Filter by commit hash
SELECT * FROM VIEW_COMMITS_NORMALIZED WHERE commit_hash = 'abc123';

-- Filter by date
SELECT * FROM VIEW_COMMITS_NORMALIZED WHERE commit_date > '2024-01-01';

-- Group by canonical email (full scan, but efficient)
SELECT canonical_email, COUNT(*) FROM VIEW_COMMITS_NORMALIZED GROUP BY canonical_email;
```

**Slower queries** (cannot use index on canonical_email):
```sql
-- Filter by canonical email requires table scan
SELECT * FROM VIEW_COMMITS_NORMALIZED WHERE canonical_email = 'user@example.com';

-- Workaround: Query both original and mapped emails
SELECT * FROM VIEW_COMMITS_NORMALIZED
WHERE email_address = 'user@example.com'
   OR canonical_email = 'user@example.com';
```

### Scalability

- **Small mapping table** (100-1000 entries): Negligible overhead
- **Large commits table** (100K-1M rows): View performs well with proper indexes
- **LEFT JOIN** is efficient because it uses the PRIMARY KEY index

## PostgreSQL Migration Compatibility

All SQL constructs used are **100% compatible** with PostgreSQL:

- `CREATE TABLE` syntax: Standard SQL
- `LEFT JOIN`: Standard SQL
- `COALESCE`: Standard SQL
- `CREATE VIEW`: Standard SQL
- `CREATE INDEX`: Standard SQL

### Future Enhancement (PostgreSQL)

When migrating to PostgreSQL, consider using a SQL function for cleaner syntax:

```sql
-- PostgreSQL function (future)
CREATE OR REPLACE FUNCTION main_email(input_email TEXT)
RETURNS TEXT AS $$
    SELECT COALESCE(
        (SELECT main_email FROM TBL_EMAIL_MAPPING WHERE alias_email = input_email),
        input_email
    );
$$ LANGUAGE SQL STABLE;

-- Usage
SELECT commit_hash, main_email(email_address) as canonical_email
FROM TBL_GIT_COMMITS;
```

## Implementation Checklist

### For Claude Code / Developer Implementation

- [ ] Create `TBL_EMAIL_MAPPING` table with indexes
- [ ] Create `VIEW_COMMITS_NORMALIZED` view
- [ ] Add email mapping CLI command: `kospex map-email <alias> <main>`
- [ ] Add email mapping import CLI command: `kospex import-mailmap <file>`
- [ ] Add email mapping list CLI command: `kospex list-email-mappings`
- [ ] Update schema version in `TBL_KOSPEX_CONFIG` to [TBD]
- [ ] Add tests for email mapping functionality
- [ ] Update documentation with new CLI commands
- [ ] Verify view works with existing queries

## Migration Notes

### Adding to Existing Database

No schema migration required for existing tables. Simply:

1. Create the new `TBL_EMAIL_MAPPING` table
2. Create the `VIEW_COMMITS_NORMALIZED` view
3. Populate mappings as needed

```sql
-- Run these commands to add email mapping to existing database
CREATE TABLE IF NOT EXISTS TBL_EMAIL_MAPPING (
    alias_email TEXT PRIMARY KEY,
    main_email TEXT NOT NULL,
    created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
    notes TEXT
);

CREATE INDEX IF NOT EXISTS idx_email_mapping_main
ON TBL_EMAIL_MAPPING(main_email);

CREATE VIEW IF NOT EXISTS VIEW_COMMITS_NORMALIZED AS
SELECT
    c.*,
    COALESCE(m.main_email, c.email_address) as canonical_email
FROM TBL_GIT_COMMITS c
LEFT JOIN TBL_EMAIL_MAPPING m ON c.email_address = m.alias_email;
```

### Constraints

- **Cannot add constraints** to existing columns via ALTER TABLE in SQLite
- **New columns must allow NULL** when using ALTER TABLE
- Email mapping uses a separate table, avoiding these constraints

## Design Decisions

### Why a Separate Mapping Table?

**Advantages:**
- No modification to existing `TBL_GIT_COMMITS` schema
- Mappings can be managed independently
- Same alias can't map to multiple main emails (PRIMARY KEY constraint)
- Easy to bulk import/export mappings
- Clean separation of concerns

**Alternatives considered:**
- Adding `canonical_email` column to `TBL_GIT_COMMITS`: Would require complex UPDATE logic and data duplication
- Using JSON mapping in config table: Poor query performance, no relational integrity

### Why a View Instead of Function?

**Advantages:**
- 100% portable SQL (SQLite → PostgreSQL)
- Query optimizer can use indexes transparently
- Simple to use (query like a table)
- No performance overhead vs manual JOIN

**Disadvantages:**
- Computed column `canonical_email` cannot be indexed
- For filtering by canonical email, must use workarounds

### Null Handling

- `LEFT JOIN` returns NULL when no mapping exists
- `COALESCE` handles NULL gracefully by returning original email
- Result: Unmapped emails pass through unchanged (desired behavior)

## Future Enhancements

Potential improvements for future versions:

1. **Auto-detection of GitHub privacy emails** using pattern matching
2. **Fuzzy matching** for similar email addresses (typo detection)
3. **Name normalization** alongside email mapping
4. **Audit trail** with `updated_at` and `updated_by` columns
5. **Materialized view** for faster canonical_email filtering (refresh on data change)
6. **Multi-tenant support** with organization/repo scoping

## References

- [SQLite ALTER TABLE documentation](https://www.sqlite.org/lang_altertable.html)
- [Git mailmap documentation](https://git-scm.com/docs/gitmailmap)
- SQLite LEFT JOIN optimization
- PostgreSQL view performance best practices
