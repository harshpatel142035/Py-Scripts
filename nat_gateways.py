"""
Finds available NAT Gateways that are not referenced by any route in any
route table (i.e. nothing is routing traffic through them).
"""

import boto3
from utils import REGION

ec2 = boto3.client("ec2", region_name=REGION)


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


if __name__ == "__main__":
    import json
    print(json.dumps(find_unused_nat_gateways(), indent=2, default=str))
