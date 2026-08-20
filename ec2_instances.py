"""
Finds EC2 instances that have been in the 'stopped' state and whose
LaunchTime is older than STOPPED_INSTANCE_AGE_DAYS.

Note: EC2 doesn't expose a clean "time stopped" field via the API, so
LaunchTime is used as a practical proxy. For stricter accuracy, pair this
with CloudTrail 'StopInstances' events.
"""

import boto3
from utils import REGION, STOPPED_INSTANCE_AGE_DAYS, days_ago

ec2 = boto3.client("ec2", region_name=REGION)


def find_stale_stopped_instances():
    stale = []
    paginator = ec2.get_paginator("describe_instances")
    for page in paginator.paginate(Filters=[{"Name": "instance-state-name", "Values": ["stopped"]}]):
        for reservation in page["Reservations"]:
            for inst in reservation["Instances"]:
                launch_time = inst.get("LaunchTime")
                age = days_ago(launch_time)
                if age is not None and age >= STOPPED_INSTANCE_AGE_DAYS:
                    stale.append({
                        "InstanceId": inst["InstanceId"],
                        "InstanceType": inst["InstanceType"],
                        "StateTransitionReason": inst.get("StateTransitionReason", ""),
                        "LaunchTime": launch_time.isoformat() if launch_time else None,
                        "AgeDaysSinceLaunch": age,
                        "Tags": inst.get("Tags", []),
                    })
    return stale


if __name__ == "__main__":
    import json
    print(json.dumps(find_stale_stopped_instances(), indent=2, default=str))
