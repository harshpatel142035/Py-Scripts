"""
Finds Elastic IPs that are allocated but not associated with any
instance or network interface.
"""

import boto3
from utils import REGION

ec2 = boto3.client("ec2", region_name=REGION)


def find_unassociated_eips():
    stale = []
    addresses = ec2.describe_addresses().get("Addresses", [])
    for addr in addresses:
        if "AssociationId" not in addr and "InstanceId" not in addr:
            stale.append({
                "AllocationId": addr.get("AllocationId"),
                "PublicIp": addr.get("PublicIp"),
                "Domain": addr.get("Domain"),
                "Tags": addr.get("Tags", []),
            })
    return stale


if __name__ == "__main__":
    import json
    print(json.dumps(find_unassociated_eips(), indent=2, default=str))
