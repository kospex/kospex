# Click Exit Code 2 Error Fix

## Problem Description

The `kospex` CLI command was exiting with error code 2 when run without any arguments (bare command). This was happening even though the command was successfully displaying the help message.

**Note**: This behavior change occurred in Click v8.1.8 and was breaking the build process, making this fix necessary.

## Root Cause

When running the `kospex` command without any arguments, Click's default behavior for command groups is to display the help message and exit with code 2. This is the standard behavior for CLI tools when no command is provided.

Click uses the following exit code convention:
- Exit code 0 = Success
- Exit code 1 = General error  
- Exit code 2 = Misuse of shell command (incorrect arguments, etc.)

Since no command was provided, Click considers this a "misuse" scenario, hence the exit code 2.

## The Fix

To change the behavior so that showing help exits with code 0 (success), the following changes were made to `src/kospex.py`:

### Before
```python
@click.group()
@click.version_option(version=Kospex.VERSION)
def cli():
    """Kospex is a tool for assessing code and git repositories.

    It is designed to help understand the structure of code, who are developers and
    changes over time.

    For documentation on how commands run `kospex COMMAND --help`.

    See also https://kospex.io/

    """
```

### After
```python
@click.group(invoke_without_command=True)
@click.version_option(version=Kospex.VERSION)
@click.pass_context
def cli(ctx):
    """Kospex is a tool for assessing code and git repositories.

    It is designed to help understand the structure of code, who are developers and
    changes over time.

    For documentation on how commands run `kospex COMMAND --help`.

    See also https://kospex.io/

    """
    if ctx.invoked_subcommand is None:
        # Default behavior when no command is provided
        click.echo(ctx.get_help())
        ctx.exit(0)
```

## Changes Made

1. **Added `invoke_without_command=True`** to the `@click.group()` decorator
   - This allows the group function to be called even when no subcommand is provided

2. **Added `@click.pass_context`** decorator
   - This provides access to the Click context object

3. **Added parameter `ctx`** to the `cli()` function
   - This receives the Click context

4. **Added conditional check** in the `cli()` function
   - Detects when no subcommand was invoked using `ctx.invoked_subcommand is None`

5. **Explicitly call `ctx.exit(0)`**
   - Forces exit with code 0 instead of the default code 2

## Testing

### Before Fix
```bash
$ .venv/bin/python src/kospex.py
# Shows help message
# Exit code: 2
```

### After Fix
```bash
$ .venv/bin/python src/kospex.py  
# Shows help message
# Exit code: 0
```

### Verification
```bash
$ .venv/bin/python src/kospex.py --help
# Shows help message  
# Exit code: 0 (unchanged)
```

## Impact

- The command now exits with code 0 when showing help instead of code 2
- No functional changes to the help output or any other commands
- Better user experience as the command no longer appears to "fail" when showing help
- Maintains compatibility with all existing functionality
- **Critical**: Fixes build breakage caused by Click v8.1.8 behavior change

## Alternative Approaches Considered

1. **Leave as-is**: Accept that showing help exits with code 2 (standard Click behavior)
2. **Add a default command**: Instead of showing help, execute a default command like `system-status`
3. **Custom error handling**: Override Click's error handling globally
4. **Downgrade Click version**: Revert to pre-v8.1.8 behavior

The chosen approach was selected because:
- It's the least invasive change
- It maintains the expected help behavior
- It follows common CLI conventions where help is considered a successful operation
- It's easily reversible if needed
- It doesn't require dependency version pinning
