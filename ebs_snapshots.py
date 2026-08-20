"""
Finds EBS snapshots older than SNAPSHOT_AGE_DAYS that are not referenced by
any AMI owned by this account (i.e. not needed to launch an image).
"""

import boto3
from utils import REGION, SNAPSHOT_AGE_DAYS, days_ago

ec2 = boto3.client("ec2", region_name=REGION)


def find_old_snapshots():
    stale = []

    # Snapshots still referenced by an AMI shouldn't be flagged
    ami_snapshot_ids = set()
    for page in ec2.get_paginator("describe_images").paginate(Owners=["self"]):
        for image in page["Images"]:
            for bdm in image.get("BlockDeviceMappings", []):
                ebs = bdm.get("Ebs")
                if ebs and "SnapshotId" in ebs:
                    ami_snapshot_ids.add(ebs["SnapshotId"])

    paginator = ec2.get_paginator("describe_snapshots")
    for page in paginator.paginate(OwnerIds=["self"]):
        for snap in page["Snapshots"]:
            if snap["SnapshotId"] in ami_snapshot_ids:
                continue
            age = days_ago(snap["StartTime"])
            if age is not None and age >= SNAPSHOT_AGE_DAYS:
                stale.append({
                    "SnapshotId": snap["SnapshotId"],
                    "VolumeId": snap.get("VolumeId"),
                    "VolumeSizeGiB": snap.get("VolumeSize"),
                    "StartTime": snap["StartTime"].isoformat(),
                    "AgeDays": age,
                    "Tags": snap.get("Tags", []),
                })
    return stale


if __name__ == "__main__":
    import json
    print(json.dumps(find_old_snapshots(), indent=2, default=str))
