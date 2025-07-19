# Click Version Update Plan

## Current Status
- **Panopticas**: `"Click"` (unpinned) - Currently installed: 8.2.1
- **Kospex**: `"Click"` (unpinned) - Currently installed: 8.2.1
- **Latest Available**: 8.2.1
- **Problematic Version**: 8.1.8 (introduced exit code 2 behavior)

## Background

Both projects experienced build breakage due to Click v8.1.8 changing the default exit code behavior for CLI groups when no command is provided. This was fixed by implementing custom exit code handling in both projects' CLI modules.

## Recommended Update Strategy

### 1. Pin Click to Current Version (8.2.1)
Since both projects are already running Click 8.2.1 and our fix works correctly, we should pin to this version to ensure consistency and prevent unexpected behavior changes.

### 2. Update Plan for Both Projects

**For Panopticas (`pyproject.toml`):**
```toml
# Current
dependencies = ["Click", "prettytable", "pathspec", "twine", "build"]

# Recommended Update
dependencies = ["Click>=8.2.1", "prettytable", "pathspec", "twine", "build"]
```

**For Kospex (`pyproject.toml`):**
```toml
# Current
dependencies = [
    "Click",
    # ... other dependencies
]

# Recommended Update
dependencies = [
    "Click>=8.2.1",
    # ... other dependencies
]
```

### 3. Alternative Approaches

**Option A: Conservative Pinning (Recommended)**
- Pin to `"Click>=8.2.1"` - Ensures we get the current stable version and any future patch releases
- Safest approach since we've tested our fix with 8.2.1

**Option B: Exact Version Pinning**
- Pin to `"Click==8.2.1"` - Locks to exact version
- Most predictable but requires manual updates for security patches

**Option C: Range Pinning**
- Pin to `"Click>=8.2.1,<9.0.0"` - Allows minor updates within v8.x
- Balances stability with security updates

### 4. Implementation Steps

1. **Update panopticas/pyproject.toml**
2. **Update kospex/pyproject.toml** 
3. **Test both projects** to ensure CLI still works correctly
4. **Update virtual environments** with `pip install -e .`
5. **Run automated tests** if available
6. **Document the change** in both projects

### 5. Risk Assessment

**Low Risk:**
- Both projects already have the exit code 2 fix applied
- Currently running Click 8.2.1 successfully
- No breaking changes expected in patch releases

**Considerations:**
- Future major version updates (9.x) may introduce breaking changes
- Should monitor Click release notes for any behavioral changes
- The fix we applied should be forward-compatible

### 6. Testing Plan

**Before Update:**
```bash
# Test current behavior
cd /Users/peterfreiberg/dev/github.com/kospex/kospex
.venv/bin/python src/kospex.py
echo $?  # Should be 0

cd /Users/peterfreiberg/dev/github.com/kospex/panopticas
.venv/bin/python -m panopticas.cli
echo $?  # Should be 0
```

**After Update:**
```bash
# Reinstall dependencies
cd /Users/peterfreiberg/dev/github.com/kospex/kospex
pip install -e .

cd /Users/peterfreiberg/dev/github.com/kospex/panopticas
pip install -e .

# Test behavior remains unchanged
# Same tests as above
```

### 7. Rollback Plan

If issues arise:
1. Revert pyproject.toml changes
2. Reinstall with `pip install -e .`
3. Verify functionality returns to current state

## Recommendation

**Option A: Conservative Pinning** with `"Click>=8.2.1"` for both projects because:

1. **Stability**: Ensures we never get a version older than 8.2.1 (avoiding the 8.1.8 issue)
2. **Security**: Allows patch releases for security fixes
3. **Tested**: We've verified our fix works with 8.2.1
4. **Future-proof**: Provides a clear minimum version requirement

## Click Version History Context

- **8.1.8**: Introduced exit code 2 behavior change that broke builds
- **8.2.0**: Subsequent release (behavior persisted)
- **8.2.1**: Current stable version (behavior persists but we have fix)

## Related Files

- `changes/click-exit-2-error.md` - Documentation of the exit code fix
- `src/kospex.py` - Kospex CLI with exit code fix applied
- `src/panopticas/cli.py` - Panopticas CLI with exit code fix applied

## Next Steps

1. Review this plan
2. Implement the recommended updates
3. Test thoroughly
4. Monitor for any issues
5. Consider automation for future dependency updates
