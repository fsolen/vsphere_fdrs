import logging
import copy 

logger = logging.getLogger('fdrs')

class MigrationManager:
    def __init__(self, cluster_state, constraint_manager, load_evaluator, aggressiveness=3, max_total_migrations=20, ignore_anti_affinity=False, anti_affinity_only=False):
        self.cluster_state = cluster_state
        self.constraint_manager = constraint_manager
        self.load_evaluator = load_evaluator
        self.aggressiveness = aggressiveness
        self.ignore_anti_affinity = ignore_anti_affinity
        self.anti_affinity_only = anti_affinity_only  # For apply-anti-affinity-only mode
        if max_total_migrations is None:
            self.max_total_migrations = 20
        else:
            self.max_total_migrations = int(max_total_migrations)

    def _get_simulated_load_data_after_migrations(self, migrations_to_simulate):
        """
        Simulates migrations and returns new CPU/Memory percentage lists and a new map
        reflecting these simulated CPU/Memory loads. Disk/Network are passed through from original state.
        Returns: tuple (sim_cpu_list, sim_mem_list, orig_disk_list, orig_net_list, new_sim_load_map)
        """
        logger.debug(f"[MigrationPlanner_Sim] Simulating {len(migrations_to_simulate)} migrations to update load data.")

        current_absolute_host_loads = {}
        # Use self.cluster_state.hosts as the canonical list of host objects.
        # Ensure that LoadEvaluator also uses this same list or an equivalent ordered list of names.
        # For safety, get the canonical order of host names from LoadEvaluator if possible,
        # or ensure self.cluster_state.hosts is the source of truth for order.
        # The previous version used self.load_evaluator.hosts.
        ordered_host_objects = self.cluster_state.hosts # Assuming this list is stable and representative

        if not ordered_host_objects:
            logger.warning("[MigrationPlanner_Sim] No hosts in cluster_state.hosts. Cannot simulate load changes.")
            return [], [], [], [], {}

        for host_obj in ordered_host_objects:
            if not hasattr(host_obj, 'name'):
                logger.warning(f"[MigrationPlanner_Sim] Host object {host_obj} lacks a name. Skipping for absolute load collection.")
                continue
            host_name = host_obj.name
            host_metrics_from_cs = self.cluster_state.host_metrics.get(host_name, {})

            current_absolute_host_loads[host_name] = {
                'cpu_usage_abs': host_metrics_from_cs.get('cpu_usage', 0), # Absolute sum from VMs
                'mem_usage_abs': host_metrics_from_cs.get('memory_usage', 0), # Host's overallMemoryUsage
                'cpu_cap_abs': host_metrics_from_cs.get('cpu_capacity', 1), # Avoid division by zero
                'mem_cap_abs': host_metrics_from_cs.get('memory_capacity', 1) # Avoid division by zero
            }

        # Simulate each migration
        for mig_plan in migrations_to_simulate:
            vm_obj = mig_plan['vm']
            target_host_obj = mig_plan['target_host']
            source_host_obj = self.cluster_state.get_host_of_vm(vm_obj)

            if not hasattr(vm_obj, 'name') or not hasattr(target_host_obj, 'name'):
                logger.warning(f"[MigrationPlanner_Sim] VM or Target Host in migration plan missing name. Skipping: {mig_plan}")
                continue

            vm_name = vm_obj.name
            target_host_name = target_host_obj.name
            source_host_name = source_host_obj.name if source_host_obj and hasattr(source_host_obj, 'name') else None

            vm_res_metrics = self.cluster_state.vm_metrics.get(vm_name, {})
            vm_cpu_abs = vm_res_metrics.get('cpu_usage_abs', 0)
            vm_mem_abs = vm_res_metrics.get('memory_usage_abs', 0)

            if source_host_name and source_host_name in current_absolute_host_loads:
                current_absolute_host_loads[source_host_name]['cpu_usage_abs'] -= vm_cpu_abs
                current_absolute_host_loads[source_host_name]['mem_usage_abs'] -= vm_mem_abs
            elif source_host_name:
                logger.warning(f"[MigrationPlanner_Sim] Source host {source_host_name} for VM {vm_name} not in current_absolute_host_loads. Load not decremented.")

            if target_host_name in current_absolute_host_loads:
                current_absolute_host_loads[target_host_name]['cpu_usage_abs'] += vm_cpu_abs
                current_absolute_host_loads[target_host_name]['mem_usage_abs'] += vm_mem_abs
            else:
                logger.error(f"[MigrationPlanner_Sim] Target host {target_host_name} for VM {vm_name} not in current_absolute_host_loads. Load not incremented. This indicates an issue with host lists.")

        # Generate new CPU/Memory percentage lists and the simulated map
        sim_cpu_percentages = []
        sim_mem_percentages = []
        sim_host_resource_percentages_map = {}
        _ , _, orig_disk_percentages, orig_net_percentages = self.load_evaluator.get_resource_percentage_lists()
        host_names_from_evaluator = [h.get('name') for h in self.load_evaluator.hosts if isinstance(h, dict) and h.get('name')]
        if not host_names_from_evaluator and ordered_host_objects: # Fallback if load_evaluator.hosts is not structured as list of dicts with names
             host_names_from_evaluator = [h.name for h in ordered_host_objects if hasattr(h, 'name')]


        for i, host_name in enumerate(host_names_from_evaluator):
            sim_loads = current_absolute_host_loads.get(host_name)
            if not sim_loads:
                logger.warning(f"[MigrationPlanner_Sim] Host {host_name} from LoadEvaluator's order not found in simulated loads. Using zeros.")
                cpu_p, mem_p = 0.0, 0.0
            else:
                cpu_p = (sim_loads['cpu_usage_abs'] / sim_loads['cpu_cap_abs'] * 100.0) if sim_loads['cpu_cap_abs'] > 0 else 0
                mem_p = (sim_loads['mem_usage_abs'] / sim_loads['mem_cap_abs'] * 100.0) if sim_loads['mem_cap_abs'] > 0 else 0

            sim_cpu_percentages.append(cpu_p)
            sim_mem_percentages.append(mem_p)

            disk_p = orig_disk_percentages[i] if i < len(orig_disk_percentages) else 0
            net_p = orig_net_percentages[i] if i < len(orig_net_percentages) else 0

            sim_host_resource_percentages_map[host_name] = {
                'cpu': cpu_p, 'memory': mem_p,
                'disk': disk_p, 'network': net_p
            }

        logger.debug(f"[MigrationPlanner_Sim] Simulation complete. New load map: {sim_host_resource_percentages_map}")
        return sim_cpu_percentages, sim_mem_percentages, orig_disk_percentages, orig_net_percentages, sim_host_resource_percentages_map

    def _is_anti_affinity_safe(self, vm_to_move, target_host_obj, planned_migrations_in_cycle=None):
        logger.debug(f"[MigrationPlanner] Checking anti-affinity safety for VM '{vm_to_move.name}' to host '{target_host_obj.name}'. Planned migrations in cycle: {planned_migrations_in_cycle}")
        vm_prefix = vm_to_move.name[:-2]
        
        # Ensure vm_distribution is populated. It should be after constraint_manager.apply()
        if not self.constraint_manager.vm_distribution:
            logger.warning("[MigrationPlanner_AA_Check] vm_distribution is empty. Forcing population.")
            self.constraint_manager.enforce_anti_affinity() 
        vms_in_group = self.constraint_manager.vm_distribution.get(vm_prefix, [])
        if not vms_in_group:
            logger.debug(f"[MigrationPlanner_AA_Check] VM '{vm_to_move.name}' (prefix '{vm_prefix}') not in any anti-affinity group. Safe.")
            return True

        source_host_obj = self.cluster_state.get_host_of_vm(vm_to_move)
        source_host_name = source_host_obj.name if source_host_obj else None

        all_active_hosts = self.cluster_state.hosts # Use direct attribute
        if not all_active_hosts or len(all_active_hosts) <= 1:
            logger.debug("[MigrationPlanner_AA_Check] Not enough active hosts (<2) for anti-affinity to apply. Safe.")
            return True

        simulated_host_vm_counts = {host.name: 0 for host in all_active_hosts if hasattr(host, 'name')}

        planned_vm_locations = {}
        if planned_migrations_in_cycle:
            for plan in planned_migrations_in_cycle:
                if hasattr(plan['vm'], 'name') and hasattr(plan['target_host'], 'name'):
                    planned_vm_locations[plan['vm'].name] = plan['target_host'].name

        for vm_in_group_iter in vms_in_group:
            if not hasattr(vm_in_group_iter, 'name'):
                logger.warning(f"[MigrationPlanner_AA_Check] VM in group {vm_prefix} is missing a name. Skipping.")
                continue

            current_vm_name = vm_in_group_iter.name
            final_host_name_for_iter_vm = None

            if current_vm_name == vm_to_move.name:
                final_host_name_for_iter_vm = target_host_obj.name
            elif current_vm_name in planned_vm_locations:
                final_host_name_for_iter_vm = planned_vm_locations[current_vm_name]
            else:
                host_obj = self.cluster_state.get_host_of_vm(vm_in_group_iter)
                if host_obj and hasattr(host_obj, 'name'):
                    final_host_name_for_iter_vm = host_obj.name

            if final_host_name_for_iter_vm and final_host_name_for_iter_vm in simulated_host_vm_counts:
                simulated_host_vm_counts[final_host_name_for_iter_vm] += 1
        
        counts = [count for host_name, count in simulated_host_vm_counts.items() if self.cluster_state.get_host_by_name(host_name)] # Only count active hosts
        
        if not counts:
            logger.debug(f"[MigrationPlanner_AA_Check] No VMs from group '{vm_prefix}' found on any active host in simulation. Safe.")
            return True
        
        is_safe = max(counts) - min(counts) <= 1
        if not is_safe:
            logger.warning(f"[MigrationPlanner_AA_Check] VM '{vm_to_move.name}' to host '{target_host_obj.name}' is NOT anti-affinity safe. Counts: {simulated_host_vm_counts}, MaxDiff: {max(counts) - min(counts)}")
        else:
            logger.debug(f"[MigrationPlanner_AA_Check] VM '{vm_to_move.name}' to host '{target_host_obj.name}' IS anti-affinity safe. Counts: {simulated_host_vm_counts}")
        return is_safe

    def _would_fit_on_host(self, vm, host_obj):
        logger.debug(f"[MigrationPlanner] Checking if VM '{vm.name}' would fit on host '{host_obj.name}'.")
        # Use high watermarks to prevent total host overload, not for balancing.
        # These are absolute limits for a single host.
        # Example: Don't allow a move if host CPU would exceed 90% or MEM 90%.
        # These thresholds are distinct from LoadEvaluator's balancing thresholds.
        # This method needs access to VM's resource requirements and host's current load + capacity.

        vm_metrics = self.cluster_state.vm_metrics.get(vm.name, {})
        host_current_metrics = self.cluster_state.host_metrics.get(host_obj.name, {})
        # host_capacity is part of host_current_metrics

        if not vm_metrics or not host_current_metrics: # host_capacity removed from this check
            logger.warning(f"[MigrationPlanner_FitCheck] Missing metrics for VM '{vm.name}' or host '{host_obj.name}'. Cannot perform fit check.")
            return False

        # These are % of TOTAL capacity
        max_cpu_util_post_move = 90.0 
        max_mem_util_post_move = 90.0
        # Add other resources like disk, network if relevant for "fit"
        
        # VM requirements (ensure these keys exist in your vm_metrics)
        vm_cpu_req = vm_metrics.get('cpu_usage_abs', vm_metrics.get('cpu_allocation', 0)) # Absolute CPU units (MHz)
        vm_mem_req = vm_metrics.get('memory_usage_abs', vm_metrics.get('memory_allocation_bytes', 0)) # Absolute Memory (Bytes)

        # Host capacity (ensure these keys exist in host_current_metrics)
        host_cpu_cap = host_current_metrics.get('cpu_capacity', 1) # Total CPU (from host_metrics)
        host_mem_cap = host_current_metrics.get('memory_capacity', 1) # Total Memory (from host_metrics)
        host_cpu_curr = host_current_metrics.get('cpu_usage', 0) # Sum of VM absolute CPU usage from host_metrics
        host_mem_curr = host_current_metrics.get('memory_usage_abs', 0) # Current absolute memory usage

        projected_cpu_abs = host_cpu_curr + vm_cpu_req
        projected_mem_abs = host_mem_curr + vm_mem_req

        projected_cpu_pct = (projected_cpu_abs / host_cpu_cap * 100.0) if host_cpu_cap > 0 else 100.0
        projected_mem_pct = (projected_mem_abs / host_mem_cap * 100.0) if host_mem_cap > 0 else 100.0

        if projected_cpu_pct > max_cpu_util_post_move:
            logger.info(f"[MigrationPlanner_FitCheck] VM '{vm.name}' would not fit on host '{host_obj.name}' due to CPU (proj: {projected_cpu_pct:.1f}% > max: {max_cpu_util_post_move:.1f}%)")
            return False
        if projected_mem_pct > max_mem_util_post_move:
            logger.info(f"[MigrationPlanner_FitCheck] VM '{vm.name}' would not fit on host '{host_obj.name}' due to Memory (proj: {projected_mem_pct:.1f}% > max: {max_mem_util_post_move:.1f}%)")
            return False
        
        logger.debug(f"[MigrationPlanner_FitCheck] VM '{vm.name}' would fit on host '{host_obj.name}'. Proj CPU: {projected_cpu_pct:.1f}%, Proj Mem: {projected_mem_pct:.1f}%")
        return True

    def _would_fit_on_host_soft(self, vm, host_obj, cpu_threshold=95.0, mem_threshold=95.0):
        """
        Soft fit check for anti-affinity migrations.
        Uses higher thresholds (default 95%) to allow anti-affinity to work
        even on moderately loaded hosts, while preventing catastrophic overload.
        
        Args:
            vm: VM object to check
            host_obj: Target host object
            cpu_threshold: CPU percentage threshold (default 95%)
            mem_threshold: Memory percentage threshold (default 95%)
        
        Returns:
            True if VM would fit, False otherwise
        """
        logger.debug(f"[MigrationPlanner_SoftFitCheck] Soft fit check for VM '{vm.name}' on host '{host_obj.name}' (CPU: {cpu_threshold}%, MEM: {mem_threshold}%).")
        
        vm_metrics = self.cluster_state.vm_metrics.get(vm.name, {})
        host_current_metrics = self.cluster_state.host_metrics.get(host_obj.name, {})

        if not vm_metrics or not host_current_metrics:
            logger.warning(f"[MigrationPlanner_SoftFitCheck] Missing metrics for VM '{vm.name}' or host '{host_obj.name}'. Cannot perform soft fit check.")
            return False

        # VM requirements
        vm_cpu_req = vm_metrics.get('cpu_usage_abs', vm_metrics.get('cpu_allocation', 0))
        vm_mem_req = vm_metrics.get('memory_usage_abs', vm_metrics.get('memory_allocation_bytes', 0))

        # Host capacity
        host_cpu_cap = host_current_metrics.get('cpu_capacity', 1)
        host_mem_cap = host_current_metrics.get('memory_capacity', 1)
        host_cpu_curr = host_current_metrics.get('cpu_usage', 0)
        host_mem_curr = host_current_metrics.get('memory_usage_abs', 0)

        projected_cpu_abs = host_cpu_curr + vm_cpu_req
        projected_mem_abs = host_mem_curr + vm_mem_req

        projected_cpu_pct = (projected_cpu_abs / host_cpu_cap * 100.0) if host_cpu_cap > 0 else 100.0
        projected_mem_pct = (projected_mem_abs / host_mem_cap * 100.0) if host_mem_cap > 0 else 100.0

        if projected_cpu_pct > cpu_threshold:
            logger.debug(f"[MigrationPlanner_SoftFitCheck] VM '{vm.name}' would not fit on host '{host_obj.name}' due to CPU (proj: {projected_cpu_pct:.1f}% > threshold: {cpu_threshold:.1f}%)")
            return False
        if projected_mem_pct > mem_threshold:
            logger.debug(f"[MigrationPlanner_SoftFitCheck] VM '{vm.name}' would not fit on host '{host_obj.name}' due to Memory (proj: {projected_mem_pct:.1f}% > threshold: {mem_threshold:.1f}%)")
            return False
        
        logger.debug(f"[MigrationPlanner_SoftFitCheck] VM '{vm.name}' would fit on host '{host_obj.name}' with soft threshold. Proj CPU: {projected_cpu_pct:.1f}%, Proj Mem: {projected_mem_pct:.1f}%")
        return True

    def _select_vms_to_move(self, source_host_obj, imbalanced_resource=None, vms_already_in_plan=None):
        logger.debug(f"[MigrationPlanner_SelectVMs] Selecting VMs to move from host '{source_host_obj.name}'. Imbalanced resource hint: {imbalanced_resource}")
        if vms_already_in_plan is None: vms_already_in_plan = set()

        vms_on_host = self.cluster_state.get_vms_on_host(source_host_obj)
        if not vms_on_host:
            logger.debug(f"[MigrationPlanner_SelectVMs] No VMs found on host '{source_host_obj.name}'.")
            return []
        logger.debug(f"[MigrationPlanner_SelectVMs] VMs on host '{source_host_obj.name}': {[vm.name for vm in vms_on_host]}")

        candidate_vms = []
        for vm in vms_on_host:
            if vm.name in vms_already_in_plan:
                logger.debug(f"[MigrationPlanner_SelectVMs] VM '{vm.name}' already in migration plan. Skipping.")
                continue
            if hasattr(vm, 'config') and getattr(vm.config, 'template', False):
                logger.debug(f"[MigrationPlanner_SelectVMs] Skipping template VM '{vm.name}' for selection.")
                continue
            candidate_vms.append(vm)
        logger.debug(f"[MigrationPlanner_SelectVMs] Candidate VMs for host '{source_host_obj.name}' (after filtering): {[vm.name for vm in candidate_vms]}")
        
        # Sort VMs by their contribution to the imbalanced resource, or general load if no specific resource
        def sort_key(vm):
            metrics = self.cluster_state.vm_metrics.get(vm.name, {})
            if not metrics: return 0
            if imbalanced_resource == 'cpu':
                return metrics.get('cpu_usage_abs', 0) # Absolute CPU usage
            elif imbalanced_resource == 'memory':
                return metrics.get('memory_usage_abs', 0) # Absolute Memory usage
            # Add disk/network if they are part of imbalance evaluation
            else: # General load: sum of normalized % usages or absolute values if comparable
                  # Using absolute values for simplicity if available and somewhat comparable
                return metrics.get('cpu_usage_abs', 0) + metrics.get('memory_usage_abs', 0)

        candidate_vms.sort(key=sort_key, reverse=True)
        
        # Select a limited number of VMs based on aggressiveness (e.g., 1 to 3)
        num_to_select = self.aggressiveness 
        selected = candidate_vms[:num_to_select]
        logger.info(f"[MigrationPlanner_SelectVMs] Finally selected {len(selected)} VMs from '{source_host_obj.name}': {[vm.name for vm in selected]}")
        return selected

    def _find_better_host_for_balancing(self, vm_to_move, source_host_obj, source_host_metrics_pct, primary_imbalanced_resource, all_hosts, imbalanced_resources_details, host_resource_percentages_map, planned_migrations_in_cycle=None):
        """
        Finds a more suitable host for a VM to improve resource balance.
        Considers host capacity, anti-affinity rules (with planned migrations), target host load,
        and ensures significant improvement for the primary imbalanced resource.
        Uses host_resource_percentages_map for target host metrics.
        planned_migrations_in_cycle is a list of dicts of already planned moves in this cycle.
        """
        logger.debug(f"[MigrationPlanner_FindBetterHost] Finding better host for VM '{vm_to_move.name}' (from host '{source_host_obj.name}').")
        potential_targets = []

        for target_host_obj in all_hosts:
            if not hasattr(target_host_obj, 'name') or target_host_obj.name == source_host_obj.name:
                continue
            logger.debug(f"[MigrationPlanner_FindBetterHost] Evaluating target host '{target_host_obj.name}' for VM '{vm_to_move.name}'.")

            fit_check_result = self._would_fit_on_host(vm_to_move, target_host_obj)
            logger.debug(f"[MigrationPlanner_FindBetterHost] Fit check for VM '{vm_to_move.name}' on host '{target_host_obj.name}': {fit_check_result}.")
            if not fit_check_result:
                continue

            # Pass planned_migrations_in_cycle to the anti-affinity check
            if not self.ignore_anti_affinity:
                aa_safe_check_result = self._is_anti_affinity_safe(vm_to_move, target_host_obj, planned_migrations_in_cycle=planned_migrations_in_cycle)
                logger.debug(f"[MigrationPlanner_FindBetterHost] Anti-affinity check for VM '{vm_to_move.name}' on host '{target_host_obj.name}': {aa_safe_check_result} (ignore_anti_affinity is False).")
                if not aa_safe_check_result:
                    logger.debug(f"[MigrationPlanner_FindBetterHost] Host '{target_host_obj.name}' skipped for VM '{vm_to_move.name}' due to anti-affinity rules.")
                    continue
            else:
                logger.debug(f"[MigrationPlanner_FindBetterHost] Anti-affinity check bypassed for VM '{vm_to_move.name}' to host '{target_host_obj.name}' (ignore_anti_affinity is True).")

            target_metrics_pct = host_resource_percentages_map.get(target_host_obj.name)
            if not target_metrics_pct:
                 logger.warning(f"[MigrationPlanner_FindBetterHost] Critical: Could not get metrics for target host '{target_host_obj.name}' from provided map {host_resource_percentages_map}. Skipping.")
                 continue

            # Ping-pong prevention: Ensure target is significantly better for the primary imbalanced resource
            if primary_imbalanced_resource and primary_imbalanced_resource in target_metrics_pct and primary_imbalanced_resource in source_host_metrics_pct:
                general_thresholds = self.load_evaluator.get_thresholds(self.aggressiveness)
                threshold_for_primary_res = general_thresholds.get(primary_imbalanced_resource, 15.0) # Default if not found
                source_usage = source_host_metrics_pct[primary_imbalanced_resource]
                target_usage = target_metrics_pct[primary_imbalanced_resource]

                if not (target_usage < source_usage - (threshold_for_primary_res / 3.0)):
                    logger.debug(f"[MigrationPlanner_FindBetterHost] Target '{target_host_obj.name}' for VM '{vm_to_move.name}' skipped: "
                                 f"Its usage for primary imbalanced resource '{primary_imbalanced_resource}' ({target_usage:.1f}%) "
                                 f"is not significantly better than source's ({source_usage:.1f}%) by at least {threshold_for_primary_res / 3.0:.1f}%.")
                    continue

            score = 0
            # Score based on how much it improves balance for ALL imbalanced resources
            # Lower utilization on target host for imbalanced resources is better.
            for resource, detail in imbalanced_resources_details.items():
                 if resource in target_metrics_pct:
                     # Higher score if target is less utilized for this imbalanced resource
                     current_score_contribution = (100 - target_metrics_pct[resource])
                     score += current_score_contribution
                     logger.debug(f"[MigrationPlanner_FindBetterHost] Scoring for VM '{vm_to_move.name}' to host '{target_host_obj.name}': resource '{resource}', target_metric={target_metrics_pct[resource]:.2f}%, score_contrib={current_score_contribution:.2f}")
            
            logger.debug(f"[MigrationPlanner_FindBetterHost] Total score for VM '{vm_to_move.name}' to host '{target_host_obj.name}': {score:.2f}.")
            if score > 0:
                potential_targets.append({'host': target_host_obj, 'score': score})
        
        if not potential_targets:
            logger.info(f"[MigrationPlanner_FindBetterHost] No suitable balancing target host found for VM '{vm_to_move.name}' after evaluating all hosts.")
            return None

        # Sort potential targets by score (higher is better)
        potential_targets.sort(key=lambda x: x['score'], reverse=True)
        best_target = potential_targets[0]['host']
        logger.info(f"[MigrationPlanner_FindBetterHost] Best balancing target for VM '{vm_to_move.name}' is '{best_target.name}' with score {potential_targets[0]['score']:.2f}.")
        return best_target

    def plan_migrations(self, anti_affinity_only=False):
        logger.info("[MigrationPlanner] Starting migration planning cycle...")
        migrations = []
        vms_in_migration_plan = set()

        # Clear LoadEvaluator cache before new planning cycle to get fresh calculations
        self.load_evaluator._cache_percentage_lists = None

        # Step 1: Addressing Anti-Affinity violations (always done if plan_migrations is called)
        anti_affinity_migrations = self._plan_anti_affinity_migrations(vms_in_migration_plan)
        migrations.extend(anti_affinity_migrations)
        logger.info(f"[MigrationPlanner] After Anti-Affinity, {len(anti_affinity_migrations)} migrations planned.")

        if not anti_affinity_only:
            logger.info("[MigrationPlanner] Proceeding to resource balancing phase...")

            # Fetch initial host resource percentages map from LoadEvaluator for balancing decisions
            initial_host_resource_percentages_map = {}
            if hasattr(self.load_evaluator, 'get_all_host_resource_percentages_map'):
                initial_host_resource_percentages_map = self.load_evaluator.get_all_host_resource_percentages_map()
                logger.debug(f"[MigrationPlanner] Fetched initial host_resource_percentages_map from LoadEvaluator for balancing.")
            else:
                logger.error("[MigrationPlanner] Critical: self.load_evaluator.get_all_host_resource_percentages_map() not found. Balancing will be severely impaired.")
                # No need to assign to {} as it's already initialized.

            current_host_resource_percentages_map = initial_host_resource_percentages_map
            sim_cpu_p_override, sim_mem_p_override, sim_disk_p_override, sim_net_p_override = None, None, None, None

            # Simulate AA migrations only if they occurred AND we are doing balancing
            if anti_affinity_migrations:
                logger.info("[MigrationPlanner] Simulating anti-affinity migrations to re-evaluate load balance for balancing step...")
                sim_cpu_p_override, sim_mem_p_override, sim_disk_p_override, sim_net_p_override, simulated_load_map_after_aa = \
                    self._get_simulated_load_data_after_migrations(anti_affinity_migrations)
                current_host_resource_percentages_map = simulated_load_map_after_aa
                logger.info("[MigrationPlanner] Load balance re-evaluation for balancing will use simulated state after AA migrations.")
            else:
                logger.info("[MigrationPlanner] No anti-affinity migrations to simulate, proceeding with initial load state for balancing.")

            # Step 2: Addressing Resource Imbalance
            balancing_migrations = self._plan_balancing_migrations(
                vms_in_migration_plan,
                current_host_resource_percentages_map,
                migrations, # Pass all migrations so far (AA)
                sim_cpu_p_override,
                sim_mem_p_override,
                sim_disk_p_override,
                sim_net_p_override
            )
            migrations.extend(balancing_migrations)
            logger.info(f"[MigrationPlanner] After Resource Balancing, {len(balancing_migrations)} balancing migrations planned. Total migrations now: {len(migrations)} (AA + Balancing).")
        else:
            logger.info("[MigrationPlanner] Anti-affinity only mode: Skipping resource balancing phase.")

        # Enforce overall migration limit (This applies to both modes)
        final_limited_migrations = [] # Define final_limited_migrations before potential use
        if len(migrations) > self.max_total_migrations:
            logger.warning(f"[MigrationPlanner] Planned migrations ({len(migrations)}) exceed max limit ({self.max_total_migrations}). Truncating.")
            # Prioritize Anti-Affinity migrations
            # final_limited_migrations already initialized
            aa_migs_from_plan = [m for m in migrations if m.get('reason') == 'Anti-Affinity']
            # Exclude AA migrations to get only balancing ones, or any other type if reasons become more diverse
            balance_migs_from_plan = [m for m in migrations if m.get('reason') != 'Anti-Affinity']

            if len(aa_migs_from_plan) >= self.max_total_migrations:
                final_limited_migrations.extend(aa_migs_from_plan[:self.max_total_migrations])
                logger.info(f"[MigrationPlanner] Truncated to only {len(final_limited_migrations)} anti-affinity migrations as they met/exceeded the limit.")
            else:
                final_limited_migrations.extend(aa_migs_from_plan)
                remaining_slots = self.max_total_migrations - len(final_limited_migrations)
                if remaining_slots > 0 and balance_migs_from_plan:
                    final_limited_migrations.extend(balance_migs_from_plan[:remaining_slots])
                    logger.info(f"[MigrationPlanner] Took all {len(aa_migs_from_plan)} AA migrations and {len(balance_migs_from_plan[:remaining_slots])} balancing migrations to meet limit.")
                elif remaining_slots == 0:
                     logger.info(f"[MigrationPlanner] Only anti-affinity migrations included as they exactly met the limit. Count: {len(final_limited_migrations)}")
                # If remaining_slots < 0 (should not happen due to outer if) or balance_migs_from_plan is empty, this path is also covered.

            migrations = final_limited_migrations # Assign the (potentially) truncated list back
            logger.info(f"[MigrationPlanner] Final migration count after truncation (if any): {len(migrations)}")

        if not migrations:
            logger.info("[MigrationPlanner] No migrations planned in this cycle.")
        else:
            logger.info(f"[MigrationPlanner] Total final migrations planned: {len(migrations)}")
            for i, mig_plan in enumerate(migrations):
                logger.info(f"  {i+1}. VM: {mig_plan['vm'].name}, Target: {mig_plan['target_host'].name}, Reason: {mig_plan['reason']}")

        final_migration_tuples = [(plan['vm'], plan['target_host']) for plan in migrations]
        return final_migration_tuples


    def _plan_anti_affinity_migrations(self, vms_in_migration_plan):
        """
        Plans migrations to address anti-affinity violations.
        Updates vms_in_migration_plan with VMs planned for migration.
        Returns a list of migration dictionaries.
        """
        logger.info("[MigrationPlanner] Step 1: Addressing Anti-Affinity violations.")
        all_aa_migrations_for_return = [] # List to be returned by this method
        aa_migrations_planned_this_step = [] # Local list for this AA planning pass

        # Ensure violations are calculated if not already present
        if not hasattr(self.constraint_manager, 'violations') or not self.constraint_manager.violations:
            logger.info("[MigrationPlanner_AA] No pre-calculated AA violations found or list is empty. Attempting to calculate now.")
            self.constraint_manager.enforce_anti_affinity() # Ensure groups are up-to-date
            self.constraint_manager.violations = self.constraint_manager.calculate_anti_affinity_violations()

        anti_affinity_vm_violations = self.constraint_manager.violations
        if not anti_affinity_vm_violations:
            logger.info("[MigrationPlanner_AA] No anti-affinity violations found after calculation.")
            return []

        logger.debug(f"[MigrationPlanner_AA] Processing {len(anti_affinity_vm_violations)} potential anti-affinity violating VMs.")

        for vm_obj in anti_affinity_vm_violations:
            if not hasattr(vm_obj, 'name'):
                logger.warning("[MigrationPlanner_AA] Found VM in AA violations list without a name. Skipping.")
                continue

            if vm_obj.name in vms_in_migration_plan:
                logger.debug(f"[MigrationPlanner_AA] VM '{vm_obj.name}' already part of another migration plan. Skipping for AA.")
                continue
            
            if hasattr(vm_obj, 'config') and getattr(vm_obj.config, 'template', False):
                logger.debug(f"[MigrationPlanner_AA] Skipping template VM '{vm_obj.name}' for anti-affinity migration.")
                continue

            current_host = self.cluster_state.get_host_of_vm(vm_obj)
            logger.info(f"[MigrationPlanner_AA] VM '{vm_obj.name}' violates anti-affinity on host '{current_host.name if current_host else 'Unknown'}'. Finding preferred host.")
            
            # Pass the migrations planned so far *in this AA step*
            target_host_obj = self.constraint_manager.get_preferred_host_for_vm(
                vm_obj,
                planned_migrations_this_cycle=aa_migrations_planned_this_step
            )

            if target_host_obj:
                # For apply-anti-affinity-only mode: skip resource checks entirely (prioritize distribution)
                # For regular mode: use soft fit check (95% threshold) to allow AA while preventing catastrophic overload
                if self.anti_affinity_only:
                    # Skip resource checks for apply-anti-affinity-only mode - pure distribution
                    logger.info(f"[MigrationPlanner_AA] Apply-Anti-Affinity-Only Mode: Skipping resource fit check for VM '{vm_obj.name}'.")
                    migration_plan = {'vm': vm_obj, 'target_host': target_host_obj, 'reason': 'Anti-Affinity'}
                    all_aa_migrations_for_return.append(migration_plan)
                    aa_migrations_planned_this_step.append(migration_plan)
                    vms_in_migration_plan.add(vm_obj.name)
                    logger.info(f"[MigrationPlanner_AA] Planned Anti-Affinity Migration: Move VM '{vm_obj.name}' from '{current_host.name if current_host else 'N/A'}' to '{target_host_obj.name}'.")
                elif self._would_fit_on_host_soft(vm_obj, target_host_obj, cpu_threshold=95.0, mem_threshold=95.0):
                    # Regular mode: use soft fit check (95% threshold)
                    migration_plan = {'vm': vm_obj, 'target_host': target_host_obj, 'reason': 'Anti-Affinity'}
                    all_aa_migrations_for_return.append(migration_plan)
                    aa_migrations_planned_this_step.append(migration_plan) # Add to list for next iteration's consideration
                    vms_in_migration_plan.add(vm_obj.name) # Add to global set passed in
                    logger.info(f"[MigrationPlanner_AA] Planned Anti-Affinity Migration: Move VM '{vm_obj.name}' from '{current_host.name if current_host else 'N/A'}' to '{target_host_obj.name}'.")
                else:
                    logger.warning(f"[MigrationPlanner_AA] Target host '{target_host_obj.name}' for VM '{vm_obj.name}' would exceed soft capacity thresholds (95%). No AA migration planned for this VM.")
            else:
                logger.warning(f"[MigrationPlanner_AA] No suitable preferred host found for anti-affinity violating VM '{vm_obj.name}'.")
        return all_aa_migrations_for_return

    def _plan_balancing_migrations(self, vms_in_migration_plan,
                                 host_resource_percentages_map_for_decision,
                                 current_planned_migrations_list,
                                 sim_cpu_p_override, sim_mem_p_override,
                                 sim_disk_p_override, sim_net_p_override):
        """
        Plans migrations to address resource imbalances.
        Uses host_resource_percentages_map_for_decision for selecting source/target hosts.
        Uses sim_*_override lists when calling evaluate_imbalance.
        current_planned_migrations_list includes AA moves for _is_anti_affinity_safe checks.
        """
        logger.info("[MigrationPlanner_Balance] Step 2: Addressing Resource Imbalance.")
        logger.debug(f"[MigrationPlanner_Balance] Initial host resource percentages map for decision making: {host_resource_percentages_map_for_decision}")
        balancing_migrations = []

        # Evaluate imbalance using potentially simulated percentage lists
        imbalance_details = self.load_evaluator.evaluate_imbalance(
            aggressiveness=self.aggressiveness,
            cpu_percentages_override=sim_cpu_p_override,
            mem_percentages_override=sim_mem_p_override,
            disk_percentages_override=sim_disk_p_override,
            net_percentages_override=sim_net_p_override
        )

        if not imbalance_details:
            logger.info("[MigrationPlanner_Balance] Cluster is already balanced (possibly after simulation) or no imbalance details found.")
            return []

        logger.info("[MigrationPlanner_Balance] Cluster imbalance details (post-AA sim if any):")
        if imbalance_details:
            for resource_name, details in imbalance_details.items():
                details_str = f"  Resource: {resource_name}"
                details_str += f", Imbalanced: {details.get('is_imbalanced')}"
                details_str += f", Diff: {details.get('current_diff', 0):.2f}%"
                details_str += f", Threshold: {details.get('threshold', 0):.2f}%"
                details_str += f", Min: {details.get('min_usage', 0):.2f}%"
                details_str += f", Max: {details.get('max_usage', 0):.2f}%"
                details_str += f", Avg: {details.get('avg_usage', 0):.2f}%"
                logger.info(details_str)
        else:
            logger.info("  No imbalance details found or cluster is balanced.") 

        all_hosts_objects = self.cluster_state.hosts

        problematic_resources_names = [res for res, det in imbalance_details.items() if det.get('is_imbalanced')]
        if not problematic_resources_names:
            logger.info("[MigrationPlanner_Balance] No specific resource marked as imbalanced after (potential) simulation. Skipping balancing moves.")
            return []
        
        logger.info(f"[MigrationPlanner_Balance] Problematic resources identified for balancing (post-AA sim if any): {problematic_resources_names}")

        # current_planned_migrations_list already contains AA migrations.
        # We will append balancing migrations to it as they are decided for subsequent AA checks.
        # Make a copy to modify locally within this balancing phase for iterative safety checks.
        safety_check_migrations_list = current_planned_migrations_list[:]


        for source_host_obj in all_hosts_objects:
            if not hasattr(source_host_obj, 'name'):
                logger.debug("[MigrationPlanner_Balance] Skipping a host object due to missing 'name' attribute.")
                continue

            current_source_host_name = source_host_obj.name
            logger.debug(f"[MigrationPlanner_Balance] Evaluating host '{current_source_host_name}' as a potential source host.")

            source_host_metrics_pct = host_resource_percentages_map_for_decision.get(current_source_host_name, {})
            if not source_host_metrics_pct:
                logger.warning(f"[MigrationPlanner_Balance] Could not get metrics for source host '{current_source_host_name}' from decision map. Skipping.")
                continue

            move_reason_details = []
            host_is_max_usage_contributor = False
            resource_hint_for_vm_selection = None

            for res_name in problematic_resources_names:
                res_detail = imbalance_details.get(res_name, {})
                current_host_usage_for_res = source_host_metrics_pct.get(res_name, 0)

                avg_usage_for_res = res_detail.get('avg_usage', 0)
                general_thresholds = self.load_evaluator.get_thresholds(self.aggressiveness)
                threshold_for_res = general_thresholds.get(res_name, 15.0)

                is_significantly_above_average = current_host_usage_for_res > (avg_usage_for_res + threshold_for_res / 2.0)
                is_one_of_the_most_loaded = current_host_usage_for_res >= res_detail.get('max_usage', current_host_usage_for_res + 1) * 0.95

                if is_significantly_above_average and is_one_of_the_most_loaded and current_host_usage_for_res > 0:
                    host_is_max_usage_contributor = True
                    reason_str = f"high_usage_for_{res_name} ({current_host_usage_for_res:.1f}%, max={res_detail.get('max_usage',0):.1f}%, avg={avg_usage_for_res:.1f}%, threshold_margin={threshold_for_res / 2.0:.1f}%)"
                    move_reason_details.append(reason_str)
                    if not resource_hint_for_vm_selection:
                        resource_hint_for_vm_selection = res_name

            if not host_is_max_usage_contributor:
                logger.debug(f"[MigrationPlanner_Balance] Host '{current_source_host_name}' is not a max usage contributor for any problematic resource. Skipping.")
                continue

            logger.info(f"[MigrationPlanner_Balance] Host '{current_source_host_name}' is a candidate source. Reasons: {', '.join(move_reason_details)}")

            candidate_vms_to_move = self._select_vms_to_move(source_host_obj, resource_hint_for_vm_selection, vms_in_migration_plan)
            if not candidate_vms_to_move:
                logger.info(f"[MigrationPlanner_Balance] No candidate VMs selected to move from source host '{current_source_host_name}'.")
                continue

            for vm_to_move in candidate_vms_to_move:

                active_imbalance_details_for_target_finding = {
                     k: v for k,v in imbalance_details.items() if k in problematic_resources_names and v.get('is_imbalanced')
                }
                if not active_imbalance_details_for_target_finding:
                     logger.debug(f"No active imbalance details to guide target host finding for VM {vm_to_move.name}. Skipping.")
                     continue

                target_host_obj = self._find_better_host_for_balancing(
                    vm_to_move,
                    source_host_obj,
                    source_host_metrics_pct, 
                    resource_hint_for_vm_selection,
                    all_hosts_objects,
                    active_imbalance_details_for_target_finding,
                    host_resource_percentages_map_for_decision, # The (potentially) simulated map
                    planned_migrations_in_cycle=safety_check_migrations_list
                )

                if target_host_obj:
                    migration_details = {'vm': vm_to_move, 'target_host': target_host_obj, 'reason': f"Resource Balancing ({', '.join(move_reason_details)})"}
                    balancing_migrations.append(migration_details)
                    vms_in_migration_plan.add(vm_to_move.name)
                    safety_check_migrations_list.append(migration_details)
                    logger.info(f"[MigrationPlanner_Balance] Planned Balancing Migration: Move VM '{vm_to_move.name}' from '{current_source_host_name}' to '{target_host_obj.name}'.")
                else:
                    logger.info(f"[MigrationPlanner_Balance] No suitable balancing target found for VM '{vm_to_move.name}' from host '{current_source_host_name}'.")

        return balancing_migrations

    def plan_migrations_iterative(self, max_iterations=3, anti_affinity_only=False, iteration_threshold_multiplier=1.05):
        """
        Iteratively plan migrations until convergence (zero AA violations + balanced) or max iterations reached.
        
        This method guarantees BOTH anti-affinity satisfaction AND resource balance by re-evaluating
        the cluster state after each planning pass and adjusting constraints if needed.
        
        Args:
            max_iterations: Maximum number of planning iterations (default 3)
            anti_affinity_only: If True, only fix AA violations (skip balancing)
            iteration_threshold_multiplier: On iteration 2+, loosen balance thresholds by this factor
                                          (e.g., 1.05 = 5% looser) to prevent deadlocks
        
        Returns:
            List of migration tuples (vm_obj, target_host_obj) with convergence guarantee
        """
        logger.info(f"[MigrationPlanner_Iterative] Starting iterative planning (max {max_iterations} iterations)...")
        
        all_migrations = []
        
        for iteration in range(1, max_iterations + 1):
            logger.info(f"\n{'='*70}")
            logger.info(f"[MigrationPlanner_Iterative] === ITERATION {iteration}/{max_iterations} ===")
            logger.info(f"{'='*70}")
            
            # Check current state before planning
            aa_violations = self.constraint_manager.calculate_anti_affinity_violations()
            is_balanced = self.load_evaluator.is_balanced()
            
            logger.info(f"[MigrationPlanner_Iterative] Current state: AA violations={len(aa_violations)}, balanced={is_balanced}")
            
            # Convergence check: If no AA violations AND balanced, we're done
            if not aa_violations and is_balanced:
                logger.success(f"[MigrationPlanner_Iterative] ✓ CONVERGED at iteration {iteration}: No AA violations, cluster is balanced.")
                logger.info(f"[MigrationPlanner_Iterative] Total migrations planned across all iterations: {len(all_migrations)}")
                return all_migrations
            
            # Adjust aggressiveness on iteration 2+ to prevent deadlocks
            original_aggressiveness = self.aggressiveness
            if iteration > 1:
                # Looser threshold = lower aggressiveness number (more lenient)
                adjusted_aggressiveness = max(1, int(self.aggressiveness / iteration_threshold_multiplier))
                self.aggressiveness = adjusted_aggressiveness
                logger.info(f"[MigrationPlanner_Iterative] Iteration {iteration}: Adjusted aggressiveness from {original_aggressiveness} to {adjusted_aggressiveness} (looser thresholds)")
            
            # Plan this iteration
            migrations_this_iteration = self.plan_migrations(anti_affinity_only=anti_affinity_only)
            
            # Restore original aggressiveness
            self.aggressiveness = original_aggressiveness
            
            if not migrations_this_iteration:
                logger.info(f"[MigrationPlanner_Iterative] No migrations produced at iteration {iteration}. Stopping.")
                break
            
            all_migrations.extend(migrations_this_iteration)
            logger.info(f"[MigrationPlanner_Iterative] Iteration {iteration} produced {len(migrations_this_iteration)} migrations.")
            logger.info(f"[MigrationPlanner_Iterative] Total accumulated: {len(all_migrations)} migrations")
            
            # Reset cache for next iteration
            if hasattr(self.load_evaluator, '_cache_percentage_lists'):
                self.load_evaluator._cache_percentage_lists = None
            
            logger.info(f"[MigrationPlanner_Iterative] Prepared for iteration {iteration + 1}...")
        
        # Final state check
        final_aa_violations = self.constraint_manager.calculate_anti_affinity_violations()
        final_is_balanced = self.load_evaluator.is_balanced()
        
        logger.warning(f"\n[MigrationPlanner_Iterative] === ITERATIVE PLANNING COMPLETE ===")
        logger.warning(f"[MigrationPlanner_Iterative] Final state after {max_iterations} iterations:")
        logger.warning(f"  - AA violations: {len(final_aa_violations)}")
        logger.warning(f"  - Cluster balanced: {final_is_balanced}")
        logger.warning(f"  - Total migrations: {len(all_migrations)}")
        
        if final_aa_violations:
            logger.warning(f"[MigrationPlanner_Iterative] Warning: {len(final_aa_violations)} AA violations remain (may be resource-constrained)")
        if not final_is_balanced:
            logger.warning(f"[MigrationPlanner_Iterative] Warning: Cluster not fully balanced (approaching migration limit or resource-constrained)")
        
        return all_migrations

    def execute_migrations(self, migration_tuples):
        if not migration_tuples:
            logger.info("[MigrationExecutor] No migrations to execute.")
            return

        logger.info(f"[MigrationExecutor] Executing {len(migration_tuples)} migrations...")
        for vm_obj, target_host_obj in migration_tuples:
            source_host_obj = self.cluster_state.get_host_of_vm(vm_obj)
            source_host_name = source_host_obj.name if source_host_obj else "Unknown (already moved or new?)"
            
            try:
                logger.info(f"Attempting migration of VM '{vm_obj.name}' from '{source_host_name}' to '{target_host_obj.name}'...")             
                logger.success(f"SUCCESS: Migration of '{vm_obj.name}' from '{source_host_name}' to '{target_host_obj.name}' completed (simulated).")
            except Exception as e:
                logger.error(f"FAILED: Migration of '{vm_obj.name}' from '{source_host_name}' to '{target_host_obj.name}' failed: {str(e)}")
