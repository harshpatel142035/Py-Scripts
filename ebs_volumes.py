"""
Finds EBS volumes that are not attached to any instance (state = 'available').
"""

import boto3
from utils import REGION, days_ago

ec2 = boto3.client("ec2", region_name=REGION)


def find_unattached_ebs_volumes():
    stale = []
    paginator = ec2.get_paginator("describe_volumes")
    for page in paginator.paginate(Filters=[{"Name": "status", "Values": ["available"]}]):
        for vol in page["Volumes"]:
            stale.append({
                "VolumeId": vol["VolumeId"],
                "SizeGiB": vol["Size"],
                "VolumeType": vol["VolumeType"],
                "CreateTime": vol["CreateTime"].isoformat(),
                "AgeDays": days_ago(vol["CreateTime"]),
                "AvailabilityZone": vol["AvailabilityZone"],
                "Tags": vol.get("Tags", []),
            })
    return stale


if __name__ == "__main__":
    import json
    print(json.dumps(find_unattached_ebs_volumes(), indent=2, default=str))
