#!/usr/bin/env python

"""
 

"""
ANSIBLE_METADATA = {
    "metadata_version": "1.1",
    "status": ["preview"],

DOCUMENTATION = """
---
module: dcnm_default_interface
short_description: Warning!! This will default interface to an access port. It WILL remove any overlay attached on the port!!
"""

EXAMPLES = """
- name: "defaulting interfaces"
  dcnm_default_interface:
    base_url: https://dcnm-lab.dev.schwab.com
    username: ad.jeff.kala # "{{ ansible_user }}"
    password: "{{ ansible_password }}"
    fabric_name: PDC1-LAB-Fabric
    switches:
      rlf04lab:
        - Ethernet1/1
        - Ethernet1/2
      rlf05lab:
        - Ethernet1/1
        - Ethernet1/2
"""
from ansible.module_utils.basic import AnsibleModule
import json
import sys
from dcnm.core.supported_fabrics import valid_urls, valid_fabrics

try:
    from dcnm.core.session import Session
    from dcnm.core.supported_fabrics import valid_urls, valid_fabrics
    from dcnm.core.dcnm_calls import (
        deattach_networks,
        deattach_interfaces,
        deploy_interface_change,
        deploy_networks,
        update_interface_details,
        hostname_to_serial,
        get_interface_details_full
    )
except ImportError:
    print("\nmissing dcnm core module, please install first:\n")
    print(
        "python3 setup.py sdist bdist_wheel"
    )
    exit(1)


class DefaultInterface:
    """
    Class will be used in order to take the data in from Ansible module and discover overlays,
    remove them, and default the interface to a routed port

    args:
        conn - (obj) handles the connection to dcnm
        switch_list - (list) from ansible module
            Example: [{'name': 'rlf14lab', 'interfaces': ['Ethernet1/1']}, {'name': 'rlf15lab', 'interfaces': ['Ethernet1/1']}]
    """

    def __init__(self, conn, switch_list, fabric):
        self.conn = conn
        self.switch_list = switch_list
        self.fabric = fabric

    def check_for_int_connection(self):
        removed_interfaces = []
        bad_statuses = ["Link not connected", "XCVR not inserted"]
        for i,switch in enumerate(self.switch_list):
            serial_number = hostname_to_serial(self.conn, self.fabric, switch["name"])
            for i_face in switch["interfaces"]:
                i_face_details = get_interface_details_full(self.conn, serial_number, i_face)
                if i_face_details["operStatusStr"].lower() != "down":
                    if i_face_details["operStatusCause"] not in bad_statuses:
                        removed_interfaces.append((switch["name"],i_face))
        for n, item in enumerate(self.switch_list):
            for entry in removed_interfaces:
                if item["name"].lower() == entry[0].lower():
                    self.switch_list[n]["interfaces"].remove(entry[1])
        rem_switch = []
        for switch in self.switch_list:
            if not switch["interfaces"]:
                rem_switch.append(switch)
        if rem_switch:
            for switch in rem_switch:
                for device in self.switch_list:
                    if switch == device:
                        self.switch_list.remove(device)
        return removed_interfaces

    def check_for_overlay(self):
        """
        This function will look at the switches and interfaces provided to check if there is any overlay networks
        attached to the ports.  If there is an Overlay attached it will return a list of overlay that we must remove
        before we can default the interface
        need to grab: ['overlayNetwork']['templateName']: '10-96-107-80_28_VL402_10402' from data. (probably split it on ',' for trunk ports)

        Return (list)
            Example of deattach_list: [{'networkName': ['10-96-100-0_24_VL400_10400'], 'interfaces': 'Ethernet1/1', 'serialNumber': 'FDO22222T5B'}]
        """
        deattach_list = []
        interface_details = self.conn.get("/rest/globalInterface").json()
        for sw in self.switch_list:
            for int in sw["interfaces"]:
                for entry in interface_details:
                    if (
                        sw["name"] == entry["sysName"]
                        and entry["ifName"] == int
                        and len(entry["overlayNetwork"]) > 0
                    ):
                        deattach_list.append(
                            {
                                "networkName": [
                                    x["templateName"] for x in entry["overlayNetwork"]
                                ],
                                "interfaces": entry["ifName"],
                                "serialNumber": entry["serialNo"],
                            }
                        )
        return deattach_list

    def get_policies_to_delete(self, data):
        """
        This function takes the return data from check_for_overlay and formats it approprately for the payload needed to deattach.
        Input: [{'networkName': ['10-96-100-0_24_VL400_10400'], 'interfaces': 'Ethernet1/1', 'serialNumber': 'FDO22222T5B'}]
        /rest/control/policies/PROFILE-NETWORK-60717
        returns: (list)
            ['PROFILE-NETWORK-60715', 'PROFILE-NETWORK-60717', 'PROFILE-NETWORK-60714', 'PROFILE-NETWORK-60716']
        """
        to_delete = []
        for d in data:
            for net in d["networkName"]:
                get_info = self.conn.get(
                    f"/rest/control/policies/switches/{d['serialNumber']}?source=OVERLAY"
                )
                to_delete.append(
                    [
                        x["policyId"]
                        for x in get_info.json()
                        if x["entityName"] == d["interfaces"]
                        and x["secondaryEntityName"] == net
                    ]
                )
        return [",".join(y) for y in to_delete]

    def deattach_payload_builder(self, data):
        """
        This function takes the return data from check_for_overlay and formats it approprately for the payload needed to deattach.
        Input: [{'networkName': ['10-96-100-0_24_VL400_10400'], 'interfaces': 'Ethernet1/1', 'serialNumber': 'FDO22222T5B'}]
        Returns: [{'networkName': '10-96-100-0_24_VL400_10400', 'vlanId': 400, 'attachInfo': [{'interfaces': ['Ethernet1/1'], 'serialNumber': 'FDO22222T5B'}]}]
        """
        final_list = []
        for net in data:
            final_dict = {}
            for n in net["networkName"]:
                final_list.append(
                    {
                        "networkName": n,
                        "vlanId": n.split("_")[2].lstrip("VL"),
                        "attachInfo": [
                            {
                                "interfaces": [net["interfaces"]],
                                "serialNumber": net["serialNumber"],
                            }
                        ],
                    }
                )
        return final_list

    def routed(self):
        """
        This function takes in a list of data for routed policies and creates proper dcnm api payload for the http call
        [{'name': 'rlf14lab', 'interfaces': ['Ethernet1/1']}, {'name': 'rlf15lab', 'interfaces': ['Ethernet1/1']}]
        """
        final_dict = {"policy": "int_routed_host_11_1", "interfaces": []}

        for sw in self.switch_list:
            for int in sw["interfaces"]:
                temp = {
                    "serialNumber": hostname_to_serial(
                        self.conn, self.conn.fabric, sw["name"]
                    ),
                    "ifName": int,
                    "nvPairs": {
                        "ADMIN_STATE": "false",
                        "CONF": "",
                        "DESC": "AVAILABLE",
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

    def deploy_list(self):
        interface_list = []
        for sw in self.switch_list:
            for int in sw["interfaces"]:
                interface_list.append(
                    {
                        "ifName": int,
                        "serialNumber": hostname_to_serial(
                            self.conn, self.conn.fabric, sw["name"]
                        ),
                    }
                )
        return interface_list


def main(m):
    """
    main function will login to dcnm get a token and call the bulk create API call to
    return results

    Args: m (dict) - module.params for Ansible

    return N/A however m.exit_json or m.fail_json will exit the function with Ansible
    """
    additional_info = ""
    if m.params["force_disable"]:
        additional_info = "Warning!! This was run without connectivity checks!"
    connection = Session(
        m.params["base_url"], m.params["username"], m.params["password"]
    )
    connection.login()
    connection.update_lan_creds()
    connection.fabric = m.params["fabric_name"]
    switches_list = []
    for switch in m.params["switches"]:
        switches_list.append({"name": switch, "interfaces": m.params["switches"][switch]})
    def_int = DefaultInterface(connection, switches_list, m.params["fabric_name"])
    removed_interfaces = False
    if not m.params["force_disable"]:
        removed_interfaces = def_int.check_for_int_connection()
        if removed_interfaces:
            additional_info = f"Interfaces removed from list due to connectivity {removed_interfaces}"
    needs_detach = def_int.check_for_overlay()
    if len(needs_detach) > 0:
        policies = def_int.get_policies_to_delete(needs_detach)
        results = [
            connection.delete(f"/rest/control/policies/{policy}") for policy in policies
        ]
        if False in results:
            m.fail_json(
                msg="Interface De-attach Failed Could Not attempt defaulting interfaces. Check GUI"
            )
    # Default all interfaces to routed ports
    policy_data_list = def_int.routed()
    results = update_interface_details(connection, policy_data_list)
    if results == False:
        m.fail_json(
            msg="Interface Update Failed, deployment was not attempted. Check GUI"
        )
    else:
        interface_list = def_int.deploy_list()
        deploy_results = deploy_interface_change(connection, interface_list)
        if deploy_results == True:
            m.exit_json(
                changed=True, meta=f"Interfaces updated and deployed.",
                additional_information=f"{additional_info}"
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
        "switches": {"required": True, "type": "dict"},
        "force_disable": {"default": False, "type": "bool"}
    }
    module = AnsibleModule(argument_spec=fields)
    try:
        main(module)
    except Exception as e:

        module.fail_json(msg=e)
