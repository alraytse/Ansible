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
module: dcnm_interface_description
short_description: Add interface descriptions to ports should work on all port types. This module is NOT Idompotent

"""

EXAMPLES = """
- name: "Cisco DCNM Interface Description"
    dcnm_interface_description:
    base_url: https://dcnm-lab.dev.schwab.com
    username: ad.jeff.kala #"{{ ansible_user }}"
    password: "{{ ansible_password }}"
    fabric_name: PDC1-LAB-Fabric
    interfaces:
        - switch: rlf14lab
          interface: Ethernet1/20
          desc: 1_Ansible_desc_eth1/20
        - switch: rlf14lab
          interface: Ethernet1/10
          desc: 1_Ansible_desc_eth1/10
        - switch: rlf14lab
          interface: Ethernet1/21
          desc: 1_Ansible_desc_eth1/21
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
    interface_list = [
        {
            "ifName": x["interface"],
            "serialNumber": hostname_to_serial(
                connection, connection.fabric, x["switch"]
            ),
        }
        for x in m.params["interfaces"]
    ]
    final_list = []
    for ints in m.params["interfaces"]:
        temp = get_interface_details(
            connection,
            connection.fabric,
            hostname_to_serial(connection, connection.fabric, ints["switch"]),
            ints["interface"],
        )
        if ints["desc"] is None:
            temp[0]["interfaces"][0]["nvPairs"]["DESC"] = ""
        else:
            temp[0]["interfaces"][0]["nvPairs"]["DESC"] = ints["desc"]
        [x["nvPairs"].pop("POLICY_ID") for x in temp[0]["interfaces"]]
        final_list.append(temp[0])
    int_dict = dict((x["policy"], []) for x in final_list)
    for x in final_list:
        int_dict[x["policy"]].append(x["interfaces"][0])
    http_put_list = []
    for p, i in int_dict.items():
        http_put_list.append(dict(policy=p, interfaces=i))
    results = [update_interface_details(connection, x) for x in http_put_list]
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
        "interfaces": {"required": True, "type": "list"},
    }

    module = AnsibleModule(argument_spec=fields)
    try:
        main(module)
    except Exception as e:
        module.fail_json(msg=e)
