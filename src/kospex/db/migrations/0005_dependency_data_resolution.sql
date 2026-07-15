-- 0005_dependency_data_resolution.sql
--
-- Category for why a deps.dev version lookup resolved or failed: resolved,
-- no_version, unresolved_spec, version_yanked, package_not_found, lookup_error.
-- NULL means a legacy row not yet classified. Pairs with versions_behind, which
-- is an integer when resolved and NULL otherwise.

ALTER TABLE dependency_data ADD COLUMN resolution TEXT;
