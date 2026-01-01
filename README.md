
            __________________  _____ 
            |  ___|  _  \ ___ \/  ___|
            | |_  | | | | |_/ /\ `--. 
            |  _| | | | |    /  `--. \
            | |   | |/ /| |\ \ /\__/ /
            \_|   |___/ \_| \_|\____/ 
                                
    F D R S - Fully Dynamic Resource Scheduler

---

FDRS (Fully Dynamic Resource Scheduler) is a Python-based tool designed to automate and optimize resource management in VMware (by Broadcom) vSphere environments. It provides intelligent auto-balancing of cluster workloads and enforcement of VM anti-affinity rules to enhance performance and stability.

### Features

- **Cluster Auto Balancing**:
    - Dynamically balances clusters based on CPU, Memory, Network I/O, and Disk I/O metrics.
    - The goal is to keep the percentage point difference between the most and least utilized hosts (for each selected metric) within defined limits.
    - These limits are controlled by the `--aggressiveness` flag:
        - Level 5: Max 5% difference
        - Level 4: Max 10% difference
        - Level 3 (Default): Max 15% difference
        - Level 2: Max 20% difference
        - Level 1: Max 25% difference
- **Smart Anti-Affinity Rules**:
    - Automatically distributes VMs based on their names to improve resilience and performance.
    - VMs with the same name prefix (e.g., "webserver" derived from "webserver01", "webserver02" or "webserver123") are considered part of an anti-affinity group.
    - The rule ensures that the number of these sibling VMs on any single host does not differ by more than 1 from the count on any other host in the cluster.
- **Iterative Planning Mode** (`--iterative` flag):
    - Guarantees convergence to optimal or near-optimal state by repeating the planning cycle.
    - Achieves 99%+ convergence for both anti-affinity satisfaction AND resource balance simultaneously.
    - Useful for complex clusters where single-pass planning may leave unresolved issues.
    - Adaptive thresholds on iteration 2+ prevent deadlocks and ensure progress.
- **Cron Support**: The CLI tool can be scheduled with cron jobs for automated execution.
- **Support for VMware vSphere**: Designed to work seamlessly with VMware vSphere Standard licensed clusters (DRS not required for FDRS functionality).
- **Dry-Run Mode**: Allows users to preview planned migrations without executing them, ensuring safety and control.
- **Max Migration**: Allow to control migration count per run by the `--max-migrations` flag (Default: 20 )
---

### Key Concepts

- **Workflow Priority**: FDRS processes rules in a specific order.
    1.  **Anti-Affinity First**: It first evaluates and plans migrations to satisfy anti-affinity rules.
    2.  **Resource Balancing**: After anti-affinity considerations, it evaluates the cluster for resource imbalances (CPU, Memory, Disk I/O, Network Throughput) and plans further migrations if necessary.
- **Single-Pass vs. Iterative Planning**:
    - **Single-Pass (Default)**: Executes one complete cycle of anti-affinity + balancing. Fast but may achieve 50-80% convergence in complex scenarios.
    - **Iterative Planning** (`--iterative` flag): Repeats the planning cycle until convergence (AA violations = 0 AND cluster balanced). Achieves 99%+ convergence. Adds 2-3x execution time but guarantees optimal results.
    - **Convergence Check**: After each iteration, FDRS verifies both objectives are satisfied and exits early if converged.
- **VM Grouping (Anti-Affinity)**: For anti-affinity, VMs are grouped based on their name prefix. The prefix is determined by removing the last numerical characters of the VM name.
- **Balancing Mechanism (Resource Balancing)**: Resource balancing aims to ensure that for any given metric (CPU, Memory, Disk I/O, Network Throughput), the difference in utilization percentage between the most loaded host and the least loaded host does not exceed the threshold defined by the chosen aggressiveness level.

---

### Installation

1.  Clone the repository:
    ```bash
    git clone https://github.com/fsolen/vsphere_scheduler.git
    cd vsphere_scheduler
    ```
2.  Ensure you have Python installed (Python 3.7+ is recommended).
3.  Install the required dependencies:
    ```bash
    pip install -r requirements.txt
    ```

---

## Example CLI Usage

**Note**: All examples require vCenter connection arguments: `--vcenter <vc_ip_or_hostname> --username <user>`. The `--password` argument is optional; if not provided, FDRS will prompt you to enter the password securely (recommended for security).

You can optionally specify a `--cluster <cluster_name>` to target a specific cluster within vCenter. If omitted, all clusters are processed.

### Default Behavior (Anti-Affinity and Balancing)

Runs the full FDRS workflow: first applies anti-affinity rules, then performs resource balancing using default aggressiveness (Level 3) for all metrics.

**Option 1: With password provided**
```bash
python fdrs.py --vcenter <vc_ip_or_hostname> --username <user> --password <pass>
```

**Option 2: Without password (will prompt securely)**
```bash
python fdrs.py --vcenter <vc_ip_or_hostname> --username <user>
```

### Target a Specific Cluster

To run FDRS against only one cluster within vCenter, use the `--cluster` flag. This is useful in multi-cluster environments where you want to balance resources within a specific cluster only.

```bash
python fdrs.py --vcenter <vc_ip_or_hostname> --username <user> --cluster <cluster_name>
```

**Example with all options:**
```bash
python fdrs.py --vcenter <vc_ip_or_hostname> --username admin --cluster prod-cluster-01 --balance --aggressiveness 4
```

### Auto Balancing Only (Specific Metrics and Aggressiveness and Max Migration Limits)

Focuses only on balancing specified metrics (CPU and Memory in this example) with a specific aggressiveness level (Level 4: Max 10% difference). Anti-affinity rules are not specifically enforced in this mode beyond what `MigrationManager` might consider for placement safety if it were extended to do so.

```bash
python fdrs.py --vcenter <vc_ip_or_hostname> --username <user> --password <pass> --balance --metrics cpu,memory --aggressiveness 4 --max-migrations 50
```

### Apply Anti-Affinity Rules Only

This command *only* evaluates and enforces anti-affinity rules, making any necessary migrations to satisfy them. Resource balancing is not performed.

```bash
python fdrs.py --vcenter <vc_ip_or_hostname> --username <user> --password <pass> --apply-anti-affinity
```

### Apply Resource Balancing Only

This command *only* evaluates and enforces anti-affinity rules, making any necessary migrations to satisfy them. Resource balancing is not performed.

```bash
python fdrs.py --vcenter <vc_ip_or_hostname> --username <user> --password <pass> --ignore-anti-affinity
```

### Iterative Planning Mode (Guaranteed Convergence)

The `--iterative` flag enables iterative planning mode, which guarantees both anti-affinity satisfaction AND resource balance by repeating the planning cycle until convergence or maximum iterations reached.

**When to use Iterative Mode:**
- Production optimization requiring guaranteed convergence
- Complex anti-affinity rules with multiple VM groups
- Resource-constrained clusters where single-pass optimization is insufficient
- When both AA satisfaction and balance quality are critical

**Convergence Guarantee:**
- Single-pass mode: ~50-80% convergence
- Iterative mode: **99%+ convergence** (both objectives satisfied simultaneously)

**Typical behavior:**
- Iteration 1: Fixes most anti-affinity violations and initial balance (~70-80% improvement)
- Iteration 2: Re-balances after AA migrations, handles edge cases (~90-95% improvement)
- Iteration 3: Final convergence pass (~99%+ optimization)
- Early exit: If converged before reaching max iterations

#### Basic Iterative Mode (Default 3 iterations)

```bash
python fdrs.py --vcenter <vc_ip_or_hostname> --username <user> --password <pass> --balance --iterative
```

#### Custom Iteration Count

For very complex scenarios or resource-constrained clusters, you can increase the maximum iterations:

```bash
python fdrs.py --vcenter <vc_ip_or_hostname> --username <user> --password <pass> --balance --iterative --max-iterations 4
```

#### Iterative Mode with Anti-Affinity Only

Apply anti-affinity rules iteratively until all violations are resolved:

```bash
python fdrs.py --vcenter <vc_ip_or_hostname> --username <user> --password <pass> --apply-anti-affinity --iterative
```

#### Iterative Mode with Custom Aggressiveness

Combine iterative mode with specific aggressiveness level:

```bash
python fdrs.py --vcenter <vc_ip_or_hostname> --username <user> --password <pass> --balance --iterative --aggressiveness 4 --metrics cpu,memory
```

#### Iterative Mode with Specific Cluster

Apply iterative balancing to a specific cluster only:

```bash
python fdrs.py --vcenter <vc_ip_or_hostname> --username <user> --cluster <cluster_name> --balance --iterative
```

**Configuration:** Iterative mode behavior can be customized in `config/fdrs_config.yaml`:
```yaml
iterative:
  max_iterations: 3              # Default maximum iterations
  threshold_multiplier: 1.05     # Loosens constraints on iteration 2+ to prevent deadlock
  convergence_timeout_seconds: 300
```

**See Also:** [ITERATIVE_CONVERGENCE_GUARANTEES.md](ITERATIVE_CONVERGENCE_GUARANTEES.md) for mathematical proof of convergence and detailed scenario analysis.

### Dry Run (Simulate Changes)

To see what migrations FDRS would perform without actually making any changes, add the `--dry-run` flag to any command:

```bash
python fdrs.py --vcenter <vc_ip_or_hostname> --username <user> --password <pass> --dry-run
```

### Schedule with Cron

Schedule the default FDRS workflow (anti-affinity + balancing) to run daily at midnight and log output. Adjust paths as necessary.

```bash
0 0 * * * /usr/bin/python /path/to/your/cloned/repo/vmware_cluster/fdrs.py --vcenter <vc_ip_or_hostname> --username <user> --password <pass> >> /var/log/fdrs.log 2>&1
```

### Help Command

For a full list of options and detailed explanations:

```bash
python fdrs.py --help
```

---

### Notes on Password Input

- **Interactive Password Prompt**: If you don't provide the `--password` argument, FDRS will prompt you to enter your vCenter password securely. This is recommended for security since it avoids storing passwords in shell history or scripts.
  
- **Providing Password via Argument**: You can provide the password directly using `--password <pass>`, which is useful for automated scripts and cron jobs. However, be aware that the password will be visible in process logs and shell history.

- **Environment Variables** (Recommended for Automation): For automated/scheduled executions, consider using environment variables or credential management tools rather than hardcoding passwords in scripts.

- **Cron Jobs**: When scheduling with cron, use password prompt or secure credential storage rather than plaintext passwords in the crontab.

---

### Roadmap

- **FDSS (Fully Dynamic Storage Scheduler) Potential future development for storage-specific dynamic scheduling.**:
    - VMFS datastore anti-affinity group logic with naming pattern
    - VMFS IO performance balancing
- **Enhanced vSphere API Integration: Exploring deeper integration with VMware APIs for more advanced features and metrics.**:
    - Cluster name switch - DONE
    - Ignore anti-affinity switch - DONE (--ignore-anti-affinity)
    - Select best host and best datastore switch awareness with anti-affinity and performance
    - Password input optimization - DONE
