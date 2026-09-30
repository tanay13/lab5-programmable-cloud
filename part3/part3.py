#!/usr/bin/env python3


from google.cloud import compute_v1


PROJECT_ID            = "lab-5-510117"  

ZONE                  = "us-central1-a"
VM1_INSTANCE_NAME     = "launcher-vm"   
VM2_INSTANCE_NAME     = "flask-app-from-vm"
MACHINE_TYPE          = "e2-micro"
IMAGE_FAMILY          = "ubuntu-2204-lts"
IMAGE_PROJECT         = "ubuntu-os-cloud"
FIREWALL_NAME         = "allow-5000"
FLASK_PORT            = 5000

SERVICE_ACCOUNT_EMAIL = "275399515402-compute@developer.gserviceaccount.com"


# VM-2 startup script  (shell, runs inside VM-2)
# This is the same Flask installation script used in Part 1
VM2_STARTUP_SCRIPT = """#!/bin/bash
set -e
echo "=== VM-2: Installing Flask application ==="

apt-get update -y
apt-get install -y python3 python3-pip git

mkdir -p /srv/flask-app
cd /srv/flask-app
git clone https://github.com/cu-csci-4253-datacenter/flask-tutorial .

python3 setup.py install
pip3 install -e .

export FLASK_APP=flaskr
flask init-db

# Start Flask in the background so the script can exit cleanly.
nohup flask run -h 0.0.0.0 > /var/log/flask.log 2>&1 &
echo "=== VM-2: Flask started on port 5000 ==="
"""


VM1_LAUNCH_SCRIPT = f"""#!/usr/bin/env python3
\"\"\"
VM-1 Launch Script – executes INSIDE the launcher VM.
Creates VM-2 (the Flask application VM) using service account credentials
provided automatically by the GCP metadata server.
\"\"\"
import sys
import subprocess
import time
import urllib.request

# Install google-cloud-compute inside VM-1 if not present.
# We need this to use the typed Cloud client library on VM-1 as well.
subprocess.check_call(
    [sys.executable, "-m", "pip", "install", "--quiet",
     "google-cloud-compute", "google-auth"],
    stdout=subprocess.DEVNULL,
)

import google.auth
from google.cloud import compute_v1

# ---------------------------------------------------------------------------
# Read metadata from the GCP metadata server
# ---------------------------------------------------------------------------
# The metadata server is a link-local HTTP endpoint (169.254.169.254) that
# GCP provides inside every VM.  It serves instance-level and project-level
# metadata, including the custom items we passed when creating VM-1.
METADATA_BASE = "http://metadata.google.internal/computeMetadata/v1"
HEADERS       = {{"Metadata-Flavor": "Google"}}   # Required header for all metadata requests

def get_metadata(path: str) -> str:
    \"\"\"Fetch a value from the GCP instance metadata server.\"\"\"
    req = urllib.request.Request(f"{{METADATA_BASE}}/{{path}}", headers=HEADERS)
    with urllib.request.urlopen(req) as resp:
        return resp.read().decode("utf-8").strip()

# Retrieve the project ID and the VM-2 startup script from metadata.
# We embedded 'vm2-startup-script' as a metadata item when creating VM-1.
project        = get_metadata("project/project-id")
vm2_script     = get_metadata("instance/attributes/vm2-startup-script")
zone           = "{ZONE}"
vm2_name       = "{VM2_INSTANCE_NAME}"
machine_type   = "{MACHINE_TYPE}"
image_family   = "{IMAGE_FAMILY}"
image_project  = "{IMAGE_PROJECT}"
firewall_tag   = "{FIREWALL_NAME}"
flask_port     = {FLASK_PORT}

print(f"[VM-1] Project: {{project}}")
print(f"[VM-1] Creating VM-2: {{vm2_name}} in {{zone}}")

# ---------------------------------------------------------------------------
# Authenticate using the service account attached to VM-1
# ---------------------------------------------------------------------------
# google.auth.default() on a GCP VM queries the metadata server for an OAuth2
# access token.  No credentials file is needed – the service account was
# attached via the serviceAccounts property when VM-1 was created.
credentials, _ = google.auth.default(
    scopes=["https://www.googleapis.com/auth/cloud-platform"]
)

# ---------------------------------------------------------------------------
# Resolve the latest Ubuntu image (same family as Part 1)
# ---------------------------------------------------------------------------
images_client = compute_v1.ImagesClient(credentials=credentials)
image = images_client.get_from_family(project=image_project, family=image_family)
print(f"[VM-1] Boot image: {{image.name}}")

# ---------------------------------------------------------------------------
# Build and create VM-2
# ---------------------------------------------------------------------------
# Boot disk from the Ubuntu image
disk = compute_v1.AttachedDisk()
disk.boot        = True
disk.auto_delete = True
disk_params = compute_v1.AttachedDiskInitializeParams()
disk_params.source_image = image.self_link
disk.initialize_params   = disk_params

# External IP access config
access_config = compute_v1.AccessConfig()
access_config.type_ = "ONE_TO_ONE_NAT"
access_config.name  = "External NAT"

network_interface = compute_v1.NetworkInterface()
network_interface.name           = "default"
network_interface.access_configs = [access_config]

# Startup script for VM-2 (downloaded from VM-1's metadata above)
metadata_item = compute_v1.Items()
metadata_item.key   = "startup-script"
metadata_item.value = vm2_script
metadata = compute_v1.Metadata()
metadata.items = [metadata_item]

# Apply the allow-5000 network tag so the firewall rule targets VM-2
tags = compute_v1.Tags()
tags.items = [firewall_tag]

# Assemble the Instance resource for VM-2
vm2_instance = compute_v1.Instance()
vm2_instance.name              = vm2_name
vm2_instance.machine_type      = f"zones/{{zone}}/machineTypes/{{machine_type}}"
vm2_instance.disks             = [disk]
vm2_instance.network_interfaces = [network_interface]
vm2_instance.metadata          = metadata
vm2_instance.tags              = tags

# Create VM-2; .result() blocks until the operation is DONE
instances_client = compute_v1.InstancesClient(credentials=credentials)
operation = instances_client.insert(
    project=project, zone=zone, instance_resource=vm2_instance
)
print("[VM-1] Waiting for VM-2 to be created …")
operation.result()
print("[VM-1] VM-2 created successfully!")

# Retrieve and display VM-2's external IP
vm2 = instances_client.get(project=project, zone=zone, instance=vm2_name)
external_ip = vm2.network_interfaces[0].access_configs[0].nat_i_p
print(f"[VM-1] Flask application will be available at: http://{{external_ip}}:{{flask_port}}")
"""


# VM-1 shell startup script 
VM1_STARTUP_SCRIPT = """#!/bin/bash
set -e
echo "=== VM-1: Startup script beginning ==="

apt-get update -y
apt-get install -y python3 python3-pip curl

mkdir -p /srv
cd /srv

# Download the Python launch script from the instance metadata server.
# The Metadata-Flavor header is required for all GCP metadata requests.
echo "=== VM-1: Fetching launch script from metadata server ==="
curl -s "http://metadata.google.internal/computeMetadata/v1/instance/attributes/vm1-launch-code" \\
     -H "Metadata-Flavor: Google" > /srv/vm1-launch-vm2.py

echo "=== VM-1: Running Python launch script ==="
# Redirect all output to a log file so you can debug via SSH if needed:
#   gcloud compute ssh launcher-vm --zone us-west1-b
#   cat /var/log/vm1-launch.log
python3 /srv/vm1-launch-vm2.py 2>&1 | tee /var/log/vm1-launch.log

echo "=== VM-1: Launch script complete ==="
"""


# Ensure the allow-5000 firewall rule exists 
def ensure_firewall_rule(project: str) -> None:

    firewalls_client = compute_v1.FirewallsClient()
    existing_names = {fw.name for fw in firewalls_client.list(project=project)}

    if FIREWALL_NAME in existing_names:
        print(f"  Firewall rule '{FIREWALL_NAME}' already exists – skipping.")
        return

    print(f"  Creating firewall rule '{FIREWALL_NAME}' …")

    # Describe the traffic to allow
    allowed = compute_v1.Allowed()
    allowed.I_p_protocol = "tcp"
    allowed.ports        = [str(FLASK_PORT)]

    firewall = compute_v1.Firewall()
    firewall.name          = FIREWALL_NAME
    firewall.allowed       = [allowed]
    firewall.source_ranges = ["0.0.0.0/0"]    
    firewall.target_tags   = [FIREWALL_NAME]  
    firewall.description   = "Allow TCP port 5000 for the Flask tutorial application"

    operation = firewalls_client.insert(project=project, firewall_resource=firewall)
    print("  Waiting for firewall rule …", end="", flush=True)
    operation.result()
    print(" done.")
    print(f"  Firewall rule '{FIREWALL_NAME}' created.")


# Create VM-1
def create_vm1(
    project: str,
    zone: str,
    vm1_name: str,
    vm1_startup_script: str,
    vm1_launch_code: str,
    vm2_startup_script: str,
    service_account_email: str,
) -> None:

    images_client = compute_v1.ImagesClient()
    image = images_client.get_from_family(project=IMAGE_PROJECT, family=IMAGE_FAMILY)
    print(f"  Boot image: {image.name}")

    # Boot disk
    disk = compute_v1.AttachedDisk()
    disk.boot        = True
    disk.auto_delete = True
    disk_params = compute_v1.AttachedDiskInitializeParams()
    disk_params.source_image = image.self_link
    disk_params.disk_size_gb = 20
    disk.initialize_params   = disk_params

    # Network interface
    access_config = compute_v1.AccessConfig()
    access_config.type_ = "ONE_TO_ONE_NAT"
    access_config.name  = "External NAT"

    network_interface = compute_v1.NetworkInterface()
    network_interface.name           = "default"
    network_interface.access_configs = [access_config]

    def make_item(key: str, value: str) -> compute_v1.Items:
        """Helper to build a compute_v1.Items metadata key-value pair."""
        item = compute_v1.Items()
        item.key   = key
        item.value = value
        return item

    metadata = compute_v1.Metadata()
    metadata.items = [
        make_item("startup-script",     vm1_startup_script),
        make_item("vm1-launch-code",    vm1_launch_code),
        make_item("vm2-startup-script", vm2_startup_script),
    ]

    sa = compute_v1.ServiceAccount()
    sa.email  = service_account_email
    sa.scopes = ["https://www.googleapis.com/auth/cloud-platform"]

    vm1 = compute_v1.Instance()
    vm1.name              = vm1_name
    vm1.machine_type      = f"zones/{zone}/machineTypes/{MACHINE_TYPE}"
    vm1.disks             = [disk]
    vm1.network_interfaces = [network_interface]
    vm1.metadata          = metadata
    vm1.service_accounts  = [sa]  

    instances_client = compute_v1.InstancesClient()
    operation = instances_client.insert(
        project=project,
        zone=zone,
        instance_resource=vm1,
    )
    print(f"  Waiting for VM-1 '{vm1_name}' creation …", end="", flush=True)
    operation.result()
    print(" done.")


# Main entry point
def main() -> None:

    print("Part 3 – Use a VM to Create Another VM")
    print("  (using google-cloud-compute Cloud Client Library)")
    print("=" * 60)
    print(f"  Project          : {PROJECT_ID}")
    print(f"  Zone             : {ZONE}")
    print(f"  VM-1 (launcher)  : {VM1_INSTANCE_NAME}")
    print(f"  VM-2 (Flask app) : {VM2_INSTANCE_NAME}")
    print(f"  Service account  : {SERVICE_ACCOUNT_EMAIL}")
    print()


    print("[1/2] Checking firewall rule..")
    ensure_firewall_rule(PROJECT_ID)
    print()

    print("[2/2] Creating VM-1 (launcher)..")
    create_vm1(
        project=PROJECT_ID,
        zone=ZONE,
        vm1_name=VM1_INSTANCE_NAME,
        vm1_startup_script=VM1_STARTUP_SCRIPT,
        vm1_launch_code=VM1_LAUNCH_SCRIPT,
        vm2_startup_script=VM2_STARTUP_SCRIPT,
        service_account_email=SERVICE_ACCOUNT_EMAIL,
    )
    print(f"  VM-1 '{VM1_INSTANCE_NAME}' created.\n")

    print("=" * 60)
    print("SUCCESS – VM-1 is running its startup script.")
    print()
    print("VM-1 will automatically:")
    print("  1. Install Python 3 + google-cloud-compute.")
    print("  2. Download 'vm1-launch-code' from the metadata server.")
    print("  3. Run it to create VM-2 (Flask application).")
    print()
    print("Monitor VM-1 startup via SSH:")
    print(f"  gcloud compute ssh {VM1_INSTANCE_NAME} --zone {ZONE}")
    print("  sudo journalctl -f -u google-startup-scripts.service")
    print("  # or check: cat /var/log/vm1-launch.log")
    print()
    print("Once VM-2 exists, get its external IP with:")
    print(f"  gcloud compute instances describe {VM2_INSTANCE_NAME} \\")
    print(f"    --zone {ZONE} \\")
    print("    --format='get(networkInterfaces[0].accessConfigs[0].natIP)'")
    print()
    print("Then open in your browser:")
    print(f"  http://<VM-2 IP>:{FLASK_PORT}")
    print("=" * 60)


if __name__ == "__main__":
    main()