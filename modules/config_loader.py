import yaml
import logging
import os

logger = logging.getLogger('fdrs')

class ConfigLoader:
    """
    Loads and manages FDRS configuration from YAML file.
    Provides default values if config file is missing or incomplete.
    """
    
    # Default configuration values
    DEFAULTS = {
        'storage': {
            'disk_io_capacity_mbps': 4000
        },
        'network': {
            'bandwidth_mbps': 1250
        },
        'performance': {
            'cpu_ready_percent_threshold': 10.0,
            'memory_swap_threshold': 1000,
            'disk_latency_threshold_ms': 20
        },
        'migration': {
            'default_max_migrations': 20,
            'migration_timeout_seconds': 300,
            'host_cpu_high_watermark_percent': 90,
            'host_memory_high_watermark_percent': 90
        },
        'logging': {
            'level': 'INFO',
            'file': ''
        },
        'optimization': {
            'enable_percentage_cache': True,
            'enable_prefix_cache': True
        }
    }

    def __init__(self, config_file='config/fdrs_config.yaml'):
        """
        Initialize config loader and load configuration.
        
        Args:
            config_file: Path to YAML config file (relative or absolute)
        """
        self.config_file = config_file
        self.config = self._load_config()

    def _load_config(self):
        """
        Load configuration from YAML file or return defaults if file doesn't exist.
        """
        if not os.path.exists(self.config_file):
            logger.warning(f"[ConfigLoader] Config file not found at '{self.config_file}'. Using default values.")
            return self.DEFAULTS.copy()
        
        try:
            with open(self.config_file, 'r') as f:
                file_config = yaml.safe_load(f) or {}
            
            # Merge loaded config with defaults (defaults are overridden by file values)
            merged_config = self._deep_merge(self.DEFAULTS.copy(), file_config)
            
            logger.info(f"[ConfigLoader] Configuration loaded from '{self.config_file}'.")
            return merged_config
        
        except yaml.YAMLError as e:
            logger.error(f"[ConfigLoader] Error parsing YAML config file: {e}. Using default values.")
            return self.DEFAULTS.copy()
        except Exception as e:
            logger.error(f"[ConfigLoader] Error loading config file: {e}. Using default values.")
            return self.DEFAULTS.copy()

    @staticmethod
    def _deep_merge(defaults, overrides):
        """
        Deep merge overrides into defaults (overrides take precedence).
        """
        result = defaults.copy()
        
        for key, value in overrides.items():
            if isinstance(value, dict) and key in result and isinstance(result[key], dict):
                result[key] = ConfigLoader._deep_merge(result[key], value)
            else:
                result[key] = value
        
        return result

    def get(self, *keys, default=None):
        """
        Get a config value using dot notation.
        Example: config.get('storage', 'disk_io_capacity_mbps')
        
        Args:
            *keys: Nested keys to traverse
            default: Default value if key not found
        
        Returns:
            Config value or default if not found
        """
        value = self.config
        
        for key in keys:
            if isinstance(value, dict) and key in value:
                value = value[key]
            else:
                logger.warning(f"[ConfigLoader] Config key not found: {'.'.join(keys)}. Using default: {default}")
                return default
        
        return value

    def get_storage_disk_io_capacity(self):
        """Get disk I/O capacity in MBps."""
        return self.get('storage', 'disk_io_capacity_mbps', default=self.DEFAULTS['storage']['disk_io_capacity_mbps'])

    def get_network_bandwidth(self):
        """Get network bandwidth in MBps."""
        return self.get('network', 'bandwidth_mbps', default=self.DEFAULTS['network']['bandwidth_mbps'])

    def get_migration_timeout(self):
        """Get migration timeout in seconds."""
        return self.get('migration', 'migration_timeout_seconds', default=self.DEFAULTS['migration']['migration_timeout_seconds'])

    def get_max_migrations(self):
        """Get default max migrations."""
        return self.get('migration', 'default_max_migrations', default=self.DEFAULTS['migration']['default_max_migrations'])

    def get_host_cpu_watermark(self):
        """Get CPU high watermark percentage."""
        return self.get('migration', 'host_cpu_high_watermark_percent', default=self.DEFAULTS['migration']['host_cpu_high_watermark_percent'])

    def get_host_memory_watermark(self):
        """Get memory high watermark percentage."""
        return self.get('migration', 'host_memory_high_watermark_percent', default=self.DEFAULTS['migration']['host_memory_high_watermark_percent'])

    def is_percentage_cache_enabled(self):
        """Check if percentage caching is enabled."""
        return self.get('optimization', 'enable_percentage_cache', default=self.DEFAULTS['optimization']['enable_percentage_cache'])

    def is_prefix_cache_enabled(self):
        """Check if prefix caching is enabled."""
        return self.get('optimization', 'enable_prefix_cache', default=self.DEFAULTS['optimization']['enable_prefix_cache'])

    def log_config(self):
        """Log loaded configuration for debugging."""
        logger.info("[ConfigLoader] Current Configuration:")
        logger.info(f"  Storage Disk I/O Capacity: {self.get_storage_disk_io_capacity()} MBps")
        logger.info(f"  Network Bandwidth: {self.get_network_bandwidth()} MBps")
        logger.info(f"  Migration Timeout: {self.get_migration_timeout()}s")
        logger.info(f"  Default Max Migrations: {self.get_max_migrations()}")
        logger.info(f"  CPU High Watermark: {self.get_host_cpu_watermark()}%")
        logger.info(f"  Memory High Watermark: {self.get_host_memory_watermark()}%")
        logger.info(f"  Percentage Cache Enabled: {self.is_percentage_cache_enabled()}")
        logger.info(f"  Prefix Cache Enabled: {self.is_prefix_cache_enabled()}")
