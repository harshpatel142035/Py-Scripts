"""
Finds self-owned AMIs that are not currently used to launch any existing
EC2 instance.
"""

import boto3
from utils import REGION

ec2 = boto3.client("ec2", region_name=REGION)


def find_unused_amis():
    stale = []
    in_use_ami_ids = set()
    for page in ec2.get_paginator("describe_instances").paginate():
        for reservation in page["Reservations"]:
            for inst in reservation["Instances"]:
                if inst.get("ImageId"):
                    in_use_ami_ids.add(inst["ImageId"])

    for page in ec2.get_paginator("describe_images").paginate(Owners=["self"]):
        for image in page["Images"]:
            if image["ImageId"] not in in_use_ami_ids:
                stale.append({
                    "ImageId": image["ImageId"],
                    "Name": image.get("Name"),
                    "CreationDate": image.get("CreationDate"),
                    "Tags": image.get("Tags", []),
                })
    return stale


if __name__ == "__main__":
    import json
    print(json.dumps(find_unused_amis(), indent=2, default=str))
