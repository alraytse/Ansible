import boto3
import paramiko
import time

# AWS Configuration
INSTANCE_ID = 'i-0abcdef1234567890'
KEY_PATH = '/path/to/your-key.pem'
USERNAME = 'ec2-user'  # or 'ubuntu' for Ubuntu AMIs
REGION = 'us-east-1'

# 1. Start EC2 Instance (if it's not running)
ec2 = boto3.client('ec2', region_name=REGION)
response = ec2.describe_instances(InstanceIds=[INSTANCE_ID])
state = response['Reservations'][0]['Instances'][0]['State']['Name']

if state != 'running':
    print(f"Starting instance {INSTANCE_ID}...")
    ec2.start_instances(InstanceIds=[INSTANCE_ID])
    waiter = ec2.get_waiter('instance_running')
    waiter.wait(InstanceIds=[INSTANCE_ID])
    print("Instance is now running.")

# 2. Get Public IP
instance_info = ec2.describe_instances(InstanceIds=[INSTANCE_ID])
public_ip = instance_info['Reservations'][0]['Instances'][0]['PublicIpAddress']
print(f"Public IP: {public_ip}")

# 3. Connect via SSH
print("Connecting via SSH...")
key = paramiko.RSAKey.from_private_key_file(KEY_PATH)
ssh = paramiko.SSHClient()
ssh.set_missing_host_key_policy(paramiko.AutoAddPolicy())

# Wait for SSH to become available
time.sleep(15)

ssh.connect(hostname=public_ip, username=USERNAME, pkey=key)

# 4. Run a command
stdin, stdout, stderr = ssh.exec_command('uptime')
print("Uptime:", stdout.read().decode())

# Close connection
ssh.close()
