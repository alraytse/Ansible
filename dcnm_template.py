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
module: dcnm_template
short_description: add/update templates

"""

EXAMPLES = """
- name: "Add/Update Template"
    dcnm_template:
    base_url: https://dcnm-lab.dev.schwab.com
    username: "{{ ansible_user }}"
    password: "{{ ansible_password }}"
    fabric_name: PDC1-LAB-Fabric
    backout: False
    ise_template:
      name:
        - rlf14lab
        - rlf15lab
      ise_server_ip:
      ise_secret:
"""
from ansible.module_utils.basic import AnsibleModule
from dcnm.core.supported_fabrics import valid_urls, valid_fabrics
import json

try:
    from dcnm.core.session import Session
    from dcnm.core.dcnm_calls import (
        interface_state,
        hostname_to_serial,
        update_template,
        single_switch_deploy,
    )
except ImportError:
    print("\nmissing dcnm core module, please install first:\n")
    print(
        "python3 -m pip install git+https://bitbucket.schwab.com/scm/ens/dcnm_core.git"
    )
    exit(1)


def ise_template(current_policy_data):
    """
    Will update schwab_go_live template/policy to add ISE deployments.
    It will ask for input on whether you want AAA Authorize turned on and the same for logging.

    returns:
        (json) - json object with the information needed for the template POST call.
    """
    data = {
        "id": int(current_policy_data["policy"].split("-")[1]),
        "source": "",
        "serialNumber": current_policy_data["sn"],
        "policyId": current_policy_data["policy"],
        "entityType": "SWITCH",
        "entityName": "SWITCH",
        "templateName": "schwab_go_live",
        "priority": 490,
        "nvPairs": current_policy_data["ats"],
    }
    return data


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
    policy_list = []
    for sw in m.params["ise_template"]["name"]:
        get_policies = connection.get(
            f"/rest/control/policies/switches?serialNumber={hostname_to_serial(connection,connection.fabric,sw)}"
        ).json()
        for x in get_policies:
            if x["templateName"] == "schwab_go_live":
                policies = {
                    "policy": x["policyId"],
                    "sn": x["serialNumber"],
                    "ats": x["nvPairs"],
                }
                if m.params["backout"] == True:
                    policies["ats"].update({"ISE_SERVER_1": ""})
                    policies["ats"].update({"ISE_SECRET": ""})
                    policies["ats"].update({"AAA_TIMEOUT": "5"})
                else:
                    policies["ats"].update(
                        {"ISE_SERVER_1": m.params["ise_template"]["ise_server_ip"]}
                    )
                    policies["ats"].update(
                        {"ISE_SECRET": m.params["ise_template"]["ise_secret"]}
                    )
                    policies["ats"].update(
                        {"AAA_TIMEOUT": m.params["ise_template"]["timeout"]}
                    )
                policy_list.append(policies)
    updated_policies = [ise_template(policy) for policy in policy_list]
    results = [update_template(connection, json.dumps(x)) for x in updated_policies]
    if False in results:
        m.fail_json(
            msg="Template Update Failed, deployment was not attempted. Check GUI"
        )
    else:
        deploy_results = [
            single_switch_deploy(
                connection, connection.fabric, x["serialNumber"], showrun="false"
            )
            for x in updated_policies
        ]
        if False in deploy_results:
            m.fail_json(
                msg="Template Update Successful; however, deployment failed. Check GUI"
            )
        else:
            m.exit_json(
                changed=True, meta="Template updated and deployed.",
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
        "ise_template": {"required": False, "type": "dict"},
        "snmpv3_user": {"required": False, "type": "dict"},
    }

    module = AnsibleModule(argument_spec=fields)
    try:
        main(module)
    except Exception as e:
        module.fail_json(msg=e)
