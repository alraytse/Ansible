import paramiko

def ssh_to_nexus(host, username, password, command="show running-config"):
    try:
        # Create SSH client
        ssh = paramiko.SSHClient()
        ssh.set_missing_host_key_policy(paramiko.AutoAddPolicy())
        
        # Connect to device
        ssh.connect(hostname=host, username=username, password=password, look_for_keys=False, allow_agent=False)
        
        # Open interactive shell
        shell = ssh.invoke_shell()
        shell.send("terminal length 0\n")  # disable paging
        shell.send(f"{command}\n")
        shell.send("exit\n")
        
        # Wait for command to run
        buff = ""
        while True:
            if shell.recv_ready():
                output = shell.recv(65535).decode("utf-8")
                buff += output
                if "exit" in output.lower():
                    break
        
        ssh.close()

        # Clean and return output
        return buff

    except Exception as e:
        return f"Error: {str(e)}"

# Example usage
if __name__ == "__main__":
    host = "192.168.1.10"        # Replace with Nexus switch IP
    username = "admin"           # Replace with your username
    password = "yourpassword"    # Replace with your password
    
    output = ssh_to_nexus(host, username, password)
    print(output)