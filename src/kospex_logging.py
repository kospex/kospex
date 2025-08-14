"""
Centralized logging system for Kospex CLI tools.

This module provides:
- Per-module loggers with daily rotation
- Configurable log levels and retention
- Graceful fallbacks if logging setup fails
- Directory validation and creation
"""

import os
import sys
import json
import logging
import logging.handlers
from pathlib import Path
from datetime import datetime, timedelta
from typing import Optional, Dict, Any


class KospexLoggingError(Exception):
    """Custom exception for logging-related errors."""
    pass


class KospexLogger:
    """Centralized logging manager for Kospex CLI tools."""
    
    _loggers: Dict[str, logging.Logger] = {}
    _initialized = False
    _config_cache: Optional[Dict[str, Any]] = None
    
    def __init__(self):
        self.kospex_home = self._get_kospex_home()
        self.logs_dir = self.kospex_home / "logs"
        self.config_file = self.kospex_home / "config.json"
        
    @staticmethod
    def _get_kospex_home() -> Path:
        """Get the Kospex home directory with fallback."""
        return Path(os.getenv("KOSPEX_HOME", os.path.expanduser("~/kospex")))
    
    def _load_config(self) -> Dict[str, Any]:
        """Load configuration from environment variables and config file."""
        if self._config_cache is not None:
            return self._config_cache
            
        # Default configuration
        config = {
            "log_level": "INFO",
            "retention_days": 30,
            "console_logging": False,
            "modules": {}
        }
        
        # Override with environment variables
        env_level = os.getenv("KOSPEX_LOG_LEVEL")
        if env_level:
            config["log_level"] = env_level.upper()
            
        env_retention = os.getenv("KOSPEX_LOG_RETENTION_DAYS")
        if env_retention and env_retention.isdigit():
            config["retention_days"] = int(env_retention)
            
        env_console = os.getenv("KOSPEX_CONSOLE_LOGGING")
        if env_console:
            config["console_logging"] = env_console.lower() in ("true", "1", "yes")
        
        # Override with config file if it exists
        if self.config_file.exists():
            try:
                with open(self.config_file, 'r') as f:
                    file_config = json.load(f)
                    logging_config = file_config.get("logging", {})
                    config.update(logging_config)
            except (json.JSONDecodeError, OSError):
                # Config file exists but is invalid - continue with defaults
                pass
        
        self._config_cache = config
        return config
    
    def _ensure_directories(self) -> bool:
        """Ensure logging directories exist with proper permissions."""
        try:
            # Create KOSPEX_HOME if it doesn't exist
            self.kospex_home.mkdir(mode=0o750, exist_ok=True)
            
            # Create logs directory if it doesn't exist
            self.logs_dir.mkdir(mode=0o750, exist_ok=True)
            
            # Check if directories are writable
            if not os.access(self.logs_dir, os.W_OK):
                raise KospexLoggingError(f"Logs directory not writable: {self.logs_dir}")
                
            return True
            
        except (OSError, PermissionError) as e:
            raise KospexLoggingError(f"Failed to create logging directories: {e}")
    
    def _create_file_handler(self, module_name: str, log_level: str) -> logging.handlers.TimedRotatingFileHandler:
        """Create a rotating file handler for a module."""
        log_file = self.logs_dir / f"{module_name}.log"
        
        # Use TimedRotatingFileHandler for daily rotation
        handler = logging.handlers.TimedRotatingFileHandler(
            filename=str(log_file),
            when='midnight',
            interval=1,
            backupCount=self._load_config()["retention_days"],
            encoding='utf-8'
        )
        
        # Set formatter
        formatter = logging.Formatter(
            '%(asctime)s [%(levelname)8s] [%(name)s] %(message)s',
            datefmt='%Y-%m-%d %H:%M:%S'
        )
        handler.setFormatter(formatter)
        handler.setLevel(getattr(logging, log_level.upper()))
        
        return handler
    
    def _create_console_handler(self, log_level: str) -> logging.StreamHandler:
        """Create a console handler for development/debugging."""
        handler = logging.StreamHandler(sys.stdout)
        
        # Simpler format for console output
        formatter = logging.Formatter(
            '%(asctime)s [%(levelname)s] [%(name)s] %(message)s',
            datefmt='%H:%M:%S'
        )
        handler.setFormatter(formatter)
        handler.setLevel(getattr(logging, log_level.upper()))
        
        return handler
    
    def _cleanup_old_logs(self):
        """Clean up old log files beyond retention period."""
        try:
            config = self._load_config()
            retention_days = config["retention_days"]
            cutoff_date = datetime.now() - timedelta(days=retention_days)
            
            for log_file in self.logs_dir.glob("*.log.*"):
                try:
                    file_stat = log_file.stat()
                    file_date = datetime.fromtimestamp(file_stat.st_mtime)
                    
                    if file_date < cutoff_date:
                        log_file.unlink()
                        
                except (OSError, ValueError):
                    # Skip files we can't process
                    continue
                    
        except Exception:
            # Don't fail the logging setup if cleanup fails
            pass
    
    def get_logger(self, module_name: str) -> logging.Logger:
        """Get or create a logger for the specified module."""
        # Return cached logger if it exists
        if module_name in self._loggers:
            return self._loggers[module_name]
        
        # Initialize logging system if not already done
        if not self._initialized:
            try:
                self._ensure_directories()
                self._cleanup_old_logs()
                self._initialized = True
            except KospexLoggingError:
                # Return a basic logger that only logs to console
                logger = logging.getLogger(module_name)
                logger.setLevel(logging.WARNING)
                if not logger.handlers:
                    handler = logging.StreamHandler()
                    handler.setFormatter(logging.Formatter('%(levelname)s: %(message)s'))
                    logger.addHandler(handler)
                return logger
        
        # Load configuration
        config = self._load_config()
        
        # Get module-specific config or use defaults
        module_config = config.get("modules", {}).get(module_name, {})
        log_level = module_config.get("level", config["log_level"])
        
        # Create logger
        logger = logging.getLogger(module_name)
        logger.setLevel(getattr(logging, log_level.upper()))
        
        # Clear any existing handlers
        logger.handlers.clear()
        
        try:
            # Add file handler
            file_handler = self._create_file_handler(module_name, log_level)
            logger.addHandler(file_handler)
            
            # Add console handler if requested
            if config["console_logging"]:
                console_handler = self._create_console_handler(log_level)
                logger.addHandler(console_handler)
                
        except Exception as e:
            # If file logging fails, fall back to console only
            console_handler = logging.StreamHandler()
            console_handler.setFormatter(logging.Formatter('%(levelname)s: %(message)s'))
            logger.addHandler(console_handler)
            logger.warning(f"File logging failed, using console only: {e}")
        
        # Prevent propagation to root logger
        logger.propagate = False
        
        # Cache the logger
        self._loggers[module_name] = logger
        
        return logger
    
    def validate_setup(self) -> Dict[str, Any]:
        """Validate the logging setup and return status information."""
        status = {
            "kospex_home": str(self.kospex_home),
            "logs_dir": str(self.logs_dir),
            "config_file": str(self.config_file),
            "directories_exist": False,
            "directories_writable": False,
            "config_loaded": False,
            "errors": []
        }
        
        try:
            # Check if directories exist
            status["directories_exist"] = self.kospex_home.exists() and self.logs_dir.exists()
            
            # Check if directories are writable
            status["directories_writable"] = (
                os.access(self.kospex_home, os.W_OK) and 
                os.access(self.logs_dir, os.W_OK)
            )
            
            # Try to load config
            self._load_config()
            status["config_loaded"] = True
            
        except Exception as e:
            status["errors"].append(str(e))
        
        return status


# Global instance
_kospex_logger = KospexLogger()


def get_logger(module_name: str) -> logging.Logger:
    """
    Get a logger for the specified Kospex module.
    
    Args:
        module_name: Name of the module (e.g., 'kospex', 'kgit', 'kweb2')
        
    Returns:
        Configured logger instance
        
    Usage:
        logger = get_logger('kospex')
        logger.info("Starting repository analysis")
    """
    return _kospex_logger.get_logger(module_name)


def validate_logging_setup() -> Dict[str, Any]:
    """
    Validate the logging setup and return detailed status information.
    
    Returns:
        Dictionary with validation results and error information
    """
    return _kospex_logger.validate_setup()


def create_sample_config() -> Path:
    """
    Create a sample configuration file with all available options.
    
    Returns:
        Path to the created config file
    """
    sample_config = {
        "logging": {
            "log_level": "INFO",
            "retention_days": 30,
            "console_logging": False,
            "modules": {
                "kospex": {"level": "INFO"},
                "kgit": {"level": "DEBUG"},
                "kweb2": {"level": "WARNING"},
                "krunner": {"level": "INFO"},
                "kwatch": {"level": "INFO"}
            }
        }
    }
    
    config_path = _kospex_logger.config_file
    config_path.parent.mkdir(mode=0o750, exist_ok=True)
    
    with open(config_path, 'w') as f:
        json.dump(sample_config, f, indent=2)
    
    return config_path