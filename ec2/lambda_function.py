import boto3
import concurrent.futures
import time

def lambda_handler(event, context):
    resources = int(event["r"])
    aws_region = "us-east-1"
    image_id = 'ami-0bb84b8ffd87024d8'
    instance_type = 't2.micro'
    key_name = 'us-east-1kp'
    security_group_ids = ['sg-078a5f36a303a32dd']

    ec2_client = boto3.client("ec2", region_name=aws_region)

    def launch_ec2_instance(_):
        try:
            response = ec2_client.run_instances(
                ImageId=image_id,
                InstanceType=instance_type,
                MinCount=1,
                MaxCount=1,
                KeyName=key_name,
                SecurityGroupIds=security_group_ids
            )
            instance_id = response["Instances"][0]["InstanceId"]
            print(f"Launched EC2 instance: {instance_id}")
            time.sleep(1)
            return {"InstanceId": instance_id}
        except Exception as e:
            print(f"Error launching EC2 instance: {e}")
            return None

    def launch_ec2_instances(num_instances):
        with concurrent.futures.ThreadPoolExecutor() as executor:
            return list(executor.map(launch_ec2_instance, range(num_instances)))

    instances_info = launch_ec2_instances(resources)

    for instance_info in instances_info:
        if instance_info:  # Check if instance_info is not None
            instance_id = instance_info["InstanceId"]
            try:
                response = ec2_client.describe_instances(InstanceIds=[instance_id])
                instance_details = response["Reservations"][0]["Instances"][0]
                instance_info["PublicDnsName"] = instance_details.get("PublicDnsName", "Not Available")
                instance_info["PublicIpAddress"] = instance_details.get("PublicIpAddress", "Not Available")
            except Exception as e:
                print(f"Error describing instance {instance_id}: {e}")

    return {"result": "ok", "instances_info": instances_info}
