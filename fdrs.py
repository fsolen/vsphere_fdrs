#!/usr/bin/env python3

import argparse
import getpass
import logging
import sys
from modules.banner import print_banner
from modules.config_loader import ConfigLoader
from modules.connection_manager import ConnectionManager
from modules.resource_monitor import ResourceMonitor
from modules.constraint_manager import ConstraintManager
from modules.cluster_state import ClusterState
from modules.load_evaluator import LoadEvaluator
from modules.migration_planner import MigrationManager
from modules.scheduler import Scheduler
import logging
import sys

logger = logging.getLogger('fdrs')

def parse_args():
    """
    Parse the command-line arguments.
    """
    parser = argparse.ArgumentParser(description="FDRS - Fully Distributed Resource Scheduler")
    parser.add_argument("--vcenter", required=True, help="vCenter hostname or IP address")
    parser.add_argument("--username", required=True, help="vCenter username")
    parser.add_argument("--password", default='', help="vCenter password (will prompt if not provided)")
    parser.add_argument("--cluster", default='', help="Specific cluster name to balance (optional; if not provided, all clusters are processed)")
    parser.add_argument("--dry-run", action="store_true", help="Enable dry-run mode")
    parser.add_argument("--aggressiveness", type=int, default=3, choices=range(1, 6), help="Aggressiveness level (1-5)")
    parser.add_argument("--balance", action="store_true", help="Auto-balance the cluster based on selected metrics")
    parser.add_argument("--metrics", type=str, default="cpu,memory,disk,network", help="Comma-separated list of metrics to balance: cpu,memory,disk,network")
    parser.add_argument("--apply-anti-affinity", action="store_true", help="Apply anti-affinity rules only")
    parser.add_argument("--ignore-anti-affinity", action="store_true", help="Ignore anti-affinity rules for resource balancing.")
    parser.add_argument("--max-migrations",type=int,default=None, help="Maximum total migrations to perform in a single run (default: MigrationManager's internal default)")
    parser.add_argument("--iterative", action="store_true", help="Enable iterative planning mode for guaranteed convergence (AA satisfaction + balanced cluster)")
    parser.add_argument("--max-iterations", type=int, default=3, help="Maximum number of planning iterations when --iterative is enabled (default: 3)")

    return parser.parse_args()

def main():
    print_banner()
    args = parse_args()

    # Validate flag conflicts
    if args.apply_anti_affinity and args.ignore_anti_affinity:
        logger.warning("[Main] Conflicting flags detected: --apply-anti-affinity and --ignore-anti-affinity cannot be used together.")
        logger.warning("[Main] Reason: --apply-anti-affinity enforces anti-affinity rules, --ignore-anti-affinity disables them.")
        logger.warning("[Main] Resolution: Ignoring --ignore-anti-affinity flag. Running in anti-affinity-only mode.")
        args.ignore_anti_affinity = False

    if not args.password:
        args.password = getpass.getpass("vCenter Password: ")

    logging.basicConfig(
        level=logging.INFO,
        format='%(asctime)s [%(levelname)s] [%(name)s] %(message)s',
        datefmt='%Y-%m-%d %H:%M:%S',
        handlers=[logging.StreamHandler(sys.stdout)] 
    )
    logging.getLogger('fdrs').setLevel(logging.INFO)
    
    # Load configuration
    logger.info("[Main] Loading configuration...")
    config = ConfigLoader('config/fdrs_config.yaml')
    config.log_config()
    
    logger.info(f"[Main] Starting FDRS...")
    logger.info(f"[Main] Iterative mode: {'ENABLED' if args.iterative else 'disabled'}")
    if args.iterative:
        logger.info(f"[Main] Maximum iterations: {args.max_iterations}")
    
    connection_manager = ConnectionManager(args.vcenter, args.username, args.password)
    service_instance = connection_manager.connect()

    resource_monitor = ResourceMonitor(service_instance, config=config)
    cluster_state = ClusterState(service_instance, cluster_name=args.cluster if args.cluster else None)
    
    if args.cluster:
        logger.info(f"[Main] Targeting cluster: '{args.cluster}'")
    else:
        logger.info("[Main] Targeting all clusters in vCenter")
    
    cluster_state.update_metrics(resource_monitor)
    state = cluster_state.get_cluster_state()

    if args.apply_anti_affinity:
        logger.info("Applying anti-affinity rules only (with relaxed resource checks)...")
        constraint_manager = ConstraintManager(cluster_state)
        constraint_manager.apply()
        load_evaluator = LoadEvaluator(state['hosts']) 
        migration_planner = MigrationManager(
            cluster_state,
            constraint_manager,
            load_evaluator,
            aggressiveness=args.aggressiveness,
            max_total_migrations=args.max_migrations,
            ignore_anti_affinity=False,  # Always enforce anti-affinity in AA-only mode
            anti_affinity_only=True  # Skip resource checks entirely for pure distribution
        )
        
        if args.iterative:
            logger.info(f"[Main] Planning with iterative mode ({args.max_iterations} max iterations)...")
            migrations = migration_planner.plan_migrations_iterative(
                max_iterations=args.max_iterations,
                anti_affinity_only=True
            )
        else:
            migrations = migration_planner.plan_migrations(anti_affinity_only=True)
        
        if migrations:
            scheduler = Scheduler(connection_manager, dry_run=args.dry_run)
            scheduler.execute_migrations(migrations)
        else:
            logger.info("No anti-affinity migrations needed.")
        connection_manager.disconnect()
        return

    if args.balance:
        logger.info(f"Auto-balancing cluster using metrics: {args.metrics}")
        metrics_list = [m.strip() for m in args.metrics.split(",") if m.strip()]
        
        load_evaluator = LoadEvaluator(state['hosts'])
        constraint_manager = ConstraintManager(cluster_state)

        migration_planner = MigrationManager(
            cluster_state,
            constraint_manager,
            load_evaluator,
            aggressiveness=args.aggressiveness,
            max_total_migrations=args.max_migrations,
            ignore_anti_affinity=args.ignore_anti_affinity,
            anti_affinity_only=False  # Regular mode: enforce soft resource checks
        )

        statistical_imbalance_detected = load_evaluator.evaluate_imbalance(metrics_to_check=metrics_list, aggressiveness=args.aggressiveness)
        if statistical_imbalance_detected:
            logger.info("Statistical load imbalance detected by LoadEvaluator. MigrationPlanner will now determine actions.")
        else:
            logger.info("LoadEvaluator reports no significant statistical imbalance. MigrationPlanner will still check for individual host overloads and anti-affinity rules.")

        logger.info("Applying constraints before migration planning...")
        constraint_manager.apply()
        
        logger.info("Proceeding with migration planning phase...")
        if args.iterative:
            logger.info(f"[Main] Planning with iterative mode ({args.max_iterations} max iterations)...")
            migrations = migration_planner.plan_migrations_iterative(max_iterations=args.max_iterations)
        else:
            migrations = migration_planner.plan_migrations()

        if migrations:
            logger.info(f"Found {len(migrations)} migration(s) to perform for load balancing and/or anti-affinity.")
            scheduler = Scheduler(connection_manager, dry_run=args.dry_run)
            scheduler.execute_migrations(migrations)
        else:
            logger.info("Migration planning complete. No actionable migrations found or needed at this time.")
        
        connection_manager.disconnect()
        return

    logger.info("Running default FDRS workflow (evaluating load and planning migrations if needed)...")
    load_evaluator = LoadEvaluator(state['hosts'])
    constraint_manager = ConstraintManager(cluster_state)
    migration_planner = MigrationManager(
        cluster_state,
        constraint_manager,
        load_evaluator,
        aggressiveness=args.aggressiveness,
            max_total_migrations=args.max_migrations,
            ignore_anti_affinity=args.ignore_anti_affinity,
            anti_affinity_only=False  # Regular mode: enforce soft resource checks
    )

    statistical_imbalance_detected = load_evaluator.evaluate_imbalance(aggressiveness=args.aggressiveness)
    if statistical_imbalance_detected:
        logger.info("Statistical load imbalance detected by LoadEvaluator. MigrationPlanner will now determine actions.")
    else:
        logger.info("LoadEvaluator reports no significant statistical imbalance. MigrationPlanner will still check for individual host overloads and anti-affinity rules.")
    
    logger.info("Applying constraints before migration planning...")
    constraint_manager.apply()

    logger.info("Proceeding with migration planning phase...")
    if args.iterative:
        logger.info(f"[Main] Planning with iterative mode ({args.max_iterations} max iterations)...")
        migrations = migration_planner.plan_migrations_iterative(max_iterations=args.max_iterations)
    else:
        migrations = migration_planner.plan_migrations()

    if migrations:
        logger.info(f"Found {len(migrations)} migration(s) to perform for load balancing and/or anti-affinity.")
        scheduler = Scheduler(connection_manager, dry_run=args.dry_run)
        scheduler.execute_migrations(migrations)
    else:
        logger.info("Migration planning complete. No actionable migrations found or needed at this time.")
    connection_manager.disconnect()

if __name__ == "__main__":
    try:
        main()
    except Exception as e:
        logger.error("An error occurred: {}".format(e))
        sys.exit(1)
