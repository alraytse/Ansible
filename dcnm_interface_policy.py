#!/usr/bin/env python

"""
    __data__ = '7/13/2020'
    __author__ = 'jeff.kala@schwab.com, jose.lima@schwab.com'

"""
ANSIBLE_METADATA = {
    "metadata_version": "1.1",
    "status": ["preview"],
    "supported_by": "jeff.kala@schwab.com, jose.lima@schwab.com",
}

DOCUMENTATION = """
---
module: dcnm_interface_policy
short_description: Set interface policy to trunk,access, or routed. NOTE: It is expected that there is NO overlay attach on the ports before using this module.

"""

EXAMPLES = """
policy:
    - int_trunk_host_11_1
    - int_access_host_11_1
    - int_routed_host_11_1

- name: "Cisco DCNM Interface Policy Change"
  dcnm_interface_policy:
    base_url: https://dcnm-lab.dev.schwab.com
    username: ad.jeff.kala #"{{ ansible_user }}"
    password: "{{ ansible_password }}"
    fabric_name: PDC1-LAB-Fabric
    policy:
      - name: int_trunk_host_11_1
        switch:
          - name: rlf14lab
            interfaces:
              - Ethernet1/20
              - Ethernet1/21
      - name: int_access_host_11_1
        switch:
          - name: rlf14lab
            interfaces:
              - Ethernet1/22
              - Ethernet1/23
      - name: int_routed_host_11_1
        switch:
          - name: rlf14lab
            interfaces:
              - Ethernet1/24
"""
from ansible.module_utils.basic import AnsibleModule
from dcnm.core.supported_fabrics import valid_urls, valid_fabrics

try:
    from dcnm.core.session import Session
    from dcnm.core.dcnm_calls import (
        get_interface_details,
        hostname_to_serial,
        deploy_interface_change,
        update_interface_details,
    )
    from dcnm.core.dcnm_parsers import network_name_generator, is_network_valid
except ImportError:
    print("\nmissing dcnm core module, please install first:\n")
    print(
        "python3 -m pip install git+https://bitbucket.schwab.com/scm/ens/dcnm_core.git"
    )
    exit(1)


def trunk(conn, switch_list):
    """
    This function takes in a list of data for trunk policies and creates proper dcnm api payload for the http call

    Args: (list):
        'policy': [{'name': 'int_trunk_host_11_1', 'switch': [{'name': 'rlf14lab', 'interfaces': ['Ethernet1/20', 'Ethernet1/21']}]}]

    returns:
        (dict) with proper payload for the http PUT call
    """
    final_dict = {"policy": "int_trunk_host_11_1", "interfaces": []}

    for sw in switch_list:
        for int in sw["interfaces"]:
            temp = {
                "serialNumber": hostname_to_serial(conn, conn.fabric, sw["name"]),
                "ifName": int,
                "nvPairs": {
                    "BPDUGUARD_ENABLED": "no",
                    "PTP": "false",
                    "INTF_NAME": int,
                    "DESC": "",
                    "PORTTYPE_FAST_ENABLED": "true",
                    "MTU": "jumbo",
                    "SPEED": "Auto",
                    "ADMIN_STATE": "true",
                    "POLICY_DESC": "",
                    "CONF": "",
                    "ALLOWED_VLANS": "none",
                    "GF": "",
                },
            }
            final_dict["interfaces"].append(temp)
    return final_dict


def access(conn, switch_list):
    """
    This function takes in a list of data for access policies and creates proper dcnm api payload for the http call

    Args: (list):
        'policy': [{'name': 'int_access_host_11_1', 'switch': [{'name': 'rlf14lab', 'interfaces': ['Ethernet1/20', 'Ethernet1/21']}]}]

    returns:
        (dict) with proper payload for the http PUT call
    """
    final_dict = {"policy": "int_access_host_11_1", "interfaces": []}

    for sw in switch_list:
        for int in sw["interfaces"]:
            temp = {
                "serialNumber": hostname_to_serial(conn, conn.fabric, sw["name"]),
                "ifName": int,
                "nvPairs": {
                    "BPDUGUARD_ENABLED": "no",
                    "PORTTYPE_FAST_ENABLED": "true",
                    "MTU": "jumbo",
                    "SPEED": "Auto",
                    "ACCESS_VLAN": "",
                    "DESC": "",
                    "CONF": "",
                    "ADMIN_STATE": "true",
                    "INTF_NAME": int,
                },
            }
            final_dict["interfaces"].append(temp)
    return final_dict


def routed(conn, switch_list):
    """
    This function takes in a list of data for routed policies and creates proper dcnm api payload for the http call

    Args: (list):
        'policy': [{'name': 'int_routed_host_11_1', 'switch': [{'name': 'rlf14lab', 'interfaces': ['Ethernet1/20', 'Ethernet1/21']}]}]

    returns:
        (dict) with proper payload for the http PUT call
    """
    final_dict = {"policy": "int_routed_host_11_1", "interfaces": []}

    for sw in switch_list:
        for int in sw["interfaces"]:
            temp = {
                "serialNumber": hostname_to_serial(conn, conn.fabric, sw["name"]),
                "ifName": int,
                "nvPairs": {
                    "ADMIN_STATE": "true",
                    "CONF": "",
                    "DESC": "",
                    "INTF_NAME": int,
                    "INTF_VRF": "",
                    "IP": "",
                    "MTU": "9216",
                    "PREFIX": "",
                    "ROUTING_TAG": "",
                    "SPEED": "Auto",
                },
            }
            final_dict["interfaces"].append(temp)
    return final_dict


def main(m):
    """
    main function will login to dcnm get a token and call the bulk create API call to
    return results

    Args: m (dict) - module.params for Ansible

    return N/A however m.exit_json or m.fail_json will exit the function with Ansible
    """
    connection = Session(
        m.params["base_url"], m.params["username"], m.params["password"]
    )
    connection.login()
    connection.update_lan_creds()
    connection.fabric = m.params["fabric_name"]
    policy_data_list = []
    for p in m.params["policy"]:
        if p["name"] == "int_trunk_host_11_1":
            policy_data_list.append(trunk(connection, p["switch"]))
        elif p["name"] == "int_access_host_11_1":
            policy_data_list.append(access(connection, p["switch"]))
        elif p["name"] == "int_routed_host_11_1":
            policy_data_list.append(routed(connection, p["switch"]))
        else:
            m.fail_json(
                msg="The 3 supported policies are: int_trunk_host_11_1, int_access_host_11_1, int_routed_host_11_1 anything else should be done manually in GUI"
            )
    # m.fail_json(msg=policy_data_list)
    interface_list = []
    for p in m.params["policy"]:
        for sw in p["switch"]:
            for int in sw["interfaces"]:
                interface_list.append(
                    {
                        "ifName": int,
                        "serialNumber": hostname_to_serial(
                            connection, connection.fabric, sw["name"]
                        ),
                    }
                )
    results = [update_interface_details(connection, x) for x in policy_data_list]
    if False in results:
        m.fail_json(
            msg="Interface Update Failed, deployment was not attempted. Check GUI"
        )
    else:
        deploy_results = deploy_interface_change(connection, interface_list)
        if deploy_results == True:
            m.exit_json(
                changed=True, meta="Interfaces updated and deployed.",
            )
        else:
            m.fail_json(
                msg="Interface Update Successful; however, deployment failed. Check GUI"
            )
    connection.logout()


if __name__ == "__main__":
    fields = {
        "base_url": {
            "required": True,
            "type": "str",
            "default": None,
            "choices": valid_urls,
        },
        "username": {"required": True, "type": "str"},
        "password": {"required": True, "type": "str", "no_log": True},
        "fabric_name": {
            "required": True,
            "type": "str",
            "default": None,
            "choices": valid_fabrics,
        },
        "policy": {"required": True, "type": "list"},
    }

    module = AnsibleModule(argument_spec=fields)
    try:
        main(module)
    except Exception as e:
        module.fail_json(msg=e)
