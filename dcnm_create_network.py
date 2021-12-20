#!/usr/bin/env python

"""


"""
ANSIBLE_METADATA = {
    "metadata_version": "1.1",
    "status": ["preview"],
}

DOCUMENTATION = """
---
module: dcnm_create_network
short_description: Create Networks in DCNM with Ansible

"""

EXAMPLES = """
- name: "Cisco DCNM create network"
    dcnm_create_network:
    base_url: https://dcnm-lab.dev.com
    username: "{{ ansible_user }}"
    password: "{{ ansible_password }}"
    fabric_name: PDC1-LAB-Fabric
    networks:
      - subnet: 192.168.66.0/24
        vlan: 966

"""
from ansible.module_utils.basic import AnsibleModule
from dcnm.core.supported_fabrics import valid_urls, valid_fabrics

try:
    from dcnm.core.session import Session
    from dcnm.core.dcnm_calls import bulk_create_networks
    from dcnm.core.dcnm_parsers import network_name_generator, is_network_valid
except ImportError:
    print("\nmissing dcnm core module, please install first:\n")
    print(
        "python3 setup.py sdist bdist_wheel"
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
    # calls the login() method which will get API Token and add it to the headers
    connection.login()
    # set fabric name for the work
    connection.fabric = m.params["fabric_name"]
    bulk_create_data = []
    for n in m.params["networks"]:
        network = network_name_generator(n["subnet"], n["vlan"])
        nets = {
            "networkName": network[0],
            "vlanId": int(network[1]),
            "segmentId": int(network[2]),
        }
        bulk_create_data.append(nets)
    create_nets = bulk_create_networks(
        connection, m.params["fabric_name"], bulk_create_data
    )
    if "failed" in create_nets:
        m.fail_json(msg={"Network Create Failed": create_nets["failed"]})
    else:
        if len(create_nets["successful"]) > 0:
            m.exit_json(
                changed=True, meta={"Created": create_nets["successful"]},
            )
        elif len(create_nets["duplicates"]) > 0:
            m.exit_json(
                changed=False, meta={"Already Exist": create_nets["duplicates"]}
            )
        else:
            m.fail_json(msg={"Something else went wrong": create_nets})
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
        "networks": {"required": True, "type": "list"},
    }

    module = AnsibleModule(argument_spec=fields)
    try:
        for network in module.params["networks"]:
            valid_vlan = network["vlan"] > 1 and network["vlan"] < 4094
            if (
                not is_network_valid(network["subnet"]) == True
                and not valid_vlan == True
            ):
                module.fail_json(
                    msg="Subnet or Vlan Failed Validation Checks, doublecheck input data"
                )
        main(module)
    except Exception as e:
        module.fail_json(msg=e)
