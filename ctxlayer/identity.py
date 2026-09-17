"""Provider identity helpers; cloud resources are identified by ARN."""

from __future__ import annotations

from dataclasses import dataclass
from typing import Optional


@dataclass(frozen=True)
class Arn:
    text: str
    partition: str
    service: str
    region: str
    account_id: str
    resource_type: Optional[str]
    resource_id: str


def parse_arn(text: Optional[str]) -> Optional[Arn]:
    if not isinstance(text, str):
        return None
    parts = text.split(":", 5)
    if len(parts) != 6 or parts[0] != "arn":
        return None
    _, partition, service, region, account_id, resource = parts
    if "/" in resource:
        resource_type, resource_id = resource.split("/", 1)
    elif ":" in resource:
        resource_type, resource_id = resource.split(":", 1)
    else:
        resource_type, resource_id = None, resource
    return Arn(text, partition, service, region, account_id, resource_type, resource_id)


def ec2_instance_arn(account_id: str, region: str, instance_id: str) -> str:
    return f"arn:aws:ec2:{region}:{account_id}:instance/{instance_id}"


# Terraform resource type <-> model entity kind <-> ARN service.
TF_TYPE_TO_KIND = {"aws_instance": "ec2_instance", "aws_db_instance": "db_instance"}
KIND_TO_TF_TYPE = {kind: tf_type for tf_type, kind in TF_TYPE_TO_KIND.items()}
ARN_SERVICE_TO_KIND = {"ec2": "ec2_instance", "rds": "db_instance"}
