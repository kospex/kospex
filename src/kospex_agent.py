#!/usr/bin/env python3
"""Kospex Agent - Continuous repository monitoring and synchronization tool."""
import os
import time
import signal
import sys
import logging
import select
import threading
from datetime import datetime, timezone, timedelta
import click
from kospex_core import Kospex
import kospex_utils as KospexUtils
from kospex_query import KospexQuery

# Initialize Kospex environment with console logging enabled for agent visibility
KospexUtils.init(create_directories=True, setup_logging=True, verbose=False)

# Enable console logging for agent operations by setting environment variable
os.environ['KOSPEX_CONSOLE_LOGGING'] = 'true'

# Get logger using the new centralized logging system
log = KospexUtils.get_kospex_logger('kospex_agent')

class KospexAgent:
    """Agent for continuous repository monitoring and synchronization."""

    def __init__(self):
        self.kospex = Kospex()
        self.kospex_query = KospexQuery(kospex_db=self.kospex.kospex_db)
        self.running = False
        self.interval = 300  # Default 5 minutes
        self.manual_sync_requested = False

    def start(self, interval: int = 300):
        """Start the agent with the specified check interval."""
        self.interval = interval
        self.running = True

        # Set up signal handlers for graceful shutdown
        signal.signal(signal.SIGINT, self._signal_handler)
        signal.signal(signal.SIGTERM, self._signal_handler)

        log.info(f"Starting Kospex Agent with {interval} second interval")
        log.info("Press Ctrl+C to stop the agent or Enter to trigger immediate sync")
        log.info(f"Agent logging to: ~/kospex/logs/kospex_agent.log")

        try:
            self._run_loop()
        except KeyboardInterrupt:
            log.info("Received interrupt signal")
        finally:
            self._shutdown()

    def _signal_handler(self, signum, frame):
        """Handle shutdown signals gracefully."""
        log.info(f"Received signal {signum}, shutting down...")
        self.running = False

    def _run_loop(self):
        """Main execution loop with Enter key support for manual sync."""
        # Start input monitoring thread
        input_thread = threading.Thread(target=self._monitor_input, daemon=True)
        input_thread.start()

        while self.running:
            try:
                # Check if manual sync was requested
                if self.manual_sync_requested:
                    log.info("Manual sync requested via Enter key")
                    self.manual_sync_requested = False
                else:
                    log.info("Checking repositories for updates...")

                repos_to_update = self._check_repos_need_update()

                if repos_to_update:
                    log.info(f"Found {len(repos_to_update)} repositories that need updating")
                    self._update_repositories(repos_to_update)
                else:
                    log.info("No repositories need updating")

                if self.running:  # Check if still running before sleeping
                    log.info(f"Sleeping for {self.interval} seconds... (press Enter for immediate sync)")
                    self._interruptible_sleep(self.interval)

            except Exception as e:
                log.error(f"Error during repository check: {e}")
                log.debug(f"Error details: {e}", exc_info=True)
                if self.running:
                    log.info(f"Continuing after error, sleeping for {self.interval} seconds...")
                    self._interruptible_sleep(self.interval)

    def _monitor_input(self):
        """Monitor for Enter key presses to trigger manual sync."""
        try:
            while self.running:
                if sys.stdin in select.select([sys.stdin], [], [], 0.1)[0]:
                    line = sys.stdin.readline().strip()
                    if line == "":  # Enter key pressed
                        self.manual_sync_requested = True
                        log.info("Enter key detected - triggering immediate sync")
                time.sleep(0.1)  # Small delay to prevent excessive CPU usage
        except Exception as e:
            # Input monitoring failed (might be running in non-interactive mode)
            log.debug(f"Input monitoring disabled: {e}")

    def _interruptible_sleep(self, duration: int):
        """Sleep for the specified duration but allow interruption by manual sync or shutdown."""
        start_time = time.time()
        while self.running and (time.time() - start_time) < duration:
            # Check if manual sync was requested
            if self.manual_sync_requested:
                log.info("Manual sync requested - interrupting sleep")
                break
            time.sleep(0.5)  # Sleep in shorter intervals to be more responsive

    def _check_repos_need_update(self) -> list:
        """
        Check which repositories need updating.

        This is a stub method that you can implement later with your specific
        update checking logic.

        Returns:
            list: List of repository paths or identifiers that need updating
        """
        # TODO: Implement your update checking logic here
        # This could include:
        # - Checking remote Git repositories for new commits
        # - Comparing last sync time with repository modification time
        # - Checking for new branches or tags
        # - Monitoring filesystem changes
        # - Checking external APIs for repository updates

        log.debug("Checking repos for updates (stub implementation)")

        repos = self.kospex_query.get_repos_last_sync(older_than=datetime.now() - timedelta(hours=1))

        # For now, return an empty list
        # You can modify this to return actual repositories that need updating
        repos_needing_update = []
        if repos:
            return repos

        # Example stub logic (uncomment and modify as needed):
        # try:
        #     # Get all known repositories from the database
        #     known_repos = self.kospex_query.get_all_repos()
        #
        #     for repo in known_repos:
        #         if self._repo_needs_update(repo):
        #             repos_needing_update.append(repo)
        #
        # except Exception as e:
        #     log.error(f"Error checking repositories: {e}")

        return repos_needing_update

    def _repo_needs_update(self, repo_info: dict) -> bool:
        """
        Check if a specific repository needs updating.

        Args:
            repo_info: Dictionary containing repository information

        Returns:
            bool: True if the repository needs updating, False otherwise
        """
        # TODO: Implement specific update checking logic for a single repo
        # This could include:
        # - Checking if remote has new commits since last sync
        # - Comparing timestamps
        # - Checking file modification times

        return False

    def _update_repositories(self, repos: list):
        """
        Update the specified repositories.

        Args:
            repos: List of repositories to update
        """
        count = 0
        for repo in repos:
            if count > 2:
                log.info("Threshold of 2 reached ... will wait for another cycle")
                return
            try:
                log.info(f"Updating repository: {repo.get('_repo_id', 'Unknown repo_id')}")
                # TODO: Implement the actual update logic
                # This could call existing kospex sync methods:
                # self.kospex.sync_repo(repo)
                current = os.getcwd()
                git_base = KospexUtils.find_git_base(repo.get("file_path"))
                if git_base:
                    os.chdir(git_base)
                    os.system("git pull")
                    log.info("Syncing repository to kospex database...")
                    self.kospex.sync_repo(git_base)
                    os.chdir(current)
                else:
                    log.warning(f"Repository path {repo.get('file_path')} does not appear to be in a git repo")

                log.info(f"Successfully updated repository: {repo.get('_repo_id', 'Unknown repo_id')}")

            except Exception as e:
                log.error(f"Failed to update repository {repo.get('_repo_id', 'Unknown repo_id')}: {e}")
            count += 1


    def _shutdown(self):
        """Perform cleanup on shutdown."""
        log.info("Kospex Agent shutting down...")
        self.running = False


# Initialize the agent
agent = KospexAgent()

@click.group()
@click.version_option(version=Kospex.VERSION)
@click.option('--debug', is_flag=True, default=False, help="Enable debug logging.")
@click.option('--verbose', '-v', is_flag=True, default=False, help="Enable verbose logging.")
@click.option('--quiet', '-q', is_flag=True, default=False, help="Suppress most output (errors only).")
@click.pass_context
def cli(ctx, debug, verbose, quiet):
    """
    Kospex Agent - Continuous repository monitoring and synchronization.

    The agent runs continuously in the background, periodically checking
    repositories for updates and synchronizing them to the Kospex database.

    Logging is automatically sent to both console and log files for visibility.
    """
    # Store logging preferences in context for subcommands
    ctx.ensure_object(dict)
    ctx.obj['debug'] = debug
    ctx.obj['verbose'] = verbose
    ctx.obj['quiet'] = quiet
    
    # Configure runtime logging based on CLI flags
    if debug:
        os.environ['KOSPEX_LOG_LEVEL'] = 'DEBUG'
        log.setLevel(logging.DEBUG)
        log.debug("Debug logging enabled for agent")
    elif verbose:
        os.environ['KOSPEX_LOG_LEVEL'] = 'INFO' 
        log.setLevel(logging.INFO)
        log.info("Verbose logging enabled for agent")
    elif quiet:
        os.environ['KOSPEX_LOG_LEVEL'] = 'ERROR'
        log.setLevel(logging.ERROR)

@cli.command("start")
@click.option(
    '--interval', '-i',
    type=int,
    default=300,
    help='Check interval in seconds (default: 300 seconds / 5 minutes)'
)
@click.pass_context
def start(ctx, interval: int):
    """
    Start the Kospex Agent to continuously monitor repositories.

    The agent will check for repository updates at the specified interval
    and automatically synchronize any repositories that need updating.

    While running, you can press Enter at any time to trigger an immediate
    sync check without waiting for the next scheduled interval.

    Examples:
        kospex-agent start                         # Start with default 5-minute interval
        kospex-agent start --interval 60          # Check every minute
        kospex-agent --verbose start -i 1800      # Check every 30 minutes with verbose logging
        kospex-agent --debug start                # Start with debug logging enabled
    """
    # Global logging settings are handled by the main CLI group
    
    if interval < 10:
        log.warning("Very short interval specified. Consider using at least 10 seconds.")

    log.info(f"Starting agent with {interval} second check interval")
    log.info(f"Logging configuration: console={os.environ.get('KOSPEX_CONSOLE_LOGGING', 'false')}, level={os.environ.get('KOSPEX_LOG_LEVEL', 'INFO')}")
    agent.start(interval)

@cli.command("status")
def status():
    """Check the status of repositories in the Kospex database."""
    try:
        # Get basic statistics about repositories
        query = KospexQuery(kospex_db=agent.kospex.kospex_db)

        # TODO: Implement status checking logic
        # This could show:
        # - Number of repositories being monitored
        # - Last sync times
        # - Repositories that need updating
        # - Agent health status

        click.echo("Repository Status:")
        click.echo("=" * 50)
        click.echo("TODO: Implement status display")
        click.echo("This will show:")
        click.echo("- Number of monitored repositories")
        click.echo("- Last synchronization times")
        click.echo("- Repositories needing updates")
        click.echo("- Agent health information")

    except Exception as e:
        log.error(f"Error checking status: {e}")
        log.debug(f"Status check error details: {e}", exc_info=True)
        click.echo(f"Error: {e}")

@cli.command("test")
def test():
    """Test the update checking functionality."""
    click.echo("Testing repository update checking...")

    try:
        repos_to_update = agent._check_repos_need_update()

        if repos_to_update:
            click.echo(f"Found {len(repos_to_update)} repositories that need updating:")
            for repo in repos_to_update:
                click.echo(f"  - {repo}")
        else:
            click.echo("No repositories need updating at this time.")

    except Exception as e:
        log.error(f"Error during test: {e}")
        log.debug(f"Test error details: {e}", exc_info=True)
        click.echo(f"Error: {e}")

if __name__ == '__main__':
    cli()
