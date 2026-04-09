"""FDRS Modules Package.

Contains all core components for the Fully Distributed Resource Scheduler:
- banner: Display banner and version info
- cluster_state: Maintain cluster VM and host state
- config_loader: Load and manage YAML configuration
- connection_manager: Handle vCenter connections
- constraint_manager: Manage anti-affinity constraints
- load_evaluator: Evaluate cluster load balance
- migration_planner: Plan VM migrations
- resource_monitor: Monitor VM and host resources
- scheduler: Execute planned migrations
"""

__version__ = "1.0.0"
__all__ = [
    "banner",
    "cluster_state",
    "config_loader",
    "connection_manager",
    "constraint_manager",
    "load_evaluator",
    "migration_planner",
    "resource_monitor",
    "scheduler",
]
