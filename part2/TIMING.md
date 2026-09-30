# Part 2 – VM Provisioning Timing Results

This file records the time required to create each VM instance from
the custom image produced in Part 2 of Lab 5.

## Method

Timing starts immediately before `instances.insert()` is submitted
and ends when the instance reaches `RUNNING` status.

## Results

| Instance | Time (seconds) |
|----------|----------------|
| `flask-clone-1` | 39.57 |
| `flask-clone-2` | 56.51 |
| `flask-clone-3` | 14.58 |

**Average provisioning time:** 36.89 seconds

## Observations

Using a pre-configured custom image eliminates the time normally spent
running a startup script (installing packages, cloning the repo, etc.).
The times above primarily reflect GCP's disk-copy and hardware-allocation
overhead rather than software configuration time.
