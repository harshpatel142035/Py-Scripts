"""
Finds security groups (excluding the default group in each VPC) that are
not attached to any network interface.
"""

import boto3
from utils import REGION

ec2 = boto3.client("ec2", region_name=REGION)


def find_unused_security_groups():
    stale = []
    all_sgs = {}
    for page in ec2.get_paginator("describe_security_groups").paginate():
        for sg in page["SecurityGroups"]:
            if sg["GroupName"] != "default":
                all_sgs[sg["GroupId"]] = sg

    used_sg_ids = set()
    for page in ec2.get_paginator("describe_network_interfaces").paginate():
        for eni in page["NetworkInterfaces"]:
            for group in eni.get("Groups", []):
                used_sg_ids.add(group["GroupId"])

    for sg_id, sg in all_sgs.items():
        if sg_id not in used_sg_ids:
            stale.append({
                "GroupId": sg_id,
                "GroupName": sg["GroupName"],
                "VpcId": sg.get("VpcId"),
                "Tags": sg.get("Tags", []),
            })
    return stale


if __name__ == "__main__":
    import json
    print(json.dumps(find_unused_security_groups(), indent=2, default=str))
