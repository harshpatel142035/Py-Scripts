"""
Finds load balancers that are effectively idle:
  - ALB/NLB with no target groups, or no healthy targets
  - Classic ELBs with no registered instances
"""

import boto3
from botocore.exceptions import ClientError
from utils import REGION

elbv2 = boto3.client("elbv2", region_name=REGION)
elb = boto3.client("elb", region_name=REGION)


def find_idle_load_balancers():
    stale = []

    # ALB / NLB
    for page in elbv2.get_paginator("describe_load_balancers").paginate():
        for lb in page["LoadBalancers"]:
            lb_arn = lb["LoadBalancerArn"]
            has_healthy_target = False
            has_any_target_group = False
            for tg_page in elbv2.get_paginator("describe_target_groups").paginate(LoadBalancerArn=lb_arn):
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
                if not lb.get("Instances"):
                    stale.append({
                        "LoadBalancerName": lb["LoadBalancerName"],
                        "Type": "classic",
                        "Reason": "No registered instances",
                    })
    except ClientError:
        pass  # No classic ELBs / no permission — safe to skip

    return stale


if __name__ == "__main__":
    import json
    print(json.dumps(find_idle_load_balancers(), indent=2, default=str))
