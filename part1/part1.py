#!/usr/bin/env python3

import time

from google.cloud import compute_v1

PROJECT_ID     = "lab-5-510117"

ZONE           = "us-west1-b"            
INSTANCE_NAME  = "flask-app-instance"    
MACHINE_TYPE   = "f1-micro"              # Machine type ('f1-micro' for free tier)
IMAGE_FAMILY   = "ubuntu-2204-lts"       # Ubuntu 22.04 LTS image family
IMAGE_PROJECT  = "ubuntu-os-cloud"       
FIREWALL_NAME  = "allow-5000"            
FLASK_PORT     = 5000                    


# ---------------------------------------------------------------------------
# Startup script – executed as root inside the VM on first boot
# ---------------------------------------------------------------------------

STARTUP_SCRIPT = """#!/bin/bash
set -e   # Exit immediately if a command exits with a non-zero status

echo "=== Startup: installing dependencies ==="
apt-get update -y
apt-get install -y python3 python3-pip git

echo "=== Startup: cloning Flask tutorial ==="
mkdir -p /srv/flask-app
cd /srv/flask-app
git clone https://github.com/cu-csci-4253-datacenter/flask-tutorial .

echo "=== Startup: installing flaskr ==="
python3 setup.py install
pip3 install -e .

echo "=== Startup: initialising database and starting Flask ==="
export FLASK_APP=flaskr
flask init-db

# nohup keeps Flask alive after this script exits.
# Output is redirected to a log file for debugging.
nohup flask run -h 0.0.0.0 > /var/log/flask.log 2>&1 &

echo "=== Startup: Flask running on port 5000 ==="
"""


# Create a new Compute Engine VM instance using the Cloud client library
def create_instance(project: str, zone: str, name: str, startup_script: str) -> str:
    images_client = compute_v1.ImagesClient()
    image = images_client.get_from_family(project=IMAGE_PROJECT, family=IMAGE_FAMILY)
    print(f"  Using boot image: {image.name}")

    # initializeParams tells GCP to create a new persistent disk from the image
    disk = compute_v1.AttachedDisk()
    disk.boot = True          
    disk.auto_delete = True   # Delete the disk when the instance is deleted

    disk_params = compute_v1.AttachedDiskInitializeParams()
    disk_params.source_image = image.self_link  # Boot from the Ubuntu image
    disk_params.disk_size_gb = 20               # Extra headroom for apt packages
    disk.initialize_params = disk_params

    # ONE_TO_ONE_NAT for ensuring Flask application is reachable from the public internet
    access_config = compute_v1.AccessConfig()
    access_config.type_ = "ONE_TO_ONE_NAT"   # Assigns a public IP
    access_config.name  = "External NAT"

    network_interface = compute_v1.NetworkInterface()
    network_interface.name = "default"         # Use the 'default' VPC network
    network_interface.access_configs = [access_config]

    
    # The startup-script metadata key is special: GCP's guest agent runs it
    # automatically when the VM first boots
    metadata_item = compute_v1.Items()
    metadata_item.key   = "startup-script"
    metadata_item.value = startup_script

    metadata = compute_v1.Metadata()
    metadata.items = [metadata_item]

    # Assemble the Instance resource 
    instance = compute_v1.Instance()
    instance.name         = name
    instance.machine_type = f"zones/{zone}/machineTypes/{MACHINE_TYPE}"
    instance.disks        = [disk]
    instance.network_interfaces = [network_interface]
    instance.metadata     = metadata

    # Submit the insert request
    instances_client = compute_v1.InstancesClient()
    operation = instances_client.insert(
        project=project,
        zone=zone,
        instance_resource=instance,
    )

    print(f"  Waiting for instance '{name}' to be created …", end="", flush=True)
    operation.result()   # Blocks until DONE; raises on error
    print(" done.")

    return f"https://compute.googleapis.com/compute/v1/projects/{project}/zones/{zone}/instances/{name}"


# Create the 'allow-5000' firewall rule if it does not already exist
def ensure_firewall_rule(project: str) -> None:

    firewalls_client = compute_v1.FirewallsClient()

    # List existing rules and check whether 'allow-5000' is present
    existing_names = {fw.name for fw in firewalls_client.list(project=project)}
    if FIREWALL_NAME in existing_names:
        print(f"  Firewall rule '{FIREWALL_NAME}' already exists – skipping.")
        return

    print(f"  Creating firewall rule '{FIREWALL_NAME}'..")

    allowed = compute_v1.Allowed()
    allowed.I_p_protocol = "tcp"          
    allowed.ports = [str(FLASK_PORT)]     

    firewall = compute_v1.Firewall()
    firewall.name          = FIREWALL_NAME
    firewall.allowed       = [allowed]
    firewall.source_ranges = ["0.0.0.0/0"]   # Allow from anywhere
    firewall.target_tags   = [FIREWALL_NAME] # Only applies to VMs tagged 'allow-5000'
    firewall.description   = "Allow TCP port 5000 for the Flask tutorial application"


    operation = firewalls_client.insert(project=project, firewall_resource=firewall)
    print("  Waiting for firewall rule creation …", end="", flush=True)
    operation.result()
    print(" done.")
    print(f"  Firewall rule '{FIREWALL_NAME}' created successfully.")



# Apply a network tag to a VM instance using the setTags API

def apply_network_tag(project: str, zone: str, instance_name: str, tag: str) -> None:

    instances_client = compute_v1.InstancesClient()

    # Fetch current instance state to get the existing tags + fingerprint
    instance = instances_client.get(project=project, zone=zone, instance=instance_name)

    # Preserve any tags already on the instance
    current_tags = list(instance.tags.items) if instance.tags.items else []
    if tag not in current_tags:
        current_tags.append(tag)

    # Build the Tags resource with the current fingerprint
    tags_resource = compute_v1.Tags()
    tags_resource.items       = current_tags
    tags_resource.fingerprint = instance.tags.fingerprint  # Required for optimistic lock

    operation = instances_client.set_tags(
        project=project,
        zone=zone,
        instance=instance_name,
        tags_resource=tags_resource,
    )
    print(f"  Waiting for tag '{tag}' to be applied …", end="", flush=True)
    operation.result()
    print(" done.")
    print(f"  Network tag '{tag}' applied to instance '{instance_name}'.")


# Return the public IP address of the given instance
def get_external_ip(project: str, zone: str, instance_name: str) -> str | None:

    instances_client = compute_v1.InstancesClient()
    instance = instances_client.get(project=project, zone=zone, instance=instance_name)

    try:
        return instance.network_interfaces[0].access_configs[0].nat_i_p
    except (IndexError, AttributeError):
        return None


def main() -> None:
    """
    Orchestrate the full Part 1 sequence:
      1. Create the VM instance with the Flask startup script.
      2. Create the 'allow-5000' firewall rule (if it doesn't exist).
      3. Apply the 'allow-5000' network tag to the VM.
      4. Retrieve and display the external IP address and access URL.
    """

    print("Part 1: Create VM and Deploy Flask Application")
    print("  (using google-cloud-compute Cloud Client Library)")
    print("=" * 60)
    print(f"  Project : {PROJECT_ID}")
    print(f"  Zone    : {ZONE}")
    print(f"  Instance: {INSTANCE_NAME}")
    print()

    print("[1/4] Creating VM instance..")
    create_instance(PROJECT_ID, ZONE, INSTANCE_NAME, STARTUP_SCRIPT)
    print(f"  Instance '{INSTANCE_NAME}' created.\n")

    print("[2/4] Checking firewall rule..")
    ensure_firewall_rule(PROJECT_ID)
    print()

    print("[3/4] Applying network tag..")
    apply_network_tag(PROJECT_ID, ZONE, INSTANCE_NAME, FIREWALL_NAME)
    print()

    print("[4/4] Retrieving external IP address..")
    external_ip = get_external_ip(PROJECT_ID, ZONE, INSTANCE_NAME)

    print()
    print("=" * 60)
    if external_ip:
        print("SUCCESS: The Flask application is being installed.")
        print("The startup script may take a few minutes to finish")
        print()
        print("Once ready, visit the application at:")
        print()
        print(f"  http://{external_ip}:{FLASK_PORT}")
    else:
        print("WARNING: External IP not yet available.")
        print("Run the following to find it once the VM starts:")
        print(f"  gcloud compute instances describe {INSTANCE_NAME} \\")
        print(f"    --zone {ZONE} \\")
        print("    --format='get(networkInterfaces[0].accessConfigs[0].natIP)'")
    print("=" * 60)


if __name__ == "__main__":
    main()