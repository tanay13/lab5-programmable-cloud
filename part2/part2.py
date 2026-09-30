#!/usr/bin/env python3

import time

from google.cloud import compute_v1


PROJECT_ID      = "lab-5-510117"

ZONE            = "us-west1-b"  
SOURCE_INSTANCE = "flask-app-instance"   

SNAPSHOT_NAME   = f"base-snapshot-{SOURCE_INSTANCE}"
IMAGE_NAME      = "flask-app-image"      
MACHINE_TYPE    = "f1-micro"             
NUM_INSTANCES   = 3                      


# Create a disk snapshot from the boot disk of the given instance
def create_snapshot(
    project: str, zone: str, instance_name: str, snapshot_name: str
) -> compute_v1.Snapshot:

    instances_client = compute_v1.InstancesClient()

    # Retrieve instance details to identify the boot disk
    instance = instances_client.get(project=project, zone=zone, instance=instance_name)

    boot_disk_name = None
    for disk in instance.disks:
        if disk.boot:
            # The disk source URL ends with the disk resource name
            boot_disk_name = disk.source.split("/")[-1]
            break

    if not boot_disk_name:
        raise ValueError(f"No boot disk found for instance '{instance_name}'")

    print(f"  Boot disk identified: '{boot_disk_name}'")

    # Build the Snapshot resource with the required name and description
    snapshot_resource = compute_v1.Snapshot()
    snapshot_resource.name        = snapshot_name
    snapshot_resource.description = (f"Snapshot of boot disk from '{instance_name}' – Part 2")

    disks_client = compute_v1.DisksClient()
    operation = disks_client.create_snapshot(
        project=project,
        zone=zone,
        disk=boot_disk_name,
        snapshot_resource=snapshot_resource,
    )
    print(f"  Creating snapshot '{snapshot_name}' …", end="", flush=True)
    operation.result()
    print(" done.")

    snapshots_client = compute_v1.SnapshotsClient()
    snapshot = snapshots_client.get(project=project, snapshot=snapshot_name)
    print(f"  Snapshot selfLink: {snapshot.self_link}")
    return snapshot



# Create a custom VM image from the snapshot
def create_image_from_snapshot(
    project: str, snapshot: compute_v1.Snapshot, image_name: str
) -> compute_v1.Image:

    images_client = compute_v1.ImagesClient()

    existing_names = {img.name for img in images_client.list(project=project)}
    if image_name in existing_names:
        print(f"  Image '{image_name}' already exists – skipping creation.")
        return images_client.get(project=project, image=image_name)

    image_resource = compute_v1.Image()
    image_resource.name            = image_name
    image_resource.source_snapshot = snapshot.self_link
    image_resource.description     = (
        "Custom Flask application image created from Part 1 snapshot"
    )

    operation = images_client.insert(project=project, image_resource=image_resource)
    print(f"  Creating image '{image_name}' …", end="", flush=True)
    operation.result()
    print(" done.")

    image = images_client.get(project=project, image=image_name)
    print(f"  Image selfLink: {image.self_link}")
    return image



#  Create a VM instance from the custom image (timed)
def create_instance_from_image(
    project: str,
    zone: str,
    instance_name: str,
    image: compute_v1.Image,
) -> float:

    # Boot disk from the custom image
    disk = compute_v1.AttachedDisk()
    disk.boot        = True
    disk.auto_delete = True   

    disk_params = compute_v1.AttachedDiskInitializeParams()
    disk_params.source_image = image.self_link   
    disk.initialize_params   = disk_params

    # Network interface with external IP
    access_config = compute_v1.AccessConfig()
    access_config.type_ = "ONE_TO_ONE_NAT"
    access_config.name  = "External NAT"

    network_interface = compute_v1.NetworkInterface()
    network_interface.name           = "default"
    network_interface.access_configs = [access_config]

    # Assemble Instance resource
    instance = compute_v1.Instance()
    instance.name         = instance_name
    instance.machine_type = f"zones/{zone}/machineTypes/{MACHINE_TYPE}"
    instance.disks        = [disk]
    instance.network_interfaces = [network_interface]

    # Submit and time the creation
    instances_client = compute_v1.InstancesClient()

    # Start the timer immediately before submitting the API request
    start_time = time.time()

    operation = instances_client.insert(
        project=project,
        zone=zone,
        instance_resource=instance,
    )
    
    operation.result()

    # The zone operation finishing confirms the instance resource was created,
    # but the instance may still be transitioning to RUNNING.  Poll until it
    # reaches RUNNING to get the most accurate provisioning time
    while True:
        inst = instances_client.get(project=project, zone=zone, instance=instance_name)
        if inst.status == "RUNNING":
            break
        time.sleep(2)

    elapsed = time.time() - start_time
    return elapsed


# Write timing results to TIMING.md
def write_timing_md(timings: list[tuple[str, float]], output_path: str = "TIMING.md") -> None:

    avg = sum(t for _, t in timings) / len(timings) if timings else 0.0

    lines = [
        "# VM Provisioning Timing Results\n\n",
        "This file records the time required to create each VM instance from\n",
        "the custom image produced in Part 2 of Lab 5.\n\n",
        "## Method\n\n",
        "Timing starts immediately before `instances.insert()` is submitted\n",
        "and ends when the instance reaches `RUNNING` status.\n\n",
        "## Results\n\n",
        "| Instance | Time (seconds) |\n",
        "|----------|----------------|\n",
    ]

    for name, elapsed in timings:
        lines.append(f"| `{name}` | {elapsed:.2f} |\n")

    lines += [
        "\n",
        f"**Average provisioning time:** {avg:.2f} seconds\n\n",
        "## Observations\n\n",
        "Using a pre-configured custom image eliminates the time normally spent\n",
        "running a startup script (installing packages, cloning the repo, etc.).\n",
        "The times above primarily reflect GCP's disk-copy and hardware-allocation\n",
        "overhead rather than software configuration time.\n",
    ]

    with open(output_path, "w") as f:
        f.writelines(lines)

    print(f"  Timing results written to '{output_path}'.")


def main() -> None:

    print("Part 2: Clone VM and Measure Provisioning Time")
    print("  (using google-cloud-compute Cloud Client Library)")
    print("=" * 60)
    print(f"  Project       : {PROJECT_ID}")
    print(f"  Zone          : {ZONE}")
    print(f"  Source VM     : {SOURCE_INSTANCE}")
    print(f"  Snapshot name : {SNAPSHOT_NAME}")
    print(f"  Image name    : {IMAGE_NAME}")
    print()

    
    print("[1/4] Creating disk snapshot …")
    snapshot = create_snapshot(PROJECT_ID, ZONE, SOURCE_INSTANCE, SNAPSHOT_NAME)
    print()

    
    print("[2/4] Creating custom VM image …")
    image = create_image_from_snapshot(PROJECT_ID, snapshot, IMAGE_NAME)
    print()

    print(f"[3/4] Creating {NUM_INSTANCES} instances from image '{IMAGE_NAME}' …")
    timings: list[tuple[str, float]] = []

    for i in range(1, NUM_INSTANCES + 1):
        instance_name = f"flask-clone-{i}"
        print(f"\n  --- Instance {i}/{NUM_INSTANCES}: '{instance_name}' ---")
        elapsed = create_instance_from_image(PROJECT_ID, ZONE, instance_name, image)
        timings.append((instance_name, elapsed))
        print(f"  '{instance_name}' ready in {elapsed:.2f} seconds.")
    print()


    print("[4/4] Writing TIMING.md …")
    write_timing_md(timings, output_path="TIMING.md")
    print()

    # Console summary table
    print("TIMING SUMMARY")
    print("=" * 60)
    print(f"  {'Instance':<25} {'Seconds':>8}")
    print("  " + "-" * 35)
    for name, elapsed in timings:
        print(f"  {name:<25} {elapsed:>8.2f}")
    avg = sum(e for _, e in timings) / len(timings)
    print("  " + "-" * 35)
    print(f"  {'Average':<25} {avg:>8.2f}")
    print("=" * 60)


if __name__ == "__main__":
    main()