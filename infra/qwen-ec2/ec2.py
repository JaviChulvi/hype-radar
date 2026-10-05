"""Small AWS CLI launcher and lifecycle commands for one Qwen test instance."""

import argparse
import ipaddress
import json
import os
import subprocess
from pathlib import Path
from urllib.request import urlopen
from uuid import uuid4

ROOT = Path(__file__).resolve().parent
STATE = ROOT / ".state"
RECORD = STATE / "ec2.json"


def aws(state, *arguments):
    command = [
        "aws",
        "--profile",
        state["profile"],
        "--region",
        state["region"],
        "--no-cli-pager",
        *arguments,
        "--output",
        "json",
    ]
    result = subprocess.run(command, check=True, text=True, capture_output=True)
    return json.loads(result.stdout) if result.stdout.strip() else {}


def save(state):
    STATE.mkdir(mode=0o700, exist_ok=True)
    RECORD.write_text(json.dumps(state, indent=2) + "\n")
    RECORD.chmod(0o600)


def instance(state):
    data = aws(state, "ec2", "describe-instances", "--instance-ids", state["instance_id"])
    return data["Reservations"][0]["Instances"][0]


def ssh(state, *arguments):
    host = instance(state)["PublicIpAddress"]
    return [
        "ssh",
        "-i",
        str(STATE / "ssh_key"),
        "-o",
        "IdentitiesOnly=yes",
        "-o",
        "StrictHostKeyChecking=accept-new",
        "-o",
        f"UserKnownHostsFile={STATE / 'known_hosts'}",
        "-o",
        "ConnectTimeout=10",
        *arguments,
        f"ubuntu@{host}",
    ]


def launch(profile, region):
    if RECORD.exists():
        state = json.loads(RECORD.read_text())
        if state.get("instance_id"):
            raise ValueError("This test already has an instance. Use status/start or delete before another launch.")
        if state["profile"] != profile or state["region"] != region:
            raise ValueError("A partial launch exists in another profile/region; clean it up first.")
        existing = aws(
            state,
            "ec2",
            "describe-instances",
            "--filters",
            f"Name=tag:Name,Values={state['name']}",
            "Name=instance-state-name,Values=pending,running,stopping,stopped",
        )["Reservations"]
        if existing:
            state["instance_id"] = existing[0]["Instances"][0]["InstanceId"]
            save(state)
            print("Recovered the already launched instance; use status/tunnel.")
            return
    else:
        state = {
            "profile": profile,
            "region": region,
            "name": f"hype-radar-qwen-{uuid4().hex[:8]}",
        }
    aws(state, "sts", "get-caller-identity")
    quota = aws(
        state,
        "service-quotas",
        "get-service-quota",
        "--service-code",
        "ec2",
        "--quota-code",
        "L-DB2E81BA",
    )["Quota"]["Value"]
    if quota < 4:
        raise ValueError(f"{region} G/VT On-Demand quota is {quota} vCPUs; g5.xlarge needs 4.")
    offerings = aws(
        state,
        "ec2",
        "describe-instance-type-offerings",
        "--location-type",
        "availability-zone",
        "--filters",
        "Name=instance-type,Values=g5.xlarge",
    )["InstanceTypeOfferings"]
    zones = {item["Location"] for item in offerings}
    vpcs = aws(state, "ec2", "describe-vpcs", "--filters", "Name=is-default,Values=true")["Vpcs"]
    if not vpcs:
        raise ValueError(f"No default VPC in {region}; select a reviewed VPC/subnet before launching.")
    vpc = vpcs[0]["VpcId"]
    subnets = aws(state, "ec2", "describe-subnets", "--filters", f"Name=vpc-id,Values={vpc}")["Subnets"]
    subnets = sorted(
        (item for item in subnets if item["AvailabilityZone"] in zones and item["MapPublicIpOnLaunch"]),
        key=lambda item: item["AvailabilityZone"],
    )
    if not subnets:
        raise ValueError("No public default subnet offers g5.xlarge in this region.")
    ami = aws(
        state,
        "ssm",
        "get-parameter",
        "--name",
        "/aws/service/deeplearning/ami/x86_64/base-oss-nvidia-driver-gpu-ubuntu-22.04/latest/ami-id",
    )
    state["ami_id"] = ami["Parameter"]["Value"]
    image = aws(state, "ec2", "describe-images", "--image-ids", state["ami_id"])["Images"][0]
    if image["Architecture"] != "x86_64" or image["State"] != "available":
        raise ValueError("Expected an available x86_64 AWS GPU AMI.")
    with urlopen("https://checkip.amazonaws.com", timeout=10) as response:
        address = str(ipaddress.IPv4Address(response.read().decode().strip()))
    state["ssh_cidr"] = f"{address}/32"
    save(state)
    if not state.get("key_name"):
        subprocess.run(
            [
                "ssh-keygen",
                "-q",
                "-t",
                "ed25519",
                "-N",
                "",
                "-f",
                str(STATE / "ssh_key"),
            ],
            check=True,
        )
        aws(
            state,
            "ec2",
            "import-key-pair",
            "--key-name",
            state["name"],
            "--public-key-material",
            f"fileb://{STATE / 'ssh_key.pub'}",
        )
        state["key_name"] = state["name"]
        save(state)
    if not state.get("security_group_id"):
        group = aws(
            state,
            "ec2",
            "create-security-group",
            "--group-name",
            state["name"],
            "--description",
            "Qwen test: SSH from the launching client only; model port is loopback",
            "--vpc-id",
            vpc,
        )
        state["security_group_id"] = group["GroupId"]
        save(state)
        aws(
            state,
            "ec2",
            "authorize-security-group-ingress",
            "--group-id",
            state["security_group_id"],
            "--protocol",
            "tcp",
            "--port",
            "22",
            "--cidr",
            state["ssh_cidr"],
        )
    tags = [
        {"Key": "Name", "Value": state["name"]},
        {"Key": "Project", "Value": "hype-radar"},
        {"Key": "Purpose", "Value": "qwen-ec2-test"},
    ]
    aws(
        state,
        "ec2",
        "create-tags",
        "--resources",
        state["security_group_id"],
        "--tags",
        json.dumps(tags),
    )
    for subnet in subnets:
        try:
            result = aws(
                state,
                "ec2",
                "run-instances",
                "--image-id",
                state["ami_id"],
                "--instance-type",
                "g5.xlarge",
                "--count",
                "1",
                "--key-name",
                state["key_name"],
                "--subnet-id",
                subnet["SubnetId"],
                "--security-group-ids",
                state["security_group_id"],
                "--associate-public-ip-address",
                "--instance-initiated-shutdown-behavior",
                "stop",
                "--metadata-options",
                "HttpTokens=required",
                "--client-token",
                state["name"],
                "--user-data",
                f"file://{ROOT / 'bootstrap.sh'}",
                "--block-device-mappings",
                json.dumps(
                    [
                        {
                            "DeviceName": image["RootDeviceName"],
                            "Ebs": {
                                "VolumeSize": 100,
                                "VolumeType": "gp3",
                                "Encrypted": True,
                                "DeleteOnTermination": True,
                            },
                        }
                    ]
                ),
                "--tag-specifications",
                json.dumps(
                    [
                        {"ResourceType": "instance", "Tags": tags},
                        {"ResourceType": "volume", "Tags": tags},
                    ]
                ),
            )
            state["availability_zone"] = subnet["AvailabilityZone"]
            break
        except subprocess.CalledProcessError as error:
            if "InsufficientInstanceCapacity" not in error.stderr or subnet == subnets[-1]:
                raise
            print(
                f"No g5.xlarge capacity in {subnet['AvailabilityZone']}; trying the next zone in {region}.",
                flush=True,
            )
    state["instance_id"] = result["Instances"][0]["InstanceId"]
    save(state)
    print(json.dumps(state, indent=2))
    print("Instance launched. Bootstrap/model loading takes several minutes. Use status, logs, then tunnel.")


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument(
        "action",
        choices=["launch", "status", "logs", "start", "stop", "tunnel", "delete"],
    )
    parser.add_argument("--profile", default=os.getenv("AWS_PROFILE", "default"))
    parser.add_argument("--region", default=os.getenv("AWS_REGION", "eu-west-1"))
    args = parser.parse_args()
    if args.action == "launch":
        launch(args.profile, args.region)
        return
    state = json.loads(RECORD.read_text())
    if args.action == "status":
        if not state.get("instance_id"):
            print(json.dumps({"region": state["region"], "state": "launch incomplete; no instance saved"}))
            return
        info = instance(state)
        print(
            json.dumps(
                {
                    "region": state["region"],
                    "instance_id": state["instance_id"],
                    "state": info["State"]["Name"],
                    "public_ip": info.get("PublicIpAddress"),
                },
                indent=2,
            )
        )
    elif args.action in {"start", "stop"}:
        print(
            json.dumps(
                aws(
                    state,
                    "ec2",
                    f"{args.action}-instances",
                    "--instance-ids",
                    state["instance_id"],
                )
            )
        )
    elif args.action == "logs":
        subprocess.run(ssh(state) + ["sudo journalctl -u qwen-vllm -n 80 --no-pager"], check=True)
    elif args.action == "tunnel":
        # Capture the generated token into a private file; do not print it.
        result = subprocess.run(
            ssh(state) + ["sudo cat /opt/qwen/server.env"],
            check=True,
            capture_output=True,
            text=True,
        )
        key = result.stdout.strip().removeprefix("VLLM_API_KEY=")
        if len(key) != 64 or any(char not in "0123456789abcdef" for char in key):
            raise ValueError("Invalid or missing generated vLLM API key; check cloud-init bootstrap.")
        environment = STATE / "client.env"
        environment.write_text(
            f"OPENROUTER_API_KEY={key}\nOPENROUTER_MODEL=qwen3.5-9b\n"
            "OPENROUTER_API_URL=http://127.0.0.1:8001/v1/chat/completions\n"
            "OPENROUTER_TIMEOUT_SECONDS=120\nOPENROUTER_MAX_COMPLETION_TOKENS=2048\n"
            f"OPENAI_API_KEY={key}\nOPENAI_BASE_URL=http://127.0.0.1:8001/v1\nOPENAI_MODEL=qwen3.5-9b\n"
        )
        environment.chmod(0o600)
        print(
            f"Local endpoint: http://127.0.0.1:8001/v1\nPrivate environment: {environment}",
            flush=True,
        )
        subprocess.run(
            ssh(
                state,
                "-N",
                "-L",
                "127.0.0.1:8001:127.0.0.1:8000",
                "-o",
                "ExitOnForwardFailure=yes",
                "-o",
                "ServerAliveInterval=30",
                "-o",
                "ServerAliveCountMax=3",
            ),
            check=True,
        )
    elif args.action == "delete":
        if state.get("instance_id"):
            aws(
                state,
                "ec2",
                "terminate-instances",
                "--instance-ids",
                state["instance_id"],
            )
            subprocess.run(
                [
                    "aws",
                    "--profile",
                    state["profile"],
                    "--region",
                    state["region"],
                    "ec2",
                    "wait",
                    "instance-terminated",
                    "--instance-ids",
                    state["instance_id"],
                ],
                check=True,
            )
        if state.get("security_group_id"):
            aws(
                state,
                "ec2",
                "delete-security-group",
                "--group-id",
                state["security_group_id"],
            )
        if state.get("key_name"):
            aws(state, "ec2", "delete-key-pair", "--key-name", state["key_name"])
        RECORD.unlink()
        for filename in ("ssh_key", "ssh_key.pub", "client.env", "known_hosts"):
            (STATE / filename).unlink(missing_ok=True)
        print("Only this test's instance, root volume, security group and EC2 key pair were deleted.")


if __name__ == "__main__":
    try:
        main()
    except subprocess.CalledProcessError as error:
        raise SystemExit(error.stderr or str(error)) from error
    except (ValueError, FileNotFoundError) as error:
        raise SystemExit(str(error)) from error
