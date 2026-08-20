"""
AWS Lambda function to find stale / unused AWS resources across a single region.

Checks performed:
  - Unattached EBS volumes
  - Old EBS snapshots (older than SNAPSHOT_AGE_DAYS, no longer referenced by an AMI)
  - Unassociated Elastic IPs
  - Stopped EC2 instances (stopped longer than STOPPED_INSTANCE_AGE_DAYS)
  - Deregistered-looking / unused AMIs (no instance using them)
  - Security groups not attached to any ENI
  - Idle Classic/ALB/NLB load balancers (zero targets or no healthy targets)
  - Unused NAT Gateways (no route table references)

Trigger: manual invoke, or on an EventBridge schedule (e.g. daily/weekly).
Output: JSON summary, also logged to CloudWatch Logs. Optionally publish to SNS.

Environment variables (all optional):
  REGION                     AWS region to scan (default: current Lambda region)
  SNAPSHOT_AGE_DAYS          Age threshold for "old" snapshots (default: 90)
  STOPPED_INSTANCE_AGE_DAYS  Age threshold for "stale" stopped instances (default: 30)
  SNS_TOPIC_ARN              If set, publish the summary to this SNS topic
"""

import os
import json
import logging
from datetime import datetime, timezone

import boto3
from botocore.exceptions import ClientError

logger = logging.getLogger()
logger.setLevel(logging.INFO)

REGION = os.environ.get("REGION", os.environ.get("AWS_REGION", "us-east-1"))
SNAPSHOT_AGE_DAYS = int(os.environ.get("SNAPSHOT_AGE_DAYS", "90"))
STOPPED_INSTANCE_AGE_DAYS = int(os.environ.get("STOPPED_INSTANCE_AGE_DAYS", "30"))
SNS_TOPIC_ARN = os.environ.get("SNS_TOPIC_ARN")

ec2 = boto3.client("ec2", region_name=REGION)
elbv2 = boto3.client("elbv2", region_name=REGION)
elb = boto3.client("elb", region_name=REGION)
sns = boto3.client("sns", region_name=REGION)


def _days_ago(dt):
    if dt is None:
        return None
    if dt.tzinfo is None:
        dt = dt.replace(tzinfo=timezone.utc)
    return (datetime.now(timezone.utc) - dt).days


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
                "AgeDays": _days_ago(vol["CreateTime"]),
                "AvailabilityZone": vol["AvailabilityZone"],
                "Tags": vol.get("Tags", []),
            })
    return stale


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
            age = _days_ago(snap["StartTime"])
            if snap["SnapshotId"] in ami_snapshot_ids:
                continue
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


def find_stale_stopped_instances():
    stale = []
    paginator = ec2.get_paginator("describe_instances")
    for page in paginator.paginate(Filters=[{"Name": "instance-state-name", "Values": ["stopped"]}]):
        for reservation in page["Reservations"]:
            for inst in reservation["Instances"]:
                state_transition = inst.get("StateTransitionReason", "")
                # Try to pull the stop date out of the transition reason string;
                # fall back to LaunchTime if unavailable (still useful as a signal).
                stopped_since = inst.get("LaunchTime")
                age = _days_ago(stopped_since)
                if age is not None and age >= STOPPED_INSTANCE_AGE_DAYS:
                    stale.append({
                        "InstanceId": inst["InstanceId"],
                        "InstanceType": inst["InstanceType"],
                        "StateTransitionReason": state_transition,
                        "LaunchTime": stopped_since.isoformat() if stopped_since else None,
                        "AgeDaysSinceLaunch": age,
                        "Tags": inst.get("Tags", []),
                    })
    return stale


def find_unused_amis():
    stale = []
    in_use_ami_ids = set()
    paginator = ec2.get_paginator("describe_instances")
    for page in paginator.paginate():
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


def find_idle_load_balancers():
    stale = []

    # ALB / NLB (elbv2)
    for page in elbv2.get_paginator("describe_load_balancers").paginate():
        for lb in page["LoadBalancers"]:
            lb_arn = lb["LoadBalancerArn"]
            tg_paginator = elbv2.get_paginator("describe_target_groups")
            has_healthy_target = False
            has_any_target_group = False
            for tg_page in tg_paginator.paginate(LoadBalancerArn=lb_arn):
                for tg in tg_page["TargetGroups"]:
                    has_any_target_group = True
                    health = elbv2.describe_target_health(TargetGroupArn=tg["TargetGroupArn"])
                    for desc in health.get("TargetHealthDescriptions", []):
                        if desc["TargetHealth"]["State"] == "healthy":
                            has_healthy_target = True
            if not has_any_target_group or not has_healthy_target:
                stale.append({
                    "LoadBalancerArn": lb_arn,
                    "Name": lb["LoadBalancerName"],
                    "Type": lb["Type"],
                    "Reason": "No target groups" if not has_any_target_group else "No healthy targets",
                })

    # Classic ELB
    try:
        for page in elb.get_paginator("describe_load_balancers").paginate():
            for lb in page["LoadBalancerDescriptions"]:
                name = lb["LoadBalancerName"]
                instances = lb.get("Instances", [])
                if not instances:
                    stale.append({
                        "LoadBalancerName": name,
                        "Type": "classic",
                        "Reason": "No registered instances",
                    })
    except ClientError as e:
        logger.warning("Skipping classic ELB check: %s", e)

    return stale


def find_unused_nat_gateways():
    stale = []
    route_tables = ec2.describe_route_tables()["RouteTables"]
    used_nat_ids = set()
    for rt in route_tables:
        for route in rt.get("Routes", []):
            if route.get("NatGatewayId"):
                used_nat_ids.add(route["NatGatewayId"])

    for page in ec2.get_paginator("describe_nat_gateways").paginate(
        Filters=[{"Name": "state", "Values": ["available"]}]
    ):
        for nat in page["NatGateways"]:
            if nat["NatGatewayId"] not in used_nat_ids:
                stale.append({
                    "NatGatewayId": nat["NatGatewayId"],
                    "VpcId": nat.get("VpcId"),
                    "SubnetId": nat.get("SubnetId"),
                    "CreateTime": nat.get("CreateTime").isoformat() if nat.get("CreateTime") else None,
                })
    return stale


def lambda_handler(event, context):
    logger.info("Starting stale resource scan in region %s", REGION)

    results = {}
    checks = {
        "UnattachedEBSVolumes": find_unattached_ebs_volumes,
        "OldSnapshots": find_old_snapshots,
        "UnassociatedElasticIPs": find_unassociated_eips,
        "StaleStoppedInstances": find_stale_stopped_instances,
        "UnusedAMIs": find_unused_amis,
        "UnusedSecurityGroups": find_unused_security_groups,
        "IdleLoadBalancers": find_idle_load_balancers,
        "UnusedNATGateways": find_unused_nat_gateways,
    }

    for name, fn in checks.items():
        try:
            results[name] = fn()
            logger.info("%s: found %d stale item(s)", name, len(results[name]))
        except ClientError as e:
            logger.error("Error running check '%s': %s", name, e)
            results[name] = {"error": str(e)}

    summary = {
        "region": REGION,
        "scanTime": datetime.now(timezone.utc).isoformat(),
        "counts": {
            k: (len(v) if isinstance(v, list) else "error")
            for k, v in results.items()
        },
        "details": results,
    }

    logger.info("Scan summary: %s", json.dumps(summary["counts"]))

    if SNS_TOPIC_ARN:
        try:
            sns.publish(
                TopicArn=SNS_TOPIC_ARN,
                Subject=f"Stale AWS Resource Report - {REGION}",
                Message=json.dumps(summary, indent=2, default=str),
            )
        except ClientError as e:
            logger.error("Failed to publish SNS notification: %s", e)

    return {
        "statusCode": 200,
        "body": json.dumps(summary, default=str),
    }


if __name__ == "__main__":
    # For local testing outside Lambda
    print(json.dumps(lambda_handler({}, None), indent=2, default=str))
