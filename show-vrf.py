import paramiko
import time

def ssh_run_bgp_command(host, username, password, vrf="all"):
    try:
        # Connect via SSH
        ssh = paramiko.SSHClient()
        ssh.set_missing_host_key_policy(paramiko.AutoAddPolicy())
        ssh.connect(hostname=host, username=username, password=password, look_for_keys=False, allow_agent=False)

        # Start interactive shell
        shell = ssh.invoke_shell()
        time.sleep(1)

        # Clear initial buffer
        if shell.recv_ready():
            shell.recv(1000)

        # Disable paging
        shell.send("terminal length 0\n")
        time.sleep(1)

        # Run BGP command
        command = f"show bgp vrf {vrf}\n"
        shell.send(command)
        time.sleep(3)

        # Exit session
        shell.send("exit\n")

        # Read output
        output = ""
        while shell.recv_ready():
            output += shell.recv(65535).decode("utf-8")
            time.sleep(0.5)

        ssh.close()
        return output

    except Exception as e:
        return f"Error: {str(e)}"

# Example usage
if __name__ == "__main__":
    host = "192.168.1.10"      # Replace with your Nexus IP
    username = "admin"         # Replace with your username
    password = "yourpassword"  # Replace with your password

    result = ssh_run_bgp_command(host, username, password)
    print(result)
