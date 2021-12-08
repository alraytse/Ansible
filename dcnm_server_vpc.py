#!/usr/bin/env python

"""
    __data__ = '7/7/2020'
    __author__ = 'jeff.kala@schwab.com, jose.lima@schwab.com'

"""
ANSIBLE_METADATA = {
    "metadata_version": "1.1",
    "status": ["preview"],
    "supported_by": "jeff.kala@schwab.com, jose.lima@schwab.com",
}

DOCUMENTATION = """
---
module: dcnm_server_vpc
short_description: creates a dcnm vpc for a server
"""

EXAMPLES = """
    - name: "create server vpc"
      dcnm_server_vpc:
        base_url: https://dcnm-lab.dev.schwab.com
        username: ad.jeff.kala # "{{ ansible_user }}"
        password: "{{ ansible_password }}"
        fabric_name: PDC1-LAB-Fabric
        vpc_info:
          - vpc_id: 1200
            policy: trunk #trunk or access
            switch_one:
              name: rlf14lab
              po_description: test_desc_rlf14lab
              member_interface: Ethernet1/20
            switch_two:
              name: rlf15lab
              po_description: test_desc_rlf15lab
              member_interface: Ethernet1/20
          - vpc_id: 1201
            policy: access #trunk or access
            switch_one:
              name: rlf14lab
              po_description: test2_desc_rlf14lab
              member_interface: Ethernet1/21
            switch_two:
              name: rlf15lab
              po_description: test2_desc_rlf15lab
              member_interface: Ethernet1/21
"""
from ansible.module_utils.basic import AnsibleModule
from dcnm.core.supported_fabrics import valid_urls, valid_fabrics

try:
    from dcnm.core.session import Session
    from dcnm.core.dcnm_calls import (
        get_vpc_pair_serials,
        hostname_to_serial,
        create_interface,
        deploy_interface_change,
    )
except ImportError:
    print("\nmissing dcnm core module, please install first:\n")
    print(
        "python3 -m pip install git+https://bitbucket.schwab.com/scm/ens/dcnm_core.git"
    )
    exit(1)


def vpc_data(conn, data):
    """
    Will update schwab_go_live template/policy to add ISE deployments.
    It will ask for input on whether you want AAA Authorize turned on and the same for logging.
    data - (list)
            [{
                "vpc_id": 1200,
                "policy": "trunk",
                "switch_one": {
                    "name": "rlf14lab",
                    "po_description": "test_desc_rlf14lab",
                    "member_interface": "Ethernet1/20",
                },
                "switch_two": {
                    "name": "rlf15lab",
                    "po_description": "test_desc_rlf15lab",
                    "member_interface": "Ethernet1/20",
                },
            }]
    returns:
        (json) - json object with the information needed for the template POST call.
    """
    final_list = []
    deploy_info = []
    for vpc in data:
        vpc["switch_one"]["serialNumber"] = hostname_to_serial(
            conn, conn.fabric, vpc["switch_one"]["name"]
        )
        vpc["switch_two"]["serialNumber"] = hostname_to_serial(
            conn, conn.fabric, vpc["switch_two"]["name"]
        )
        vpc_pair = get_vpc_pair_serials(conn, conn.fabric, vpc["switch_one"]["name"])
        peer_one, peer_two = vpc_pair[0], vpc_pair[1]
        if vpc["policy"] == "trunk":
            if vpc["switch_one"]["serialNumber"] == peer_one:
                result = {
                    "interfaceType": "INTERFACE_VPC",
                    "interfaces": [
                        {
                            "fabricName": conn.fabric,
                            "ifName": f"vPC{vpc['vpc_id']}",
                            "interfaceType": "INTERFACE_VPC",
                            "nvPairs": {
                                "ADMIN_STATE": True,
                                "BPDUGUARD_ENABLED": "true",
                                "INTF_NAME": f"vPC{vpc['vpc_id']}",
                                "MTU": "jumbo",
                                "PC_MODE": "active",
                                "PEER1_ALLOWED_VLANS": "none",
                                "PEER1_MEMBER_INTERFACES": f"e1/{vpc['switch_one']['member_interface'].split('/')[1]}",
                                "PEER1_PCID": vpc["vpc_id"],
                                "PEER1_PO_CONF": "",
                                "PEER1_PO_DESC": vpc["switch_one"]["po_description"],
                                "PEER2_ALLOWED_VLANS": "none",
                                "PEER2_MEMBER_INTERFACES": f"e1/{vpc['switch_two']['member_interface'].split('/')[1]}",
                                "PEER2_PCID": vpc["vpc_id"],
                                "PEER2_PO_CONF": "",
                                "PEER2_PO_DESC": vpc["switch_two"]["po_description"],
                                "PORTTYPE_FAST_ENABLED": True,
                            },
                            "serialNumber": "~".join(vpc_pair),
                        }
                    ],
                    "policy": f"int_vpc_trunk_host_11_1",
                }
                final_list.append(result)
                deploy_info.append(
                    {
                        "serialNumber": "~".join(vpc_pair),
                        "ifName": f"vPC{vpc['vpc_id']}",
                        "fabricName": conn.fabric,
                    }
                )
            else:
                result = {
                    "interfaceType": "INTERFACE_VPC",
                    "interfaces": [
                        {
                            "fabricName": conn.fabric,
                            "ifName": f"vPC{vpc['vpc_id']}",
                            "interfaceType": "INTERFACE_VPC",
                            "nvPairs": {
                                "ADMIN_STATE": True,
                                "BPDUGUARD_ENABLED": "true",
                                "INTF_NAME": f"vPC{vpc['vpc_id']}",
                                "MTU": "jumbo",
                                "PC_MODE": "active",
                                "PEER1_ALLOWED_VLANS": "none",
                                "PEER1_MEMBER_INTERFACES": f"e1/{vpc['switch_two']['member_interface'].split('/')[1]}",
                                "PEER1_PCID": vpc["vpc_id"],
                                "PEER1_PO_CONF": "",
                                "PEER1_PO_DESC": vpc["switch_two"]["po_description"],
                                "PEER2_ALLOWED_VLANS": "none",
                                "PEER2_MEMBER_INTERFACES": f"e1/{vpc['switch_one']['member_interface'].split('/')[1]}",
                                "PEER2_PCID": vpc["vpc_id"],
                                "PEER2_PO_CONF": "",
                                "PEER2_PO_DESC": vpc["switch_one"]["po_description"],
                                "PORTTYPE_FAST_ENABLED": True,
                            },
                            "serialNumber": "~".join(vpc_pair),
                        }
                    ],
                    "policy": f"int_vpc_trunk_host_11_1",
                }
                final_list.append(result)
                deploy_info.append(
                    {
                        "serialNumber": "~".join(vpc_pair),
                        "ifName": f"vPC{vpc['vpc_id']}",
                        "fabricName": conn.fabric,
                    }
                )
        elif vpc["policy"] == "access":
            if vpc["switch_one"]["serialNumber"] == peer_one:
                result = {
                    "interfaceType": "INTERFACE_VPC",
                    "interfaces": [
                        {
                            "fabricName": conn.fabric,
                            "ifName": f"vPC{vpc['vpc_id']}",
                            "interfaceType": "INTERFACE_VPC",
                            "nvPairs": {
                                "ADMIN_STATE": True,
                                "BPDUGUARD_ENABLED": "true",
                                "INTF_NAME": f"vPC{vpc['vpc_id']}",
                                "MTU": "jumbo",
                                "PC_MODE": "active",
                                "PEER1_ACCESS_VLAN": "",
                                "PEER1_MEMBER_INTERFACES": f"e1/{vpc['switch_one']['member_interface'].split('/')[1]}",
                                "PEER1_PCID": vpc["vpc_id"],
                                "PEER1_PO_CONF": "",
                                "PEER1_PO_DESC": vpc["switch_one"]["po_description"],
                                "PEER2_ACCESS_VLAN": "",
                                "PEER2_MEMBER_INTERFACES": f"e1/{vpc['switch_two']['member_interface'].split('/')[1]}",
                                "PEER2_PCID": vpc["vpc_id"],
                                "PEER2_PO_CONF": "",
                                "PEER2_PO_DESC": vpc["switch_two"]["po_description"],
                                "PORTTYPE_FAST_ENABLED": True,
                            },
                            "serialNumber": "~".join(vpc_pair),
                        }
                    ],
                    "policy": f"int_vpc_access_host_11_1",
                }
                final_list.append(result)
                deploy_info.append(
                    {
                        "serialNumber": "~".join(vpc_pair),
                        "ifName": f"vPC{vpc['vpc_id']}",
                        "fabricName": conn.fabric,
                    }
                )
            else:
                result = {
                    "interfaceType": "INTERFACE_VPC",
                    "interfaces": [
                        {
                            "fabricName": conn.fabric,
                            "ifName": f"vPC{vpc['vpc_id']}",
                            "interfaceType": "INTERFACE_VPC",
                            "nvPairs": {
                                "ADMIN_STATE": True,
                                "BPDUGUARD_ENABLED": "true",
                                "INTF_NAME": f"vPC{vpc['vpc_id']}",
                                "MTU": "jumbo",
                                "PC_MODE": "active",
                                "PEER1_ACCESS_VLAN": "",
                                "PEER1_MEMBER_INTERFACES": f"e1/{vpc['switch_two']['member_interface'].split('/')[1]}",
                                "PEER1_PCID": vpc["vpc_id"],
                                "PEER1_PO_CONF": "",
                                "PEER1_PO_DESC": vpc["switch_two"]["po_description"],
                                "PEER2_ACCESS_VLAN": "",
                                "PEER2_MEMBER_INTERFACES": f"e1/{vpc['switch_one']['member_interface'].split('/')[1]}",
                                "PEER2_PCID": vpc["vpc_id"],
                                "PEER2_PO_CONF": "",
                                "PEER2_PO_DESC": vpc["switch_one"]["po_description"],
                                "PORTTYPE_FAST_ENABLED": True,
                            },
                            "serialNumber": "~".join(vpc_pair),
                        }
                    ],
                    "policy": f"int_vpc_access_host_11_1",
                }
                final_list.append(result)
                deploy_info.append(
                    {
                        "serialNumber": "~".join(vpc_pair),
                        "ifName": f"vPC{vpc['vpc_id']}",
                        "fabricName": conn.fabric,
                    }
                )
        else:
            return [], []
    return final_list, deploy_info


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
    payload_data, for_deploy = vpc_data(connection, m.params["vpc_info"])
    if len(payload_data) == 0 and len(for_deploy) == 0:
        m.fail_json(
            msg="Check playbook for accurate inputs policy can only be 'trunk or access'"
        )
    results = [create_interface(connection, x) for x in payload_data]
    if False in results:
        m.fail_json(
            msg="Interface Update Failed, deployment was not attempted. Check GUI"
        )
    else:
        deploy_results = deploy_interface_change(connection, for_deploy)
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
        "vpc_info": {"required": True, "type": "list"},
    }
    module = AnsibleModule(argument_spec=fields)
    try:
        main(module)
    except Exception as e:
        module.fail_json(msg=e)
