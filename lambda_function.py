"""
Main Lambda entry point. Imports each per-resource check module and
aggregates their results into a single JSON report.

Handler: lambda_function.lambda_handler

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

from utils import REGION
from ebs_volumes import find_unattached_ebs_volumes
from ebs_snapshots import find_old_snapshots
from elastic_ips import find_unassociated_eips
from ec2_instances import find_stale_stopped_instances
from amis import find_unused_amis
from security_groups import find_unused_security_groups
from load_balancers import find_idle_load_balancers
from nat_gateways import find_unused_nat_gateways

logger = logging.getLogger()
logger.setLevel(logging.INFO)

SNS_TOPIC_ARN = os.environ.get("SNS_TOPIC_ARN")
sns = boto3.client("sns", region_name=REGION)

CHECKS = {
    "UnattachedEBSVolumes": find_unattached_ebs_volumes,
    "OldSnapshots": find_old_snapshots,
    "UnassociatedElasticIPs": find_unassociated_eips,
    "StaleStoppedInstances": find_stale_stopped_instances,
    "UnusedAMIs": find_unused_amis,
    "UnusedSecurityGroups": find_unused_security_groups,
    "IdleLoadBalancers": find_idle_load_balancers,
    "UnusedNATGateways": find_unused_nat_gateways,
}


def lambda_handler(event, context):
    logger.info("Starting stale resource scan in region %s", REGION)

    results = {}
    for name, fn in CHECKS.items():
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
