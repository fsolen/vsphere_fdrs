# Cluster Selection Feature - Implementation Summary

## Feature Added: `--cluster` Switch

Added the ability to target a specific cluster within vCenter for balancing VMs, instead of processing all clusters.

---

## Changes Made

### 1. **fdrs.py** - CLI Argument
Added `--cluster` argument to the argument parser:
```python
parser.add_argument("--cluster", default='', 
    help="Specific cluster name to balance (optional; if not provided, all clusters are processed)")
```

**Usage:**
```bash
python fdrs.py --vcenter <vc_ip_or_hostname> --username <user> --cluster <cluster_name>
```

### 2. **fdrs.py** - Main Function
Modified ClusterState instantiation to pass cluster_name:
```python
cluster_state = ClusterState(service_instance, cluster_name=args.cluster if args.cluster else None)

if args.cluster:
    logger.info(f"[Main] Targeting cluster: '{args.cluster}'")
else:
    logger.info("[Main] Targeting all clusters in vCenter")
```

### 3. **cluster_state.py** - Constructor
Updated `__init__` to accept optional cluster_name parameter:
```python
def __init__(self, service_instance, cluster_name=None):
    self.service_instance = service_instance
    self.cluster_name = cluster_name  # Optional: filter by specific cluster name
    self.vms = self._get_all_vms()
    self.hosts = self._get_all_hosts()
```

### 4. **cluster_state.py** - VM Filtering
Updated `_get_all_vms()` to filter VMs by cluster when specified:
```python
def _get_all_vms(self):
    """Get all VMs in the datacenter, optionally filtered by cluster."""
    # ... get all active VMs ...
    
    # If cluster_name is specified, filter VMs to only those in the specified cluster
    if self.cluster_name:
        filtered_vms = []
        for vm in active_vms:
            try:
                if vm.runtime.host and vm.runtime.host.parent and hasattr(vm.runtime.host.parent, 'name'):
                    if vm.runtime.host.parent.name == self.cluster_name:
                        filtered_vms.append(vm)
            except Exception as e:
                logger.debug(f"Could not determine cluster for VM {vm.name}: {e}")
        return filtered_vms
    
    return active_vms
```

### 5. **cluster_state.py** - Host Filtering
Updated `_get_all_hosts()` to filter hosts by cluster when specified:
```python
def _get_all_hosts(self):
    """Get all ESXi hosts in the datacenter."""
    # ... get all connected hosts ...
    
    # If cluster_name is specified, filter by cluster
    if self.cluster_name:
        filtered_hosts = []
        for host in connected_hosts:
            try:
                if host.parent and hasattr(host.parent, 'name') and host.parent.name == self.cluster_name:
                    filtered_hosts.append(host)
            except Exception as e:
                logger.debug(f"Could not determine cluster for host {host.name}: {e}")
        
        if not filtered_hosts:
            logger.warning(f"[ClusterState] No hosts found in cluster '{self.cluster_name}'")
        else:
            logger.info(f"[ClusterState] Filtered to {len(filtered_hosts)} hosts in cluster '{self.cluster_name}'")
        
        return filtered_hosts
    
    return connected_hosts
```

### 6. **README.md** - Documentation
Added cluster usage documentation with examples:

**Main note:**
> You can optionally specify a `--cluster <cluster_name>` to target a specific cluster within vCenter. If omitted, all clusters are processed.

**New section: "Target a Specific Cluster"**
```bash
python fdrs.py --vcenter <vc_ip_or_hostname> --username <user> --cluster <cluster_name>
```

**Example with all options:**
```bash
python fdrs.py --vcenter vc01.fatihsolen.com --username admin --cluster prod-cluster-01 --balance --aggressiveness 4
```

**Iterative mode with specific cluster:**
```bash
python fdrs.py --vcenter <vc_ip_or_hostname> --username <user> --cluster <cluster_name> --balance --iterative
```

---

## Behavior

### Without `--cluster` flag (Default)
- Processes **all clusters** in vCenter
- All VMs and hosts are included in balancing
- Original behavior unchanged

### With `--cluster <cluster_name>`
- Processes **only the specified cluster**
- Only VMs running on hosts in that cluster are included
- Only hosts in that cluster are considered for migrations
- Logging indicates which cluster is being targeted
- Warning logged if cluster not found

---

## Examples

### Balance all clusters
```bash
python fdrs.py --vcenter vc01.fatihsolen.com --username admin --balance
```

### Balance specific cluster
```bash
python fdrs.py --vcenter vc01.fatihsolen.com --username admin --cluster prod-cluster-01 --balance
```

### Anti-affinity rules for specific cluster
```bash
python fdrs.py --vcenter vc01.fatihsolen.com --username admin --cluster prod-cluster-01 --apply-anti-affinity
```

### Iterative mode with cluster targeting
```bash
python fdrs.py --vcenter vc01.fatihsolen.com --username admin --cluster prod-cluster-01 --balance --iterative
```

### With custom aggressiveness on specific cluster
```bash
python fdrs.py --vcenter vc01.fatihsolen.com --username admin --cluster prod-cluster-01 --balance --aggressiveness 4 --metrics cpu,memory
```

---

## Technical Details

### Cluster Detection
The code detects cluster membership by examining:
- For **Hosts**: `host.parent.name` (the cluster is the parent of the host)
- For **VMs**: `vm.runtime.host.parent.name` (cluster is the parent of the host the VM runs on)

### Error Handling
- If cluster name doesn't match any hosts/VMs, warning is logged
- Code gracefully handles missing parent objects
- No breaking changes to existing functionality

### Backward Compatibility
- ✅ Fully backward compatible
- Default behavior (all clusters) unchanged
- Existing scripts and automation not affected

---

## Files Modified

1. **fdrs.py** (3 changes)
   - Added `--cluster` argument to parser
   - Updated ClusterState initialization
   - Added cluster targeting logging

2. **modules/cluster_state.py** (3 changes)
   - Updated `__init__` with cluster_name parameter
   - Updated `_get_all_vms()` with cluster filtering
   - Updated `_get_all_hosts()` with cluster filtering

3. **README.md** (Documentation)
   - Added cluster feature explanation
   - Added "Target a Specific Cluster" section
   - Added cluster examples to iterative section

---

## Testing Recommendations

1. Test with valid cluster name - should process only that cluster
2. Test with invalid cluster name - should log warning and process empty set
3. Test without `--cluster` flag - should process all clusters (original behavior)
4. Test cluster filtering with all modes (--balance, --apply-anti-affinity, --iterative)
5. Verify VM and host counts match expected cluster membership

---

## Future Enhancements

- Add ability to list available clusters with `--list-clusters` flag
- Support multiple clusters with comma-separated list: `--cluster cluster1,cluster2`
- Add cluster-specific configuration overrides
