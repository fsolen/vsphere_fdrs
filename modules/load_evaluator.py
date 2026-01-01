import logging

logger = logging.getLogger('fdrs')

class LoadEvaluator:
    def __init__(self, hosts):
        self.hosts = hosts
        self._cache_percentage_lists = None

    def get_resource_percentage_lists(self):
        if self._cache_percentage_lists is not None:
            return self._cache_percentage_lists
            
        cpu_percentages = []
        mem_percentages = []
        disk_percentages = []
        net_percentages = []

        if not isinstance(self.hosts, list) or not self.hosts:
            logger.warning(f"[LoadEvaluator] Hosts list is not a list or is empty (type: {type(self.hosts)}). Cannot calculate percentage lists.")
            self._cache_percentage_lists = ([], [], [], [])
            return self._cache_percentage_lists

        for host_data in self.hosts:
            if not isinstance(host_data, dict):
                logger.warning(f"[LoadEvaluator] Expected a dict for host_data, got {type(host_data)}. Skipping this host.")
                cpu_percentages.append(0.0)
                mem_percentages.append(0.0)
                disk_percentages.append(0.0)
                net_percentages.append(0.0)
                continue

            cpu_usage = host_data.get('cpu_usage', 0.0)
            cpu_capacity = host_data.get('cpu_capacity', 0.0)
            cpu_perc = (cpu_usage / cpu_capacity * 100.0) if cpu_capacity > 0 else 0.0
            cpu_percentages.append(cpu_perc)

            mem_usage = host_data.get('memory_usage', 0.0)
            mem_capacity = host_data.get('memory_capacity', 0.0)
            mem_perc = (mem_usage / mem_capacity * 100.0) if mem_capacity > 0 else 0.0
            mem_percentages.append(mem_perc)

            disk_usage = host_data.get('disk_io_usage', 0.0) 
            disk_capacity = host_data.get('disk_io_capacity', 0.0) 
            disk_perc = (disk_usage / disk_capacity * 100.0) if disk_capacity > 0 else 0.0
            disk_percentages.append(disk_perc)

            net_usage = host_data.get('network_io_usage', 0.0)
            net_capacity = host_data.get('network_capacity', 0.0)
            net_perc = (net_usage / net_capacity * 100.0) if net_capacity > 0 else 0.0
            net_percentages.append(net_perc)
        
        self._cache_percentage_lists = (cpu_percentages, mem_percentages, disk_percentages, net_percentages)
        return self._cache_percentage_lists

    def get_thresholds(self, aggressiveness=3):
        mapping = {
            5: 5.0,
            4: 10.0,
            3: 15.0,
            2: 20.0,
            1: 25.0
        }
        threshold_value = mapping.get(aggressiveness, 15.0) 
        
        if aggressiveness not in mapping:
            logger.warning(f"[LoadEvaluator] Invalid aggressiveness level: {aggressiveness}. Defaulting to threshold: {threshold_value}%.")

        thresholds = {
            'cpu': threshold_value,
            'memory': threshold_value,
            'disk': threshold_value,
            'network': threshold_value
        }
        logger.debug(f"[LoadEvaluator] Aggressiveness: {aggressiveness}, Max Difference Thresholds: {thresholds}")
        return thresholds

    def evaluate_imbalance(self, metrics_to_check=None, aggressiveness=3,
                       cpu_percentages_override=None,
                       mem_percentages_override=None,
                       disk_percentages_override=None,
                       net_percentages_override=None):
        if cpu_percentages_override is not None and \
           mem_percentages_override is not None and \
           disk_percentages_override is not None and \
           net_percentages_override is not None:
            logger.debug("[LoadEvaluator] Using overridden resource percentage lists for imbalance evaluation.")
            cpu_percentages = cpu_percentages_override
            mem_percentages = mem_percentages_override
            disk_percentages = disk_percentages_override
            net_percentages = net_percentages_override
        else:
            logger.debug("[LoadEvaluator] Using internal resource percentage lists for imbalance evaluation.")
            cpu_percentages, mem_percentages, disk_percentages, net_percentages = self.get_resource_percentage_lists()
        
        all_metrics_data = {
            'cpu': cpu_percentages,
            'memory': mem_percentages,
            'disk': disk_percentages,
            'network': net_percentages
        }

        if metrics_to_check is None:
            metrics_to_check = ['cpu', 'memory', 'disk', 'network']

        allowed_thresholds = self.get_thresholds(aggressiveness) 
        imbalance_results = {}

        for resource_name in metrics_to_check:
            percentages = all_metrics_data.get(resource_name, []) # Default to empty list
            resource_threshold = allowed_thresholds.get(resource_name) # Get threshold for the resource
            
            if resource_threshold is None: 
                logger.error(f"[LoadEvaluator] Critical: No threshold defined for resource: {resource_name}. Skipping this resource.")
                imbalance_results[resource_name] = {
                    'is_imbalanced': False, 'current_diff': 0, 'threshold': 0, 
                    'min_usage': 0, 'max_usage': 0, 'avg_usage': 0, 'all_percentages': []
                }
                continue 

            if not percentages or len(percentages) < 2:
                logger.debug(f"[LoadEvaluator] Not enough data points for resource '{resource_name}' (count: {len(percentages)}), considered balanced.")
                imbalance_results[resource_name] = {
                    'is_imbalanced': False, 'current_diff': 0, 'threshold': resource_threshold, 
                    'min_usage': 0, 'max_usage': 0, 'avg_usage': 0, 'all_percentages': percentages
                }
                continue

            current_min_usage = min(percentages)
            current_max_usage = max(percentages)
            current_avg_usage = sum(percentages) / len(percentages) # len will not be 0 here
            current_diff = current_max_usage - current_min_usage
            
            is_res_imbalanced = False
            if current_diff > resource_threshold:
                logger.warning(f"[LoadEvaluator] Resource '{resource_name}' is imbalanced. Difference {current_diff:.2f}% > Threshold {resource_threshold:.2f}% (Aggressiveness: {aggressiveness})")
                is_res_imbalanced = True
            else:
                logger.debug(f"[LoadEvaluator] Resource '{resource_name}' is balanced. Difference {current_diff:.2f}% <= Threshold {resource_threshold:.2f}% (Aggressiveness: {aggressiveness})")

            imbalance_results[resource_name] = {
                'is_imbalanced': is_res_imbalanced,
                'current_diff': round(current_diff, 2),
                'threshold': resource_threshold,
                'min_usage': round(current_min_usage, 2),
                'max_usage': round(current_max_usage, 2),
                'avg_usage': round(current_avg_usage, 2),
                'all_percentages': [round(p, 2) for p in percentages]
            }
        return imbalance_results

    def is_balanced(self, metrics=None, aggressiveness=3,
                    cpu_percentages_override=None,
                    mem_percentages_override=None,
                    disk_percentages_override=None,
                    net_percentages_override=None):
        # Pass through the overrides to evaluate_imbalance
        imbalance_details = self.evaluate_imbalance(
            metrics_to_check=metrics,
            aggressiveness=aggressiveness,
            cpu_percentages_override=cpu_percentages_override,
            mem_percentages_override=mem_percentages_override,
            disk_percentages_override=disk_percentages_override,
            net_percentages_override=net_percentages_override
        )
        if not imbalance_details:
            return True
        for resource_name, details in imbalance_details.items():
            if details.get('is_imbalanced', False): 
                return False # Found an imbalanced resource
        return True # All resources are balanced

    def get_resource_usage_lists(self):
        if not isinstance(self.hosts, list) or not all(isinstance(h, dict) for h in self.hosts if h is not None):
            logger.error("[LoadEvaluator] self.hosts is not a list of dictionaries or contains None values.")
            return [], [], [], []

        cpu_usage = [metrics.get('cpu_usage', 0.0) for metrics in self.hosts if metrics]
        mem_usage = [metrics.get('memory_usage', 0.0) for metrics in self.hosts if metrics]
        disk_io = [metrics.get('disk_io_usage', 0.0) for metrics in self.hosts if metrics]
        net_io = [metrics.get('network_io_usage', 0.0) for metrics in self.hosts if metrics]
        
        self.cluster_totals = {
            'cpu': sum(cpu_usage),
            'memory': sum(mem_usage),
            'disk_io': sum(disk_io),
            'network_io': sum(net_io)
        }
        
        num_hosts = len([h for h in self.hosts if h])
        self.target_per_host = {
            'cpu': self.cluster_totals['cpu'] / num_hosts if num_hosts > 0 else 0,
            'memory': self.cluster_totals['memory'] / num_hosts if num_hosts > 0 else 0,
            'disk_io': self.cluster_totals['disk_io'] / num_hosts if num_hosts > 0 else 0,
            'network_io': self.cluster_totals['network_io'] / num_hosts if num_hosts > 0 else 0
        }
        
        self.resource_deviations = {
            'cpu': [abs(usage - self.target_per_host['cpu']) for usage in cpu_usage],
            'memory': [abs(usage - self.target_per_host['memory']) for usage in mem_usage],
            'disk_io': [abs(usage - self.target_per_host['disk_io']) for usage in disk_io],
            'network_io': [abs(usage - self.target_per_host['network_io']) for usage in net_io]
        }
        return cpu_usage, mem_usage, disk_io, net_io

    def get_all_host_resource_percentages_map(self):
        """
        Calculates and returns a dictionary mapping host names to their resource usage percentages.
        Example structure: {host_name: {'cpu': %, 'memory': %, 'disk': %, 'network': %}, ...}
        """
        logger.debug("[LoadEvaluator] Generating all host resource percentages map.")
        cpu_p, mem_p, disk_p, net_p = self.get_resource_percentage_lists()
        host_names = []
        if isinstance(self.hosts, list):
            for i, host_data in enumerate(self.hosts):
                if isinstance(host_data, dict):
                    name = host_data.get('name')
                    if name:
                        host_names.append(name)
                    else:
                        logger.warning(f"[LoadEvaluator] Host at index {i} is missing a 'name' key. Using placeholder name.")
                        host_names.append(f"unknown_host_{i}")
                else:
                    logger.warning(f"[LoadEvaluator] Host data at index {i} is not a dict. Using placeholder name.")
                    host_names.append(f"invalid_host_data_{i}")
        else:
            logger.error("[LoadEvaluator] self.hosts is not a list. Cannot generate host resource map.")
            return {}

        if not (len(cpu_p) == len(mem_p) == len(disk_p) == len(net_p) == len(host_names)):
            logger.error(f"[LoadEvaluator] Mismatch in lengths of percentage lists and host names. "
                         f"Hosts: {len(host_names)}, CPU: {len(cpu_p)}, MEM: {len(mem_p)}, "
                         f"DISK: {len(disk_p)}, NET: {len(net_p)}. Returning partial/empty map.")

        result_map = {}
        for i, hn in enumerate(host_names):
            if i < len(cpu_p) and i < len(mem_p) and i < len(disk_p) and i < len(net_p):
                result_map[hn] = {
                    'cpu': cpu_p[i],
                    'memory': mem_p[i],
                    'disk': disk_p[i],
                    'network': net_p[i],
                }
            else:
                logger.warning(f"[LoadEvaluator] Data for host '{hn}' (index {i}) might be incomplete due to list length mismatch. Assigning empty metrics.")
                result_map[hn] = {'cpu': 0, 'memory': 0, 'disk': 0, 'network': 0}

        logger.debug(f"[LoadEvaluator] Generated host resource percentages map: {result_map}")
        return result_map
