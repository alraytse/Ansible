#!/usr/bin/env python

"""
    __data__ = '7/7/2020'
    

"""
ANSIBLE_METADATA = {
    "metadata_version": "1.1",
    "status": ["preview"],
    
DOCUMENTATION = """
---
module: dcnm_interface_state
short_description: Shut/No Shut List of Interfaces

"""

EXAMPLES = """
- name: "Shut/No Shut List of Interfaces"
    dcnm_interface_state:
    base_url: https://dcnm-lab.dev.com
    username: "{{ ansible_user }}"
    password: "{{ ansible_password }}"
    fabric_name: PDC1-LAB-Fabric
    interfaces:
      - switch:
          - name: rlf14lab
            state: disable
            interfaces:
              - Ethernet1/20
              - Ethernet1/21

"""
from ansible.module_utils.basic import AnsibleModule
from dcnm.core.supported_fabrics import valid_urls, valid_fabrics

try:
    from dcnm.core.session import Session
    from dcnm.core.dcnm_calls import interface_state, hostname_to_serial
except ImportError:
    print("\nmissing dcnm core module, please install first:\n")
    print(
        "python3 -m pip install git+https://bitbucket.com/scm/ens/dcnm_core.git"
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
    version = connection.version.json()["Dcnm-Version"]
    connection.fabric = m.params["fabric_name"]
    # module.fail_json(msg=m.params)
    #'interfaces': [{'switch': 'rlf14lab', 'state': 'disable', 'interfaces': ['Ethernet1/20', 'Ethernet1/21']}, {'switch': 'rlf15lab', 'state': 'enable', 'interfaces': ['Ethernet1/20', 'Ethernet1/21']}]}
    # {"operation": "shut","interfaces": [{"serialNumber": "FDO22222T3N","ifName": "Ethernet1/10"}]}
    http_post_list = []
    # This is a workaround for this bug: 'https://bst.cloudapps.cisco.com/bugsearch/bug/CSCvv59562'
    if "11.4(1)" in version:
        for i in m.params["interfaces"]:
            if i["state"].lower() == "disable":
                for int in i["interfaces"]:
                    f = {"operation": "shut"}
                    f["interfaces"] = [{"serialNumber": i["switch"], "ifName": int}]
                    http_post_list.append(f)
            elif i["state"].lower() == "enable":
                for int in i["interfaces"]:
                    f = {"operation": "noshut"}
                    f["interfaces"] = [{"serialNumber": i["switch"], "ifName": int}]
                    http_post_list.append(f)
    else:
        for i in m.params["interfaces"]:
            if i["state"].lower() == "disable":
                final_dict = {
                    "operation": "shut",
                    "interfaces": [
                        {
                            "serialNumber": hostname_to_serial(
                                connection, connection.fabric, i["switch"]
                            ),
                            "ifName": x,
                        }
                        for x in i["interfaces"]
                    ],
                }
                http_post_list.append(final_dict)
            elif i["state"].lower() == "enable":
                final_dict = {
                    "operation": "noshut",
                    "interfaces": [
                        {
                            "serialNumber": hostname_to_serial(
                                connection, connection.fabric, i["switch"]
                            ),
                            "ifName": x,
                        }
                        for x in i["interfaces"]
                    ],
                }
                http_post_list.append(final_dict)
            else:
                module.fail_json(msg="Valid state is disable or enable")
    # module.fail_json(msg=http_post_list)
    results = [interface_state(connection, x) for x in http_post_list]
    if False in results:
        m.fail_json(msg="Interface Admin State Change Failed, Check GUI")
    else:
        m.exit_json(
            changed=True, meta="Interfaces Admin State Change Successful.",
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
