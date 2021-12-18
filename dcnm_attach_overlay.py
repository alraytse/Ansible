#!/usr/bin/env python

"""
    __data__ = '7/7/2020'
    

"""
ANSIBLE_METADATA = {
    "metadata_version": "1.1",
    "status": ["preview"],
 
}

DOCUMENTATION = """
---
module: dcnm_attach_overlay
short_description: Attach overlay (vxlan profile) onto a switch(pair)/ports or Backout by turning switch to backout: True

"""

EXAMPLES = """
- name: "Cisco DCNM overlay attach"
    dcnm_attach_overlay:
    base_url: https://dcnm-lab.com
    username: ad.jeff.kala #"{{ ansible_user }}"
    password: "{{ ansible_password }}"
    fabric_name: PDC1-LAB-Fabric
    backout: False
    networks:
        - subnet: 192.168.66.0/24
        vlan: 966
        switch:
            - name: rlf14lab
            interfaces:
                - Ethernet1/20
            - name: rlf15lab
            interfaces:
                - Ethernet1/20
        - subnet: 192.168.67.0/24
        vlan: 967
        switch:
            - name: rlf14lab
            interfaces:
                - Ethernet1/21
            - name: rlf15lab
            interfaces:
                - Ethernet1/21
"""
from ansible.module_utils.basic import AnsibleModule
from dcnm.core.supported_fabrics import valid_urls, valid_fabrics

try:
    from dcnm.core.session import Session
    from dcnm.core.dcnm_calls import (
        attach_networks,
        deploy_networks,
        hostname_to_serial,
        deattach_interfaces,
    )
    from dcnm.core.dcnm_parsers import network_name_generator, is_network_valid
except ImportError:
    print("\nmissing dcnm core module, please install first:\n")
    print(
        "python3 -m pip install git+https://bitbucket.com/scm/ens/dcnm_core.git"
    )
    exit(1)


def main(m):
    """
    Args: m (dict) - module.params for Ansible

    return N/A however m.exit_json or m.fail_json will exit the function with Ansible
    """

    connection = Session(
        m.params["base_url"], m.params["username"], m.params["password"]
    )
    connection.login()
    connection.fabric = m.params["fabric_name"]
    connection.update_lan_creds()

    nets_for_function = []
    for net in m.params["networks"]:
        network = network_name_generator(net["subnet"], net["vlan"])
        net["networkName"] = net.pop("subnet")
        net["networkName"] = network[0]
        net["vlanId"] = net.pop("vlan")
        net["attachInfo"] = net.pop("switch")
        for sw in net["attachInfo"]:
            sw["serialNumber"] = sw.pop("name")
            sw["serialNumber"] = hostname_to_serial(
                connection, m.params["fabric_name"], sw["serialNumber"]
            )
    """
    Example of m.params["networks"]: [
        {"networkName": "192-168-66-0_24_VL966_10966", "vlanId": 966, "attachInfo": [
        {"interfaces": ["Ethernet1/20"], "serialNumber": "FDO22222T3N"},
        {"interfaces": ["Ethernet1/20"], "serialNumber": "FDO22201MPN"}]},
        {"networkName": "192-168-67-0_24_VL967_10967", "vlanId": 967, "attachInfo": [
        {"interfaces": ["Ethernet1/21"], "serialNumber": "FDO22222T3N"},
        {"interfaces": ["Ethernet1/21"], "serialNumber": "FDO22201MPN"}]}]}
    """
    if m.params["backout"] is False:
        result = attach_networks(connection, connection.fabric, m.params["networks"])
        if result is False:
            m.fail_json(msg="Network Attach Failed")
        elif result is True:
            deploy_result = deploy_networks(
                connection,
                connection.fabric,
                list(x["networkName"] for x in m.params["networks"]),
            )
            if deploy_result is True:
                m.exit_json(
                    changed=True,
                    meta={"Networks attached and deployed": deploy_result},
                )
            else:
                m.fail_json(msg="Network Attach Succeeded; However, Deployment Failed")
        else:
            m.fail_json(msg="Something else went wrong")
    else:
        if m.params["backout"] is True:
            result = deattach_interfaces(
                connection, connection.fabric, m.params["networks"]
            )
            if result is False:
                m.fail_json(msg="Interface De-attach Failed")
            elif result is True:
                deploy_result = deploy_networks(
                    connection,
                    connection.fabric,
                    list(x["networkName"] for x in m.params["networks"]),
                )
                if deploy_result is True:
                    m.exit_json(
                        changed=True, meta={"Interface De-attached": deploy_result},
                    )
                else:
                    m.fail_json(
                        msg="Interface De-attached Succeeded; However, Deployment Failed"
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
        "backout": {"default": False, "type": "bool"},
        "networks": {"required": True, "type": "list"},
    }

    module = AnsibleModule(argument_spec=fields)
    try:
        main(module)
    except Exception as e:
        module.fail_json(msg=e)
